"""History store: persisted under ``.troupe/``, import once, records, close (7.1-7.9, 8.1, 8.2)."""

import math
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from eventsourcing.application import Application
from eventsourcing.sqlite import SQLiteCursor

from support import MakeTheatre, Step1Record, step1
from troupe.config.constants import CONFIG_DIR_NAME, HISTORY_STORE_NAME
from troupe.config.errors import ConfigLoadError, ExitCode
from troupe.config.lineage import IMPORT_CONTEXT
from troupe.config.loader import load_config
from troupe.config.locate import find_config_file
from troupe.config.paths import ConfigPath
from troupe.config.resolve import Leaf, flatten_leaves
from troupe.config.store import HistoryStore

CONFIG = """\
name: demo
agents:
  - name: alice
    temperature: 0.5
  - name: bob
    enabled: false
notes: ~
"""

EXPECTED_IMPORT = (
    Step1Record(seq=1, op="import", path="name", old=None, new="demo"),
    Step1Record(seq=2, op="import", path="agents.0.name", old=None, new="alice"),
    Step1Record(seq=3, op="import", path="agents.0.temperature", old=None, new=0.5),
    Step1Record(seq=4, op="import", path="agents.1.name", old=None, new="bob"),
    Step1Record(seq=5, op="import", path="agents.1.enabled", old=None, new=False),
    Step1Record(seq=6, op="import", path="notes", old=None, new=None),
)


def _leaves(theatre: Path) -> tuple[Leaf, ...]:
    return flatten_leaves(load_config(find_config_file(theatre)).root)


def _config_dir(theatre: Path) -> Path:
    return theatre / CONFIG_DIR_NAME


@pytest.fixture
def theatre(make_theatre: MakeTheatre) -> Path:
    return make_theatre(CONFIG)


@pytest.fixture
def store(theatre: Path) -> Iterator[HistoryStore]:
    opened = HistoryStore.open(_config_dir(theatre))
    try:
        yield opened
    finally:
        opened.close()


def _snapshot(directory: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(directory)): path.read_bytes()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def test_location_is_history_sqlite_under_the_config_dir(
    theatre: Path, store: HistoryStore
) -> None:
    assert store.location == _config_dir(theatre) / HISTORY_STORE_NAME
    assert store.location.is_file()


def test_import_records_one_event_per_leaf_in_document_order(
    theatre: Path, store: HistoryStore
) -> None:
    assert store.is_imported() is False
    assert step1(store.records()) == ()

    assert store.ensure_imported(_leaves(theatre), IMPORT_CONTEXT) is True

    assert store.is_imported() is True
    assert step1(store.records()) == EXPECTED_IMPORT


def test_second_import_records_nothing_and_ensure_imported_never_reconciles(
    theatre: Path, store: HistoryStore
) -> None:
    """Req 7.3: once imported, ``ensure_imported`` records nothing, even for different leaves.

    ``ensure_imported`` is import-only; recording hand edits is the separate reconcile step that
    the theatre runs on open (config-file-sync 1.1, 2.1-2.3), covered in ``test_reconcile.py``.
    """
    store.ensure_imported(_leaves(theatre), IMPORT_CONTEXT)
    changed = (Leaf(path=ConfigPath.parse("name"), value="other", node=_leaves(theatre)[0].node),)

    assert store.ensure_imported(changed, IMPORT_CONTEXT) is False
    assert store.ensure_imported((), IMPORT_CONTEXT) is False
    assert step1(store.records()) == EXPECTED_IMPORT


def test_records_survive_reopening_and_reopened_store_does_not_reimport(theatre: Path) -> None:
    first = HistoryStore.open(_config_dir(theatre))
    try:
        first.ensure_imported(_leaves(theatre), IMPORT_CONTEXT)
    finally:
        first.close()

    second = HistoryStore.open(_config_dir(theatre))
    try:
        assert second.is_imported() is True
        assert step1(second.records()) == EXPECTED_IMPORT
        assert second.ensure_imported(_leaves(theatre), IMPORT_CONTEXT) is False
        assert step1(second.records()) == EXPECTED_IMPORT
    finally:
        second.close()


def test_empty_configuration_still_counts_as_imported(make_theatre: MakeTheatre) -> None:
    theatre = make_theatre("# nothing here\n")
    store = HistoryStore.open(_config_dir(theatre))
    try:
        assert _leaves(theatre) == ()
        assert store.ensure_imported((), IMPORT_CONTEXT) is True
        assert store.is_imported() is True
        assert step1(store.records()) == ()
    finally:
        store.close()

    reopened = HistoryStore.open(_config_dir(theatre))
    try:
        assert reopened.is_imported() is True
        assert reopened.ensure_imported((), IMPORT_CONTEXT) is False
    finally:
        reopened.close()


def test_persisted_values_round_trip_with_exact_types(make_theatre: MakeTheatre) -> None:
    theatre = make_theatre(
        "flag: true\n"
        "off: false\n"
        "count: 42\n"
        "big: 12345678901234567890\n"
        "ratio: 2.5\n"
        "up: .inf\n"
        "down: -.inf\n"
        "missing: .nan\n"
        "negzero: -0.0\n"
        "nothing: null\n"
        "text: 'héllo ✓ 日本'\n"
        "numtext: '42'\n"
    )
    store = HistoryStore.open(_config_dir(theatre))
    try:
        store.ensure_imported(_leaves(theatre), IMPORT_CONTEXT)
    finally:
        store.close()

    reopened = HistoryStore.open(_config_dir(theatre))
    try:
        values = {record.path: record.new for record in step1(reopened.records())}
    finally:
        reopened.close()

    expected_types: dict[str, type[Any]] = {
        "flag": bool,
        "off": bool,
        "count": int,
        "big": int,
        "ratio": float,
        "up": float,
        "down": float,
        "missing": float,
        "negzero": float,
        "nothing": type(None),
        "text": str,
        "numtext": str,
    }
    assert list(values) == list(expected_types)
    for path, expected_type in expected_types.items():
        assert type(values[path]) is expected_type, path
    assert values["flag"] is True
    assert values["off"] is False
    assert values["count"] == 42
    assert values["big"] == 12345678901234567890
    assert values["ratio"] == 2.5
    assert values["up"] == math.inf
    assert values["down"] == -math.inf
    missing = values["missing"]
    assert isinstance(missing, float) and math.isnan(missing)
    negzero = values["negzero"]
    assert isinstance(negzero, float) and negzero == 0.0 and math.copysign(1.0, negzero) == -1.0
    assert values["nothing"] is None
    assert values["text"] == "héllo ✓ 日本"
    assert values["numtext"] == "42"


def test_only_history_sqlite_files_appear_and_user_files_are_untouched(
    make_theatre: MakeTheatre,
) -> None:
    theatre = make_theatre(
        "name: demo\nprompt: file:prompt.txt\n",
        files={".troupe/prompt.txt": "Hello\n", "other/readme.md": "x"},
    )
    before = _snapshot(theatre)
    store = HistoryStore.open(_config_dir(theatre))
    try:
        store.ensure_imported(_leaves(theatre), IMPORT_CONTEXT)
        while_open = _snapshot(theatre)
    finally:
        store.close()
    after = _snapshot(theatre)

    for snapshot in (while_open, after):
        new = set(snapshot) - set(before)
        assert new, "the store file was not created"
        assert all(
            Path(name).parent == Path(CONFIG_DIR_NAME)
            and Path(name).name.startswith(HISTORY_STORE_NAME)
            for name in new
        ), new
        assert {name: snapshot[name] for name in before} == before
    assert set(after) - set(before) == {f"{CONFIG_DIR_NAME}/{HISTORY_STORE_NAME}"}
    assert sorted(p.name for p in theatre.iterdir()) == sorted([CONFIG_DIR_NAME, "other"])


def test_close_twice_leaves_no_wal_or_shm_files(theatre: Path) -> None:
    store = HistoryStore.open(_config_dir(theatre))
    store.ensure_imported(_leaves(theatre), IMPORT_CONTEXT)
    step1(store.records())

    store.close()
    store.close()

    names = sorted(p.name for p in _config_dir(theatre).iterdir())
    assert f"{HISTORY_STORE_NAME}-wal" not in names
    assert f"{HISTORY_STORE_NAME}-shm" not in names
    assert names == sorted(["troupe.yml", HISTORY_STORE_NAME])


def test_close_without_any_write_leaves_no_wal_or_shm_files(theatre: Path) -> None:
    store = HistoryStore.open(_config_dir(theatre))
    store.is_imported()
    store.close()

    assert sorted(p.name for p in _config_dir(theatre).iterdir()) == sorted(
        ["troupe.yml", HISTORY_STORE_NAME]
    )


def test_corrupt_store_file_yields_a_load_error_naming_it(theatre: Path) -> None:
    location = _config_dir(theatre) / HISTORY_STORE_NAME
    garbage = b"this is certainly not an SQLite database " * 10
    location.write_bytes(garbage)

    with pytest.raises(ConfigLoadError) as caught:
        store = HistoryStore.open(_config_dir(theatre))
        try:
            store.is_imported()
            step1(store.records())
        finally:
            store.close()

    assert caught.value.exit_code == ExitCode.LOAD
    assert caught.value.file == location
    assert str(caught.value).startswith(f"{location}: ")
    assert location.read_bytes() == garbage


def test_missing_config_dir_yields_a_load_error_and_creates_nothing(tmp_path: Path) -> None:
    missing = tmp_path / "theatre" / CONFIG_DIR_NAME

    with pytest.raises(ConfigLoadError) as caught:
        HistoryStore.open(missing)

    assert caught.value.file == missing / HISTORY_STORE_NAME
    assert list(tmp_path.iterdir()) == []


def test_failure_at_the_sqlite_boundary_during_import_leaves_no_partial_import(
    theatre: Path, store: HistoryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_execute = SQLiteCursor.execute
    inserts = 0

    def failing_execute(self: SQLiteCursor, *args: Any, **kwargs: Any) -> None:
        nonlocal inserts
        statement = str(args[0]) if args else str(kwargs.get("statement", ""))
        if statement.lstrip().upper().startswith("INSERT"):
            inserts += 1
            if inserts == 4:
                raise sqlite3.OperationalError("injected failure after three rows")
        original_execute(self, *args, **kwargs)

    monkeypatch.setattr(SQLiteCursor, "execute", failing_execute)

    with pytest.raises(ConfigLoadError) as caught:
        store.ensure_imported(_leaves(theatre), IMPORT_CONTEXT)

    assert inserts == 4, "the failure was not injected after the first rows were written"
    assert caught.value.file == store.location
    monkeypatch.setattr(SQLiteCursor, "execute", original_execute)
    assert store.is_imported() is False
    assert step1(store.records()) == ()

    assert store.ensure_imported(_leaves(theatre), IMPORT_CONTEXT) is True
    assert step1(store.records()) == EXPECTED_IMPORT


def test_close_failure_is_reported_as_a_load_error(
    theatre: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = HistoryStore.open(_config_dir(theatre))

    original_close = Application.close
    failures: list[bool] = []

    def failing_close(self: Application[Any]) -> None:
        original_close(self)
        if not failures:  # fail once; the application's own ``__del__`` calls close again
            failures.append(True)
            raise sqlite3.OperationalError("injected close failure")

    monkeypatch.setattr(Application, "close", failing_close)
    with pytest.raises(ConfigLoadError) as caught:
        store.close()
    monkeypatch.undo()

    assert caught.value.file == store.location
    store.close()  # already closed: idempotent, no error


def test_close_never_masks_an_error_already_being_raised(
    theatre: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = HistoryStore.open(_config_dir(theatre))

    original_close = Application.close
    failures: list[bool] = []

    def failing_close(self: Application[Any]) -> None:
        original_close(self)
        if not failures:  # fail once; the application's own ``__del__`` calls close again
            failures.append(True)
            raise sqlite3.OperationalError("injected close failure")

    monkeypatch.setattr(Application, "close", failing_close)
    with pytest.raises(KeyError, match="original"):
        try:
            raise KeyError("original")
        finally:
            store.close()


def test_ambient_environment_cannot_redirect_or_transform_the_store(
    theatre: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    elsewhere = tmp_path / "elsewhere.sqlite"
    for prefix in ("", "TROUPEHISTORY_", "APPLICATION_"):
        monkeypatch.setenv(f"{prefix}PERSISTENCE_MODULE", "eventsourcing.popo")
        monkeypatch.setenv(f"{prefix}SQLITE_DBNAME", str(elsewhere))
        monkeypatch.setenv(f"{prefix}COMPRESSOR_TOPIC", "eventsourcing.compressor:ZlibCompressor")
        monkeypatch.setenv(f"{prefix}CREATE_TABLE", "n")
    monkeypatch.setenv("IS_SNAPSHOTTING_ENABLED", "y")

    store = HistoryStore.open(_config_dir(theatre))
    try:
        store.ensure_imported(_leaves(theatre), IMPORT_CONTEXT)
    finally:
        store.close()

    assert not elsewhere.exists()
    connection = sqlite3.connect(store.location)
    try:
        states = [bytes(row[0]) for row in connection.execute("SELECT state FROM stored_events")]
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}
    finally:
        connection.close()
    assert len(states) == 1 + len(EXPECTED_IMPORT)
    assert all(state.startswith(b"{") for state in states)  # plain JSON: not compressed
    assert not any("snapshot" in table for table in tables)
    for name in (f"{HISTORY_STORE_NAME}-wal", f"{HISTORY_STORE_NAME}-shm"):
        assert not (_config_dir(theatre) / name).exists()
