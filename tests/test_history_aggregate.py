"""History aggregate, events and the event-to-record mapping (7.1, 7.3, 7.5, 9.1, 9.2)."""

import dataclasses
import math
from uuid import UUID

import pytest
from eventsourcing.application import Application
from eventsourcing.domain import Aggregate, event

from support import FIXED_LINEAGE, lineage_fields
from troupe.config.history import (
    HISTORY_AGGREGATE_ID,
    ConfigHistory,
    HistoryRecord,
    LeafChange,
    RemoveChange,
    SetChange,
    to_history_record,
)
from troupe.config.paths import ConfigPath

LINEAGE = lineage_fields()


def _records(history: ConfigHistory) -> list[HistoryRecord | None]:
    return [to_history_record(event) for event in history.collect_events()]


def test_new_history_has_the_fixed_identity_and_starts_at_version_one() -> None:
    first = ConfigHistory()
    second = ConfigHistory()

    assert first.id == HISTORY_AGGREGATE_ID
    assert second.id == HISTORY_AGGREGATE_ID
    assert first.version == 1
    events = first.collect_events()
    assert [type(event).__name__ for event in events] == ["HistoryStarted"]
    assert events[0].originator_version == 1


def test_history_started_maps_to_no_record() -> None:
    (started,) = ConfigHistory().collect_events()

    assert isinstance(started, ConfigHistory.HistoryStarted)
    assert to_history_record(started) is None


def test_import_set_remove_map_to_records_with_seq_one_two_three() -> None:
    history = ConfigHistory()
    history.import_leaf("name", "demo", **LINEAGE)
    history.set_leaf("name", "demo", "show", **LINEAGE)
    history.remove_leaf("name", "show", **LINEAGE)

    events = history.collect_events()

    assert [type(event).__name__ for event in events] == [
        "HistoryStarted",
        "LeafImported",
        "LeafValueSet",
        "LeafRemoved",
    ]
    assert [event.originator_version for event in events] == [1, 2, 3, 4]
    assert [to_history_record(event) for event in events] == [
        None,
        HistoryRecord(seq=1, op="import", path="name", old=None, new="demo", lineage=FIXED_LINEAGE),
        HistoryRecord(seq=2, op="set", path="name", old="demo", new="show", lineage=FIXED_LINEAGE),
        HistoryRecord(seq=3, op="remove", path="name", old="show", new=None, lineage=FIXED_LINEAGE),
    ]


def test_seq_is_gapless_across_many_imports_in_order() -> None:
    history = ConfigHistory()
    for index in range(5):
        history.import_leaf(f"list.{index}", index, **LINEAGE)

    records = [record for record in _records(history) if record is not None]

    assert [record.seq for record in records] == [1, 2, 3, 4, 5]
    assert [record.path for record in records] == [f"list.{i}" for i in range(5)]
    assert all(record.op == "import" and record.old is None for record in records)


def test_set_of_a_created_leaf_records_old_null() -> None:
    history = ConfigHistory()
    history.set_leaf("agents.0.port", None, 8080, **LINEAGE)

    assert _records(history)[1] == HistoryRecord(
        seq=1, op="set", path="agents.0.port", old=None, new=8080, lineage=FIXED_LINEAGE
    )


def test_record_values_keep_their_scalar_types() -> None:
    history = ConfigHistory()
    history.import_leaf("flag", True, **LINEAGE)
    history.import_leaf("ratio", math.inf, **LINEAGE)
    history.set_leaf("count", 1, False, **LINEAGE)

    imported_flag, imported_ratio, set_count = (
        record for record in _records(history) if record is not None
    )

    assert imported_flag.new is True
    assert isinstance(imported_ratio.new, float)
    assert imported_ratio.new == math.inf
    assert set_count.old == 1
    assert type(set_count.old) is int
    assert set_count.new is False


def test_events_round_trip_through_an_application_and_rebuild_the_aggregate() -> None:
    application: Application[UUID] = Application(env={"PERSISTENCE_MODULE": "eventsourcing.popo"})
    history = ConfigHistory()
    history.import_leaf("a", 1, **LINEAGE)
    history.set_leaf("a", 1, "x", **LINEAGE)
    history.remove_leaf("a", "x", **LINEAGE)
    application.save(history)

    stored = list(application.repository.event_store.get(HISTORY_AGGREGATE_ID))
    rebuilt: object = application.repository.get(HISTORY_AGGREGATE_ID)

    assert isinstance(rebuilt, ConfigHistory)
    assert rebuilt.version == 4
    assert [to_history_record(event) for event in stored] == [
        None,
        HistoryRecord(seq=1, op="import", path="a", old=None, new=1, lineage=FIXED_LINEAGE),
        HistoryRecord(seq=2, op="set", path="a", old=1, new="x", lineage=FIXED_LINEAGE),
        HistoryRecord(seq=3, op="remove", path="a", old="x", new=None, lineage=FIXED_LINEAGE),
    ]
    application.close()


def test_change_records_are_immutable_and_carry_path_old_new() -> None:
    path = ConfigPath.parse("agents.0.name")
    changes: list[LeafChange] = [
        SetChange(path=path, old=None, new="ada"),
        RemoveChange(path=path, old="ada"),
    ]

    set_change, remove_change = changes
    assert isinstance(set_change, SetChange)
    assert (set_change.path, set_change.old, set_change.new) == (path, None, "ada")
    assert isinstance(remove_change, RemoveChange)
    assert (remove_change.path, remove_change.old) == (path, "ada")
    with pytest.raises(dataclasses.FrozenInstanceError):
        set_change.new = "bob"  # type: ignore[misc]


def test_history_record_is_immutable() -> None:
    record = HistoryRecord(seq=1, op="import", path="a", old=None, new=1, lineage=FIXED_LINEAGE)

    with pytest.raises(dataclasses.FrozenInstanceError):
        record.seq = 2  # type: ignore[misc]


def test_an_event_of_another_aggregate_is_refused() -> None:
    class Other(Aggregate):
        @event("Happened")
        def happen(self) -> None:
            pass

    other = Other()
    other.happen()
    _created, happened = other.collect_events()

    with pytest.raises(TypeError, match="Happened"):
        to_history_record(happened)
