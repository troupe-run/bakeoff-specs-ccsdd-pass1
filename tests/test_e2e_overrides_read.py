"""Every command reads the merged configuration, end to end through the real ``troupe`` script.

A mapping-form include (``{$ref: "file:…", <overrides>}``) loads as the included mapping
deep-merged with its overrides, and that merged view is the configuration every reader sees:
``get`` and ``show`` print it (3.1); ``show --sources`` labels an overridden scalar with the
including file and every other included scalar with the included file (3.2); import and reconcile
record its leaves, so a hand edit of an override is recorded as the ``set``/``remove`` events of
the merged leaves it changes (3.3) and rewriting an include between scalar and bare mapping form
records nothing (3.4); ``watch`` follows ``$ref`` targets (3.5). ``$ref`` is not part of the merged
value, so a ``get``/``show`` PATH with a ``$ref`` segment is not found (8.3).

``history`` never reconciles, so it is the safe observer of what import and reconcile recorded.

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

import os
import signal
import time
from pathlib import Path
from typing import Any

import pytest

from set_support import snapshot_tree
from support import CliResult, MakeTheatre, RunCli, step1_members, write_theatre
from watch_support import HistoryEvent, WatchProcess

ROOT = """\
name: demo
agents:
  coder:
    $ref: file:agents/coder.yaml
    temperature: 0.3
    params:
      top_p: 0.9
    extra: on
crew:
  - $ref: file:agents/coder.yaml
    model: haiku
"""
"""A mapping-form include with an overridden scalar, a nested merged mapping and an override-only
key, and a mapping-form include as a list item."""

CODER = "model: opus\ntemperature: 0.7\nparams:\n  top_k: 5\n  top_p: 0.5\ntools: [grep]\n"

FILES = {".troupe/agents/coder.yaml": CODER}

CODER_MERGED = (
    b"model: opus\n"
    b"temperature: 0.3\n"
    b"params:\n"
    b"  top_k: 5\n"
    b"  top_p: 0.9\n"
    b"tools:\n"
    b"  - grep\n"
    b"extra: on\n"
)
"""``agents.coder`` merged: included keys in their order (overridden ones in place), then the
override-only key."""

CREW_MERGED = (
    b"- model: haiku\n"
    b"  temperature: 0.7\n"
    b"  params:\n"
    b"    top_k: 5\n"
    b"    top_p: 0.5\n"
    b"  tools:\n"
    b"    - grep\n"
)

ROOT_LABEL = b" # from .troupe/troupe.yml"
CODER_LABEL = b" # from .troupe/agents/coder.yaml"

WHOLE_SOURCES = (
    b"name: demo" + ROOT_LABEL + b"\n"
    b"agents:\n"
    b"  coder:\n"
    b"    model: opus" + CODER_LABEL + b"\n"
    b"    temperature: 0.3" + ROOT_LABEL + b"\n"
    b"    params:\n"
    b"      top_k: 5" + CODER_LABEL + b"\n"
    b"      top_p: 0.9" + ROOT_LABEL + b"\n"
    b"    tools:\n"
    b"      - grep" + CODER_LABEL + b"\n"
    b"    extra: on" + ROOT_LABEL + b"\n"
    b"crew:\n"
    b"  - model: haiku" + ROOT_LABEL + b"\n"
    b"    temperature: 0.7" + CODER_LABEL + b"\n"
    b"    params:\n"
    b"      top_k: 5" + CODER_LABEL + b"\n"
    b"      top_p: 0.5" + CODER_LABEL + b"\n"
    b"    tools:\n"
    b"      - grep" + CODER_LABEL + b"\n"
)

type Event = tuple[int, str, str, Any, Any]
"""One history line as ``(seq, op, path, old, new)``."""

IMPORTS: list[Event] = [
    (1, "import", "name", None, "demo"),
    (2, "import", "agents.coder.model", None, "opus"),
    (3, "import", "agents.coder.temperature", None, 0.3),
    (4, "import", "agents.coder.params.top_k", None, 5),
    (5, "import", "agents.coder.params.top_p", None, 0.9),
    (6, "import", "agents.coder.tools.0", None, "grep"),
    (7, "import", "agents.coder.extra", None, "on"),
    (8, "import", "crew.0.model", None, "haiku"),
    (9, "import", "crew.0.temperature", None, 0.7),
    (10, "import", "crew.0.params.top_k", None, 5),
    (11, "import", "crew.0.params.top_p", None, 0.5),
    (12, "import", "crew.0.tools.0", None, "grep"),
]
"""The merged leaves of ``ROOT``, in document order: no ``$ref`` path, overridden values."""


def _theatre(make_theatre: MakeTheatre, root: str = ROOT) -> Path:
    return make_theatre(root, files=FILES, name="t")


def _config(theatre: Path) -> Path:
    return theatre / ".troupe" / "troupe.yml"


def _run(run_cli: RunCli, theatre: Path, *args: str) -> CliResult:
    return run_cli("config", *args, "--theatre", str(theatre), cwd=theatre)


def _prints(result: CliResult, expected: bytes) -> None:
    assert (result.exit_code, result.stderr) == (0, ""), result.stderr
    assert result.stdout == expected, result.stdout.decode("utf-8", "replace")


def _events(run_cli: RunCli, theatre: Path) -> list[Event]:
    """Every recorded event, parsed from the ``history`` lines (``history`` never reconciles)."""
    result = _run(run_cli, theatre, "history")
    assert (result.exit_code, result.stderr) == (0, ""), result.stderr
    rows = [step1_members(line) for line in result.stdout.decode("utf-8").splitlines()]
    return [(row["seq"], row["op"], row["path"], row["old"], row["new"]) for row in rows]


# ---- 3.1: get and show print the merged value ------------------------------------------------


def test_show_prints_the_merged_configuration(make_theatre: MakeTheatre, run_cli: RunCli) -> None:
    """Req 3.1: `show` prints mapping-form includes merged (in a mapping and as a list item),
    with included keys in their order, overridden values in place, nested mappings merged and
    override-only keys last, and no `$ref` key."""
    theatre = _theatre(make_theatre)
    expected = (
        b"name: demo\nagents:\n  coder:\n"
        + b"".join(b"    " + line + b"\n" for line in CODER_MERGED.splitlines())
        + b"crew:\n"
        + b"".join(b"  " + line + b"\n" for line in CREW_MERGED.splitlines())
    )
    _prints(_run(run_cli, theatre, "show"), expected)
    _prints(_run(run_cli, theatre, "show", "agents.coder"), CODER_MERGED)
    _prints(_run(run_cli, theatre, "show", "crew"), CREW_MERGED)
    _prints(_run(run_cli, theatre, "show", "agents.coder.params"), b"top_k: 5\ntop_p: 0.9\n")


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("agents.coder.temperature", b"0.3\n"),
        ("agents.coder.params.top_p", b"0.9\n"),
        ("agents.coder.params.top_k", b"5\n"),
        ("agents.coder.model", b"opus\n"),
        ("agents.coder.extra", b"on\n"),
        ("crew.0.model", b"haiku\n"),
        ("crew.0.temperature", b"0.7\n"),
    ],
    ids=[
        "overridden",
        "nested-overridden",
        "nested-included",
        "included",
        "override-only",
        "list-item-overridden",
        "list-item-included",
    ],
)
def test_get_and_show_print_merged_scalars(
    make_theatre: MakeTheatre, run_cli: RunCli, path: str, value: bytes
) -> None:
    """Req 3.1: `get` and `show` of a scalar below a mapping-form include print its merged value
    (the override where there is one, else the included value, and override-only keys)."""
    theatre = _theatre(make_theatre)
    _prints(_run(run_cli, theatre, "get", path), value)
    _prints(_run(run_cli, theatre, "show", path), value)


def test_mapping_form_include_at_the_root_reads_merged(
    make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    """Req 3.1, 3.2: a root file that is itself a mapping-form include reads as the merged
    mapping in `get`, `show` and `show --sources`."""
    theatre = _theatre(make_theatre, "$ref: file:agents/coder.yaml\nmodel: sonnet\nlevel: 2\n")
    _prints(_run(run_cli, theatre, "get", "model"), b"sonnet\n")
    _prints(_run(run_cli, theatre, "get", "params.top_p"), b"0.5\n")
    _prints(
        _run(run_cli, theatre, "show"),
        b"model: sonnet\ntemperature: 0.7\nparams:\n  top_k: 5\n  top_p: 0.5\n"
        b"tools:\n  - grep\nlevel: 2\n",
    )
    _prints(
        _run(run_cli, theatre, "show", "--sources"),
        b"model: sonnet" + ROOT_LABEL + b"\n"
        b"temperature: 0.7" + CODER_LABEL + b"\n"
        b"params:\n"
        b"  top_k: 5" + CODER_LABEL + b"\n"
        b"  top_p: 0.5" + CODER_LABEL + b"\n"
        b"tools:\n"
        b"  - grep" + CODER_LABEL + b"\n"
        b"level: 2" + ROOT_LABEL + b"\n",
    )


# ---- 3.2: --sources names the file the value is written in ------------------------------------


def test_sources_name_the_including_file_for_overrides_and_the_included_file_otherwise(
    make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    """Req 3.2: with `--sources`, an overridden scalar (also inside a nested merged mapping and in
    a list item) and an override-only scalar name the including file; every scalar not
    overridden names the included file."""
    theatre = _theatre(make_theatre)
    _prints(_run(run_cli, theatre, "show", "--sources"), WHOLE_SOURCES)
    _prints(
        _run(run_cli, theatre, "show", "agents.coder.temperature", "--sources"),
        b"0.3" + ROOT_LABEL + b"\n",
    )
    _prints(
        _run(run_cli, theatre, "show", "agents.coder.params.top_k", "--sources"),
        b"5" + CODER_LABEL + b"\n",
    )


def test_sources_name_the_innermost_including_file_for_an_override_in_an_included_file(
    make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    """Req 3.2: an override written in an included file is labelled with that file, not the
    root, and the value it overrides is no longer shown."""
    theatre = make_theatre(
        "team: file:team.yml\n",
        files={
            ".troupe/team.yml": "lead:\n  $ref: file:agents/coder.yaml\n  model: sonnet\n",
            **FILES,
        },
        name="t",
    )
    output = _run(run_cli, theatre, "show", "team.lead", "--sources")
    _prints(
        output,
        b"model: sonnet # from .troupe/team.yml\n"
        b"temperature: 0.7" + CODER_LABEL + b"\n"
        b"params:\n"
        b"  top_k: 5" + CODER_LABEL + b"\n"
        b"  top_p: 0.5" + CODER_LABEL + b"\n"
        b"tools:\n"
        b"  - grep" + CODER_LABEL + b"\n",
    )


# ---- 3.3, 3.4: import and reconcile record the merged leaves ---------------------------------


def test_import_records_the_merged_leaves(make_theatre: MakeTheatre, run_cli: RunCli) -> None:
    """Req 3.3: the import records one event per merged leaf with its merged value, and no
    `$ref` path."""
    theatre = _theatre(make_theatre)
    _prints(_run(run_cli, theatre, "get", "name"), b"demo\n")
    events = _events(run_cli, theatre)
    assert events == IMPORTS
    assert not any("$ref" in path for _, _, path, _, _ in events)


def test_hand_added_override_is_recorded_as_set_events_of_the_merged_leaves(
    make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    """Req 3.3: after import, hand-adding overrides (a scalar, a nested key, an override in a
    list item) makes the next `get` record exactly one `set` per merged leaf whose value changed
    and one `set` per new leaf, without changing any file."""
    theatre = _theatre(make_theatre)
    _prints(_run(run_cli, theatre, "get", "name"), b"demo\n")
    assert _events(run_cli, theatre) == IMPORTS
    edited = ROOT.replace("    temperature: 0.3\n", "    temperature: 0.3\n    model: sonnet\n")
    edited = edited.replace("      top_p: 0.9\n", "      top_p: 0.9\n      top_k: 9\n")
    edited = edited.replace("    model: haiku\n", "    model: haiku\n    temperature: 1.0\n")
    _config(theatre).write_text(edited, encoding="utf-8")
    files = snapshot_tree(theatre)

    _prints(_run(run_cli, theatre, "get", "agents.coder.model"), b"sonnet\n")

    assert snapshot_tree(theatre) == files
    assert _events(run_cli, theatre) == [
        *IMPORTS,
        (13, "set", "agents.coder.model", "opus", "sonnet"),
        (14, "set", "agents.coder.params.top_k", 5, 9),
        (15, "set", "crew.0.temperature", 0.7, 1.0),
    ]


def test_hand_changed_and_removed_overrides_are_recorded_as_merged_leaf_events(
    make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    """Req 3.3: removing an override of an included key records a `set` back to the included
    value, removing an override-only key records a `remove`, and changing an override records a
    `set`; an edit of the included file changes only the leaves not overridden (sets in document
    order, then removes, as reconcile records them)."""
    theatre = _theatre(make_theatre)
    _prints(_run(run_cli, theatre, "get", "name"), b"demo\n")
    edited = ROOT.replace("    temperature: 0.3\n", "").replace("    extra: on\n", "")
    edited = edited.replace("      top_p: 0.9\n", "      top_p: 0.8\n")
    _config(theatre).write_text(edited, encoding="utf-8")
    coder = theatre / ".troupe" / "agents" / "coder.yaml"
    coder.write_text(CODER.replace("top_p: 0.5", "top_p: 0.6"), encoding="utf-8")

    _prints(_run(run_cli, theatre, "get", "agents.coder.temperature"), b"0.7\n")

    assert _events(run_cli, theatre) == [
        *IMPORTS,
        (13, "set", "agents.coder.temperature", 0.3, 0.7),
        (14, "set", "agents.coder.params.top_p", 0.9, 0.8),
        (15, "set", "crew.0.params.top_p", 0.5, 0.6),
        (16, "remove", "agents.coder.extra", "on", None),
    ]


@pytest.mark.parametrize(
    ("scalar_form", "mapping_form"),
    [
        (
            "agents:\n  coder: file:agents/coder.yaml\n",
            "agents:\n  coder:\n    $ref: file:agents/coder.yaml\n",
        ),
        (
            "agents:\n  coder: file:agents/coder.yaml\n",
            "agents:\n  coder: {$ref: file:agents/coder.yaml}\n",
        ),
        ("crew:\n  - file:agents/coder.yaml\n", "crew:\n  - $ref: file:agents/coder.yaml\n"),
        ("agents: {prompt: file:prompt.md}\n", "agents: {prompt: {$ref: file:prompt.md}}\n"),
    ],
    ids=["block", "flow", "list-item", "text"],
)
@pytest.mark.parametrize("direction", ["scalar-to-mapping", "mapping-to-scalar"])
def test_rewriting_an_include_between_scalar_and_bare_mapping_form_records_nothing(
    make_theatre: MakeTheatre,
    run_cli: RunCli,
    scalar_form: str,
    mapping_form: str,
    direction: str,
) -> None:
    """Req 3.4: rewriting a YAML or text include between the scalar form and a bare mapping form
    of the same string (either way, block or flow, in a mapping or a list) records no event."""
    before, after = (
        (scalar_form, mapping_form)
        if direction == "scalar-to-mapping"
        else (mapping_form, scalar_form)
    )
    theatre = make_theatre(
        before, files={**FILES, ".troupe/prompt.md": "You write code.\n"}, name="t"
    )
    shown = _run(run_cli, theatre, "show")
    assert (shown.exit_code, shown.stderr) == (0, ""), shown.stderr
    imported = _events(run_cli, theatre)
    assert imported and all(op == "import" for _, op, _, _, _ in imported)
    _config(theatre).write_text(after, encoding="utf-8")

    _prints(_run(run_cli, theatre, "show"), shown.stdout)

    assert _events(run_cli, theatre) == imported


# ---- 8.3: $ref is not part of the merged value -----------------------------------------------


@pytest.mark.parametrize("command", ["get", "show"])
@pytest.mark.parametrize(
    ("root", "path"),
    [
        (ROOT, "agents.coder.$ref"),
        (ROOT, "crew.0.$ref"),
        ("agents:\n  coder:\n    $ref: file:agents/coder.yaml\n", "agents.coder.$ref"),
        ("$ref: file:agents/coder.yaml\nmodel: sonnet\n", "$ref"),
    ],
    ids=["with-overrides", "list-item", "bare", "root"],
)
def test_ref_segment_in_a_read_path_is_not_found(
    make_theatre: MakeTheatre, run_cli: RunCli, command: str, root: str, path: str
) -> None:
    """Req 8.3: `get`/`show` of a PATH with a `$ref` segment below a mapping-form include exit
    5 with an empty stdout and a not-found message naming PATH."""
    theatre = _theatre(make_theatre, root)
    result = _run(run_cli, theatre, command, path)
    assert result.exit_code == 5, result.stderr
    assert result.stdout == b""
    assert f": {path}: not found" in result.stderr, result.stderr


# ---- 3.5: watch follows $ref targets ---------------------------------------------------------


def _write_atomically(target: Path, content: str) -> None:
    """Save like an editor: write a new file next to ``target``, then rename it over ``target``."""
    temporary = target.with_name(f".{target.name}.edit-tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, target)


def _watch_set(seq: int, path: str, old: object, new: object) -> HistoryEvent:
    return {"seq": seq, "op": "set", "path": path, "old": old, "new": new}


def test_watch_reconciles_an_edit_of_a_ref_target_file(tmp_path: Path) -> None:
    """Req 3.5: while `watch` runs, editing the target of a mapping-form include (reached only
    through `$ref`, in a mapping and in a list item) is reconciled within 2 s as `set` events of
    the merged leaves it changes; a value that stays overridden records nothing."""
    theatre = write_theatre(tmp_path / "t", ROOT, files=FILES)
    coder = theatre / ".troupe" / "agents" / "coder.yaml"

    with WatchProcess(str(theatre), cwd=tmp_path) as watch:
        assert watch.wait_ready() == f"watching {theatre}\n".encode()
        imported = watch.history()
        assert [
            (row["seq"], row["op"], row["path"], row["old"], row["new"]) for row in imported
        ] == IMPORTS
        since = time.monotonic()
        _write_atomically(
            coder, CODER.replace("model: opus", "model: o2").replace("top_p: 0.5", "top_p: 0.6")
        )
        expected = [
            *imported,
            _watch_set(13, "agents.coder.model", "opus", "o2"),
            _watch_set(14, "crew.0.params.top_p", 0.5, 0.6),
        ]
        events = watch.wait_for_history(lambda events: len(events) >= len(expected), since=since)
        assert events == expected
        assert watch.stop(signal.SIGTERM) == 0
        assert watch.stdout == f"watching {theatre}\n".encode()
        assert watch.stderr == ""
