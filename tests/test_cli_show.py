"""``troupe config show [PATH] [--sources]`` wiring (config-get-show task 3.2), via the real CLI.

Proves the CLI behaviour of the new ``show``: a scalar prints exactly as ``get`` (2.1), a mapping
prints as block YAML (2.2), no PATH prints the whole configuration (2.3), a missing path exits 5
(2.4), command-line and PATH syntax problems exit 2 (2.5), ``--sources`` on a scalar (2.6) and
``--sources`` before or after PATH (2.7), plus the registration and the no-write-back import rule
(6.5). The full byte-exact contract is in the end-to-end modules of tasks 4.2-4.4.

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

import ast
from pathlib import Path

import pytest

import troupe.cli.main as cli_main
from support import CliResult, MakeTheatre, RunCli

REPO_ROOT = Path(__file__).resolve().parents[1]
GET_SHOW_SOURCE = REPO_ROOT / "src" / "troupe" / "cli" / "get_show_command.py"

CONFIG = (
    "# a comment that is not printed\n"
    "name: demo\n"
    "count: 3\n"
    "agents:\n"
    "  lead:\n"
    "    name: alice\n"
    "    prompt: file:lead.md\n"
    "  helpers: [bob, carol]\n"
)
FILES = {".troupe/lead.md": "Be kind.\n"}

AGENTS_YAML = b"lead:\n  name: alice\n  prompt: |\n    Be kind.\nhelpers:\n  - bob\n  - carol\n"


def _show(run_cli: RunCli, theatre: Path, *args: str) -> CliResult:
    return run_cli("config", "show", *args, "--theatre", str(theatre))


def _get(run_cli: RunCli, theatre: Path, *args: str) -> CliResult:
    return run_cli("config", "get", *args, "--theatre", str(theatre))


def _assert_ok(result: CliResult, expected: bytes) -> None:
    assert (result.exit_code, result.stderr) == (0, ""), result.stderr
    assert result.stdout == expected


# ---- 2.1: a scalar without --sources prints exactly as get ----


def test_show_scalar_prints_the_same_bytes_as_get(
    make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    """Req 2.1, 6.1: for every scalar, `show PATH` and `get PATH` print the same bytes, exit 0."""
    theatre = make_theatre(CONFIG, files=FILES)

    for path in ("name", "count", "agents.lead.name", "agents.lead.prompt", "agents.helpers.1"):
        show = _show(run_cli, theatre, path)
        get = _get(run_cli, theatre, path)
        assert show.exit_code == 0, show.stderr
        assert show == get, path

    _assert_ok(_show(run_cli, theatre, "agents.lead.prompt"), b"Be kind.\n")


# ---- 2.2, 2.3: mappings, lists and the whole configuration as block YAML ----


def test_show_mapping_prints_block_yaml(make_theatre: MakeTheatre, run_cli: RunCli) -> None:
    """Req 2.2: `show agents` exits 0 printing the mapping as block YAML, includes resolved."""
    theatre = make_theatre(CONFIG, files=FILES)

    _assert_ok(_show(run_cli, theatre, "agents"), AGENTS_YAML)


def test_show_list_prints_block_yaml(make_theatre: MakeTheatre, run_cli: RunCli) -> None:
    """Req 2.2: a list target exits 0 printing its items at column 0."""
    theatre = make_theatre(CONFIG, files=FILES)

    _assert_ok(_show(run_cli, theatre, "agents.helpers"), b"- bob\n- carol\n")


def test_show_without_path_prints_the_whole_configuration(
    make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    """Req 2.3: `show` with no PATH prints the top-level mapping as YAML and exits 0."""
    theatre = make_theatre(CONFIG, files=FILES)

    expected = b"name: demo\ncount: 3\nagents:\n" + b"".join(
        b"  " + line + b"\n" for line in AGENTS_YAML.splitlines()
    )
    _assert_ok(_show(run_cli, theatre), expected)


# ---- 2.4, 2.5: path errors exit 5, command-line errors exit 2 ----


def test_show_missing_path_exits_5_naming_file_and_path(
    make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    """Req 2.4, 6.7: a missing PATH exits 5, empty stdout, stderr names the file and PATH."""
    theatre = make_theatre(CONFIG, files=FILES)

    result = _show(run_cli, theatre, "agents.nobody")

    assert (result.exit_code, result.stdout) == (5, b"")
    assert result.stderr.startswith(f"{theatre / '.troupe' / 'troupe.yml'}: agents.nobody: ")


@pytest.mark.parametrize(
    "args",
    [("a", "b"), ("a", "--bogus"), ("a.",), ("",), ("--sources", "a..b")],
    ids=["two-paths", "unknown-option", "empty-segment", "empty-path", "sources-malformed"],
)
def test_show_command_line_errors_exit_2_without_state(
    make_theatre: MakeTheatre, run_cli: RunCli, args: tuple[str, ...]
) -> None:
    """Req 2.5, 6.7: surplus PATH, unknown option or malformed PATH exits 2, no state created."""
    theatre = make_theatre(CONFIG, files=FILES)

    result = _show(run_cli, theatre, *args)

    assert (result.exit_code, result.stdout) == (2, b""), result.stderr
    assert result.stderr.strip()
    assert "Traceback" not in result.stderr
    assert not list((theatre / ".troupe").glob("history.sqlite*"))


# ---- 2.6, 2.7: --sources ----


def test_show_scalar_with_sources_prints_a_labelled_yaml_scalar(
    make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    """Req 2.6, 5.4: `show name --sources` prints the scalar as YAML with its source comment."""
    theatre = make_theatre(CONFIG, files=FILES)

    _assert_ok(_show(run_cli, theatre, "name", "--sources"), b"demo # from .troupe/troupe.yml\n")
    _assert_ok(
        _show(run_cli, theatre, "agents.lead.prompt", "--sources"),
        b"| # from .troupe/lead.md\n  Be kind.\n",
    )


def test_sources_before_or_after_path_print_the_same_bytes(
    make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    """Req 2.7, 5.1: `--sources` is accepted before or after PATH with the same output."""
    theatre = make_theatre(CONFIG, files=FILES)

    before = _show(run_cli, theatre, "--sources", "agents")
    after = _show(run_cli, theatre, "agents", "--sources")

    assert before == after
    _assert_ok(
        after,
        b"lead:\n"
        b"  name: alice # from .troupe/troupe.yml\n"
        b"  prompt: | # from .troupe/lead.md\n"
        b"    Be kind.\n"
        b"helpers:\n"
        b"  - bob # from .troupe/troupe.yml\n"
        b"  - carol # from .troupe/troupe.yml\n",
    )


def test_sources_without_path_labels_the_whole_configuration(
    make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    """Req 2.7, 5.5: `show --sources` with PATH omitted equals `show` once comments are removed."""
    theatre = make_theatre(CONFIG, files=FILES)

    labelled = _show(run_cli, theatre, "--sources")
    plain = _show(run_cli, theatre)

    assert labelled.exit_code == 0, labelled.stderr
    assert b"name: demo # from .troupe/troupe.yml\n" in labelled.stdout
    stripped = b"".join(line.split(b" # from ")[0] + b"\n" for line in labelled.stdout.splitlines())
    assert stripped == plain.stdout


# ---- structure: registration, handler, imports ----


def test_show_is_registered_with_its_handler_in_second_position() -> None:
    """Req 2.3, 2.7, 6.1: `show` takes an optional PATH, `--sources`, `--theatre`; `handle_show`."""
    from troupe.cli.get_show_command import add_show_subparser, handle_show

    parser = cli_main.build_parser()
    bare = parser.parse_args(["config", "show"])
    flagged = parser.parse_args(["config", "show", "--sources", "agents"])

    assert (bare.path, bare.sources, bare.handler, bare.theatre) == (
        None,
        False,
        handle_show,
        Path.cwd(),
    )
    assert (flagged.path, flagged.sources) == ("agents", True)
    assert cli_main.SUBCOMMAND_REGISTRARS[1] is add_show_subparser


def test_read_command_module_still_imports_nothing_from_writeback() -> None:
    """Req 6.5: with `show` added, the read-command module imports nothing from write-back."""
    tree = ast.parse(GET_SHOW_SOURCE.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)

    assert not any(name.startswith("troupe.config.writeback") for name in imported), imported
    assert "troupe.cli.main" not in imported
