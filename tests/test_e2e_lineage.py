"""History lineage end to end through the real ``troupe`` console script.

Proves, through real theatres on disk and real ``troupe config`` subprocesses, the lineage of each
origin (import 3.1, hand edits reconciled by ``get``, ``show``, ``set`` and ``watch`` 3.2, ``set``
3.3, unknown actor only where the author is unknown 3.4), change grouping (one change per import,
reconcile and ``set`` 4.1, reconcile and ``set`` separate 4.2, never shared 4.3, nothing recorded
for an empty batch 4.4), time (``at`` in UTC with six digits between the instants around the
command and shared within a change, whatever ``TZ`` says: 1.4, 5.1-5.3), the member order of every
line (1.1, 1.3, 1.6, 1.7), filtered lines (1.8), lineage stored with the history and reprinted
identically by new processes (7.1, 7.2), and no extra file (7.4).

Every ``history`` line is parsed in full (not projected onto its step-1 members), so each test
asserts the lineage members exactly as printed.
"""

import json
import os
import re
import signal
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import pytest

from support import LINEAGE_KEYS, STEP1_KEYS, CliResult, MakeTheatre, RunCli
from troupe.config.constants import CONFIG_DIR_NAME, HISTORY_STORE_NAME
from watch_support import WatchProcess

type Line = dict[str, Any]
"""One ``history`` line parsed as JSON (values are JSON, hence ``Any``)."""

UNKNOWN: Final = {"kind": "unknown", "id": None}
AT_PATTERN: Final = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z$")

CONFIG: Final = """\
# the demo theatre
name: demo
agents:
  lead:
    model: opus
    retries: 3
  helper:
    model: haiku
"""

EDITED: Final = """\
# the demo theatre, edited by hand
name: renamed
agents:
  lead:
    model: opus
    retries: 4
  helper:
    model: haiku
"""
"""``CONFIG`` with two leaves changed by hand (``name``, ``agents.lead.retries``)."""


# ---- helpers ----


def _config(theatre: Path) -> Path:
    return theatre / CONFIG_DIR_NAME / "troupe.yml"


def _ok(result: CliResult) -> CliResult:
    assert (result.exit_code, result.stderr) == (0, ""), result.stderr
    return result


def _parse(stdout: bytes) -> list[Line]:
    """Parse every ``history`` line, asserting member order and the ``actor`` members (1.2, 1.7)."""
    lines: list[Line] = []
    for raw in stdout.decode("utf-8").splitlines():
        parsed = json.loads(raw)
        assert isinstance(parsed, dict), raw
        assert tuple(parsed) == STEP1_KEYS + LINEAGE_KEYS, raw
        assert isinstance(parsed["actor"], dict) and tuple(parsed["actor"]) == ("kind", "id"), raw
        lines.append(parsed)
    return lines


def _history(run_cli: RunCli, theatre: Path, *path: str) -> CliResult:
    return _ok(run_cli("config", "history", *path, "--theatre", str(theatre)))


def _lines(run_cli: RunCli, theatre: Path) -> list[Line]:
    return _parse(_history(run_cli, theatre).stdout)


def _config_cmd(run_cli: RunCli, theatre: Path, *args: str) -> CliResult:
    return _ok(run_cli("config", *args, "--theatre", str(theatre)))


def _lineage(line: Line) -> tuple[Any, ...]:
    return tuple(line[key] for key in ("actor", "via", "reason"))


def _changes(lines: list[Line]) -> list[str]:
    """The distinct change ids of ``lines`` in order of first appearance."""
    return list(dict.fromkeys(line["change"] for line in lines))


def _parse_at(at: str) -> datetime:
    assert AT_PATTERN.match(at), at
    return datetime.strptime(at, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)


def _timed[T](command: Callable[[], T]) -> tuple[datetime, T, datetime]:
    """Run ``command`` between two UTC instants taken in this process."""
    before = datetime.now(UTC)
    outcome = command()
    after = datetime.now(UTC)
    return before, outcome, after


def _assert_one_change_between(lines: list[Line], before: datetime, after: datetime) -> None:
    """Req 4.1, 5.1, 5.2: ``lines`` share one change and one ``at`` inside [before, after]."""
    assert lines
    assert len({line["change"] for line in lines}) == 1, lines
    assert all(isinstance(line["change"], str) for line in lines), lines
    assert len({line["at"] for line in lines}) == 1, lines
    assert before <= _parse_at(lines[0]["at"]) <= after, (before, lines[0]["at"], after)


# ---- 3.1: import lineage ----


def test_first_history_records_import_events_as_one_unknown_import_change(
    make_theatre: MakeTheatre, run_cli: RunCli, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Req 1.3, 1.4, 1.6, 3.1, 3.4, 4.1, 5.1, 5.2: the first ``history`` imports every leaf with
    actor unknown, ``via`` import and ``reason`` null, whatever ``TROUPE_ACTOR`` holds; all import
    events share one change and one ``at`` taken while the command ran."""
    monkeypatch.setenv("TROUPE_ACTOR", "agent:importer")
    theatre = make_theatre(CONFIG)

    before, result, after = _timed(lambda: _history(run_cli, theatre))
    lines = _parse(result.stdout)

    assert [(line["op"], line["path"]) for line in lines] == [
        ("import", "name"),
        ("import", "agents.lead.model"),
        ("import", "agents.lead.retries"),
        ("import", "agents.helper.model"),
    ]
    assert [_lineage(line) for line in lines] == [(UNKNOWN, "import", None)] * 4
    _assert_one_change_between(lines, before, after)


# ---- 3.2: file lineage before get and show ----


@pytest.mark.parametrize("command", ["get", "show"])
def test_hand_edit_reconciled_by_get_or_show_is_one_unknown_file_change(
    command: str, make_theatre: MakeTheatre, run_cli: RunCli, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Req 3.2, 3.4, 4.1, 4.3, 5.1, 5.2: two hand edits reconciled by ``get``/``show`` are file
    events with actor unknown and ``reason`` null even with ``TROUPE_ACTOR`` set, sharing one change
    and one ``at`` from that command, and a change distinct from the import's."""
    theatre = make_theatre(CONFIG)
    imported = _lines(run_cli, theatre)
    monkeypatch.setenv("TROUPE_ACTOR", "agent:x")
    _config(theatre).write_text(EDITED, encoding="utf-8")

    before, _, after = _timed(lambda: _config_cmd(run_cli, theatre, command, "name"))
    lines = _lines(run_cli, theatre)

    assert lines[:4] == imported
    edits = lines[4:]
    assert [(line["op"], line["path"], line["new"]) for line in edits] == [
        ("set", "name", "renamed"),
        ("set", "agents.lead.retries", 4),
    ]
    assert [_lineage(line) for line in edits] == [(UNKNOWN, "file", None)] * 2
    _assert_one_change_between(edits, before, after)
    assert edits[0]["change"] != imported[0]["change"]


# ---- 3.3: cli lineage of set ----


def test_set_of_a_mapping_records_one_cli_change_with_the_declared_actor(
    make_theatre: MakeTheatre, run_cli: RunCli, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Req 3.3, 4.1, 4.3, 5.1, 5.2: a ``set`` of a mapping changing several leaves records one cli
    change with the actor from ``TROUPE_ACTOR``, every event sharing that change and ``at``."""
    theatre = make_theatre(CONFIG)
    imported = _lines(run_cli, theatre)
    monkeypatch.setenv("TROUPE_ACTOR", "agent:planner")

    before, _, after = _timed(
        lambda: _config_cmd(run_cli, theatre, "set", "agents.lead", "{model: sonnet, retries: 5}")
    )
    lines = _lines(run_cli, theatre)

    assert lines[:4] == imported
    own = lines[4:]
    assert [(line["op"], line["path"], line["new"]) for line in own] == [
        ("set", "agents.lead.model", "sonnet"),
        ("set", "agents.lead.retries", 5),
    ]
    planner = {"kind": "agent", "id": "planner"}
    assert [_lineage(line) for line in own] == [(planner, "cli", None)] * 2
    _assert_one_change_between(own, before, after)
    assert own[0]["change"] != imported[0]["change"]


# ---- 4.2: a reconcile and a set are two changes ----


def test_hand_edit_then_set_records_a_file_change_then_a_cli_change(
    make_theatre: MakeTheatre, run_cli: RunCli, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Req 3.2, 3.3, 4.1, 4.2, 5.1: a ``set`` on a hand-edited theatre records the reconcile as one
    file change (unknown actor) and then its own events as a different cli change (declared actor),
    both stamped while the command ran."""
    theatre = make_theatre(CONFIG)
    _history(run_cli, theatre)
    monkeypatch.setenv("TROUPE_ACTOR", "human:alice")
    _config(theatre).write_text(EDITED, encoding="utf-8")

    before, _, after = _timed(
        lambda: _config_cmd(run_cli, theatre, "set", "agents.helper.model", "sonnet")
    )
    lines = _lines(run_cli, theatre)[4:]

    assert [(line["path"], line["via"]) for line in lines] == [
        ("name", "file"),
        ("agents.lead.retries", "file"),
        ("agents.helper.model", "cli"),
    ]
    file_events, cli_events = lines[:2], lines[2:]
    _assert_one_change_between(file_events, before, after)
    _assert_one_change_between(cli_events, before, after)
    assert file_events[0]["change"] != cli_events[0]["change"]
    assert [_lineage(line) for line in file_events] == [(UNKNOWN, "file", None)] * 2
    assert cli_events[0]["actor"] == {"kind": "human", "id": "alice"}


# ---- 4.3, 7.1: successive commands never share a change; earlier lines never change ----


def test_successive_commands_never_share_a_change(
    make_theatre: MakeTheatre, run_cli: RunCli, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Req 4.3, 7.1: across an import, reconciles by ``get``, ``show`` and ``set``, and several
    ``set`` runs (including two identical-looking ones), every command's events form changes whose
    ids were never used before, and lines printed earlier are printed byte-identically later."""
    monkeypatch.setenv("TROUPE_ACTOR", "agent:planner")
    theatre = make_theatre(CONFIG)
    config = _config(theatre)
    printed: list[bytes] = []
    seen: list[str] = []

    def step(command: Callable[[], object], expected_changes: int) -> None:
        command()
        stdout = _history(run_cli, theatre).stdout.splitlines(keepends=True)
        assert stdout[: len(printed)] == printed
        new = _parse(b"".join(stdout[len(printed) :]))
        changes = _changes(new)
        assert len(changes) == expected_changes, new
        assert not set(changes) & set(seen), (changes, seen)
        printed.extend(stdout[len(printed) :])
        seen.extend(changes)

    step(lambda: None, 1)  # the import, made by the history run itself
    step(lambda: config.write_text(EDITED, encoding="utf-8"), 0)  # history never reconciles
    step(lambda: _config_cmd(run_cli, theatre, "get", "name"), 1)
    step(lambda: _config_cmd(run_cli, theatre, "set", "name", "one"), 1)
    step(lambda: _config_cmd(run_cli, theatre, "set", "name", "two"), 1)
    step(lambda: _config_cmd(run_cli, theatre, "set", "name", "one"), 1)
    step(lambda: config.write_text(CONFIG, encoding="utf-8"), 0)
    step(lambda: _config_cmd(run_cli, theatre, "show", "agents"), 1)
    step(lambda: config.write_text(EDITED, encoding="utf-8"), 0)
    step(lambda: _config_cmd(run_cli, theatre, "set", "agents.helper.model", "opus"), 2)

    assert len(seen) == len(set(seen)) == 8


# ---- 3.2, 4.3: watch rounds ----


def _at_least(count: int) -> Callable[[list[dict[str, Any]]], bool]:
    return lambda events: len(events) >= count


def test_watch_rounds_are_separate_unknown_file_changes(
    tmp_path: Path, make_theatre: MakeTheatre, run_cli: RunCli, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Req 3.2, 3.4, 4.1, 4.3, 5.2: two hand edits that ``watch`` reconciles in two rounds (each
    awaited before the next) are two file changes with unknown actor and null reason, even with
    ``TROUPE_ACTOR`` set, with distinct change ids that differ from the import's."""
    monkeypatch.setenv("TROUPE_ACTOR", "agent:watcher")  # inherited by the watch child
    theatre = make_theatre(CONFIG)
    imported = _lines(run_cli, theatre)
    config = _config(theatre)

    with WatchProcess(str(theatre), cwd=tmp_path) as watch:
        assert watch.wait_ready() == f"watching {theatre}\n".encode()
        for count, content in (
            (5, CONFIG.replace("name: demo", "name: first")),
            (6, CONFIG.replace("name: demo", "name: second")),
        ):
            since = time.monotonic()
            temporary = config.with_name(".troupe.yml.edit-tmp")
            temporary.write_text(content, encoding="utf-8")
            os.replace(temporary, config)
            watch.wait_for_history(_at_least(count), since=since)
        assert watch.stop(signal.SIGTERM) == 0

    lines = _lines(run_cli, theatre)
    assert lines[:4] == imported
    rounds = lines[4:]
    assert [(line["path"], line["new"]) for line in rounds] == [
        ("name", "first"),
        ("name", "second"),
    ]
    assert [_lineage(line) for line in rounds] == [(UNKNOWN, "file", None)] * 2
    assert len({imported[0]["change"], rounds[0]["change"], rounds[1]["change"]}) == 3
    assert rounds[0]["at"] < rounds[1]["at"]


# ---- 1.4, 5.1, 5.3: at is UTC whatever TZ says ----


def test_at_is_utc_with_six_digits_when_tz_is_not_utc(
    make_theatre: MakeTheatre, run_cli: RunCli, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Req 1.4, 5.1, 5.2, 5.3: with ``TZ=Asia/Tokyo`` (UTC+9) in the command's environment, the
    import and ``set`` events still have ``at`` in UTC (``...ffffffZ``) between the UTC instants
    taken around each command, not nine hours off."""
    monkeypatch.setenv("TZ", "Asia/Tokyo")
    theatre = make_theatre(CONFIG)

    import_before, _, import_after = _timed(lambda: _history(run_cli, theatre))
    set_before, _, set_after = _timed(
        lambda: _config_cmd(run_cli, theatre, "set", "agents.lead", "{model: a, retries: 1}")
    )
    lines = _lines(run_cli, theatre)

    _assert_one_change_between(lines[:4], import_before, import_after)
    _assert_one_change_between(lines[4:], set_before, set_after)


# ---- 1.1, 1.2, 1.7: member order of every line ----


def test_every_line_has_the_ten_members_in_contract_order(
    make_theatre: MakeTheatre, run_cli: RunCli, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Req 1.1, 1.2, 1.3, 1.6, 1.7: import, file and cli lines each print exactly ``seq``, ``op``,
    ``path``, ``old``, ``new``, ``actor``, ``via``, ``at``, ``reason``, ``change`` in that order,
    with ``actor`` holding exactly ``kind`` then ``id``."""
    monkeypatch.setenv("TROUPE_ACTOR", "agent:planner")
    theatre = make_theatre(CONFIG)
    _history(run_cli, theatre)
    _config(theatre).write_text(EDITED, encoding="utf-8")
    _config_cmd(run_cli, theatre, "set", "agents.helper.model", "opus", "--reason", "why")

    stdout = _history(run_cli, theatre).stdout
    for raw in stdout.decode("utf-8").splitlines():
        line = json.loads(raw)
        actor = line["actor"]
        ordered = {key: line[key] for key in STEP1_KEYS}
        ordered["actor"] = {"kind": actor["kind"], "id": actor["id"]}
        ordered.update({key: line[key] for key in LINEAGE_KEYS[1:]})
        assert raw == json.dumps(ordered)
    assert {line["via"] for line in _parse(stdout)} == {"import", "file", "cli"}


# ---- 7.1, 7.2: reprints are identical ----


def test_reprinting_history_in_a_new_process_gives_identical_bytes(
    make_theatre: MakeTheatre, run_cli: RunCli, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Req 7.1, 7.2: lineage of import, file and cli events is stored with the history, so later
    ``history`` runs (new processes, with a different ``TROUPE_ACTOR`` and ``TZ``) print exactly the
    same bytes."""
    monkeypatch.setenv("TROUPE_ACTOR", "agent:planner")
    theatre = make_theatre(CONFIG)
    _history(run_cli, theatre)
    _config(theatre).write_text(EDITED, encoding="utf-8")
    _config_cmd(run_cli, theatre, "set", "agents.helper.model", "opus", "--reason", "why")

    first = _history(run_cli, theatre).stdout
    monkeypatch.setenv("TROUPE_ACTOR", "human:someone-else")
    monkeypatch.setenv("TZ", "Asia/Tokyo")
    second = _history(run_cli, theatre).stdout
    third = _history(run_cli, theatre).stdout

    assert len(_parse(first)) == 7
    assert second == first
    assert third == first


# ---- 1.8: filtered lines ----


def test_history_path_lines_equal_their_unfiltered_lines(
    make_theatre: MakeTheatre, run_cli: RunCli, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Req 1.8: ``history PATH`` prints each selected import, file and cli event byte-identically
    to its line in the unfiltered listing, lineage included."""
    monkeypatch.setenv("TROUPE_ACTOR", "agent:planner")
    theatre = make_theatre(CONFIG)
    _history(run_cli, theatre)
    _config(theatre).write_text(EDITED, encoding="utf-8")
    _config_cmd(run_cli, theatre, "set", "agents.lead.model", "sonnet", "--reason", "upgrade")

    unfiltered = _history(run_cli, theatre).stdout.splitlines(keepends=True)
    filtered = _history(run_cli, theatre, "agents.lead").stdout

    selected = [raw for raw in unfiltered if json.loads(raw)["path"].startswith("agents.lead.")]
    assert filtered == b"".join(selected)
    assert {json.loads(raw)["via"] for raw in selected} == {"import", "file", "cli"}


# ---- 4.4: no events, no change ----


def test_a_no_op_set_and_a_command_without_hand_edits_record_no_change(
    make_theatre: MakeTheatre, run_cli: RunCli, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Req 4.4: a ``set`` that changes nothing and ``get``/``show``/``set`` runs with no hand edit
    to reconcile record no event and no change: ``history`` prints exactly the same lines."""
    monkeypatch.setenv("TROUPE_ACTOR", "agent:planner")
    theatre = make_theatre(CONFIG)
    _history(run_cli, theatre)
    _config_cmd(run_cli, theatre, "set", "name", "changed")
    before = _history(run_cli, theatre).stdout

    _config_cmd(run_cli, theatre, "set", "name", "changed")
    _config_cmd(run_cli, theatre, "set", "agents.lead", "{model: opus, retries: 3}")
    _config_cmd(run_cli, theatre, "get", "name")
    _config_cmd(run_cli, theatre, "show", "agents")

    assert len(_parse(before)) == 5
    assert _history(run_cli, theatre).stdout == before


# ---- 7.4: no extra file ----


def _tree(root: Path) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for directory, _subdirectories, names in os.walk(root):
        for name in names:
            file = Path(directory) / name
            files[file.relative_to(root).as_posix()] = file.read_bytes()
    return files


def test_lineage_leaves_no_extra_file_inside_or_outside_the_config_directory(
    tmp_path: Path, make_theatre: MakeTheatre, run_cli: RunCli, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Req 7.4: after import, reconciles, ``set`` with ``--reason`` and ``history`` runs, the
    configuration directory holds only the config file and ``history.sqlite``, and nothing outside
    it was created or changed."""
    monkeypatch.setenv("TROUPE_ACTOR", "agent:planner")
    theatre = make_theatre(CONFIG)
    (tmp_path / "outside.txt").write_text("untouched\n", encoding="utf-8")
    config_dir = theatre / CONFIG_DIR_NAME

    def outside() -> dict[str, bytes]:
        return {k: v for k, v in _tree(tmp_path).items() if not k.startswith("theatre/.troupe/")}

    before = outside()
    _history(run_cli, theatre)
    _config(theatre).write_text(EDITED, encoding="utf-8")
    _config_cmd(run_cli, theatre, "get", "name")
    _config_cmd(run_cli, theatre, "set", "agents.lead", "{model: a, retries: 1}", "--reason", "r")
    _config(theatre).write_text(CONFIG, encoding="utf-8")
    _config_cmd(run_cli, theatre, "set", "name", "last")
    _history(run_cli, theatre)

    assert sorted(os.listdir(config_dir)) == sorted(["troupe.yml", HISTORY_STORE_NAME])
    assert outside() == before
