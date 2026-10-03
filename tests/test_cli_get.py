"""``troupe config get PATH`` wiring (config-get-show task 3.1), through the real console script.

Proves the CLI behaviour of ``get``: scalars print exactly as step 1's ``show`` (1.1), a mapping or
list exits 5 with the ``config show`` hint (1.2), a missing path exits 5 (1.3), command-line and
PATH syntax problems exit 2 (1.4, 1.5), the default opening (reconcile or import first, 6.2, 6.3),
an empty stdout on failure (6.7) and no file written (6.5). The full byte-exact contract is in the
end-to-end module of task 4.1.

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

import ast
import json
from pathlib import Path

import pytest

import troupe.cli.main as cli_main
from support import CliResult, MakeTheatre, RunCli

REPO_ROOT = Path(__file__).resolve().parents[1]
GET_SHOW_SOURCE = REPO_ROOT / "src" / "troupe" / "cli" / "get_show_command.py"

CONFIG = (
    "name: demo\n"
    "count: 3\n"
    "ratio: 0.5\n"
    "on: true\n"
    "nothing: null\n"
    "agents:\n"
    "  lead: alice\n"
    "items:\n"
    "  - x\n"
    "  - y\n"
)


def _config(theatre: Path) -> Path:
    return theatre / ".troupe" / "troupe.yml"


def _get(run_cli: RunCli, theatre: Path, *args: str) -> CliResult:
    return run_cli("config", "get", *args, "--theatre", str(theatre))


def _events(run_cli: RunCli, theatre: Path) -> list[tuple[str, str, object]]:
    result = run_cli("config", "history", "--theatre", str(theatre))
    assert result.exit_code == 0, result.stderr
    rows = [json.loads(line) for line in result.stdout.decode("utf-8").splitlines()]
    return [(row["op"], row["path"], row["new"]) for row in rows]


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and not p.name.startswith("history.sqlite")
    }


def _assert_usage_error(result: CliResult) -> None:
    assert result.exit_code == 2, result.stderr
    assert result.stdout == b""
    assert result.stderr.endswith("\n") and result.stderr.strip(), result.stderr


# ---- 1.1: scalars print exactly as step 1's show ----


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("name", b"demo\n"),
        ("count", b"3\n"),
        ("ratio", b"0.5\n"),
        ("on", b"true\n"),
        ("nothing", b"null\n"),
        ("agents.lead", b"alice\n"),
        ("items.1", b"y\n"),
    ],
)
def test_get_prints_a_scalar_like_step_1_show(
    make_theatre: MakeTheatre, run_cli: RunCli, path: str, expected: bytes
) -> None:
    """Req 1.1, 6.1: `get PATH` on a scalar prints it as step 1's `show` and exits 0."""
    theatre = make_theatre(CONFIG)

    result = _get(run_cli, theatre, path)

    assert (result.exit_code, result.stdout, result.stderr) == (0, expected, "")


def test_get_prints_a_text_include_exactly(make_theatre: MakeTheatre, run_cli: RunCli) -> None:
    """Req 1.1: a text include prints as the included file's exact text."""
    theatre = make_theatre("prompt: file:prompt.txt\n", files={".troupe/prompt.txt": "hi\r\n\n"})

    result = _get(run_cli, theatre, "prompt")

    assert (result.exit_code, result.stdout, result.stderr) == (0, b"hi\r\n\n", "")


def test_get_and_show_print_the_same_bytes_for_scalars(
    make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    """Req 1.1, 2.1: for every scalar, `get` and `show` without `--sources` print the same bytes."""
    theatre = make_theatre(CONFIG)

    for path in ("name", "count", "ratio", "on", "nothing", "agents.lead", "items.0"):
        get = _get(run_cli, theatre, path)
        show = run_cli("config", "show", path, "--theatre", str(theatre))
        assert get == show, path


# ---- 1.2, 1.3, 6.7: path errors exit 5 with an empty stdout ----


@pytest.mark.parametrize("path", ["agents", "items"])
def test_get_refuses_a_mapping_or_list_with_the_show_hint(
    make_theatre: MakeTheatre, run_cli: RunCli, path: str
) -> None:
    """Req 1.2, 6.7: mapping or list exits 5 naming file and PATH, hinting `config show`."""
    theatre = make_theatre(CONFIG)

    result = _get(run_cli, theatre, path)

    assert result.exit_code == 5, result.stderr
    assert result.stdout == b""
    assert result.stderr.startswith(f"{_config(theatre)}: {path}: "), result.stderr
    assert "config show" in result.stderr
    assert result.stderr.endswith("\n") and result.stderr.count("\n") == 1


def test_get_missing_path_exits_5_naming_the_file(
    make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    """Req 1.3, 6.7: a path that does not exist exits 5 naming the file; stdout stays empty."""
    theatre = make_theatre(CONFIG)

    result = _get(run_cli, theatre, "agents.missing")

    assert result.exit_code == 5, result.stderr
    assert result.stdout == b""
    assert result.stderr.startswith(f"{_config(theatre)}: agents.missing: "), result.stderr


# ---- 1.4, 1.5: usage errors exit 2 ----


@pytest.mark.parametrize(
    "args",
    [
        pytest.param((), id="no-path"),
        pytest.param(("a", "b"), id="two-paths"),
        pytest.param(("a", "--sources"), id="sources"),
        pytest.param(("--sources", "a"), id="sources-first"),
        pytest.param(("a", "--bogus"), id="unknown-option"),
    ],
)
def test_get_command_line_problems_exit_2_and_create_no_state(
    make_theatre: MakeTheatre, run_cli: RunCli, args: tuple[str, ...]
) -> None:
    """Req 1.4, 6.7: no PATH, two PATHs or an unknown option (incl. `--sources`) exit 2."""
    theatre = make_theatre("a: 1\n")

    result = _get(run_cli, theatre, *args)

    _assert_usage_error(result)
    assert not list((theatre / ".troupe").glob("history.sqlite*"))


def test_get_without_path_names_the_missing_argument(
    make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    """Req 1.4: the usage message names the missing PATH."""
    theatre = make_theatre("a: 1\n")

    result = _get(run_cli, theatre)

    _assert_usage_error(result)
    assert "PATH" in result.stderr


@pytest.mark.parametrize("path", ["a..b", "", ".a", "a."])
def test_get_malformed_path_exits_2_naming_it(
    make_theatre: MakeTheatre, run_cli: RunCli, path: str
) -> None:
    """Req 1.5: an empty PATH or one with an empty segment exits 2 naming PATH; no state."""
    theatre = make_theatre("a: 1\n")

    result = _get(run_cli, theatre, path)

    _assert_usage_error(result)
    assert f"malformed PATH {path!r}" in result.stderr, result.stderr
    assert not list((theatre / ".troupe").glob("history.sqlite*"))


# ---- 6.2, 6.3: default opening ----


def test_get_on_a_never_imported_theatre_records_imports_only(
    make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    """Req 6.3: the first `get` imports the theatre and records no reconcile event."""
    theatre = make_theatre("a: 1\nb: x\n")

    assert _get(run_cli, theatre, "a").stdout == b"1\n"

    assert _events(run_cli, theatre) == [("import", "a", 1), ("import", "b", "x")]


def test_get_reconciles_a_hand_edit_first_and_prints_the_file_value(
    make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    """Req 6.2, 6.4: a hand edit before `get` is recorded as a reconcile `set` and printed."""
    theatre = make_theatre("a: 1\nb: x\n")
    assert _get(run_cli, theatre, "a").exit_code == 0
    _config(theatre).write_bytes(b"a: 2\nb: x\n")

    result = _get(run_cli, theatre, "a")

    assert (result.exit_code, result.stdout, result.stderr) == (0, b"2\n", "")
    assert _events(run_cli, theatre) == [
        ("import", "a", 1),
        ("import", "b", "x"),
        ("set", "a", 2),
    ]


def test_get_keeps_reconcile_events_when_it_exits_5(
    make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    """Req 6.2: the reconcile events stay recorded when `get` then exits 5."""
    theatre = make_theatre("a: 1\nm:\n  k: v\n")
    assert _get(run_cli, theatre, "a").exit_code == 0
    _config(theatre).write_bytes(b"a: 2\nm:\n  k: v\n")

    assert _get(run_cli, theatre, "m").exit_code == 5

    assert _events(run_cli, theatre)[-1] == ("set", "a", 2)


# ---- 6.5: no file written ----


def test_get_leaves_every_file_byte_identical(make_theatre: MakeTheatre, run_cli: RunCli) -> None:
    """Req 6.5: `get` (success or exit 5) creates or modifies no file besides history state."""
    theatre = make_theatre(
        "inc: file:part.yml\ntext: file:t.txt\n",
        files={".troupe/part.yml": "k: 'v'  # note\n", ".troupe/t.txt": "body\r\n"},
    )
    before = _snapshot(theatre)

    for path in ("inc.k", "text", "inc", "missing"):
        _get(run_cli, theatre, path)

    assert _snapshot(theatre) == before


# ---- structure: module, registration, handler ----


def test_get_is_registered_with_its_handler() -> None:
    """Req 1.1, 6.1: `get PATH` parses with the shared `--theatre` option and `handle_get`."""
    from troupe.cli.get_show_command import handle_get

    args = cli_main.build_parser().parse_args(["config", "get", "agents.lead"])

    assert (args.path, args.handler, args.theatre) == ("agents.lead", handle_get, Path.cwd())


def test_read_command_module_imports_nothing_from_writeback() -> None:
    """Req 6.5: the read-command module imports nothing from `troupe.config.writeback`."""
    tree = ast.parse(GET_SHOW_SOURCE.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)

    assert not any(name.startswith("troupe.config.writeback") for name in imported), imported
    assert "troupe.cli.main" not in imported
