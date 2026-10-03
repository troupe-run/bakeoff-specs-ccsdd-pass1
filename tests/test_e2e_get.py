"""``troupe config get PATH`` end to end through the real ``troupe`` console script (task 4.1).

The comprehensive proof of ``get``: every step 1 scalar printing case (config-foundation 6.1-6.6)
through ``get`` (1.1); a mapping or list target exits 5 with an empty stdout and one stderr line
naming the file that holds it, PATH and the ``config show`` hint (1.2, 6.7); a missing path exits
5 naming the file holding the deepest existing part of PATH (1.3); no PATH, two PATHs and any
unknown option including ``--sources`` exit 2 (1.4); an empty PATH or one with an empty segment
exits 2 naming PATH (1.5). Both root file names, ``troupe.yml`` and ``troupe.yaml``, are covered.

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

from pathlib import Path

import pytest

from support import CliResult, MakeTheatre, RunCli

CONFIG_NAMES = ["troupe.yml", "troupe.yaml"]


def _get(run_cli: RunCli, theatre: Path, *args: str) -> CliResult:
    return run_cli("config", "get", *args, "--theatre", str(theatre))


def _assert_prints(result: CliResult, expected: bytes) -> None:
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


def _assert_usage_error(result: CliResult) -> None:
    assert result.exit_code == 2, result.stderr
    assert result.stdout == b""
    assert result.stderr.endswith("\n") and result.stderr.strip(), result.stderr


def _no_history_state(theatre: Path) -> bool:
    return not list((theatre / ".troupe").glob("history.sqlite*"))


# ---- 1.1: every step 1 scalar printing case ----

SCALAR_CASES = [
    # strings: exact text, a newline added only when missing
    ("string-plain", "v: hello world\n", b"hello world\n"),
    ("string-quoted-final-newline", 'v: "line\\n"\n', b"line\n"),
    ("string-empty", "v: ''\n", b"\n"),
    ("string-unicode", "v: caf\u00e9 \u2603\n", "caf\u00e9 \u2603\n".encode()),
    ("string-looks-like-bool", "v: 'true'\n", b"true\n"),
    ("string-looks-like-int", 'v: "5"\n', b"5\n"),
    ("literal-inner-newlines", "v: |\n  one\n  two\n", b"one\ntwo\n"),
    ("literal-strip", "v: |-\n  one\n  two\n", b"one\ntwo\n"),
    ("literal-keep", "v: |+\n  one\n\n", b"one\n\n"),
    ("folded-clip", "v: >\n  a\n  b\n", b"a b\n"),
    ("folded-strip", "v: >-\n  a\n  b\n", b"a b\n"),
    ("folded-keep", "v: >+\n  a\n  b\n\n", b"a b\n\n"),
    # booleans and null
    ("true", "v: true\n", b"true\n"),
    ("false", "v: false\n", b"false\n"),
    ("null-word", "v: null\n", b"null\n"),
    ("null-tilde", "v: ~\n", b"null\n"),
    ("null-empty", "v:\n", b"null\n"),
    # integers in decimal
    ("int", "v: 42\n", b"42\n"),
    ("int-negative", "v: -7\n", b"-7\n"),
    ("int-octal", "v: 0o17\n", b"15\n"),
    ("int-hex", "v: 0x1F\n", b"31\n"),
    # floats in Python repr() form
    ("float", "v: 1.5\n", b"1.5\n"),
    ("float-exponent", "v: 1e3\n", b"1000.0\n"),
    ("float-inf", "v: .inf\n", b"inf\n"),
    ("float-negative-inf", "v: -.inf\n", b"-inf\n"),
    ("float-nan", "v: .nan\n", b"nan\n"),
]


@pytest.mark.parametrize(
    ("content", "expected"),
    [(c, e) for _, c, e in SCALAR_CASES],
    ids=[i for i, _, _ in SCALAR_CASES],
)
def test_get_prints_each_scalar_kind_exactly_as_step_1_show(
    make_theatre: MakeTheatre, run_cli: RunCli, content: str, expected: bytes
) -> None:
    """Req 1.1: strings exactly (newline added only when missing), `true`/`false`, `null`,
    integers in decimal and floats in `repr()` form, exit 0 with an empty stderr."""
    theatre = make_theatre(content)

    _assert_prints(_get(run_cli, theatre, "v"), expected)


def test_get_folded_block_prints_no_added_character(
    make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    """Req 1.1: a folded block prints its folded text with no added character (no bell byte)."""
    theatre = make_theatre("v: >\n  first line\n  second line\n\n  third\n")

    result = _get(run_cli, theatre, "v")

    assert b"\x07" not in result.stdout
    _assert_prints(result, b"first line second line\nthird\n")


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        pytest.param(b"hello\r\nworld\r\n", b"hello\r\nworld\r\n", id="crlf"),
        pytest.param(b"hello\r\nworld", b"hello\r\nworld\n", id="crlf-no-final-newline"),
        pytest.param(
            b"  lead\n\n\ttrail  \n\n", b"  lead\n\n\ttrail  \n\n", id="spaces-and-blanks"
        ),
        pytest.param(b"no final newline", b"no final newline\n", id="no-final-newline"),
        pytest.param(b"", b"\n", id="empty"),
    ],
)
def test_get_text_include_prints_the_included_text_byte_exact(
    make_theatre: MakeTheatre, run_cli: RunCli, content: bytes, expected: bytes
) -> None:
    """Req 1.1: a text include prints as the included file's exact text (CRLF kept), with a
    newline added only when the text does not already end with one."""
    theatre = make_theatre("p: file:prompt.txt\n", files={".troupe/prompt.txt": content})

    _assert_prints(_get(run_cli, theatre, "p"), expected)


INCLUDES = {
    ".troupe/team/team.yml": "lead: file:lead.yml\nsize: 2\n",
    ".troupe/team/lead.yml": "name: alice\ntags:\n  - a\n  - b\nbio: file:bio.txt\n",
    ".troupe/team/bio.txt": "Hello.\r\n",
}


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("team.size", b"2\n"),
        ("team.lead.name", b"alice\n"),
        ("team.lead.tags.0", b"a\n"),
        ("team.lead.tags.1", b"b\n"),
        ("team.lead.bio", b"Hello.\r\n"),
        ("crew.1", b"bob\n"),
    ],
)
def test_get_reaches_values_inside_yaml_includes_and_list_items_by_index(
    make_theatre: MakeTheatre, run_cli: RunCli, path: str, expected: bytes
) -> None:
    """Req 1.1: a value inside a (nested) YAML include, a text include reached through it and a
    list item addressed by index print exactly."""
    theatre = make_theatre("team: file:team/team.yml\ncrew:\n  - ada\n  - bob\n", files=INCLUDES)

    _assert_prints(_get(run_cli, theatre, path), expected)


@pytest.mark.parametrize("config_name", CONFIG_NAMES)
def test_get_works_with_either_root_file_name(
    make_theatre: MakeTheatre, run_cli: RunCli, config_name: str
) -> None:
    """Req 1.1: `get` reads `troupe.yml` and `troupe.yaml` alike."""
    theatre = make_theatre("a:\n  b: hi\n", config_name=config_name)

    _assert_prints(_get(run_cli, theatre, "a.b"), b"hi\n")


# ---- 1.2, 6.7: a mapping or list exits 5 with the `config show` hint ----

NESTED = "a:\n  b:\n    - x\n    - [y, z]\n  s: hi\n  e: {}\n  l: []\n"


@pytest.mark.parametrize(
    ("path", "kind"),
    [
        ("a", "a mapping"),
        ("a.e", "a mapping"),
        ("a.b", "a list"),
        ("a.b.1", "a list"),
        ("a.l", "a list"),
    ],
)
@pytest.mark.parametrize("config_name", CONFIG_NAMES)
def test_get_mapping_or_list_exits_5_naming_file_path_and_show_hint(
    make_theatre: MakeTheatre, run_cli: RunCli, config_name: str, path: str, kind: str
) -> None:
    """Req 1.2, 6.7: a mapping or list target exits 5, stdout empty, one stderr line naming the
    root file and PATH and pointing to `troupe config show PATH`."""
    theatre = make_theatre(NESTED, config_name=config_name)

    result = _get(run_cli, theatre, path)

    problem = _assert_path_failure(result, theatre / ".troupe" / config_name, path)
    assert problem == f"is {kind}, not a value; print it with `troupe config show {path}`"


@pytest.mark.parametrize(
    ("path", "file", "kind"),
    [
        ("team", "team/team.yml", "a mapping"),
        ("team.lead", "team/lead.yml", "a mapping"),
        ("team.lead.tags", "team/lead.yml", "a list"),
    ],
)
def test_get_collection_in_a_yaml_include_names_the_included_file(
    make_theatre: MakeTheatre, run_cli: RunCli, path: str, file: str, kind: str
) -> None:
    """Req 1.2, 6.7: a mapping or list written in a YAML include exits 5 naming the included
    file that holds it, PATH and the `config show` hint."""
    theatre = make_theatre("team: file:team/team.yml\n", files=INCLUDES)

    result = _get(run_cli, theatre, path)

    problem = _assert_path_failure(result, theatre / ".troupe" / file, path)
    assert problem == f"is {kind}, not a value; print it with `troupe config show {path}`"


# ---- 1.3, 6.7: a missing path exits 5 naming the deepest existing file ----


@pytest.mark.parametrize(
    ("path", "file", "segment"),
    [
        ("missing", "troupe.yml", "missing"),
        ("a.missing", "troupe.yml", "missing"),
        ("a.b.2", "troupe.yml", "2"),
        ("a.s.child", "troupe.yml", "child"),
        ("team.other", "team/team.yml", "other"),
        ("team.lead.missing", "team/lead.yml", "missing"),
        ("team.lead.tags.5", "team/lead.yml", "5"),
        ("team.lead.bio.line", "team/bio.txt", "line"),
    ],
)
def test_get_missing_path_exits_5_naming_the_deepest_existing_file(
    make_theatre: MakeTheatre, run_cli: RunCli, path: str, file: str, segment: str
) -> None:
    """Req 1.3, 6.7: a path that is not found exits 5, stdout empty, one stderr line naming the
    file holding the deepest existing part of PATH (an included file when it lies there)."""
    theatre = make_theatre(NESTED + "team: file:team/team.yml\n", files=INCLUDES)

    result = _get(run_cli, theatre, path)

    problem = _assert_path_failure(result, theatre / ".troupe" / file, path)
    assert problem.startswith("not found"), problem
    assert f"'{segment}'" in problem
    assert "config show" not in problem


# ---- 1.4: command-line problems exit 2 ----


@pytest.mark.parametrize(
    ("args", "named"),
    [
        pytest.param((), "PATH", id="no-path"),
        pytest.param(("a", "b"), "b", id="two-paths"),
        pytest.param(("a", "--sources"), "--sources", id="sources-after"),
        pytest.param(("--sources", "a"), "--sources", id="sources-before"),
        pytest.param(("a", "--bogus"), "--bogus", id="unknown-option"),
    ],
)
@pytest.mark.parametrize("config_name", CONFIG_NAMES)
def test_get_command_line_problem_exits_2_with_empty_stdout(
    make_theatre: MakeTheatre,
    run_cli: RunCli,
    config_name: str,
    args: tuple[str, ...],
    named: str,
) -> None:
    """Req 1.4: no PATH, two PATHs or an unknown option (incl. `--sources` before or after PATH)
    exit 2 with an empty stdout and a stderr message naming the problem; nothing is opened."""
    theatre = make_theatre("a: 1\nb: 2\n", config_name=config_name)

    result = _get(run_cli, theatre, *args)

    _assert_usage_error(result)
    assert named in result.stderr, result.stderr
    assert _no_history_state(theatre)


# ---- 1.5: an empty PATH or an empty segment exits 2 naming PATH ----


@pytest.mark.parametrize("path", ["a..b", ".a", "a.", ""])
def test_get_malformed_path_exits_2_naming_the_path(
    make_theatre: MakeTheatre, run_cli: RunCli, path: str
) -> None:
    """Req 1.5: an empty PATH or one with an empty segment exits 2, stdout empty, stderr naming
    the offending PATH; the theatre is not opened."""
    theatre = make_theatre("a:\n  b: 1\n")

    result = _get(run_cli, theatre, path)

    _assert_usage_error(result)
    assert f"malformed PATH {path!r}" in result.stderr, result.stderr
    assert result.stderr.count("\n") == 1, result.stderr
    assert _no_history_state(theatre)
