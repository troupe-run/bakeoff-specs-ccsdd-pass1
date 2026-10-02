"""``troupe config show``, end to end through the real ``troupe`` console script.

Covers the printing rules for every scalar kind (6.1-6.5) under the YAML 1.2 core schema (3.1),
folded block scalars printed with no added character (3.8), list items by index (5.1-5.3), path
errors for mapping/list targets and missing paths (5.5, 6.7, 6.8) with exit 5, empty stdout and a
stderr message naming the file and PATH, malformed PATH syntax (1.5) with exit 2, and the
product-owner decision A: files are the truth, so a hand edit after the import is shown (6.9) and,
since step 2, recorded by the reconcile that ``show`` runs first (config-file-sync 1.1, 1.6).
"""

from pathlib import Path

import pytest

from support import CliResult, MakeTheatre, RunCli


def _show(run_cli: RunCli, theatre: Path, path: str) -> CliResult:
    return run_cli("config", "show", path, "--theatre", str(theatre))


def _history(run_cli: RunCli, theatre: Path) -> CliResult:
    return run_cli("config", "history", "--theatre", str(theatre))


def _assert_shows(result: CliResult, expected: bytes) -> None:
    assert (result.exit_code, result.stdout, result.stderr) == (0, expected, "")


def _assert_path_failure(result: CliResult, file: Path, path: str) -> str:
    """Assert exit 5, empty stdout and one stderr line ``<file>: <path>: <problem>``.

    Returns the problem text.
    """
    prefix = f"{file}: {path}: "
    assert result.exit_code == 5, result.stderr
    assert result.stdout == b""
    assert result.stderr.startswith(prefix), result.stderr
    assert result.stderr.endswith("\n")
    assert result.stderr.count("\n") == 1, result.stderr
    return result.stderr[len(prefix) : -1]


# ---- 6.1, 3.8: strings print exactly, with a newline only when missing ----

STRING_CASES = [
    ("plain", "v: hello world\n", b"hello world\n"),
    ("single-quoted", "v: 'it''s  spaced '\n", b"it's  spaced \n"),
    ("double-quoted", 'v: "tab\\there"\n', b"tab\there\n"),
    ("double-quoted-ending-in-newline", 'v: "line\\n"\n', b"line\n"),
    ("empty-string", "v: ''\n", b"\n"),
    ("unicode", "v: caf\u00e9 \u2603\n", "caf\u00e9 \u2603\n".encode()),
    ("literal-clip", "v: |\n  one\n  two\n", b"one\ntwo\n"),
    ("literal-strip", "v: |-\n  one\n  two\n", b"one\ntwo\n"),
    ("literal-keep", "v: |+\n  one\n\n", b"one\n\n"),
    ("folded-clip", "v: >\n  a\n  b\n", b"a b\n"),
    ("folded-strip", "v: >-\n  a\n  b\n", b"a b\n"),
    ("folded-keep", "v: >+\n  a\n  b\n\n", b"a b\n\n"),
    ("folded-paragraphs", "v: >\n  a\n  b\n\n  c\n", b"a b\nc\n"),
    ("folded-more-indented", "v: >2\n   x\n  y\n  z\n", b" x\ny z\n"),
    ("core-schema-yes", "v: yes\n", b"yes\n"),
    ("core-schema-on", "v: on\n", b"on\n"),
    ("core-schema-quoted-true", "v: 'true'\n", b"true\n"),
    ("core-schema-quoted-number", 'v: "5"\n', b"5\n"),
    ("core-schema-sexagesimal", "v: 1:30\n", b"1:30\n"),
]


@pytest.mark.parametrize(
    ("content", "expected"),
    [(c, e) for _, c, e in STRING_CASES],
    ids=[i for i, _, _ in STRING_CASES],
)
def test_show_string_prints_exact_text_with_newline_only_when_missing(
    make_theatre: MakeTheatre, run_cli: RunCli, content: str, expected: bytes
) -> None:
    theatre = make_theatre(content)

    _assert_shows(_show(run_cli, theatre, "v"), expected)


@pytest.mark.parametrize(
    ("indicator", "expected"),
    [
        (">", b"first line second line\nthird\n"),
        (">-", b"first line second line\nthird\n"),
        (">+", b"first line second line\nthird\n\n"),
        (">2", b"first line second line\nthird\n"),
        (">1", b" first line\n second line\n\n third\n"),
    ],
)
def test_show_folded_value_prints_folded_text_with_no_bell_byte(
    make_theatre: MakeTheatre, run_cli: RunCli, indicator: str, expected: bytes
) -> None:
    theatre = make_theatre(f"v: {indicator}\n  first line\n  second line\n\n  third\n\n")

    result = _show(run_cli, theatre, "v")

    assert b"\x07" not in result.stdout
    _assert_shows(result, expected)


# ---- 6.2, 6.3, 6.4, 6.5, 3.1: non-string scalars ----

SCALAR_CASES = [
    ("true", "v: true\n", b"true\n"),
    ("false", "v: false\n", b"false\n"),
    ("true-capitalised", "v: True\n", b"true\n"),
    ("null-word", "v: null\n", b"null\n"),
    ("null-tilde", "v: ~\n", b"null\n"),
    ("null-empty", "v:\n", b"null\n"),
    ("int", "v: 42\n", b"42\n"),
    ("int-negative", "v: -7\n", b"-7\n"),
    ("int-plus", "v: +3\n", b"3\n"),
    ("int-octal", "v: 0o17\n", b"15\n"),
    ("int-hex", "v: 0x1F\n", b"31\n"),
    ("int-zero", "v: 0\n", b"0\n"),
    ("int-large", "v: 123456789012345678901234567890\n", b"123456789012345678901234567890\n"),
    ("float", "v: 0.2\n", b"0.2\n"),
    ("float-exponent", "v: 1e3\n", b"1000.0\n"),
    ("float-negative-exponent", "v: 2.5e-3\n", b"0.0025\n"),
    ("float-leading-dot", "v: .5\n", b"0.5\n"),
    ("float-negative-zero", "v: -0.0\n", b"-0.0\n"),
    ("float-inf", "v: .inf\n", b"inf\n"),
    ("float-negative-inf", "v: -.inf\n", b"-inf\n"),
    ("float-inf-capitalised", "v: .Inf\n", b"inf\n"),
    ("float-nan", "v: .nan\n", b"nan\n"),
]


@pytest.mark.parametrize(
    ("content", "expected"),
    [(c, e) for _, c, e in SCALAR_CASES],
    ids=[i for i, _, _ in SCALAR_CASES],
)
def test_show_non_string_scalar_prints_its_canonical_form(
    make_theatre: MakeTheatre, run_cli: RunCli, content: str, expected: bytes
) -> None:
    theatre = make_theatre(content)

    _assert_shows(_show(run_cli, theatre, "v"), expected)


# ---- 5.1, 5.2, 5.3: dotted paths and list indices ----


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("team.members.0", b"ada\n"),
        ("team.members.1.name", b"bob\n"),
        ("team.members.1.roles.1", b"review\n"),
        ("team.members.2", b"3\n"),
        ("team.lead", b"ada\n"),
        ("7", b"seven\n"),
    ],
)
def test_show_follows_dotted_path_and_list_indices(
    make_theatre: MakeTheatre, run_cli: RunCli, path: str, expected: bytes
) -> None:
    theatre = make_theatre(
        "team:\n"
        "  lead: ada\n"
        "  members:\n"
        "    - ada\n"
        "    - name: bob\n"
        "      roles: [build, review]\n"
        "    - 3\n"
        "'7': seven\n"
    )

    _assert_shows(_show(run_cli, theatre, path), expected)


def test_show_works_with_troupe_yaml_root(make_theatre: MakeTheatre, run_cli: RunCli) -> None:
    theatre = make_theatre("v: 1\n", config_name="troupe.yaml")

    _assert_shows(_show(run_cli, theatre, "v"), b"1\n")


# ---- 6.7: a mapping or list target is exit 5 ----

NESTED = "a:\n  b:\n    - x\n    - [y, z]\n  s: hi\n  e: {}\n  l: []\n"


@pytest.mark.parametrize(
    ("path", "kind"),
    [("a", "mapping"), ("a.b", "list"), ("a.b.1", "list"), ("a.e", "mapping"), ("a.l", "list")],
)
@pytest.mark.parametrize("config_name", ["troupe.yml", "troupe.yaml"])
def test_show_mapping_or_list_target_exits_5_naming_file_and_path(
    make_theatre: MakeTheatre, run_cli: RunCli, config_name: str, path: str, kind: str
) -> None:
    theatre = make_theatre(NESTED, config_name=config_name)

    result = _show(run_cli, theatre, path)

    problem = _assert_path_failure(result, theatre / ".troupe" / config_name, path)
    assert kind in problem


# ---- 5.5, 6.8: a path that is not found is exit 5 ----


@pytest.mark.parametrize(
    ("path", "segment"),
    [
        ("missing", "missing"),
        ("a.missing", "missing"),
        ("a.b.2", "2"),
        ("a.b.1.5", "5"),
        ("a.b.first", "first"),
        ("a.b.-1", "-1"),
        ("a.s.child", "child"),
        ("a.s.0", "0"),
        ("a.b.0.deeper", "deeper"),
        ("a.e.k", "k"),
        ("a.l.0", "0"),
    ],
)
def test_show_path_not_found_exits_5_naming_file_path_and_segment(
    make_theatre: MakeTheatre, run_cli: RunCli, path: str, segment: str
) -> None:
    theatre = make_theatre(NESTED)

    result = _show(run_cli, theatre, path)

    problem = _assert_path_failure(result, theatre / ".troupe" / "troupe.yml", path)
    assert problem.startswith("not found"), problem
    assert f"'{segment}'" in problem


def test_show_digit_segment_on_a_mapping_selects_the_key_not_an_index(
    make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    theatre = make_theatre("m:\n  x: first\n")

    result = _show(run_cli, theatre, "m.0")

    _assert_path_failure(result, theatre / ".troupe" / "troupe.yml", "m.0")


def test_show_on_empty_configuration_reports_path_not_found(
    make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    theatre = make_theatre("# nothing here\n")

    result = _show(run_cli, theatre, "a")

    _assert_path_failure(result, theatre / ".troupe" / "troupe.yml", "a")


# ---- 1.5: malformed PATH syntax is a usage error (exit 2) ----


@pytest.mark.parametrize("path", ["", "a..b", ".a", "a.", ".", "a.b..", "..x"])
def test_show_malformed_path_exits_2_naming_path_without_creating_state(
    make_theatre: MakeTheatre, run_cli: RunCli, path: str
) -> None:
    theatre = make_theatre("a:\n  b: 1\n")

    result = _show(run_cli, theatre, path)

    assert result.exit_code == 2, result.stderr
    assert result.stdout == b""
    assert f"'{path}'" in result.stderr
    assert "malformed PATH" in result.stderr
    assert not list((theatre / ".troupe").glob("history.sqlite*"))


# ---- 6.9, 7.3 superseded by config-file-sync 1.1, 1.6: files are the truth, show reconciles ----


def test_show_after_hand_edit_prints_new_value_and_records_one_reconcile_set(
    make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    """Req 1.1, 1.3, 1.6 (config-file-sync): ``show`` after a hand edit prints the value in the
    file and records one reconcile ``set`` (old = imported value, new = edited value); ``history``
    before that ``show`` records nothing."""
    theatre = make_theatre("a: 1\n")
    config = theatre / ".troupe" / "troupe.yml"

    _assert_shows(_show(run_cli, theatre, "a"), b"1\n")
    first_history = _history(run_cli, theatre)
    assert (first_history.exit_code, first_history.stderr) == (0, "")
    assert first_history.stdout == (
        b'{"seq": 1, "op": "import", "path": "a", "old": null, "new": 1}\n'
    )

    config.write_bytes(b"a: 2\n")

    assert _history(run_cli, theatre).stdout == first_history.stdout
    _assert_shows(_show(run_cli, theatre, "a"), b"2\n")
    second_history = _history(run_cli, theatre)
    assert (second_history.exit_code, second_history.stderr) == (0, "")
    assert second_history.stdout == first_history.stdout + (
        b'{"seq": 2, "op": "set", "path": "a", "old": 1, "new": 2}\n'
    )
    assert config.read_bytes() == b"a: 2\n"
