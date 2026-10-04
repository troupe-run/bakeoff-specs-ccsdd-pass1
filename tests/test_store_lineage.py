"""Stored lineage: one stamp per recorded batch, persisted with every event, per caller context.

config-lineage 3.1-3.3, 4.1, 4.2, 4.4, 5.2, 7.1, 7.4.

The store is a real SQLite file in a real temporary theatre; the theatre, reconcile and set tests
go through ``open_theatre``, ``set_value`` and the real ``troupe`` console script.

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

import json
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from support import MakeTheatre, RunCli
from troupe.config.constants import CONFIG_DIR_NAME
from troupe.config.errors import ConfigLoadError, ExitCode
from troupe.config.history import HistoryRecord, LeafChange, RemoveChange, SetChange
from troupe.config.lineage import (
    FILE_CONTEXT,
    IMPORT_CONTEXT,
    UNKNOWN_ACTOR,
    Actor,
    ChangeContext,
    Lineage,
    cli_context,
    format_utc,
    stamp,
)
from troupe.config.loader import load_config
from troupe.config.locate import find_config_file
from troupe.config.paths import ConfigPath
from troupe.config.resolve import flatten_leaves
from troupe.config.store import HistoryStore
from troupe.config.theatre import open_theatre
from troupe.config.writeback.setter import set_value
from troupe.config.writeback.value import parse_value

CONFIG = "name: demo\nagents:\n  - name: alice\n    temperature: 0.5\n"

ALICE = Actor(kind="human", id="alice")

FIRST_BATCH: tuple[LeafChange, ...] = (
    SetChange(path=ConfigPath.parse("name"), old="demo", new="renamed"),
    SetChange(path=ConfigPath.parse("agents.0.enabled"), old=None, new=True),
)
SECOND_BATCH: tuple[LeafChange, ...] = (
    RemoveChange(path=ConfigPath.parse("agents.0.temperature"), old=0.5),
    SetChange(path=ConfigPath.parse("name"), old="renamed", new="again"),
)


def _now() -> str:
    return format_utc(datetime.now(UTC))


def _lineages(records: tuple[HistoryRecord, ...]) -> set[Lineage]:
    return {record.lineage for record in records}


def _single_lineage(records: tuple[HistoryRecord, ...]) -> Lineage:
    lineages = _lineages(records)
    assert len(lineages) == 1, f"expected one lineage for the batch, got {lineages}"
    return lineages.pop()


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


def _import(theatre: Path, store: HistoryStore) -> None:
    store.ensure_imported(
        flatten_leaves(load_config(find_config_file(theatre)).root), IMPORT_CONTEXT
    )


# --- the store stamps each batch once ------------------------------------------------------------


def test_an_import_stamps_every_imported_event_with_one_import_lineage(
    theatre: Path, store: HistoryStore
) -> None:
    """Req 3.1, 4.1, 5.2: all events of one import share one lineage: unknown actor, via import,
    no reason, one change id and one ``at`` taken while the import ran."""
    before = _now()
    _import(theatre, store)
    after = _now()

    records = store.records()
    assert len(records) == 3
    lineage = _single_lineage(records)
    assert lineage.actor == UNKNOWN_ACTOR
    assert lineage.via == "import"
    assert lineage.reason is None
    assert lineage.change != ""
    assert before <= lineage.at <= after


def test_each_batch_has_one_lineage_and_two_batches_have_distinct_change_ids(
    theatre: Path, store: HistoryStore
) -> None:
    """Req 4.1, 4.2, 5.2: every event of one recorded batch shares the batch's change id and
    ``at``; a later batch gets another change id; the context's actor, via and reason are kept."""
    _import(theatre, store)
    context = ChangeContext(actor=Actor(kind="agent", id="planner"), via="cli", reason="tidy up")

    store.record_changes(FIRST_BATCH, context)
    store.record_changes(SECOND_BATCH, FILE_CONTEXT)

    records = store.records()
    imported, first, second = records[:3], records[3:5], records[5:]
    first_lineage = _single_lineage(first)
    second_lineage = _single_lineage(second)
    assert (first_lineage.actor, first_lineage.via, first_lineage.reason) == (
        Actor(kind="agent", id="planner"),
        "cli",
        "tidy up",
    )
    assert (second_lineage.actor, second_lineage.via, second_lineage.reason) == (
        UNKNOWN_ACTOR,
        "file",
        None,
    )
    changes = {_single_lineage(imported).change, first_lineage.change, second_lineage.change}
    assert len(changes) == 3
    assert first_lineage.at <= second_lineage.at


def test_an_empty_batch_is_not_stamped_and_records_nothing(
    theatre: Path, store: HistoryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Req 4.4: an empty batch records no event and is never stamped, so it uses no change id."""
    _import(theatre, store)
    before = store.records()
    stamped: list[ChangeContext] = []

    def counting_stamp(context: ChangeContext) -> Lineage:
        stamped.append(context)
        return stamp(context)

    monkeypatch.setattr("troupe.config.store.stamp", counting_stamp)

    store.record_changes([], cli_context(ALICE, None))
    store.record_changes((), FILE_CONTEXT)

    assert stamped == []
    assert store.records() == before


def test_lineage_is_identical_after_closing_and_reopening_the_store(
    theatre: Path, store: HistoryStore
) -> None:
    """Req 7.1, 7.4: the lineage is persisted with the events in the one SQLite file, so a new
    store opened on the same directory reads exactly the same records."""
    _import(theatre, store)
    store.record_changes(FIRST_BATCH, cli_context(ALICE, "why not"))
    store.record_changes(SECOND_BATCH, FILE_CONTEXT)
    recorded = store.records()
    store.close()

    reopened = HistoryStore.open(theatre / CONFIG_DIR_NAME)
    try:
        assert reopened.records() == recorded
    finally:
        reopened.close()
    assert sorted(path.name for path in (theatre / CONFIG_DIR_NAME).iterdir()) == [
        "history.sqlite",
        "troupe.yml",
    ]


@pytest.mark.parametrize(("field", "value"), [("actor_kind", "robot"), ("via", "elsewhere")])
def test_an_unknown_stored_actor_kind_or_via_is_an_unreadable_store(
    theatre: Path, store: HistoryStore, field: str, value: str
) -> None:
    """Req 7.1 (design Error Handling): a stored event whose lineage names an unknown actor kind
    or via is reported as a load error naming the store (exit 4)."""
    _import(theatre, store)
    location = store.location
    store.close()
    connection = sqlite3.connect(location)
    try:
        rows = connection.execute("SELECT rowid, state FROM stored_events").fetchall()
        rewritten = 0
        for rowid, state in rows:
            decoded = json.loads(bytes(state))
            if field in decoded:
                decoded[field] = value
                connection.execute(
                    "UPDATE stored_events SET state = ? WHERE rowid = ?",
                    (json.dumps(decoded).encode("utf-8"), rowid),
                )
                rewritten += 1
        connection.commit()
    finally:
        connection.close()
    assert rewritten == 3

    reopened = HistoryStore.open(theatre / CONFIG_DIR_NAME)
    try:
        with pytest.raises(ConfigLoadError) as caught:
            reopened.records()
    finally:
        reopened.close()
    assert caught.value.file == location
    assert caught.value.exit_code == ExitCode.LOAD
    assert value in str(caught.value)


# --- callers pass their context -------------------------------------------------------------------


def test_opening_a_new_theatre_records_the_import_with_the_import_context(
    theatre: Path,
) -> None:
    """Req 3.1: the theatre opening imports with the import context."""
    with open_theatre(theatre) as opened:
        lineage = _single_lineage(opened.history.records())

    assert (lineage.actor, lineage.via, lineage.reason) == (UNKNOWN_ACTOR, "import", None)


def test_a_hand_edit_is_reconciled_with_the_file_context_as_its_own_change(
    theatre: Path,
) -> None:
    """Req 3.2, 4.1: reconcile records a hand edit with the file context, one change for all its
    events, distinct from the import's change."""
    with open_theatre(theatre):
        pass
    (theatre / CONFIG_DIR_NAME / "troupe.yml").write_text("name: edited\nextra: 1\n", "utf-8")

    with open_theatre(theatre) as opened:
        records = opened.history.records()

    imported, reconciled = records[:3], records[3:]
    assert [(record.op, record.path) for record in reconciled] == [
        ("set", "name"),
        ("set", "extra"),
        ("remove", "agents.0.name"),
        ("remove", "agents.0.temperature"),
    ]
    lineage = _single_lineage(reconciled)
    assert (lineage.actor, lineage.via, lineage.reason) == (UNKNOWN_ACTOR, "file", None)
    assert lineage.change != _single_lineage(imported).change


def test_set_value_records_its_batch_with_the_given_cli_context_after_the_reconcile(
    theatre: Path,
) -> None:
    """Req 3.2, 3.3, 4.1, 4.2: a hand edit then a set gives two changes: the reconcile's with the
    file context, then the set's (several events, one change) with the cli context's actor."""
    with open_theatre(theatre):
        pass
    (theatre / CONFIG_DIR_NAME / "troupe.yml").write_text(CONFIG + "extra: 1\n", "utf-8")

    path = ConfigPath.parse("agents.0")
    with open_theatre(theatre) as opened:
        set_value(
            opened,
            path,
            parse_value("{name: bob, port: 8080}", path),
            cli_context(ALICE, None),
        )
        records = opened.history.records()

    imported, reconciled, own = records[:3], records[3:4], records[4:]
    assert [(record.op, record.path) for record in reconciled] == [("set", "extra")]
    assert [(record.op, record.path) for record in own] == [
        ("set", "agents.0.name"),
        ("set", "agents.0.port"),
        ("remove", "agents.0.temperature"),
    ]
    reconcile_lineage = _single_lineage(reconciled)
    set_lineage = _single_lineage(own)
    assert reconcile_lineage.via == "file"
    assert (set_lineage.actor, set_lineage.via, set_lineage.reason) == (ALICE, "cli", None)
    assert (
        len({_single_lineage(imported).change, reconcile_lineage.change, set_lineage.change}) == 3
    )


def test_the_set_command_records_its_batch_with_the_actor_from_troupe_actor(
    theatre: Path, run_cli: RunCli, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Req 3.1, 3.3: ``troupe config set`` records the import with the import context and its own
    events with a cli context carrying the command's actor (``TROUPE_ACTOR``) and no reason."""
    monkeypatch.setenv("TROUPE_ACTOR", "agent:planner:v2")

    result = run_cli("config", "set", "name", "renamed", cwd=theatre)

    assert result.exit_code == 0, result.stderr
    reopened = HistoryStore.open(theatre / CONFIG_DIR_NAME)
    try:
        records = reopened.records()
    finally:
        reopened.close()
    imported, own = records[:3], records[3:]
    assert [(record.op, record.path, record.new) for record in own] == [("set", "name", "renamed")]
    assert _single_lineage(imported).via == "import"
    (set_lineage,) = _lineages(own)
    assert (set_lineage.actor, set_lineage.via, set_lineage.reason) == (
        Actor(kind="agent", id="planner:v2"),
        "cli",
        None,
    )
