"""``troupe config history`` and the history import, end to end through the real console script.

Covers the import on the first command (7.1, 8.5) with one ``import`` event per leaf (7.2), no
re-import and no reconciling (6.9, 7.3), an empty theatre marked imported (7.5), the JSON Lines
format with strict JSON values (8.1, 8.2, 8.6, 8.7), prefix filtering (8.3, 8.4), folded block
scalars in history events (3.8), and ``set``/``remove`` events appended through the recording API
used by the set command (9.1-9.3). Every line is parsed with a strict JSON parser that rejects the
``Infinity``/``NaN`` tokens.
"""

import json
from pathlib import Path
from typing import Any

from support import CliResult, MakeTheatre, RunCli
from troupe.config.constants import CONFIG_DIR_NAME, HISTORY_STORE_NAME
from troupe.config.history import LeafChange, RemoveChange, SetChange
from troupe.config.paths import ConfigPath
from troupe.config.store import HistoryStore

type Event = tuple[int, str, str, Any, Any]
"""One history line as ``(seq, op, path, old, new)``."""


def _history(run_cli: RunCli, theatre: Path, *path: str) -> CliResult:
    return run_cli("config", "history", *path, "--theatre", str(theatre))


def _show(run_cli: RunCli, theatre: Path, path: str) -> CliResult:
    return run_cli("config", "show", path, "--theatre", str(theatre))


def _reject_constant(token: str) -> Any:
    raise ValueError(f"non-standard JSON constant {token!r}")


def _strict_line(line: str) -> dict[str, Any]:
    parsed = json.loads(line, parse_constant=_reject_constant)
    assert isinstance(parsed, dict), line
    return parsed


def _events(result: CliResult) -> list[Event]:
    """Assert a successful history run and return its lines, each parsed as strict JSON."""
    assert (result.exit_code, result.stderr) == (0, ""), result.stderr
    text = result.stdout.decode("utf-8")
    if not text:
        return []
    assert text.endswith("\n"), text
    events: list[Event] = []
    for line in text[:-1].split("\n"):
        member = _strict_line(line)
        assert set(member) >= {"seq", "op", "path", "old", "new"}, line
        seq = member["seq"]
        assert type(seq) is int, line
        events.append((seq, member["op"], member["path"], member["old"], member["new"]))
    return events


def _store_file(theatre: Path) -> Path:
    return theatre / CONFIG_DIR_NAME / HISTORY_STORE_NAME


def _record(theatre: Path, changes: tuple[LeafChange, ...]) -> None:
    """Append ``changes`` through the recording API the set command uses."""
    store = HistoryStore.open(theatre / CONFIG_DIR_NAME)
    try:
        store.record_changes(changes)
    finally:
        store.close()


CONFIG = """\
# a theatre exercising every kind of leaf
name: demo
enabled: true
debug: false
retries: 3
mode: 0o17
ratio: 0.5
nothing: ~
answer: yes
empty_map: {}
empty_list: []
prompt: file:prompt.txt
team: file:team.yaml
agents:
  - name: alice
    tags: [a, b]
  - bob
"""

FILES = {
    ".troupe/prompt.txt": "Be nice.\r\nNo final newline",
    ".troupe/team.yaml": "lead: carol\nsize: 2\n",
}

IMPORTED: list[Event] = [
    (1, "import", "name", None, "demo"),
    (2, "import", "enabled", None, True),
    (3, "import", "debug", None, False),
    (4, "import", "retries", None, 3),
    (5, "import", "mode", None, 15),
    (6, "import", "ratio", None, 0.5),
    (7, "import", "nothing", None, None),
    (8, "import", "answer", None, "yes"),
    (9, "import", "prompt", None, "Be nice.\r\nNo final newline"),
    (10, "import", "team.lead", None, "carol"),
    (11, "import", "team.size", None, 2),
    (12, "import", "agents.0.name", None, "alice"),
    (13, "import", "agents.0.tags.0", None, "a"),
    (14, "import", "agents.0.tags.1", None, "b"),
    (15, "import", "agents.1", None, "bob"),
]


# ---- 7.1, 7.2, 8.1, 8.2, 8.6: the first command imports one event per leaf ----


def test_first_show_imports_one_event_per_leaf_listed_by_a_later_history(
    make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    theatre = make_theatre(CONFIG, files=FILES)
    assert not _store_file(theatre).exists()

    shown = _show(run_cli, theatre, "name")
    assert (shown.exit_code, shown.stdout) == (0, b"demo\n")
    assert _store_file(theatre).is_file()

    assert _events(_history(run_cli, theatre)) == IMPORTED


def test_history_prints_booleans_as_json_booleans_never_numbers(
    make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    theatre = make_theatre("on: true\noff: false\n")

    result = _history(run_cli, theatre)

    assert result.stdout == (
        b'{"seq": 1, "op": "import", "path": "on", "old": null, "new": true}\n'
        b'{"seq": 2, "op": "import", "path": "off", "old": null, "new": false}\n'
    )
    news = [new for _, _, _, _, new in _events(result)]
    assert [type(new) for new in news] == [bool, bool]


# ---- 8.5: history on a never-imported theatre imports first ----


def test_first_history_on_a_fresh_theatre_imports_and_prints_the_import(
    make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    theatre = make_theatre(CONFIG, files=FILES)
    assert not _store_file(theatre).exists()

    result = _history(run_cli, theatre)

    assert _events(result) == IMPORTED
    assert _store_file(theatre).is_file()


# ---- 7.3: a later command imports nothing ----


def test_later_commands_import_nothing_again(make_theatre: MakeTheatre, run_cli: RunCli) -> None:
    theatre = make_theatre(CONFIG, files=FILES)
    first = _history(run_cli, theatre)

    assert _show(run_cli, theatre, "agents.1").stdout == b"bob\n"
    assert _show(run_cli, theatre, "team.lead").stdout == b"carol\n"
    second = _history(run_cli, theatre)

    assert _events(second) == IMPORTED
    assert second.stdout == first.stdout


# ---- 6.9, 7.3: importing is not reconciling ----


def test_hand_edits_add_no_events_while_show_reflects_them(
    make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    theatre = make_theatre(CONFIG, files=FILES)
    before = _history(run_cli, theatre)

    config_file = theatre / CONFIG_DIR_NAME / "troupe.yml"
    edited = CONFIG.replace("name: demo", "name: edited").replace("retries: 3\n", "")
    config_file.write_text(edited + "added: new\n", encoding="utf-8")
    (theatre / CONFIG_DIR_NAME / "team.yaml").write_text("lead: dave\nsize: 2\n", encoding="utf-8")

    assert _show(run_cli, theatre, "name").stdout == b"edited\n"
    assert _show(run_cli, theatre, "added").stdout == b"new\n"
    assert _show(run_cli, theatre, "team.lead").stdout == b"dave\n"
    after = _history(run_cli, theatre)

    assert after.stdout == before.stdout
    assert _events(after) == IMPORTED


# ---- 7.5: a theatre with no leaves is marked imported with no events ----


def test_theatre_without_leaves_is_imported_with_no_events_and_never_reimported(
    make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    theatre = make_theatre("# nothing yet\nempty: {}\nnone: []\n")

    first = _history(run_cli, theatre)
    assert (first.exit_code, first.stdout, first.stderr) == (0, b"", "")
    assert _store_file(theatre).is_file()

    (theatre / CONFIG_DIR_NAME / "troupe.yml").write_text("late: 1\n", encoding="utf-8")
    assert _show(run_cli, theatre, "late").stdout == b"1\n"

    assert _events(_history(run_cli, theatre)) == []


# ---- 8.3, 8.4: prefix filtering keeps the original seq numbers ----

PREFIX_CONFIG = """\
agents:
  alice:
    model: m1
  alice2: x
agentsx: y
agents_count: 2
alice: z
"""


def test_history_path_lists_the_path_and_its_descendants_only(
    make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    theatre = make_theatre(PREFIX_CONFIG)

    assert _events(_history(run_cli, theatre, "agents")) == [
        (1, "import", "agents.alice.model", None, "m1"),
        (2, "import", "agents.alice2", None, "x"),
    ]
    assert _events(_history(run_cli, theatre, "agents.alice")) == [
        (1, "import", "agents.alice.model", None, "m1"),
    ]


def test_history_path_equal_to_a_leaf_lists_that_leaf_with_its_seq(
    make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    theatre = make_theatre(PREFIX_CONFIG)

    assert _events(_history(run_cli, theatre, "agentsx")) == [
        (3, "import", "agentsx", None, "y"),
    ]
    assert _events(_history(run_cli, theatre, "alice")) == [
        (5, "import", "alice", None, "z"),
    ]


def test_history_list_index_path_lists_that_item_only(
    make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    theatre = make_theatre("items: [a, b, c, d, e, f, g, h, i, j, k]\n")

    assert _events(_history(run_cli, theatre, "items.1")) == [
        (2, "import", "items.1", None, "b"),
    ]


def test_history_path_matching_nothing_prints_nothing_and_exits_0(
    make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    theatre = make_theatre(PREFIX_CONFIG)

    for path in ("missing", "agent", "agents.alice.model.deeper", "agents.bob"):
        result = _history(run_cli, theatre, path)
        assert (result.exit_code, result.stdout, result.stderr) == (0, b"", ""), path


# ---- 9.1-9.3: set and remove events appended through the recording API ----


def test_history_lists_recorded_set_and_remove_events_with_consecutive_seq(
    make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    theatre = make_theatre("name: demo\nagents:\n  - temperature: 0.5\n")
    _history(run_cli, theatre)  # import: seq 1-2

    _record(
        theatre,
        (
            SetChange(path=ConfigPath.parse("name"), old="demo", new="renamed"),
            SetChange(path=ConfigPath.parse("agents.0.enabled"), old=None, new=True),
            RemoveChange(path=ConfigPath.parse("agents.0.temperature"), old=0.5),
        ),
    )
    _record(theatre, (SetChange(path=ConfigPath.parse("ratio"), old=None, new=float("inf")),))

    assert _events(_history(run_cli, theatre)) == [
        (1, "import", "name", None, "demo"),
        (2, "import", "agents.0.temperature", None, 0.5),
        (3, "set", "name", "demo", "renamed"),
        (4, "set", "agents.0.enabled", None, True),
        (5, "remove", "agents.0.temperature", 0.5, None),
        (6, "set", "ratio", None, ".inf"),
    ]
    assert _events(_history(run_cli, theatre, "agents")) == [
        (2, "import", "agents.0.temperature", None, 0.5),
        (4, "set", "agents.0.enabled", None, True),
        (5, "remove", "agents.0.temperature", 0.5, None),
    ]


# ---- 3.8: a folded leaf's import event holds the folded text ----


def test_folded_leaf_import_event_holds_the_folded_text_without_bell(
    make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    theatre = make_theatre("clip: >\n  a\n  b\n\n  c\nstrip: >-\n  x\n  y\n")

    result = _history(run_cli, theatre)

    assert b"\x07" not in result.stdout
    assert b"\\u0007" not in result.stdout
    assert _events(result) == [
        (1, "import", "clip", None, "a b\nc\n"),
        (2, "import", "strip", None, "x y"),
    ]


# ---- 8.7: non-finite floats are canonical YAML strings so every line is strict JSON ----


def test_non_finite_float_leaves_appear_as_canonical_yaml_strings(
    make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    theatre = make_theatre("up: .inf\ndown: -.inf\nnot: .nan\nalso: .Inf\n")

    result = _history(run_cli, theatre)

    assert b"Infinity" not in result.stdout
    assert b"NaN" not in result.stdout
    assert _events(result) == [
        (1, "import", "up", None, ".inf"),
        (2, "import", "down", None, "-.inf"),
        (3, "import", "not", None, ".nan"),
        (4, "import", "also", None, ".inf"),
    ]
