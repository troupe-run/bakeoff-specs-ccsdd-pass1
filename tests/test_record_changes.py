"""The change-recording API used by the set command (9.1-9.4), against a real SQLite store."""

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from eventsourcing.sqlite import SQLiteCursor

from support import CLI_CONTEXT, MakeTheatre, Step1Record, step1
from troupe.config.constants import CONFIG_DIR_NAME
from troupe.config.errors import ConfigLoadError, ExitCode
from troupe.config.history import LeafChange, RemoveChange, SetChange
from troupe.config.lineage import IMPORT_CONTEXT
from troupe.config.loader import load_config
from troupe.config.locate import find_config_file
from troupe.config.paths import ConfigPath
from troupe.config.resolve import flatten_leaves
from troupe.config.store import HistoryStore

CONFIG = """\
name: demo
agents:
  - name: alice
    temperature: 0.5
"""

IMPORTED = (
    Step1Record(seq=1, op="import", path="name", old=None, new="demo"),
    Step1Record(seq=2, op="import", path="agents.0.name", old=None, new="alice"),
    Step1Record(seq=3, op="import", path="agents.0.temperature", old=None, new=0.5),
)

BATCH: tuple[LeafChange, ...] = (
    SetChange(path=ConfigPath.parse("name"), old="demo", new="renamed"),
    SetChange(path=ConfigPath.parse("agents.0.enabled"), old=None, new=True),
    RemoveChange(path=ConfigPath.parse("agents.0.temperature"), old=0.5),
)

BATCH_RECORDS = (
    Step1Record(seq=4, op="set", path="name", old="demo", new="renamed"),
    Step1Record(seq=5, op="set", path="agents.0.enabled", old=None, new=True),
    Step1Record(seq=6, op="remove", path="agents.0.temperature", old=0.5, new=None),
)


@pytest.fixture
def theatre(make_theatre: MakeTheatre) -> Path:
    return make_theatre(CONFIG)


@pytest.fixture
def store(theatre: Path) -> Iterator[HistoryStore]:
    opened = HistoryStore.open(theatre / CONFIG_DIR_NAME)
    try:
        yield opened
    finally:
        opened.close()


@pytest.fixture
def imported(theatre: Path, store: HistoryStore) -> HistoryStore:
    store.ensure_imported(
        flatten_leaves(load_config(find_config_file(theatre)).root), IMPORT_CONTEXT
    )
    assert step1(store.records()) == IMPORTED
    return store


def test_changes_follow_the_import_with_consecutive_seq_in_the_given_order(
    imported: HistoryStore,
) -> None:
    imported.record_changes(BATCH, CLI_CONTEXT)

    assert step1(imported.records()) == IMPORTED + BATCH_RECORDS


def test_a_created_leaf_records_old_null_and_a_removed_leaf_records_new_null(
    imported: HistoryStore,
) -> None:
    imported.record_changes(
        [
            SetChange(path=ConfigPath.parse("agents.1.name"), old=None, new="bob"),
            RemoveChange(path=ConfigPath.parse("name"), old="demo"),
        ],
        CLI_CONTEXT,
    )

    assert step1(imported.records())[len(IMPORTED) :] == (
        Step1Record(seq=4, op="set", path="agents.1.name", old=None, new="bob"),
        Step1Record(seq=5, op="remove", path="name", old="demo", new=None),
    )


def test_later_batches_continue_the_sequence_and_survive_reopening(
    theatre: Path, imported: HistoryStore
) -> None:
    imported.record_changes(BATCH[:1], CLI_CONTEXT)
    imported.record_changes(BATCH[1:], CLI_CONTEXT)
    imported.close()

    reopened = HistoryStore.open(theatre / CONFIG_DIR_NAME)
    try:
        assert step1(reopened.records()) == IMPORTED + BATCH_RECORDS
    finally:
        reopened.close()


def test_an_empty_batch_records_nothing(imported: HistoryStore) -> None:
    imported.record_changes([], CLI_CONTEXT)
    imported.record_changes((), CLI_CONTEXT)

    assert step1(imported.records()) == IMPORTED
    imported.record_changes(BATCH[:1], CLI_CONTEXT)
    assert step1(imported.records()) == (*IMPORTED, BATCH_RECORDS[0])


def test_a_failure_at_the_sqlite_boundary_records_no_event_of_the_batch(
    imported: HistoryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_execute = SQLiteCursor.execute
    inserts = 0

    def failing_execute(self: SQLiteCursor, *args: Any, **kwargs: Any) -> None:
        nonlocal inserts
        statement = str(args[0]) if args else str(kwargs.get("statement", ""))
        if statement.lstrip().upper().startswith("INSERT"):
            inserts += 1
            if inserts == 3:
                raise sqlite3.OperationalError("injected failure after two rows")
        original_execute(self, *args, **kwargs)

    monkeypatch.setattr(SQLiteCursor, "execute", failing_execute)

    with pytest.raises(ConfigLoadError) as caught:
        imported.record_changes(BATCH, CLI_CONTEXT)

    assert inserts == 3, "the failure was not injected after the first rows were written"
    assert caught.value.file == imported.location
    assert caught.value.exit_code == ExitCode.LOAD
    monkeypatch.setattr(SQLiteCursor, "execute", original_execute)
    assert step1(imported.records()) == IMPORTED

    imported.record_changes(BATCH, CLI_CONTEXT)
    assert step1(imported.records()) == IMPORTED + BATCH_RECORDS


def test_recording_before_the_import_is_a_load_error_and_records_nothing(
    store: HistoryStore,
) -> None:
    with pytest.raises(ConfigLoadError) as caught:
        store.record_changes(BATCH, CLI_CONTEXT)

    assert caught.value.file == store.location
    assert store.is_imported() is False
    assert step1(store.records()) == ()


def test_recording_on_a_closed_store_is_a_load_error(imported: HistoryStore) -> None:
    imported.close()

    with pytest.raises(ConfigLoadError) as caught:
        imported.record_changes(BATCH, CLI_CONTEXT)

    assert caught.value.file == imported.location
