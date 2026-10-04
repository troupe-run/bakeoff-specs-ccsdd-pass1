"""Opening a theatre under the lock and reconciling first (config-file-sync 1.1-1.7, 6.1-6.4, 7.3).

Every test opens real theatres on disk through ``open_theatre`` (or the real CLI) and observes the
history through a reconcile-free open, as ``history`` does.

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

import fcntl
import os
import sqlite3
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from typing import Any

import pytest
from eventsourcing.application import Application
from eventsourcing.sqlite import SQLiteCursor

from support import MakeTheatre, RunCli, Step1Record, step1
from troupe.config import reconcile as reconciler
from troupe.config.constants import CONFIG_DIR_NAME, HISTORY_STORE_NAME
from troupe.config.errors import ConfigLoadError, PathNotFoundError
from troupe.config.history import LeafChange
from troupe.config.model import ConfigDocument
from troupe.config.paths import ConfigPath
from troupe.config.store import HistoryStore
from troupe.config.theatre import get_value, open_theatre

CHILD_TIMEOUT_SECONDS = 60.0
HOLD_SECONDS = 0.5

IMPORTED_A = Step1Record(seq=1, op="import", path="a", old=None, new=1)

# The child announces that it is about to open, opens the theatre (blocking on the lock while the
# parent holds it) and reports the wall-clock time it got it and the number of history records.
CHILD_SCRIPT = textwrap.dedent(
    """
    import sys, time
    from pathlib import Path
    from troupe.config.theatre import open_theatre

    print("opening", flush=True)
    with open_theatre(Path(sys.argv[1])) as theatre:
        print(f"opened {time.time()!r} {len(theatre.history.records())}", flush=True)
    """
)


def _config(theatre_dir: Path) -> Path:
    return theatre_dir / CONFIG_DIR_NAME / "troupe.yml"


def _recorded(theatre_dir: Path) -> tuple[Step1Record, ...]:
    """The history as ``history`` sees it: opened without reconciling."""
    with open_theatre(theatre_dir, reconcile=False) as theatre:
        return step1(theatre.history.records())


def _assert_lock_free(theatre_dir: Path) -> None:
    """The ``.troupe/`` lock can be taken at once, by another descriptor and by ``open_theatre``."""
    fd = os.open(theatre_dir / CONFIG_DIR_NAME, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # BlockingIOError when still held
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _imported(make_theatre: MakeTheatre, config: str = "a: 1\n") -> Path:
    theatre_dir = make_theatre(config)
    with open_theatre(theatre_dir):
        pass
    return theatre_dir


def _spy_on_reconcile(monkeypatch: pytest.MonkeyPatch) -> list[ConfigDocument]:
    calls: list[ConfigDocument] = []
    original = reconciler.reconcile

    def spy(document: ConfigDocument, store: HistoryStore) -> tuple[LeafChange, ...]:
        calls.append(document)
        return original(document, store)

    monkeypatch.setattr(reconciler, "reconcile", spy)
    return calls


def _fail_inserts(monkeypatch: pytest.MonkeyPatch) -> None:
    original_execute = SQLiteCursor.execute

    def failing_execute(self: SQLiteCursor, *args: Any, **kwargs: Any) -> None:
        statement = str(args[0]) if args else str(kwargs.get("statement", ""))
        if statement.lstrip().upper().startswith("INSERT"):
            raise sqlite3.OperationalError("injected insert failure")
        original_execute(self, *args, **kwargs)

    monkeypatch.setattr(SQLiteCursor, "execute", failing_execute)


# --- reconcile only when already imported and not opted out ------------------------------------


def test_first_open_imports_and_does_not_reconcile(
    make_theatre: MakeTheatre, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Req 1.4: opening a never-imported theatre imports it and runs no reconcile."""
    theatre_dir = make_theatre("a: 1\n")
    calls = _spy_on_reconcile(monkeypatch)

    with open_theatre(theatre_dir) as theatre:
        assert step1(theatre.history.records()) == (IMPORTED_A,)

    assert calls == []


def test_open_of_an_imported_theatre_reconciles_a_hand_edit_before_returning(
    make_theatre: MakeTheatre, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Req 1.1, 1.6: an imported theatre is reconciled on open (the events are committed before
    the caller runs), while ``document`` and ``get_value`` stay the files as read from disk and
    no file is written."""
    theatre_dir = _imported(make_theatre)
    _config(theatre_dir).write_bytes(b"a: 2   # edited\n")
    calls = _spy_on_reconcile(monkeypatch)

    with open_theatre(theatre_dir) as theatre:
        assert step1(theatre.history.records()) == (
            IMPORTED_A,
            Step1Record(seq=2, op="set", path="a", old=1, new=2),
        )
        assert calls == [theatre.document]
        assert get_value(theatre, ConfigPath.parse("a")) == 2

    assert _config(theatre_dir).read_bytes() == b"a: 2   # edited\n"


def test_opt_out_records_nothing_and_a_later_default_open_reconciles(
    make_theatre: MakeTheatre, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Req 1.3: ``reconcile=False`` records nothing for a hand edit (the document still shows
    it); the next default open records it once, and a further open records nothing more."""
    theatre_dir = _imported(make_theatre)
    _config(theatre_dir).write_bytes(b"a: 2\n")
    calls = _spy_on_reconcile(monkeypatch)

    with open_theatre(theatre_dir, reconcile=False) as theatre:
        assert step1(theatre.history.records()) == (IMPORTED_A,)
        assert get_value(theatre, ConfigPath.parse("a")) == 2
    assert calls == []

    with open_theatre(theatre_dir):
        pass
    with open_theatre(theatre_dir):
        pass

    assert _recorded(theatre_dir) == (
        IMPORTED_A,
        Step1Record(seq=2, op="set", path="a", old=1, new=2),
    )


def test_opt_out_still_imports_a_never_imported_theatre(make_theatre: MakeTheatre) -> None:
    """Req 1.3, 1.4: ``reconcile=False`` (``history``) still imports a never-imported theatre."""
    theatre_dir = make_theatre("a: 1\n")

    assert _recorded(theatre_dir) == (IMPORTED_A,)


# --- failures release what was acquired ------------------------------------------------------


def test_load_failure_records_nothing_and_leaves_the_lock_free(make_theatre: MakeTheatre) -> None:
    """Req 1.5, 7.3: a configuration that no longer loads records nothing, and the lock is free
    at once afterwards (a second open succeeds immediately)."""
    theatre_dir = _imported(make_theatre)
    _config(theatre_dir).write_bytes(b"a: [1, 2\n")

    with pytest.raises(ConfigLoadError) as caught:
        open_theatre(theatre_dir)

    assert caught.value.file == _config(theatre_dir)
    _assert_lock_free(theatre_dir)
    _config(theatre_dir).write_bytes(b"a: 1\n")
    assert _recorded(theatre_dir) == (IMPORTED_A,)


def test_store_failure_on_open_releases_the_lock(make_theatre: MakeTheatre) -> None:
    """Req 7.3, 6.4: a history store that cannot be opened (``history.sqlite`` is a directory)
    is a load error naming the store, and the lock is released."""
    theatre_dir = make_theatre("a: 1\n")
    store = theatre_dir / CONFIG_DIR_NAME / HISTORY_STORE_NAME
    store.mkdir()

    with pytest.raises(ConfigLoadError) as caught:
        open_theatre(theatre_dir)

    assert caught.value.file == store
    _assert_lock_free(theatre_dir)
    store.rmdir()
    assert _recorded(theatre_dir) == (IMPORTED_A,)


def test_store_failure_while_reconciling_records_nothing_and_releases_everything(
    make_theatre: MakeTheatre, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Req 2.7, 7.3: a store failure while recording the reconcile raises the store's load
    error, records nothing, closes the store and releases the lock; the edit is reconciled by the
    next open."""
    theatre_dir = _imported(make_theatre)
    _config(theatre_dir).write_bytes(b"a: 2\n")
    _fail_inserts(monkeypatch)

    with pytest.raises(ConfigLoadError) as caught:
        open_theatre(theatre_dir)

    assert caught.value.file == theatre_dir / CONFIG_DIR_NAME / HISTORY_STORE_NAME
    assert "injected insert failure" in str(caught.value)
    _assert_lock_free(theatre_dir)
    monkeypatch.undo()
    assert _recorded(theatre_dir) == (IMPORTED_A,)
    with open_theatre(theatre_dir):
        pass
    assert _recorded(theatre_dir)[-1] == Step1Record(seq=2, op="set", path="a", old=1, new=2)


def test_store_close_failure_still_releases_the_lock(
    make_theatre: MakeTheatre, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Req 7.3: closing the theatre releases the lock even when closing the store fails."""
    theatre_dir = make_theatre("a: 1\n")
    theatre = open_theatre(theatre_dir)
    original_close = Application.close
    failures: list[bool] = []

    def failing_close(self: Application[Any]) -> None:
        original_close(self)
        if not failures:
            failures.append(True)
            raise sqlite3.OperationalError("injected close failure")

    monkeypatch.setattr(Application, "close", failing_close)

    with pytest.raises(ConfigLoadError), theatre:
        pass

    _assert_lock_free(theatre_dir)
    with open_theatre(theatre_dir):
        pass


# --- reconcile events survive the command's own failure ------------------------------------


def test_reconcile_events_remain_when_the_block_fails(make_theatre: MakeTheatre) -> None:
    """Req 1.2: the reconcile events are committed on open, so they remain when ``get_value``
    then fails inside the block."""
    theatre_dir = _imported(make_theatre)
    _config(theatre_dir).write_bytes(b"a: 2\n")

    with pytest.raises(PathNotFoundError), open_theatre(theatre_dir) as theatre:
        get_value(theatre, ConfigPath.parse("missing"))

    _assert_lock_free(theatre_dir)
    assert _recorded(theatre_dir) == (
        IMPORTED_A,
        Step1Record(seq=2, op="set", path="a", old=1, new=2),
    )


def test_reconcile_events_remain_after_show_exits_5_and_set_exits_3(
    make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    """Req 1.2, 1.3: ``show`` exiting 5 and a refused ``set`` exiting 3 keep the reconcile events
    each recorded first; ``history`` in between records nothing."""
    theatre_dir = make_theatre("a: 1\nl:\n  - a: 1\n")
    args = ("--theatre", str(theatre_dir))
    assert run_cli("config", "history", *args).exit_code == 0  # the import

    _config(theatre_dir).write_bytes(b"a: 2\nl:\n  - a: 1\n")
    assert run_cli("config", "show", "missing", *args).exit_code == 5
    _config(theatre_dir).write_bytes(b"a: 3\nl:\n  - a: 1\n")
    refused = run_cli("config", "set", "l.0", "{b: 2}", *args)
    history = run_cli("config", "history", *args)

    assert refused.exit_code == 3, refused.stderr
    assert history.exit_code == 0, history.stderr
    assert history.stdout.decode("utf-8").splitlines()[-2:] == [
        '{"seq": 3, "op": "set", "path": "a", "old": 1, "new": 2}',
        '{"seq": 4, "op": "set", "path": "a", "old": 2, "new": 3}',
    ]


# --- one opener at a time ------------------------------------------------------------------


def test_a_second_process_waits_until_the_first_closes_the_theatre(
    make_theatre: MakeTheatre,
) -> None:
    """Req 6.3, 6.4, 1.7: while one process holds the theatre open, another process's open waits
    until it is closed, then reconciles the hand edit made meanwhile after the holder's events."""
    theatre_dir = _imported(make_theatre)
    theatre = open_theatre(theatre_dir)
    child = subprocess.Popen(
        [sys.executable, "-c", CHILD_SCRIPT, str(theatre_dir)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "opening"
        _config(theatre_dir).write_bytes(b"a: 2\n")
        time.sleep(HOLD_SECONDS)
        assert child.poll() is None
        released_at = time.time()
        theatre.close()
        opened = child.stdout.readline().strip()
        child.wait(timeout=CHILD_TIMEOUT_SECONDS)
    finally:
        theatre.close()
        if child.poll() is None:
            child.kill()
            child.wait()

    assert child.returncode == 0, child.stderr.read() if child.stderr else ""
    word, stamp, count = opened.split()
    assert word == "opened"
    assert float(stamp) >= released_at
    assert count == "2"  # the import, then the reconcile of the edit made while waiting


def test_opening_the_same_theatre_inside_an_open_block_raises_instead_of_deadlocking(
    make_theatre: MakeTheatre,
) -> None:
    """Req 6.4: a nested open of the same theatre in one process is a programming error raised at
    once; the outer theatre stays usable and its close frees the lock."""
    theatre_dir = _imported(make_theatre)

    with open_theatre(theatre_dir) as theatre:
        with pytest.raises(RuntimeError):
            open_theatre(theatre_dir)
        with pytest.raises(RuntimeError):
            open_theatre(theatre_dir, reconcile=False)
        assert step1(theatre.history.records()) == (IMPORTED_A,)

    _assert_lock_free(theatre_dir)
