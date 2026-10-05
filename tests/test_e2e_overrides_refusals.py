"""Mapping-form includes that cannot mean anything, end to end through the real ``troupe`` script.

``troupe config get`` on a theatre holding a refused ``$ref`` mapping exits 4 with an empty
stdout and one stderr line ``<file>: <path>: <problem>`` (2.1, 2.2, 2.3); like any configuration
that cannot be loaded, no file is changed and no history store is created (2.4).

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

from pathlib import Path

import pytest

from set_support import snapshot_tree
from support import CliResult, MakeTheatre, RunCli

TEXT_TARGET = "agents:\n  coder:\n    $ref: file:prompt.md\n    temperature: 0.3\n"
LIST_TARGET = "agents:\n  coder:\n    $ref: file:coder.yaml\n    temperature: 0.3\n"


def _get(run_cli: RunCli, theatre: Path, path: str) -> CliResult:
    return run_cli("config", "get", path, "--theatre", str(theatre))


def _refused(run_cli: RunCli, theatre: Path, file: Path, path: str, get_path: str) -> str:
    """Run ``get get_path`` and assert a load refusal naming ``file`` and ``path``.

    Asserts exit 4, empty stdout, one stderr line, no file changed and no history state. Returns
    the problem text.
    """
    before = snapshot_tree(theatre)
    result = _get(run_cli, theatre, get_path)
    prefix = f"{file}: {path}: "
    assert result.exit_code == 4, result.stderr
    assert result.stdout == b""
    assert result.stderr.startswith(prefix), result.stderr
    assert result.stderr.endswith("\n")
    assert result.stderr.count("\n") == 1, result.stderr
    assert snapshot_tree(theatre) == before
    assert not list((theatre / ".troupe").glob("history.sqlite*"))
    return result.stderr[len(prefix) : -1]


@pytest.mark.parametrize("get_path", ["agents.coder.temperature", "other"])
def test_overrides_on_a_text_target_exit_4(
    make_theatre: MakeTheatre, run_cli: RunCli, get_path: str
) -> None:
    """Req 2.1, 2.4: overrides on a non-YAML target name file, path and target; nothing written."""
    theatre = make_theatre(
        TEXT_TARGET + "other: 1\n", files={".troupe/prompt.md": "You write code.\n"}
    )
    config = theatre / ".troupe" / "troupe.yml"

    problem = _refused(run_cli, theatre, config, "agents.coder", get_path)

    assert str(theatre / ".troupe" / "prompt.md") in problem
    assert "overrides apply only to YAML includes" in problem


@pytest.mark.parametrize("ref", ["coder.yaml", "7", "[file:coder.yaml]", "null"])
def test_ref_value_that_is_not_an_include_string_exits_4(
    make_theatre: MakeTheatre, run_cli: RunCli, ref: str
) -> None:
    """Req 2.2, 2.4: a `$ref` that is not a `file:` string names file and path; nothing written."""
    theatre = make_theatre(
        f"agents:\n  coder:\n    $ref: {ref}\n    temperature: 0.3\nother: 1\n",
        files={".troupe/coder.yaml": "model: small\n"},
    )
    config = theatre / ".troupe" / "troupe.yml"

    problem = _refused(run_cli, theatre, config, "agents.coder", "other")

    assert "`$ref` must be an include string" in problem


def test_ref_refusal_in_an_included_file_names_that_file(
    make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    """Req 2.2, 2.4: a bad `$ref` written in an included file names the included file."""
    theatre = make_theatre(
        "agents: file:agents.yml\n",
        files={".troupe/agents.yml": "coder:\n  $ref: coder.yaml\n  temperature: 0.3\n"},
    )
    included = theatre / ".troupe" / "agents.yml"

    problem = _refused(run_cli, theatre, included, "agents.coder", "agents")

    assert "`$ref` must be an include string" in problem


@pytest.mark.parametrize(
    ("content", "kind"),
    [
        ("- read\n- write\n", "a list"),
        ("small\n", "a scalar"),
        ("", "nothing (the file is empty)"),
    ],
)
def test_overrides_on_non_mapping_yaml_content_exit_4(
    make_theatre: MakeTheatre, run_cli: RunCli, content: str, kind: str
) -> None:
    """Req 2.3, 2.4: overrides on a list, scalar or empty YAML target name the included file."""
    theatre = make_theatre(LIST_TARGET, files={".troupe/coder.yaml": content})
    config = theatre / ".troupe" / "troupe.yml"

    problem = _refused(run_cli, theatre, config, "agents.coder", "agents.coder.temperature")

    assert "overrides need a mapping" in problem
    assert f"{theatre / '.troupe' / 'coder.yaml'} holds {kind}" in problem


def test_refusal_does_not_disturb_an_existing_history_store(
    make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    """Req 2.4: a refused configuration leaves an existing history store byte-identical."""
    theatre = make_theatre("agents:\n  coder:\n    temperature: 0.3\n")
    assert _get(run_cli, theatre, "agents.coder.temperature").exit_code == 0
    stores = sorted((theatre / ".troupe").glob("history.sqlite*"))
    assert stores
    history = {store.name: store.read_bytes() for store in stores}
    (theatre / ".troupe" / "troupe.yml").write_text(TEXT_TARGET)
    (theatre / ".troupe" / "prompt.md").write_text("p\n")

    result = _get(run_cli, theatre, "agents.coder.temperature")

    assert (result.exit_code, result.stdout) == (4, b"")
    assert "overrides apply only to YAML includes" in result.stderr
    after = sorted((theatre / ".troupe").glob("history.sqlite*"))
    assert {store.name: store.read_bytes() for store in after} == history
