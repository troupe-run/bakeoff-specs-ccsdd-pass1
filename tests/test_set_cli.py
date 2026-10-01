"""End-to-end validation of `troupe config set` invocation, VALUE parsing, import and errors (5.1).

Every case runs the real ``troupe`` console script (``set_support``). VALUE is the exact argv
string, written as a Python literal. Success cases assert exit 0, empty stdout, empty stderr, the
exact bytes of the changed file and that no other file changed; failure cases assert the exit
code, empty stdout, the stderr naming PATH and/or the file, and an unchanged theatre.

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

import json
from pathlib import Path

import pytest

from set_support import assert_file_bytes, run_config, run_set, snapshot_tree
from support import CliResult, MakeTheatre


def _config(theatre: Path) -> Path:
    return theatre / ".troupe" / "troupe.yml"


def _history_state(theatre: Path) -> list[str]:
    return sorted(p.name for p in (theatre / ".troupe").glob("history.sqlite*"))


def _history(theatre: Path) -> list[tuple[str, str, object, object]]:
    result = run_config("history", "--theatre", str(theatre), cwd=theatre)
    assert (result.exit_code, result.stderr) == (0, ""), result.stderr
    rows = [json.loads(line) for line in result.stdout.decode("utf-8").splitlines()]
    return [(row["op"], row["path"], row["old"], row["new"]) for row in rows]


def _assert_only_config_changed(theatre: Path, before: dict[str, bytes], expected: str) -> None:
    after = snapshot_tree(theatre)
    assert_file_bytes(_config(theatre), expected)
    assert {k: v for k, v in after.items() if k != ".troupe/troupe.yml"} == {
        k: v for k, v in before.items() if k != ".troupe/troupe.yml"
    }


def _assert_failure(result: CliResult, code: int) -> None:
    assert result.exit_code == code, (result.exit_code, result.stderr)
    assert result.stdout == b""
    assert result.stderr.endswith("\n") and result.stderr.count("\n") == 1, result.stderr


# ---- 1.1, 1.2: the contract examples of VALUE parsing ----

CONTRACT_EXAMPLES = [
    pytest.param("0.2", "a: 0.2\n", id="float"),
    pytest.param("true", "a: true\n", id="bool"),
    pytest.param("null", "a: null\n", id="null"),
    pytest.param("abc", "a: abc\n", id="plain-string"),
    pytest.param('"0.2"', "a: '0.2'\n", id="quoted-number-string"),
    pytest.param("{a: 1}", "a:\n  a: 1\n", id="mapping"),
    pytest.param("[a, b]", "a:\n  - a\n  - b\n", id="list"),
    pytest.param('"line one\\nline two\\n"', "a: |\n  line one\n  line two\n", id="multi-line"),
]


@pytest.mark.parametrize(("value", "expected"), CONTRACT_EXAMPLES)
def test_contract_values_are_parsed_as_one_flow_node_and_written_byte_exactly(
    make_theatre: MakeTheatre, value: str, expected: str
) -> None:
    """Req 1.1, 1.2: each contract VALUE on `'a: x\\n'` gives the exact file bytes, exit 0."""
    theatre = make_theatre("a: x\n")
    before = snapshot_tree(theatre)

    result = run_set(theatre, "a", value)

    assert (result.exit_code, result.stdout, result.stderr) == (0, b"", ""), result.stderr
    _assert_only_config_changed(theatre, before, expected)


def test_contract_values_are_recorded_with_their_types(make_theatre: MakeTheatre) -> None:
    """Req 1.2: `0.2` is a float, `true` a bool, `null` null and `"0.2"` a string in the history."""
    theatre = make_theatre("a: x\n")

    for value in ("0.2", "true", "null", '"0.2"', "abc"):
        result = run_set(theatre, "a", value)
        assert result.exit_code == 0, result.stderr

    sets = [(old, new) for op, _, old, new in _history(theatre) if op == "set"]
    assert sets == [("x", 0.2), (0.2, True), (True, None), (None, "0.2"), ("0.2", "abc")]


# ---- 1.3, 1.4, 1.5, 16.1: usage errors exit 2 and create no history state ----

VALUE_ERRORS = [
    pytest.param("", "not a single flow node", id="empty"),
    pytest.param("# c", "not a single flow node", id="comment-only"),
    pytest.param("a: 1", "not a single flow node", id="block-mapping"),
    pytest.param("- a", "not a single flow node", id="block-list"),
    pytest.param("!!str 5", "not a single flow node", id="tag"),
    pytest.param("&x 1", "not a single flow node", id="anchor"),
    pytest.param("{a: 1, a: 2}", "'a'", id="duplicate-key"),
]


@pytest.mark.parametrize(("value", "fragment"), VALUE_ERRORS)
def test_invalid_values_exit_2_naming_path_and_create_no_state(
    make_theatre: MakeTheatre, value: str, fragment: str
) -> None:
    """Req 1.3, 1.4, 16.1: a VALUE that is not one flow node exits 2 as `<PATH>: ...`, no state."""
    theatre = make_theatre("top: x\n")
    before = snapshot_tree(theatre)

    result = run_set(theatre, "top", value)

    _assert_failure(result, 2)
    assert result.stderr.startswith("top: "), result.stderr
    assert fragment in result.stderr, result.stderr
    assert snapshot_tree(theatre) == before
    assert _history_state(theatre) == []


def test_duplicate_key_error_names_the_offending_key(make_theatre: MakeTheatre) -> None:
    """Req 1.4: `{a: 1, a: 2}` exits 2 with a message naming PATH and the duplicate key `a`."""
    theatre = make_theatre("top: x\n")

    result = run_set(theatre, "top", "{a: 1, a: 2}")

    _assert_failure(result, 2)
    assert result.stderr.startswith("top: ")
    assert "duplicate" in result.stderr and "'a'" in result.stderr, result.stderr


@pytest.mark.parametrize(
    ("args", "fragment"),
    [
        pytest.param(("set", "a"), "", id="missing-value"),
        pytest.param(("set", "a", "1", "b"), "", id="surplus-argument"),
        pytest.param(("set", "a", "1", "--bogus"), "--bogus", id="unknown-option"),
        pytest.param(("set", "a..b", "1"), "a..b", id="malformed-path"),
    ],
)
def test_malformed_command_lines_exit_2_and_create_no_state(
    make_theatre: MakeTheatre, args: tuple[str, ...], fragment: str
) -> None:
    """Req 1.5, 16.1: missing VALUE, surplus argument, `--bogus`, PATH `a..b` exit 2, no state."""
    theatre = make_theatre("a: x\n")
    before = snapshot_tree(theatre)

    result = run_config(*args, "--theatre", str(theatre), cwd=theatre)

    assert result.exit_code == 2, result.stderr
    assert result.stdout == b""
    assert result.stderr.strip(), result.stderr
    assert fragment in result.stderr, result.stderr
    assert snapshot_tree(theatre) == before
    assert _history_state(theatre) == []


# ---- 16.4: a configuration that cannot be loaded exits 4 naming the file ----


def test_broken_yaml_exits_4_naming_the_file_and_changes_nothing(
    make_theatre: MakeTheatre,
) -> None:
    """Req 16.4, 16.1: a broken YAML file exits 4 as `<file>: ...`, files and history untouched."""
    theatre = make_theatre("a: [1\n")
    before = snapshot_tree(theatre)

    result = run_set(theatre, "a", "2")

    _assert_failure(result, 4)
    assert result.stderr.startswith(f"{_config(theatre)}: "), result.stderr
    assert snapshot_tree(theatre) == before
    assert _history_state(theatre) == []


# ---- 1.6: the first set imports the theatre, whatever its outcome ----


def test_successful_first_set_imports_then_records_the_set(make_theatre: MakeTheatre) -> None:
    """Req 1.6, 1.1: on a fresh theatre the history lists the import events, then the set."""
    theatre = make_theatre("a: x\nb: 1\n")
    before = snapshot_tree(theatre)
    assert _history_state(theatre) == []

    result = run_set(theatre, "a", "y")

    assert (result.exit_code, result.stdout, result.stderr) == (0, b"", ""), result.stderr
    _assert_only_config_changed(theatre, before, "a: y\nb: 1\n")
    assert _history(theatre) == [
        ("import", "a", None, "x"),
        ("import", "b", None, 1),
        ("set", "a", "x", "y"),
    ]


def test_refused_first_set_exit_3_still_imports_and_records_no_set(
    make_theatre: MakeTheatre,
) -> None:
    """Req 1.6, 16.1, 16.2: an include-root refusal exits 3 naming file and PATH; import is kept."""
    theatre = make_theatre(
        "agents:\n  coder: file:agents/coder.yaml\n",
        files={".troupe/agents/coder.yaml": "model: small\n"},
    )
    before = snapshot_tree(theatre)

    result = run_set(theatre, "agents.coder", "{model: x}")

    _assert_failure(result, 3)
    included = theatre / ".troupe" / "agents" / "coder.yaml"
    assert result.stderr.startswith(f"{included}: agents.coder: "), result.stderr
    assert snapshot_tree(theatre) == before
    assert _history(theatre) == [("import", "agents.coder.model", None, "small")]


def test_not_found_first_set_exit_5_still_imports_and_records_no_set(
    make_theatre: MakeTheatre,
) -> None:
    """Req 1.6, 16.1: `l.5` on `'l: [a]\\n'` exits 5 naming file and PATH; the import is kept."""
    theatre = make_theatre("l: [a]\n")
    before = snapshot_tree(theatre)

    result = run_set(theatre, "l.5", "x")

    _assert_failure(result, 5)
    assert result.stderr.startswith(f"{_config(theatre)}: l.5: "), result.stderr
    assert snapshot_tree(theatre) == before
    assert _history(theatre) == [("import", "l.0", None, "a")]
