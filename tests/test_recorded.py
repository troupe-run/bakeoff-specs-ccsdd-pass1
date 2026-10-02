"""Folding the history into the recorded configuration (2.1, 2.2, 2.3, 2.6), on a real store."""

import math
from collections.abc import Iterator
from pathlib import Path

import pytest

from support import MakeTheatre
from troupe.config.constants import CONFIG_DIR_NAME
from troupe.config.history import LeafChange, RemoveChange, SetChange
from troupe.config.loader import load_config
from troupe.config.locate import find_config_file
from troupe.config.paths import ConfigPath
from troupe.config.recorded import RecordedLeaf, recorded_leaves
from troupe.config.resolve import flatten_leaves
from troupe.config.scalars import ScalarValue
from troupe.config.store import HistoryStore

CONFIG = """\
name: demo
note: null
limits:
  rate: .inf
  ratio: .nan
  weight: 1.0
  enabled: true
"""


@pytest.fixture
def store(make_theatre: MakeTheatre) -> Iterator[HistoryStore]:
    theatre: Path = make_theatre(CONFIG)
    opened = HistoryStore.open(theatre / CONFIG_DIR_NAME)
    try:
        opened.ensure_imported(flatten_leaves(load_config(find_config_file(theatre)).root))
        yield opened
    finally:
        opened.close()


def _set(path: str, old: ScalarValue, new: ScalarValue) -> SetChange:
    return SetChange(path=ConfigPath.parse(path), old=old, new=new)


def _remove(path: str, old: ScalarValue) -> RemoveChange:
    return RemoveChange(path=ConfigPath.parse(path), old=old)


def _record(store: HistoryStore, *changes: LeafChange) -> tuple[RecordedLeaf, ...]:
    store.record_changes(changes)
    return recorded_leaves(store.records())


def _by_path(leaves: tuple[RecordedLeaf, ...]) -> dict[str, RecordedLeaf]:
    return {leaf.path: leaf for leaf in leaves}


def test_imported_leaves_are_live_with_their_import_seq_oldest_first(
    store: HistoryStore,
) -> None:
    """Req 2.6: each imported leaf is created by its import event, in seq order."""
    leaves = recorded_leaves(store.records())

    assert [(leaf.path, leaf.created_seq) for leaf in leaves] == [
        ("name", 1),
        ("note", 2),
        ("limits.rate", 3),
        ("limits.ratio", 4),
        ("limits.weight", 5),
        ("limits.enabled", 6),
    ]
    assert leaves[0] == RecordedLeaf(path="name", value="demo", created_seq=1)


def test_no_records_give_no_leaves() -> None:
    """Req 2.3: an empty history records no leaf."""
    assert recorded_leaves(()) == ()


def test_import_set_remove_set_again_follows_re_creation(store: HistoryStore) -> None:
    """Req 2.1, 2.2, 2.3, 2.6: a set updates in place; a set after a remove re-creates the leaf."""
    after_update = _record(store, _set("name", "demo", "renamed"))  # seq 7
    assert _by_path(after_update)["name"] == RecordedLeaf("name", "renamed", created_seq=1)
    assert after_update[0].path == "name"

    after_remove = _record(store, _remove("name", "renamed"))  # seq 8
    assert "name" not in _by_path(after_remove)
    assert len(after_remove) == 5

    after_recreate = _record(store, _set("name", None, "again"))  # seq 9
    assert after_recreate[-1] == RecordedLeaf("name", "again", created_seq=9)
    assert [leaf.path for leaf in after_recreate] == [
        "note",
        "limits.rate",
        "limits.ratio",
        "limits.weight",
        "limits.enabled",
        "name",
    ]

    after_second_update = _record(store, _set("name", "again", "final"))  # seq 10
    assert after_second_update[-1] == RecordedLeaf("name", "final", created_seq=9)


def test_a_set_on_an_absent_path_creates_a_leaf(store: HistoryStore) -> None:
    """Req 2.2: a set on a path the fold does not hold creates a leaf at that set's seq."""
    leaves = _record(store, _set("agents.0.name", None, "alice"))  # seq 7

    assert leaves[-1] == RecordedLeaf("agents.0.name", "alice", created_seq=7)
    assert len(leaves) == 7


def test_a_set_on_a_null_valued_leaf_is_not_a_creation(store: HistoryStore) -> None:
    """Req 2.1, 2.6: a set on a present, null-valued leaf keeps its creation seq."""
    leaves = _by_path(_record(store, _set("note", None, "hello")))  # seq 7

    assert leaves["note"] == RecordedLeaf("note", "hello", created_seq=2)


def test_a_set_to_null_keeps_the_leaf(store: HistoryStore) -> None:
    """Req 2.1, 2.3: a leaf set to null still exists in the recorded configuration."""
    leaves = _by_path(_record(store, _set("name", "demo", None)))

    assert leaves["name"] == RecordedLeaf("name", None, created_seq=1)


def test_a_remove_deletes_the_leaf_and_a_later_set_re_creates_it_last(
    store: HistoryStore,
) -> None:
    """Req 2.3, 2.6: removed leaves are gone; re-created leaves order by their new creation seq."""
    leaves = _record(
        store,
        _remove("limits.rate", math.inf),  # seq 7
        _remove("note", None),  # seq 8
        _set("note", None, 3),  # seq 9
    )

    assert [(leaf.path, leaf.created_seq) for leaf in leaves] == [
        ("name", 1),
        ("limits.ratio", 4),
        ("limits.weight", 5),
        ("limits.enabled", 6),
        ("note", 9),
    ]


def test_values_keep_their_types(store: HistoryStore) -> None:
    """Req 2.1, 2.3: inf, nan, 1.0 and True keep their types through the store and the fold."""
    leaves = _by_path(recorded_leaves(store.records()))

    rate = leaves["limits.rate"].value
    ratio = leaves["limits.ratio"].value
    weight = leaves["limits.weight"].value
    enabled = leaves["limits.enabled"].value
    assert type(rate) is float and rate == math.inf
    assert type(ratio) is float and math.isnan(ratio)
    assert type(weight) is float and weight == 1.0
    assert enabled is True

    updated = _by_path(_record(store, _set("limits.weight", 1.0, 1), _set("name", "demo", True)))
    assert type(updated["limits.weight"].value) is int
    assert updated["name"].value is True
