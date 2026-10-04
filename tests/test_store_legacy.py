"""Histories recorded before step 4: stored events without lineage are upcast on read.

config-lineage 7.2, 7.3 (requirements Boundary Context choice 7; research Decisions 4 and 5).

A real store is recorded with the current code in a real temporary theatre, closed, and then every
stored event row in its SQLite file is rewritten to the pre-step-4 shape: the six lineage fields
and the schema version are removed from the stored state. Reading it back must give the legacy
lineage, the same records on every read, and new events recorded after it must get full lineage.

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

import json
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import UUID

import pytest

from support import MakeTheatre, RunCli, step1
from troupe.config.constants import CONFIG_DIR_NAME
from troupe.config.errors import ConfigLoadError, ExitCode
from troupe.config.history import HistoryRecord, LeafChange, RemoveChange, SetChange
from troupe.config.lineage import (
    IMPORT_CONTEXT,
    UNKNOWN_ACTOR,
    Actor,
    Lineage,
    cli_context,
    format_utc,
)
from troupe.config.loader import load_config
from troupe.config.locate import find_config_file
from troupe.config.paths import ConfigPath
from troupe.config.resolve import flatten_leaves
from troupe.config.store import HistoryStore

CONFIG = "name: demo\nagents:\n  - name: alice\n    temperature: 0.5\n"

ALICE = Actor(kind="human", id="alice")

LINEAGE_KEYS = ("actor_kind", "actor_id", "via", "at", "reason", "change")

SET_BATCH: tuple[LeafChange, ...] = (
    SetChange(path=ConfigPath.parse("name"), old="demo", new="renamed"),
    SetChange(path=ConfigPath.parse("agents.0.enabled"), old=None, new=True),
)
REMOVE_BATCH: tuple[LeafChange, ...] = (
    RemoveChange(path=ConfigPath.parse("agents.0.temperature"), old=0.5),
)
LATER_BATCH: tuple[LeafChange, ...] = (
    SetChange(path=ConfigPath.parse("name"), old="renamed", new="later"),
)


@dataclass(frozen=True)
class StoredRow:
    """One stored event row: its version, topic and decoded JSON state."""

    version: int
    topic: str
    state: dict[str, object]


def _rows(location: Path) -> tuple[StoredRow, ...]:
    connection = sqlite3.connect(location)
    try:
        rows = connection.execute(
            "SELECT originator_version, topic, state FROM stored_events ORDER BY originator_version"
        ).fetchall()
    finally:
        connection.close()
    return tuple(
        StoredRow(version=version, topic=topic, state=json.loads(bytes(state)))
        for version, topic, state in rows
    )


def _rewrite_to_pre_step_4(location: Path) -> int:
    """Remove the lineage fields and the schema version from every stored event's state."""
    connection = sqlite3.connect(location)
    try:
        rows = connection.execute("SELECT rowid, state FROM stored_events").fetchall()
        rewritten = 0
        for rowid, state in rows:
            decoded = json.loads(bytes(state))
            if "change" not in decoded:
                continue  # the history-start event has no lineage
            for key in LINEAGE_KEYS:
                del decoded[key]
            decoded.pop("class_version", None)  # stored since step 4 (schema version 2)
            connection.execute(
                "UPDATE stored_events SET state = ? WHERE rowid = ?",
                (json.dumps(decoded).encode("utf-8"), rowid),
            )
            rewritten += 1
        connection.commit()
    finally:
        connection.close()
    return rewritten


def _stored_at(row: StoredRow) -> str:
    """``format_utc`` of the time eventsourcing stored with the event (its ``timestamp``)."""
    timestamp = row.state["timestamp"]
    assert isinstance(timestamp, dict)
    data = timestamp["_data_"]
    assert isinstance(data, str)
    return format_utc(datetime.fromisoformat(data))


def _read(theatre: Path) -> tuple[HistoryRecord, ...]:
    reopened = HistoryStore.open(theatre / CONFIG_DIR_NAME)
    try:
        return reopened.records()
    finally:
        reopened.close()


@pytest.fixture
def theatre(make_theatre: MakeTheatre) -> Path:
    return make_theatre(CONFIG)


@pytest.fixture
def legacy(theatre: Path) -> Iterator[tuple[HistoryRecord, ...]]:
    """Record an import, a set batch and a remove batch, then rewrite the store to pre-step-4.

    Yields the records as read before the rewrite.
    """
    store = HistoryStore.open(theatre / CONFIG_DIR_NAME)
    try:
        store.ensure_imported(
            flatten_leaves(load_config(find_config_file(theatre)).root), IMPORT_CONTEXT
        )
        store.record_changes(SET_BATCH, cli_context(ALICE, "a reason"))
        store.record_changes(REMOVE_BATCH, cli_context(ALICE, None))
        recorded = store.records()
    finally:
        store.close()
    rewritten = _rewrite_to_pre_step_4(theatre / CONFIG_DIR_NAME / "history.sqlite")
    assert rewritten == len(recorded) == 6
    yield recorded


def test_new_events_are_stored_with_schema_version_2(
    theatre: Path, legacy: tuple[HistoryRecord, ...]
) -> None:
    """Req 7.3 (design Migration Strategy): new history events are written with schema version 2
    and their lineage, while the history-start event has no schema version."""
    store = HistoryStore.open(theatre / CONFIG_DIR_NAME)
    try:
        store.record_changes(LATER_BATCH, cli_context(ALICE, None))
    finally:
        store.close()

    rows = _rows(theatre / CONFIG_DIR_NAME / "history.sqlite")
    assert "class_version" not in rows[0].state
    assert [row.state.get("class_version") for row in rows[1:]] == [None] * 6 + [2]
    assert all(key in rows[-1].state for key in LINEAGE_KEYS)


def test_a_pre_step_4_store_is_read_with_the_legacy_lineage(
    theatre: Path, legacy: tuple[HistoryRecord, ...]
) -> None:
    """Req 7.3 (Boundary Context choice 7): pre-step-4 events keep their step 1 members and get
    actor unknown, reason null, ``at`` from the stored event time, via import for imports and cli
    otherwise, one shared change for the imports and a separate one per other event."""
    rows = _rows(theatre / CONFIG_DIR_NAME / "history.sqlite")

    records = _read(theatre)

    assert step1(records) == step1(legacy)
    stored_at = [_stored_at(row) for row in rows[1:]]
    assert all(at.endswith("Z") for at in stored_at)
    assert [record.lineage for record in records] == [
        Lineage(UNKNOWN_ACTOR, "import", stored_at[0], None, "legacy-import"),
        Lineage(UNKNOWN_ACTOR, "import", stored_at[1], None, "legacy-import"),
        Lineage(UNKNOWN_ACTOR, "import", stored_at[2], None, "legacy-import"),
        Lineage(UNKNOWN_ACTOR, "cli", stored_at[3], None, "legacy-4"),
        Lineage(UNKNOWN_ACTOR, "cli", stored_at[4], None, "legacy-5"),
        Lineage(UNKNOWN_ACTOR, "cli", stored_at[5], None, "legacy-6"),
    ]


def test_a_pre_step_4_store_reads_identically_every_time(
    theatre: Path, legacy: tuple[HistoryRecord, ...]
) -> None:
    """Req 7.2, 7.3: the legacy lineage is deterministic, so reading the same store twice, and
    reopening it, gives identical records; reading does not rewrite the stored rows."""
    rows_before = _rows(theatre / CONFIG_DIR_NAME / "history.sqlite")
    store = HistoryStore.open(theatre / CONFIG_DIR_NAME)
    try:
        first, second = store.records(), store.records()
    finally:
        store.close()

    assert first == second == _read(theatre)
    assert _rows(theatre / CONFIG_DIR_NAME / "history.sqlite") == rows_before


def test_new_events_after_a_pre_step_4_history_get_full_lineage(
    theatre: Path, legacy: tuple[HistoryRecord, ...]
) -> None:
    """Req 7.2, 7.3: a batch recorded after a pre-step-4 history continues its seq and gets the
    batch's full stamped lineage (a UUID change id), which survives reopening, while the legacy
    records stay unchanged."""
    before = _read(theatre)
    store = HistoryStore.open(theatre / CONFIG_DIR_NAME)
    try:
        store.record_changes(LATER_BATCH, cli_context(ALICE, "later"))
        recorded = store.records()
    finally:
        store.close()

    reopened = _read(theatre)
    assert reopened == recorded
    assert reopened[:6] == before
    (later,) = reopened[6:]
    assert (later.seq, later.op, later.path, later.old, later.new) == (
        7,
        "set",
        "name",
        "renamed",
        "later",
    )
    assert (later.lineage.actor, later.lineage.via, later.lineage.reason) == (ALICE, "cli", "later")
    assert str(UUID(later.lineage.change)) == later.lineage.change
    assert later.lineage.at.endswith("Z")
    assert later.lineage.at >= max(record.lineage.at for record in before)


def test_the_history_command_reads_a_pre_step_4_store(
    theatre: Path, legacy: tuple[HistoryRecord, ...], run_cli: RunCli
) -> None:
    """Req 7.3: ``troupe config history`` on a pre-step-4 store succeeds and prints one line per
    recorded event."""
    result = run_cli("config", "history", cwd=theatre)

    assert result.exit_code == 0, result.stderr
    assert len(result.stdout.decode("utf-8").splitlines()) == len(legacy)


def test_a_pre_step_4_event_without_a_stored_time_is_an_unreadable_store(
    theatre: Path, legacy: tuple[HistoryRecord, ...]
) -> None:
    """Req 7.3 (design Error Handling): a pre-step-4 event whose stored time is not a datetime
    cannot get its legacy ``at``, so reading it is a load error naming the store (exit 4)."""
    location = theatre / CONFIG_DIR_NAME / "history.sqlite"
    connection = sqlite3.connect(location)
    try:
        connection.execute(
            "UPDATE stored_events SET state = ? WHERE originator_version = 2",
            (json.dumps({"timestamp": "not a time", "path": "name", "new": "demo"}).encode(),),
        )
        connection.commit()
    finally:
        connection.close()

    store = HistoryStore.open(theatre / CONFIG_DIR_NAME)
    try:
        with pytest.raises(ConfigLoadError) as caught:
            store.records()
    finally:
        store.close()
    assert caught.value.file == location
    assert caught.value.exit_code == ExitCode.LOAD
