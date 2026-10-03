"""``troupe config set`` wiring (task 4.5): registration, handler, usage errors, leading-dash VALUE.

Driven through the real ``troupe`` console script (``set_support.run_config``) and, where the
handler itself is observed, through the in-process runner. The full byte-exact contract is
validated by the task-5 end-to-end modules; this module proves the CLI wiring.

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

import ast
import io
import json
from pathlib import Path

import pytest

import troupe.cli.main as cli_main
from set_support import (
    assert_file_bytes,
    run_config,
    run_config_in_process,
    run_set,
    set_args,
    snapshot_tree,
)
from support import CliResult, MakeTheatre

REPO_ROOT = Path(__file__).resolve().parents[1]
SET_COMMAND_SOURCE = REPO_ROOT / "src" / "troupe" / "cli" / "set_command.py"


def _config(theatre: Path) -> Path:
    return theatre / ".troupe" / "troupe.yml"


def _history_state(theatre: Path) -> list[str]:
    return sorted(p.name for p in (theatre / ".troupe").glob("history.sqlite*"))


def _events(result: CliResult) -> list[tuple[str, str, object]]:
    assert result.exit_code == 0, result.stderr
    lines = [json.loads(line) for line in result.stdout.decode("utf-8").splitlines()]
    return [(line["op"], line["path"], line["new"]) for line in lines]


def _assert_success(result: CliResult) -> None:
    assert (result.exit_code, result.stdout, result.stderr) == (0, b"", ""), result.stderr


def _assert_usage_error(result: CliResult) -> None:
    assert result.exit_code == 2, result.stderr
    assert result.stdout == b""
    assert result.stderr.endswith("\n") and result.stderr.strip(), result.stderr


# ---- 1.1: success from the repository root, nothing on stdout ----


def test_set_from_the_repository_root_changes_the_value_and_prints_nothing(
    make_theatre: MakeTheatre,
) -> None:
    """Req 1.1: `troupe config set a 2 --theatre DIR` from the repo root: exit 0, empty stdout."""
    theatre = make_theatre("a: 1\n")

    result = run_config("set", "a", "2", "--theatre", str(theatre), cwd=REPO_ROOT)

    _assert_success(result)
    assert_file_bytes(_config(theatre), "a: 2\n")


def test_set_records_the_change_after_the_import_on_a_fresh_theatre(
    make_theatre: MakeTheatre,
) -> None:
    """Req 1.6, 1.1: the first set imports the theatre first; history lists import then set."""
    theatre = make_theatre("a: 1\nb: x\n")
    assert _history_state(theatre) == []

    _assert_success(run_set(theatre, "a", "2"))

    history = run_config("history", "--theatre", str(theatre), cwd=theatre)
    assert _events(history) == [("import", "a", 1), ("import", "b", "x"), ("set", "a", 2)]


def test_handler_writes_nothing_to_the_output_buffer(make_theatre: MakeTheatre) -> None:
    """Req 1.1: in process, a successful set leaves stdout empty and changes the file."""
    theatre = make_theatre("a: 1\n")

    result = run_config_in_process(*set_args(theatre, "a", "[x, y]"))

    _assert_success(result)
    assert_file_bytes(_config(theatre), "a:\n  - x\n  - y\n")


# ---- 1.5: malformed command lines and malformed PATH or VALUE exit 2 and create no state ----


@pytest.mark.parametrize(
    "args",
    [
        pytest.param(("set", "a"), id="missing-value"),
        pytest.param(("set",), id="missing-path-and-value"),
        pytest.param(("set", "a", "1", "b"), id="surplus-argument"),
        pytest.param(("set", "a", "1", "--bogus"), id="unknown-option"),
        pytest.param(("set", "--bogus", "a", "1"), id="unknown-option-first"),
        pytest.param(("set", "a..b", "1"), id="malformed-path"),
        pytest.param(("set", "a", "a: 1"), id="block-value"),
        pytest.param(("set", "a", ""), id="empty-value"),
        pytest.param(("set", "a", "{a: 1, a: 2}"), id="duplicate-key"),
        pytest.param(("set", "a", "-"), id="lone-dash-is-a-block-list"),
        pytest.param(("set", "a", "--theatre"), id="value-spelled-like-the-option"),
    ],
)
def test_usage_errors_exit_2_with_no_output_and_no_history_state(
    make_theatre: MakeTheatre, args: tuple[str, ...]
) -> None:
    """Req 1.5, 1.3, 1.4, 16.1: usage errors exit 2, empty stdout, files and history untouched."""
    theatre = make_theatre("a: 1\n")
    before = snapshot_tree(theatre)

    result = run_config(*args, "--theatre", str(theatre), cwd=theatre)

    _assert_usage_error(result)
    assert snapshot_tree(theatre) == before
    assert _history_state(theatre) == []


@pytest.mark.parametrize(
    ("value", "key"),
    [("a: 1", None), ("{a: 1, a: 2}", "a"), ("-", None)],
)
def test_value_errors_name_path(make_theatre: MakeTheatre, value: str, key: str | None) -> None:
    """Req 1.3, 1.4: VALUE errors come from the value parser and name PATH (and the key)."""
    theatre = make_theatre("top: 1\n")

    result = run_set(theatre, "top", value)

    _assert_usage_error(result)
    assert "top" in result.stderr
    if key is not None:
        assert f"key {key!r}" in result.stderr or f"'{key}'" in result.stderr, result.stderr


# ---- 16.2, 16.4: refusals and load errors use the foundation's mapping ----


def test_include_root_refusal_exits_3_naming_file_and_path(make_theatre: MakeTheatre) -> None:
    """Req 16.2, 16.1, 1.6: a YAML include root refusal exits 3 as `<file>: <PATH>: <reason>`."""
    theatre = make_theatre(
        "agents:\n  coder: file:coder.yml\n", files={".troupe/coder.yml": "model: a\n"}
    )
    before = snapshot_tree(theatre)

    result = run_set(theatre, "agents.coder", "{model: x}")

    assert (result.exit_code, result.stdout) == (3, b""), result.stderr
    included = theatre / ".troupe" / "coder.yml"
    assert result.stderr.startswith(f"{included}: agents.coder: "), result.stderr
    assert result.stderr.count("\n") == 1
    assert snapshot_tree(theatre) == before
    history = run_config("history", "--theatre", str(theatre), cwd=theatre)
    assert [op for op, _, _ in _events(history)] == ["import"]


def test_broken_configuration_exits_4_naming_the_file(make_theatre: MakeTheatre) -> None:
    """Req 16.4: a configuration that cannot be loaded exits 4 and changes no file."""
    theatre = make_theatre("a: [1\n")
    before = snapshot_tree(theatre)

    result = run_set(theatre, "a", "2")

    assert (result.exit_code, result.stdout) == (4, b""), result.stderr
    assert result.stderr.startswith(f"{_config(theatre)}: "), result.stderr
    assert snapshot_tree(theatre) == before


# ---- 1.1, 1.5: VALUEs starting with "-" are values, not options ----

LEADING_DASH = [
    ("-1e3", "a: -1e3\n"),
    ("-.inf", "a: -.inf\n"),
    ("-x", "a: -x\n"),
    ("-1", "a: -1\n"),
    ("-0.5", "a: -0.5\n"),
    ("-hello", "a: -hello\n"),
    ("-theatre", "a: -theatre\n"),
    ("[-1e3, -x]", "a: [-1e3, -x]\n"),
]


def _theatre_layouts(theatre: Path, value: str) -> list[tuple[str, ...]]:
    return [
        ("set", "a", value, "--theatre", str(theatre)),
        ("set", "--theatre", str(theatre), "a", value),
        ("set", "a", "--theatre", str(theatre), value),
        ("set", f"--theatre={theatre}", "a", value),
        ("set", "a", value, f"--theatre={theatre}"),
    ]


@pytest.mark.parametrize(("value", "expected"), LEADING_DASH)
def test_leading_dash_values_are_set_through_the_console_script(
    make_theatre: MakeTheatre, tmp_path: Path, value: str, expected: str
) -> None:
    """Req 1.1, 1.5: `set a -1e3` etc. are values, with --theatre anywhere (and `--theatre=DIR`)."""
    theatre = make_theatre("a: [0]\n")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    for args in _theatre_layouts(theatre, value):
        _config(theatre).write_text("a: [0]\n", encoding="utf-8")
        result = run_config(*args, cwd=elsewhere)
        _assert_success(result)
        assert_file_bytes(_config(theatre), expected)
    assert list(elsewhere.iterdir()) == []


@pytest.mark.parametrize(("value", "expected"), [("-1e3", "a: -1e3\n"), ("-x", "a: -x\n")])
def test_leading_dash_values_in_process(
    make_theatre: MakeTheatre, value: str, expected: str
) -> None:
    """Req 1.1, 1.5: the in-process runner parses leading-dash VALUEs the same way."""
    theatre = make_theatre("a: 1\n")

    _assert_success(run_config_in_process(*set_args(theatre, "a", value)))

    assert_file_bytes(_config(theatre), expected)


def test_double_dash_ends_options_so_any_value_can_be_given(make_theatre: MakeTheatre) -> None:
    """Req 1.1, 1.5: after `--`, a VALUE spelled like an option (`--theatre`, `-h`) is a value."""
    theatre = make_theatre("a: 1\nb: 1\n")

    _assert_success(
        run_config("set", "--theatre", str(theatre), "a", "--", "--theatre", cwd=theatre)
    )
    _assert_success(run_config("set", "--theatre", str(theatre), "--", "b", "-h", cwd=theatre))

    assert_file_bytes(_config(theatre), "a: --theatre\nb: -h\n")


def test_help_still_works_and_lists_path_value_and_theatre(tmp_path: Path) -> None:
    """Req 1.5: `set -h` / `set --help` print the usage (exit 0) like the other subcommands."""
    for flag in ("-h", "--help"):
        result = run_config("set", flag, cwd=tmp_path)
        assert result.exit_code == 0, result.stderr
        assert result.stdout.startswith(b"usage: troupe config set")
        for word in (b"PATH", b"VALUE", b"--theatre"):
            assert word in result.stdout


def test_config_help_lists_set(tmp_path: Path) -> None:
    """Req 1.1: `troupe config --help` lists the `set` subcommand next to show and history."""
    result = run_config("--help", cwd=tmp_path)

    assert result.exit_code == 0
    assert b"set" in result.stdout and b"show" in result.stdout and b"history" in result.stdout


# ---- module boundary: set_command depends on the shell only, main has one more registrar ----


def test_set_command_imports_the_shell_and_never_main() -> None:
    """Req 1.1: set_command imports troupe.cli.shell and never troupe.cli.main (no import cycle)."""
    tree = ast.parse(SET_COMMAND_SOURCE.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)

    assert "troupe.cli.shell" in imported
    assert "troupe.cli.main" not in imported
    assert not any(name == "troupe.cli" for name in imported)


def test_main_registers_set_after_show_and_history() -> None:
    """Req 1.1: SUBCOMMAND_REGISTRARS gains exactly the `set` registrar."""
    from troupe.cli.get_show_command import add_get_subparser, add_show_subparser
    from troupe.cli.set_command import add_set_subparser, handle_set

    registrars = cli_main.SUBCOMMAND_REGISTRARS
    # Later specs append their registrars after `set` (config-file-sync's `watch`) or put them
    # first (config-get-show's `get`, followed by its `show`, which replaced step 1's).
    assert registrars[:4] == (
        add_get_subparser,
        add_show_subparser,
        cli_main.add_history_subparser,
        add_set_subparser,
    )
    assert registrars.count(add_set_subparser) == 1
    args = cli_main.build_parser().parse_args(["config", "set", "a", "-1e3"])
    assert (args.path, args.value, args.handler) == ("a", "-1e3", handle_set)


def test_handle_set_parses_before_opening_the_theatre(
    make_theatre: MakeTheatre, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Req 1.5: PATH and VALUE are parsed before the theatre is opened (no state on exit 2)."""
    import troupe.cli.set_command as set_command
    from troupe.config.errors import UsageError

    theatre = make_theatre("a: 1\n")
    opened: list[Path] = []

    def _forbidden(directory: Path) -> object:
        opened.append(directory)
        raise AssertionError("open_theatre called before VALUE was parsed")

    monkeypatch.setattr(set_command, "open_theatre", _forbidden)
    args = cli_main.build_parser().parse_args(
        ["config", "set", "a", "a: 1", "--theatre", str(theatre)]
    )

    with pytest.raises(UsageError):
        set_command.handle_set(args, io.BytesIO())
    assert opened == []
