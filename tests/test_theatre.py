"""Theatre use cases: open, get, node_at, history, close (6.7, 6.9, 7.4, 7.9, 8.3-8.5)."""

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from eventsourcing.application import Application
from eventsourcing.sqlite import SQLiteCursor

from support import MakeTheatre
from troupe.config.constants import CONFIG_DIR_NAME, HISTORY_STORE_NAME
from troupe.config.errors import (
    ConfigLoadError,
    ExitCode,
    PathNotFoundError,
    WrongNodeKindError,
)
from troupe.config.history import HistoryRecord
from troupe.config.model import ConfigMapping, ConfigScalar, ConfigSequence
from troupe.config.paths import ConfigPath
from troupe.config.theatre import Theatre, get_value, list_history, node_at, open_theatre

CONFIG = """\
name: demo
agents: file:agents.yml
tools:
  - grep
  - sed
notes: ~
"""

AGENTS = """\
alice:
  temperature: 0.5
  enabled: true
alicex: 1
"""

EXPECTED_IMPORT = (
    HistoryRecord(seq=1, op="import", path="name", old=None, new="demo"),
    HistoryRecord(seq=2, op="import", path="agents.alice.temperature", old=None, new=0.5),
    HistoryRecord(seq=3, op="import", path="agents.alice.enabled", old=None, new=True),
    HistoryRecord(seq=4, op="import", path="agents.alicex", old=None, new=1),
    HistoryRecord(seq=5, op="import", path="tools.0", old=None, new="grep"),
    HistoryRecord(seq=6, op="import", path="tools.1", old=None, new="sed"),
    HistoryRecord(seq=7, op="import", path="notes", old=None, new=None),
)


@pytest.fixture
def theatre_dir(make_theatre: MakeTheatre) -> Path:
    return make_theatre(CONFIG, files={".troupe/agents.yml": AGENTS})


def _store_files(theatre_dir: Path) -> list[str]:
    return sorted(
        p.name
        for p in (theatre_dir / CONFIG_DIR_NAME).iterdir()
        if p.name.startswith(HISTORY_STORE_NAME)
    )


def _failing_application_close(monkeypatch: pytest.MonkeyPatch) -> None:
    original_close = Application.close
    failures: list[bool] = []

    def failing_close(self: Application[Any]) -> None:
        original_close(self)
        if not failures:  # fail once; the application's own ``__del__`` calls close again
            failures.append(True)
            raise sqlite3.OperationalError("injected close failure")

    monkeypatch.setattr(Application, "close", failing_close)


# --- open_theatre ---------------------------------------------------------------------------


def test_open_theatre_imports_the_files_and_exposes_the_session(theatre_dir: Path) -> None:
    with open_theatre(theatre_dir) as theatre:
        assert isinstance(theatre, Theatre)
        assert theatre.directory == theatre_dir
        assert theatre.config_file == theatre_dir / CONFIG_DIR_NAME / "troupe.yml"
        assert theatre.document.root_file.path == theatre.config_file
        assert theatre.history.location == theatre_dir / CONFIG_DIR_NAME / HISTORY_STORE_NAME
        assert theatre.history.records() == EXPECTED_IMPORT


def test_open_theatre_twice_imports_only_once(theatre_dir: Path) -> None:
    with open_theatre(theatre_dir):
        pass
    with open_theatre(theatre_dir) as theatre:
        assert theatre.history.records() == EXPECTED_IMPORT


def test_open_theatre_finds_a_troupe_yaml_config(make_theatre: MakeTheatre) -> None:
    theatre_dir = make_theatre("a: 1\n", config_name="troupe.yaml")

    with open_theatre(theatre_dir) as theatre:
        assert theatre.config_file == theatre_dir / CONFIG_DIR_NAME / "troupe.yaml"
        assert get_value(theatre, ConfigPath.parse("a")) == 1


def test_failed_load_creates_no_history_state(make_theatre: MakeTheatre) -> None:
    theatre_dir = make_theatre("a: [1, 2\n")

    with pytest.raises(ConfigLoadError) as caught:
        open_theatre(theatre_dir)

    assert caught.value.exit_code == ExitCode.LOAD
    assert caught.value.file == theatre_dir / CONFIG_DIR_NAME / "troupe.yml"
    assert _store_files(theatre_dir) == []


def test_failed_include_creates_no_history_state(make_theatre: MakeTheatre) -> None:
    theatre_dir = make_theatre("agents: file:missing.yml\n")

    with pytest.raises(ConfigLoadError):
        open_theatre(theatre_dir)

    assert _store_files(theatre_dir) == []


def test_missing_config_file_creates_nothing(tmp_path: Path) -> None:
    theatre_dir = tmp_path / "theatre"
    theatre_dir.mkdir()

    with pytest.raises(ConfigLoadError) as caught:
        open_theatre(theatre_dir)

    assert caught.value.file == theatre_dir / CONFIG_DIR_NAME / "troupe.yml"
    assert list(theatre_dir.iterdir()) == []


def test_failed_import_closes_the_store_before_reraising(
    theatre_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_execute = SQLiteCursor.execute

    def failing_execute(self: SQLiteCursor, *args: Any, **kwargs: Any) -> None:
        statement = str(args[0]) if args else str(kwargs.get("statement", ""))
        if statement.lstrip().upper().startswith("INSERT"):
            raise sqlite3.OperationalError("injected import failure")
        original_execute(self, *args, **kwargs)

    monkeypatch.setattr(SQLiteCursor, "execute", failing_execute)

    with pytest.raises(ConfigLoadError) as caught:
        open_theatre(theatre_dir)

    assert caught.value.file == theatre_dir / CONFIG_DIR_NAME / HISTORY_STORE_NAME
    assert "injected import failure" in str(caught.value)
    # The store was closed: SQLite removed its write-ahead-log side files.
    assert _store_files(theatre_dir) == [HISTORY_STORE_NAME]

    monkeypatch.undo()
    with open_theatre(theatre_dir) as theatre:
        assert theatre.history.records() == EXPECTED_IMPORT


# --- lifecycle --------------------------------------------------------------------------------


def test_leaving_the_with_block_closes_the_store(theatre_dir: Path) -> None:
    with open_theatre(theatre_dir) as theatre:
        theatre.history.records()

    assert _store_files(theatre_dir) == [HISTORY_STORE_NAME]
    with pytest.raises(ConfigLoadError, match="closed"):
        theatre.history.records()


def test_leaving_the_with_block_by_an_exception_closes_the_store_and_propagates(
    theatre_dir: Path,
) -> None:
    with pytest.raises(PathNotFoundError), open_theatre(theatre_dir) as theatre:
        get_value(theatre, ConfigPath.parse("missing"))

    assert _store_files(theatre_dir) == [HISTORY_STORE_NAME]
    with pytest.raises(ConfigLoadError, match="closed"):
        theatre.history.records()


def test_close_is_idempotent(theatre_dir: Path) -> None:
    theatre = open_theatre(theatre_dir)
    theatre.close()
    theatre.close()

    assert _store_files(theatre_dir) == [HISTORY_STORE_NAME]


def test_close_failure_after_a_successful_body_is_a_load_error(
    theatre_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    theatre = open_theatre(theatre_dir)
    _failing_application_close(monkeypatch)

    with pytest.raises(ConfigLoadError) as caught, theatre:
        pass

    assert caught.value.file == theatre_dir / CONFIG_DIR_NAME / HISTORY_STORE_NAME


def test_close_failure_never_replaces_the_error_of_the_body(
    theatre_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    theatre = open_theatre(theatre_dir)
    _failing_application_close(monkeypatch)

    with pytest.raises(PathNotFoundError) as caught, theatre:
        get_value(theatre, ConfigPath.parse("missing"))

    assert caught.value.path == "missing"


def test_close_failure_never_replaces_a_non_troupe_error_of_the_body(
    theatre_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    theatre = open_theatre(theatre_dir)
    _failing_application_close(monkeypatch)

    with pytest.raises(KeyError, match="original"), theatre:
        raise KeyError("original")


def test_exit_with_exception_info_never_raises_a_close_error_even_with_no_active_exception(
    theatre_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # ``__exit__`` may be called (e.g. by an exit stack) when no exception is active; the
    # exception info it receives, not ``sys.exception()``, decides that the body failed.
    theatre = open_theatre(theatre_dir)
    _failing_application_close(monkeypatch)
    body_error = KeyError("original")

    theatre.__exit__(KeyError, body_error, None)  # returns normally: no close error

    assert _store_files(theatre_dir) == [HISTORY_STORE_NAME]


# --- get_value ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("name", "demo"),
        ("agents.alice.temperature", 0.5),
        ("agents.alice.enabled", True),
        ("tools.1", "sed"),
        ("notes", None),
    ],
)
def test_get_value_returns_the_scalar_at_path_unchanged(
    theatre_dir: Path, text: str, expected: object
) -> None:
    """Req 1.1, 6.4: ``get_value`` returns the scalar at PATH as read from the files, keeping its
    type."""
    with open_theatre(theatre_dir) as theatre:
        value = get_value(theatre, ConfigPath.parse(text))

    assert value == expected
    assert type(value) is type(expected)


@pytest.mark.parametrize(
    ("text", "file", "kind"),
    [
        ("agents", "agents.yml", "a mapping"),
        ("agents.alice", "agents.yml", "a mapping"),
        ("tools", "troupe.yml", "a list"),
    ],
)
def test_get_value_refuses_mappings_and_lists_with_a_config_show_hint(
    theatre_dir: Path, text: str, file: str, kind: str
) -> None:
    """Req 1.2: a mapping or list is refused with exit 5, naming the node's file and PATH, and the
    problem points at ``troupe config show <PATH>``."""
    with open_theatre(theatre_dir) as theatre, pytest.raises(WrongNodeKindError) as caught:
        get_value(theatre, ConfigPath.parse(text))

    assert caught.value.exit_code == ExitCode.PATH
    assert caught.value.path == text
    assert caught.value.file == theatre_dir / CONFIG_DIR_NAME / file
    assert str(caught.value) == (
        f"{theatre_dir / CONFIG_DIR_NAME / file}: {text}: "
        f"is {kind}, not a value; print it with `troupe config show {text}`"
    )


def test_get_value_reports_a_missing_path_naming_the_deepest_existing_file(
    theatre_dir: Path,
) -> None:
    """Req 1.3: a missing PATH is a not-found error (exit 5) naming the deepest existing file."""
    with open_theatre(theatre_dir) as theatre, pytest.raises(PathNotFoundError) as caught:
        get_value(theatre, ConfigPath.parse("agents.bob"))

    assert caught.value.exit_code == ExitCode.PATH
    assert caught.value.path == "agents.bob"
    assert caught.value.file == theatre_dir / CONFIG_DIR_NAME / "agents.yml"


# --- node_at --------------------------------------------------------------------------------


def test_node_at_without_a_path_is_the_root_mapping(theatre_dir: Path) -> None:
    """Req 2.3, 6.4: with PATH omitted, ``node_at`` returns the loaded document's root mapping."""
    with open_theatre(theatre_dir) as theatre:
        node = node_at(theatre, None)

        assert node is theatre.document.root
    assert isinstance(node, ConfigMapping)
    assert [entry.key for entry in node.entries] == ["name", "agents", "tools", "notes"]


@pytest.mark.parametrize(
    ("text", "node_type", "file"),
    [
        ("agents", ConfigMapping, "agents.yml"),
        ("agents.alice", ConfigMapping, "agents.yml"),
        ("tools", ConfigSequence, "troupe.yml"),
        ("agents.alice.temperature", ConfigScalar, "agents.yml"),
        ("name", ConfigScalar, "troupe.yml"),
    ],
)
def test_node_at_returns_the_mapping_list_or_scalar_at_path(
    theatre_dir: Path, text: str, node_type: type, file: str
) -> None:
    """Req 2.3, 6.4: ``node_at`` returns the node at PATH from the files as read, whatever its
    kind, with includes resolved in place."""
    with open_theatre(theatre_dir) as theatre:
        node = node_at(theatre, ConfigPath.parse(text))

    assert isinstance(node, node_type)
    assert node.origin.file.path == theatre_dir / CONFIG_DIR_NAME / file


def test_node_at_reports_a_missing_path_naming_the_deepest_existing_file(
    theatre_dir: Path,
) -> None:
    """Req 2.4: a missing PATH is a not-found error (exit 5) naming the deepest existing file."""
    with open_theatre(theatre_dir) as theatre, pytest.raises(PathNotFoundError) as caught:
        node_at(theatre, ConfigPath.parse("agents.alice.missing"))

    assert caught.value.exit_code == ExitCode.PATH
    assert caught.value.path == "agents.alice.missing"
    assert caught.value.file == theatre_dir / CONFIG_DIR_NAME / "agents.yml"


def test_node_at_reads_a_hand_edit_from_the_files(make_theatre: MakeTheatre) -> None:
    """Req 6.4: after a hand edit, ``node_at`` returns the node as now written in the file."""
    theatre_dir = make_theatre("a:\n  x: 1\n")
    with open_theatre(theatre_dir):
        pass
    (theatre_dir / CONFIG_DIR_NAME / "troupe.yml").write_text("a:\n  y: 2\n", encoding="utf-8")

    with open_theatre(theatre_dir) as theatre:
        node = node_at(theatre, ConfigPath.parse("a"))

    assert isinstance(node, ConfigMapping)
    assert [entry.key for entry in node.entries] == ["y"]


def test_hand_edit_is_returned_by_get_value_and_recorded_by_the_reconcile_on_open(
    make_theatre: MakeTheatre,
) -> None:
    """Req 6.4; config-file-sync 1.1, 1.6: the next open reconciles a hand edit (a changed and a
    created leaf) while ``get_value`` still reads the files as loaded."""
    theatre_dir = make_theatre("a: 1\n")
    with open_theatre(theatre_dir) as theatre:
        assert get_value(theatre, ConfigPath.parse("a")) == 1
        before = list_history(theatre, None)

    (theatre_dir / CONFIG_DIR_NAME / "troupe.yml").write_text("a: 2\nb: x\n", encoding="utf-8")

    with open_theatre(theatre_dir) as theatre:
        assert get_value(theatre, ConfigPath.parse("a")) == 2
        assert get_value(theatre, ConfigPath.parse("b")) == "x"
        after = list_history(theatre, None)

    assert before == (HistoryRecord(seq=1, op="import", path="a", old=None, new=1),)
    assert after == (
        *before,
        HistoryRecord(seq=2, op="set", path="a", old=1, new=2),
        HistoryRecord(seq=3, op="set", path="b", old=None, new="x"),
    )


# --- list_history -----------------------------------------------------------------------------


def test_list_history_without_prefix_returns_every_record(theatre_dir: Path) -> None:
    with open_theatre(theatre_dir) as theatre:
        assert list_history(theatre, None) == EXPECTED_IMPORT


def test_list_history_on_a_fresh_theatre_imports_first(make_theatre: MakeTheatre) -> None:
    theatre_dir = make_theatre("x: true\n")

    with open_theatre(theatre_dir) as theatre:
        records = list_history(theatre, None)

    assert records == (HistoryRecord(seq=1, op="import", path="x", old=None, new=True),)


@pytest.mark.parametrize(
    ("prefix", "seqs"),
    [
        ("agents", (2, 3, 4)),
        ("agents.alice", (2, 3)),
        ("agents.alice.enabled", (3,)),
        ("tools.1", (6,)),
        ("tools", (5, 6)),
        ("agent", ()),
        ("nothing.here", ()),
    ],
)
def test_list_history_filters_by_textual_prefix_keeping_seq(
    theatre_dir: Path, prefix: str, seqs: tuple[int, ...]
) -> None:
    with open_theatre(theatre_dir) as theatre:
        records = list_history(theatre, ConfigPath.parse(prefix))

    assert tuple(record.seq for record in records) == seqs
    assert records == tuple(r for r in EXPECTED_IMPORT if r.seq in seqs)
