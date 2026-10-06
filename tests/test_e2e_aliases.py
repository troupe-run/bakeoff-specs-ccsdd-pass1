"""Anchors, aliases and merge keys end to end through the real ``troupe`` console script
(config-aliases).

Task 2.1: a file with an anchor is read like any other file (the anchor-only row that
config-foundation refused with exit 4), and an anchor on a mapping key is still refused, with no
history state created.

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

from pathlib import Path

import pytest

from support import CliResult, MakeTheatre, RunCli

CONFIG_NAMES = ["troupe.yml", "troupe.yaml"]


def _get(run_cli: RunCli, theatre: Path, path: str) -> CliResult:
    return run_cli("config", "get", path, "--theatre", str(theatre))


def _no_history_state(theatre: Path) -> bool:
    return not list((theatre / ".troupe").glob("history.sqlite*"))


@pytest.mark.parametrize("config_name", CONFIG_NAMES)
def test_get_reads_an_anchored_scalar(
    make_theatre: MakeTheatre, run_cli: RunCli, config_name: str
) -> None:
    """Req 1.1, 1.6: ``a: &x 1`` loads; ``get a`` prints the anchored value, without the anchor,
    and the file is unchanged."""
    content = "a: &x 1\n"
    theatre = make_theatre(content, config_name=config_name)

    result = _get(run_cli, theatre, "a")

    assert (result.exit_code, result.stdout, result.stderr) == (0, b"1\n", "")
    assert (theatre / ".troupe" / config_name).read_bytes() == content.encode()


@pytest.mark.parametrize("config_name", CONFIG_NAMES)
def test_get_reads_values_inside_anchored_collections(
    make_theatre: MakeTheatre, run_cli: RunCli, config_name: str
) -> None:
    """Req 1.1, 1.5: values inside an anchored block mapping, an anchored list and an anchored
    flow mapping are read at their paths."""
    theatre = make_theatre(
        "defaults: &d\n  model: opus\nlist: &l\n  - one\nflow: &f {k: 'v'}\n",
        config_name=config_name,
    )

    results = [_get(run_cli, theatre, path) for path in ("defaults.model", "list.0", "flow.k")]

    assert [(r.exit_code, r.stdout, r.stderr) for r in results] == [
        (0, b"opus\n", ""),
        (0, b"one\n", ""),
        (0, b"v\n", ""),
    ]


@pytest.mark.parametrize("config_name", CONFIG_NAMES)
def test_anchor_on_a_mapping_key_exits_4_naming_the_file_and_key_path(
    make_theatre: MakeTheatre, run_cli: RunCli, config_name: str
) -> None:
    """Req 1.4, 1.7: an anchor on a mapping key exits 4 with empty stdout, one stderr line naming
    the file and the key path, no file changed and no history state created."""
    content = "a:\n  &k b: 1\n"
    theatre = make_theatre(content, config_name=config_name)
    config = theatre / ".troupe" / config_name

    result = _get(run_cli, theatre, "a.b")

    assert (result.exit_code, result.stdout) == (4, b"")
    assert result.stderr == f"{config}: a.b: anchor on a mapping key is not supported\n"
    assert config.read_bytes() == content.encode()
    assert _no_history_state(theatre)
