"""CLI usage behaviour, driven through the real ``troupe`` console script and ``run``.

Covers command invocation and theatre selection (1.1-1.5), the usage-error cases of 1.4, exit-code
and stderr mapping with empty stdout on failure (10.1-10.3, 10.5), and the store being closed at
the end of every command (7.9).
"""

import argparse
import ast
import io
import json
from pathlib import Path

import pytest

import troupe.cli.main as cli_main
from support import MakeTheatre, RunCli, step1_members
from troupe.cli import shell
from troupe.cli.shell import Handler
from troupe.config.errors import PathNotFoundError

CONFIG = "name: demo\nagents:\n  lead: alice\n  count: 3\nflags: [true, null]\n"


def _history_files(theatre: Path) -> list[str]:
    return sorted(p.name for p in (theatre / ".troupe").iterdir() if p.name.startswith("history"))


def test_config_help_exits_zero_from_a_yml_theatre(
    make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    theatre = make_theatre("name: demo\n", config_name="troupe.yml")
    assert (theatre / ".troupe" / "troupe.yml").read_bytes() == b"name: demo\n"

    result = run_cli("config", "--help", cwd=theatre)

    assert result.exit_code == 0
    assert result.stdout.startswith(b"usage: troupe config")


# ---- 1.1, 1.2: show and history with --theatre from another directory ----


def test_show_with_theatre_option_prints_the_value(
    make_theatre: MakeTheatre, run_cli: RunCli, tmp_path: Path
) -> None:
    theatre = make_theatre(CONFIG)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    result = run_cli("config", "show", "agents.lead", "--theatre", str(theatre), cwd=elsewhere)

    assert (result.exit_code, result.stdout, result.stderr) == (0, b"alice\n", "")
    assert list(elsewhere.iterdir()) == []


def test_show_list_item_by_index(make_theatre: MakeTheatre, run_cli: RunCli) -> None:
    theatre = make_theatre(CONFIG)

    result = run_cli("config", "show", "flags.0", "--theatre", str(theatre))

    assert (result.exit_code, result.stdout) == (0, b"true\n")


def test_history_with_theatre_option_lists_the_import(
    make_theatre: MakeTheatre, run_cli: RunCli, tmp_path: Path
) -> None:
    theatre = make_theatre(CONFIG)

    result = run_cli("config", "history", "--theatre", str(theatre), cwd=tmp_path)

    assert result.exit_code == 0
    assert result.stderr == ""
    lines = [step1_members(line) for line in result.stdout.decode("utf-8").splitlines()]
    assert [(r["seq"], r["op"], r["path"], r["old"], r["new"]) for r in lines] == [
        (1, "import", "name", None, "demo"),
        (2, "import", "agents.lead", None, "alice"),
        (3, "import", "agents.count", None, 3),
        (4, "import", "flags.0", None, True),
        (5, "import", "flags.1", None, None),
    ]


def test_history_with_path_filters_by_prefix(make_theatre: MakeTheatre, run_cli: RunCli) -> None:
    theatre = make_theatre(CONFIG)

    result = run_cli("config", "history", "--theatre", str(theatre), "agents")

    assert result.exit_code == 0
    paths = [json.loads(line)["path"] for line in result.stdout.decode("utf-8").splitlines()]
    seqs = [json.loads(line)["seq"] for line in result.stdout.decode("utf-8").splitlines()]
    assert paths == ["agents.lead", "agents.count"]
    assert seqs == [2, 3]


def test_theatre_option_before_path_is_accepted(make_theatre: MakeTheatre, run_cli: RunCli) -> None:
    theatre = make_theatre(CONFIG)

    result = run_cli("config", "show", "--theatre", str(theatre), "name")

    assert (result.exit_code, result.stdout) == (0, b"demo\n")


# ---- 1.3: --theatre defaults to the current working directory ----


def test_show_defaults_to_the_current_directory(make_theatre: MakeTheatre, run_cli: RunCli) -> None:
    theatre = make_theatre(CONFIG)

    result = run_cli("config", "show", "agents.count", cwd=theatre)

    assert (result.exit_code, result.stdout, result.stderr) == (0, b"3\n", "")


def test_history_defaults_to_the_current_directory(
    make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    theatre = make_theatre("a: 1\n")

    result = run_cli("config", "history", cwd=theatre)

    assert result.exit_code == 0
    assert step1_members(result.stdout) == {
        "seq": 1,
        "op": "import",
        "path": "a",
        "old": None,
        "new": 1,
    }


def test_theatre_env_var_is_the_default_and_option_wins(
    make_theatre: MakeTheatre, run_cli: RunCli, monkeypatch: pytest.MonkeyPatch
) -> None:
    theatre = make_theatre(CONFIG)
    other = make_theatre("name: other\n", name="other")
    monkeypatch.setenv("TROUPE_THEATRE", str(theatre))

    from_env = run_cli("config", "show", "name", cwd=other)
    from_option = run_cli("config", "show", "name", "--theatre", str(other), cwd=other)
    monkeypatch.setenv("TROUPE_THEATRE", "")
    empty_env = run_cli("config", "show", "name", cwd=other)

    assert (from_env.exit_code, from_env.stdout) == (0, b"demo\n")
    assert (from_option.exit_code, from_option.stdout) == (0, b"other\n")
    assert (empty_env.exit_code, empty_env.stdout) == (0, b"other\n")


def test_default_theatre_without_config_is_a_load_error(run_cli: RunCli, tmp_path: Path) -> None:
    result = run_cli("config", "show", "a", cwd=tmp_path)

    assert result.exit_code == 4
    assert result.stdout == b""
    assert "troupe.yml" in result.stderr


# ---- 1.4: malformed command lines exit 2, empty stdout, usage message, no state ----

USAGE_CASES = {
    "no subcommand": ("config",),
    "no command group": (),
    "unknown subcommand": ("config", "bogus"),
    "get missing PATH": ("config", "get"),
    "show unknown option": ("config", "show", "a", "--bogus"),
    "history unknown option": ("config", "history", "--bogus"),
    "show surplus argument": ("config", "show", "a", "b"),
    "history surplus argument": ("config", "history", "a", "b"),
    "theatre missing value": ("config", "show", "a", "--theatre"),
}


@pytest.mark.parametrize("args", USAGE_CASES.values(), ids=USAGE_CASES.keys())
def test_malformed_command_line_exits_2(
    args: tuple[str, ...], make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    theatre = make_theatre(CONFIG)

    result = run_cli(*args, cwd=theatre)

    assert result.exit_code == 2
    assert result.stdout == b""
    assert result.stderr.strip() != ""
    assert "Traceback" not in result.stderr
    assert _history_files(theatre) == []


def test_unknown_subcommand_message_names_it(make_theatre: MakeTheatre, run_cli: RunCli) -> None:
    theatre = make_theatre(CONFIG)

    result = run_cli("config", "bogus", cwd=theatre)

    assert "bogus" in result.stderr


def test_unknown_option_message_names_it(make_theatre: MakeTheatre, run_cli: RunCli) -> None:
    theatre = make_theatre(CONFIG)

    result = run_cli("config", "show", "a", "--bogus", cwd=theatre)

    assert "--bogus" in result.stderr


def test_missing_argument_message_names_it(make_theatre: MakeTheatre, run_cli: RunCli) -> None:
    theatre = make_theatre(CONFIG)

    result = run_cli("config", "get", cwd=theatre)

    assert "PATH" in result.stderr


# ---- 1.5: malformed PATH exits 2, names PATH, creates no state ----


@pytest.mark.parametrize("subcommand", ["show", "history"])
@pytest.mark.parametrize("path", ["a..b", ".a", "a."])
def test_malformed_path_exits_2_without_state(
    subcommand: str, path: str, make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    theatre = make_theatre(CONFIG)

    result = run_cli("config", subcommand, path, "--theatre", str(theatre))

    assert result.exit_code == 2
    assert result.stdout == b""
    assert path in result.stderr
    assert _history_files(theatre) == []


def test_empty_path_exits_2(make_theatre: MakeTheatre, run_cli: RunCli) -> None:
    theatre = make_theatre(CONFIG)

    result = run_cli("config", "show", "", "--theatre", str(theatre))

    assert result.exit_code == 2
    assert result.stdout == b""
    assert "PATH" in result.stderr
    assert _history_files(theatre) == []


# ---- 10.1-10.3: error mapping through the console script ----


def test_missing_key_exits_5_naming_file_and_path(
    make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    theatre = make_theatre(CONFIG)

    result = run_cli("config", "show", "agents.nobody", "--theatre", str(theatre))

    assert result.exit_code == 5
    assert result.stdout == b""
    assert str(theatre / ".troupe" / "troupe.yml") in result.stderr
    assert "agents.nobody" in result.stderr


def test_mapping_target_of_get_exits_5(make_theatre: MakeTheatre, run_cli: RunCli) -> None:
    """config-get-show 1.2: `get` refuses a mapping (step 3's `show` prints it)."""
    theatre = make_theatre(CONFIG)

    result = run_cli("config", "get", "agents", "--theatre", str(theatre))

    assert result.exit_code == 5
    assert result.stdout == b""
    assert result.stderr.startswith(f"{theatre / '.troupe' / 'troupe.yml'}: agents: ")


def test_missing_theatre_directory_exits_4(run_cli: RunCli, tmp_path: Path) -> None:
    missing = tmp_path / "nowhere"

    result = run_cli("config", "history", "--theatre", str(missing))

    assert result.exit_code == 4
    assert result.stdout == b""
    assert str(missing) in result.stderr
    assert not missing.exists()


# ---- 7.9: the store is closed at the end of every command ----


@pytest.mark.parametrize(
    "args",
    [("show", "name"), ("history",), ("show", "missing")],
    ids=["show", "history", "path error"],
)
def test_store_is_closed_after_the_command(
    args: tuple[str, ...], make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    theatre = make_theatre(CONFIG)

    run_cli("config", *args, "--theatre", str(theatre))

    assert _history_files(theatre) == ["history.sqlite"]


# ---- run(): buffered output and error mapping in process ----


def test_run_writes_output_to_stdout_on_success(make_theatre: MakeTheatre) -> None:
    theatre = make_theatre(CONFIG)
    stdout, stderr = io.BytesIO(), io.StringIO()

    code = cli_main.run(["config", "show", "name", "--theatre", str(theatre)], stdout, stderr)

    assert (code, stdout.getvalue(), stderr.getvalue()) == (0, b"demo\n", "")


def test_run_maps_usage_error_to_exit_2(make_theatre: MakeTheatre) -> None:
    theatre = make_theatre(CONFIG)
    stdout, stderr = io.BytesIO(), io.StringIO()

    code = cli_main.run(["config", "show", "a", "b", "--theatre", str(theatre)], stdout, stderr)

    assert code == 2
    assert stdout.getvalue() == b""
    assert stderr.getvalue().endswith("\n")
    assert "b" in stderr.getvalue()


def _register_handler(monkeypatch: pytest.MonkeyPatch, handler: Handler) -> None:
    """Replace the registration point by one ``probe`` subcommand running ``handler``."""

    def add_probe(subparsers: "argparse._SubParsersAction[shell.TroupeArgumentParser]") -> None:
        parser = subparsers.add_parser("probe", parents=[shell.theatre_option_parent()])
        parser.set_defaults(handler=handler)

    monkeypatch.setattr(cli_main, "SUBCOMMAND_REGISTRARS", (add_probe,))


def test_run_dispatches_through_the_registration_point(monkeypatch: pytest.MonkeyPatch) -> None:
    def probe(args: argparse.Namespace, buffer: io.BytesIO) -> None:
        buffer.write(f"probe {args.theatre}\n".encode())

    _register_handler(monkeypatch, probe)
    stdout, stderr = io.BytesIO(), io.StringIO()

    code = cli_main.run(["config", "probe", "--theatre", "/x"], stdout, stderr)

    assert (code, stdout.getvalue(), stderr.getvalue()) == (0, b"probe /x\n", "")


def test_run_discards_buffered_output_when_the_handler_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failing_handler(args: argparse.Namespace, buffer: io.BytesIO) -> None:
        buffer.write(b"partial output\n")
        raise PathNotFoundError("does not exist", file=Path("f.yml"), path="x.y")

    _register_handler(monkeypatch, failing_handler)
    stdout, stderr = io.BytesIO(), io.StringIO()

    code = cli_main.run(["config", "probe"], stdout, stderr)

    assert code == 5
    assert stdout.getvalue() == b""
    assert stderr.getvalue() == "f.yml: x.y: does not exist\n"


def test_run_does_not_catch_unexpected_exceptions(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken_handler(args: argparse.Namespace, buffer: io.BytesIO) -> None:
        buffer.write(b"partial output\n")
        raise RuntimeError("bug")

    _register_handler(monkeypatch, broken_handler)
    stdout, stderr = io.BytesIO(), io.StringIO()

    with pytest.raises(RuntimeError, match="bug"):
        cli_main.run(["config", "probe"], stdout, stderr)
    assert stdout.getvalue() == b""


# ---- structure: registration point and the leaf shell module ----


def test_registration_point_holds_show_and_history() -> None:
    """config-file-sync 4.1, config-get-show 1.1, 2.1: ``get, show`` first, ``watch`` last."""
    # config-set-writeback appends its `set` registrar (its only change to main.py);
    # config-file-sync appends its `watch` registrar; config-get-show puts `get` first and
    # replaces step 1's `show` with its own, both from its read-command module.
    from troupe.cli.get_show_command import add_get_subparser, add_show_subparser
    from troupe.cli.set_command import add_set_subparser
    from troupe.cli.watch_command import add_watch_subparser

    registrars = cli_main.SUBCOMMAND_REGISTRARS
    assert registrars == (
        add_get_subparser,
        add_show_subparser,
        cli_main.add_history_subparser,
        add_set_subparser,
        add_watch_subparser,
    )


def test_every_subcommand_sets_a_handler() -> None:
    from troupe.cli.get_show_command import handle_show

    parser = cli_main.build_parser()
    assert isinstance(parser, shell.TroupeArgumentParser)
    show = parser.parse_args(["config", "show", "a"])
    history = parser.parse_args(["config", "history"])
    assert show.handler is handle_show
    assert history.handler is cli_main.handle_history
    assert history.path is None
    assert show.theatre == Path.cwd()


def test_parser_errors_raise_usage_error() -> None:
    from troupe.config.errors import UsageError

    with pytest.raises(UsageError):
        cli_main.build_parser().parse_args(["config", "bogus"])


def test_shell_imports_no_other_cli_module() -> None:
    source = Path(shell.__file__).read_text(encoding="utf-8")
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
            if node.module == "troupe.cli":
                imported.update(f"troupe.cli.{alias.name}" for alias in node.names)
    assert not any(name == "troupe.cli" or name.startswith("troupe.cli.") for name in imported)
    assert all(
        not name.startswith("troupe.") or name == "troupe.config.errors" for name in imported
    )
