"""Tests of the set use case (task 4.4): ``troupe.config.writeback.setter.set_value``.

Every test opens a real theatre through the foundation session (``open_theatre``) and calls the use
case directly (the CLI is wired in 4.5). Failures are injected at the OS boundary (``os.replace``)
or at the SQLite boundary (the recorder's ``INSERT``). Every test asserts the exact bytes of the
files, the history and that the listing of each directory holding a written file is unchanged
(product-owner decision E).

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from eventsourcing.sqlite import SQLiteCursor

from set_support import assert_file_bytes, snapshot_tree
from support import CLI_CONTEXT, MakeTheatre, Step1Record, step1
from troupe.config.constants import HISTORY_STORE_NAME
from troupe.config.errors import ConfigLoadError, ExitCode, WriteRefusedError
from troupe.config.history import RemoveChange, SetChange
from troupe.config.paths import ConfigPath
from troupe.config.theatre import Theatre, open_theatre
from troupe.config.writeback.setter import SetOutcome, set_value
from troupe.config.writeback.value import parse_value

P_ROOT = "p:\n  t: file:t.txt\n  n: 1\n"
P_SET = ("p", "{t: new, n: 2}")


@contextmanager
def _opened(directory: Path) -> Iterator[Theatre]:
    with open_theatre(directory) as theatre:
        yield theatre


def _set(theatre: Theatre, path: str, value: str) -> SetOutcome:
    config_path = ConfigPath.parse(path)
    return set_value(theatre, config_path, parse_value(value, config_path), CLI_CONTEXT)


def _listing(directory: Path) -> list[str]:
    """Directory entries, without the history store's own (SQLite) files."""
    return sorted(name for name in os.listdir(directory) if not name.startswith(HISTORY_STORE_NAME))


def _two_file_theatre(make_theatre: MakeTheatre) -> Path:
    return make_theatre(P_ROOT, files={".troupe/t.txt": "old\n"})


def _fail_replace(monkeypatch: pytest.MonkeyPatch, failing: dict[str, int]) -> list[str]:
    """Make ``os.replace`` fail for the n-th rename onto each named target (1-based).

    Returns the list of rename targets (file names) in call order, failed ones included.
    """
    original = os.replace
    calls: list[str] = []

    def replace(src: Any, dst: Any, *args: Any, **kwargs: Any) -> None:
        name = Path(dst).name
        calls.append(name)
        if failing.get(name) == calls.count(name):
            raise PermissionError(13, "Permission denied (injected)")
        original(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "replace", replace)
    return calls


def _fail_inserts(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Make every SQLite ``INSERT`` fail (the recorder's insert raises)."""
    original = SQLiteCursor.execute
    inserts: list[int] = []

    def execute(self: SQLiteCursor, *args: Any, **kwargs: Any) -> None:
        statement = str(args[0]) if args else str(kwargs.get("statement", ""))
        if statement.lstrip().upper().startswith("INSERT"):
            inserts.append(1)
            raise sqlite3.OperationalError("injected insert failure")
        original(self, *args, **kwargs)

    monkeypatch.setattr(SQLiteCursor, "execute", execute)
    return inserts


# --- outcomes that write -------------------------------------------------------------------------


def test_successful_single_file_set_writes_one_file_and_records_the_diff(
    make_theatre: MakeTheatre,
) -> None:
    """Req 13.4, 15.1: `a, 2` on `'a: 1\\nb: x\\n'` writes only troupe.yml and records one set
    event with old 1 and new 2; the directory listing is unchanged."""
    directory = make_theatre("a: 1\nb: x\n", files={"other.txt": "keep\n"})
    config_dir = directory / ".troupe"
    with _opened(directory) as theatre:
        before = step1(theatre.history.records())
        listing = _listing(config_dir)
        outcome = _set(theatre, "a", "2")
        records = step1(theatre.history.records())

    assert outcome == SetOutcome(
        written=(config_dir / "troupe.yml",),
        changes=(SetChange(ConfigPath.parse("a"), 1, 2),),
    )
    assert_file_bytes(config_dir / "troupe.yml", "a: 2\nb: x\n")
    assert_file_bytes(directory / "other.txt", "keep\n")
    assert records == (*before, Step1Record(len(before) + 1, "set", "a", 1, 2))
    assert _listing(config_dir) == listing


def test_two_file_set_writes_both_files_and_records_both_leaves(
    make_theatre: MakeTheatre,
) -> None:
    """Req 13.6, 15.1, 15.5: the two-file set of 3.6 writes troupe.yml and t.txt byte-exactly, in
    order, and records `set p.t` and `set p.n` in one batch with consecutive seq."""
    directory = _two_file_theatre(make_theatre)
    config_dir = directory / ".troupe"
    with _opened(directory) as theatre:
        before = step1(theatre.history.records())
        listing = _listing(config_dir)
        outcome = _set(theatre, *P_SET)
        records = step1(theatre.history.records())

    assert outcome.written == (config_dir / "troupe.yml", config_dir / "t.txt")
    assert_file_bytes(config_dir / "troupe.yml", "p:\n  t: file:t.txt\n  n: 2\n")
    assert_file_bytes(config_dir / "t.txt", "new")
    seq = len(before)
    assert records == (
        *before,
        Step1Record(seq + 1, "set", "p.t", "old\n", "new"),
        Step1Record(seq + 2, "set", "p.n", 1, 2),
    )
    assert _listing(config_dir) == listing


def test_set_after_hand_edit_follows_the_reconcile_and_takes_old_values_from_the_files(
    make_theatre: MakeTheatre,
) -> None:
    """Req 1.7 (config-file-sync), 15.1, 15.2, 15.5 (decision A): opening after a hand edit records
    the reconcile events first; the set's old values are the hand-edited values from the files,
    which now chain from the reconcile events; the removed leaf is recorded after the set leaf."""
    directory = make_theatre("a: 1\n")
    config = directory / ".troupe" / "troupe.yml"
    with _opened(directory):
        pass  # the import
    config.write_text("a:\n  x: 5\n  y: 6\n", encoding="utf-8")
    with _opened(directory) as theatre:
        outcome = _set(theatre, "a", "{x: 7}")
        records = step1(theatre.history.records())

    assert outcome.changes == (
        SetChange(ConfigPath.parse("a.x"), 5, 7),
        RemoveChange(ConfigPath.parse("a.y"), 6),
    )
    assert records == (
        Step1Record(1, "import", "a", None, 1),
        Step1Record(2, "set", "a.x", None, 5),
        Step1Record(3, "set", "a.y", None, 6),
        Step1Record(4, "remove", "a", 1, None),
        Step1Record(5, "set", "a.x", 5, 7),
        Step1Record(6, "remove", "a.y", 6, None),
    )
    assert_file_bytes(config, "a:\n  x: 7\n")


# --- outcomes that write nothing -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("config", "files", "path", "value"),
    [
        ("a: 1\n", {}, "a", "1"),
        ("a: {x: 1, y: 2}\n", {}, "a", "{y: 2, x: 1}"),
        # An include directive set to its own `file:` string (2.8).
        ("c: file:c.yaml\n", {".troupe/c.yaml": "k: 1\n"}, "c", "file:c.yaml"),
        # A no-op that a change would refuse (a folded value with a more-indented line, 10.7).
        ("a: >\n  x\n    y\n", {}, "a", '"x\\n  y\\n"'),
    ],
)
def test_no_op_writes_nothing_and_records_nothing(
    make_theatre: MakeTheatre,
    monkeypatch: pytest.MonkeyPatch,
    config: str,
    files: dict[str, str],
    path: str,
    value: str,
) -> None:
    """Req 14.6, 15.4: VALUE equal to the current value writes no file (no rename at all), records
    nothing and returns an empty outcome, before any check that would refuse the set."""
    directory = make_theatre(config, files=files)
    snapshot = snapshot_tree(directory)
    with _opened(directory) as theatre:
        before = step1(theatre.history.records())
        renames = _fail_replace(monkeypatch, {})
        outcome = _set(theatre, path, value)
        monkeypatch.undo()
        records = step1(theatre.history.records())

    assert outcome == SetOutcome(written=(), changes=())
    assert renames == []
    assert records == before
    assert snapshot_tree(directory) == snapshot


@pytest.mark.parametrize(
    ("config", "files", "path", "value"),
    [
        # 2.5: a YAML include root that would change (refused while planning).
        ("c: file:c.yaml\n", {".troupe/c.yaml": "k: 1\n"}, "c", "{k: 2}"),
        # 14.8: a new include whose target is missing (refused while verifying).
        ("a: 1\n", {}, "b", "file:missing.yaml"),
    ],
)
def test_refusal_leaves_files_and_history_untouched(
    make_theatre: MakeTheatre, config: str, files: dict[str, str], path: str, value: str
) -> None:
    """Req 14.7, 13.4: a refused set (exit 3) raises before any write, changes no file and records
    no event."""
    directory = make_theatre(config, files=files)
    snapshot = snapshot_tree(directory)
    listing = _listing(directory / ".troupe")
    with _opened(directory) as theatre:
        before = step1(theatre.history.records())
        with pytest.raises(WriteRefusedError) as caught:
            _set(theatre, path, value)
        records = step1(theatre.history.records())

    assert caught.value.exit_code is ExitCode.WRITE_REFUSED
    assert caught.value.path == path
    assert records == before
    assert snapshot_tree(directory) == snapshot
    assert _listing(directory / ".troupe") == listing


# --- write failures ------------------------------------------------------------------------------


def test_second_write_failure_restores_the_first_file(
    make_theatre: MakeTheatre, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Req 14.5, 14.7, 16.2, 13.6: writing t.txt fails at the OS boundary after troupe.yml was
    written; troupe.yml is restored byte-identically, the error names PATH, t.txt and the reason,
    exit 3, and no event is recorded; no temporary file remains."""
    directory = _two_file_theatre(make_theatre)
    config_dir = directory / ".troupe"
    snapshot = snapshot_tree(directory)
    with _opened(directory) as theatre:
        before = step1(theatre.history.records())
        listing = _listing(config_dir)
        calls = _fail_replace(monkeypatch, {"t.txt": 1})
        with pytest.raises(WriteRefusedError) as caught:
            _set(theatre, *P_SET)
        monkeypatch.undo()
        records = step1(theatre.history.records())

    assert calls == ["troupe.yml", "t.txt", "troupe.yml"]  # write, write, restore
    error = caught.value
    assert error.exit_code is ExitCode.WRITE_REFUSED
    assert error.file == config_dir / "t.txt"
    assert error.path == "p"
    assert "Permission denied (injected)" in error.problem
    assert "restore" not in error.problem
    assert str(error).startswith(f"{config_dir / 't.txt'}: p: ")
    assert records == before
    assert snapshot_tree(directory) == snapshot
    assert _listing(config_dir) == listing


def test_first_write_failure_writes_nothing_and_names_path(
    make_theatre: MakeTheatre, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Req 14.5, 16.2: a failure of the only write is re-raised with PATH added (file and reason
    kept); nothing is restored because nothing was written."""
    directory = make_theatre("a: 1\n")
    config_dir = directory / ".troupe"
    snapshot = snapshot_tree(directory)
    with _opened(directory) as theatre:
        before = step1(theatre.history.records())
        listing = _listing(config_dir)
        calls = _fail_replace(monkeypatch, {"troupe.yml": 1})
        with pytest.raises(WriteRefusedError) as caught:
            _set(theatre, "a", "2")
        monkeypatch.undo()
        records = step1(theatre.history.records())

    assert calls == ["troupe.yml"]
    assert caught.value.file == config_dir / "troupe.yml"
    assert caught.value.path == "a"
    assert "Permission denied (injected)" in caught.value.problem
    assert records == before
    assert snapshot_tree(directory) == snapshot
    assert _listing(config_dir) == listing


def test_write_failure_with_failing_restore_names_the_unrestored_file(
    make_theatre: MakeTheatre, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Req 14.10, 14.5: writing t.txt fails and restoring troupe.yml fails too; the error keeps
    exit 3 and file t.txt, names PATH and names troupe.yml as not restored; no event is recorded
    and no temporary file remains."""
    directory = _two_file_theatre(make_theatre)
    config_dir = directory / ".troupe"
    with _opened(directory) as theatre:
        before = step1(theatre.history.records())
        listing = _listing(config_dir)
        calls = _fail_replace(monkeypatch, {"t.txt": 1, "troupe.yml": 2})
        with pytest.raises(WriteRefusedError) as caught:
            _set(theatre, *P_SET)
        monkeypatch.undo()
        records = step1(theatre.history.records())

    assert calls == ["troupe.yml", "t.txt", "troupe.yml"]
    error = caught.value
    assert error.exit_code is ExitCode.WRITE_REFUSED
    assert error.file == config_dir / "t.txt"
    assert error.path == "p"
    assert "Permission denied (injected)" in error.problem
    assert f"not restored: {config_dir / 'troupe.yml'}" in error.problem
    assert records == before
    # The restore failed, so troupe.yml keeps the new text; t.txt was never written.
    assert_file_bytes(config_dir / "troupe.yml", "p:\n  t: file:t.txt\n  n: 2\n")
    assert_file_bytes(config_dir / "t.txt", "old\n")
    assert _listing(config_dir) == listing


# --- history failures ----------------------------------------------------------------------------


def test_history_failure_restores_every_written_file(
    make_theatre: MakeTheatre, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Req 15.6, 14.7, 14.4: recording fails at the SQLite boundary after both files were written;
    both are restored byte-identically and the store error (exit 4, naming history.sqlite) is
    re-raised; no event is recorded and no temporary file remains."""
    directory = _two_file_theatre(make_theatre)
    config_dir = directory / ".troupe"
    snapshot = snapshot_tree(directory)
    with _opened(directory) as theatre:
        before = step1(theatre.history.records())
        listing = _listing(config_dir)
        calls = _fail_replace(monkeypatch, {})
        inserts = _fail_inserts(monkeypatch)
        with pytest.raises(ConfigLoadError) as caught:
            _set(theatre, *P_SET)
        monkeypatch.undo()
        records = step1(theatre.history.records())

    assert inserts, "the failure was not injected at the SQLite boundary"
    assert calls == ["troupe.yml", "t.txt", "t.txt", "troupe.yml"]  # restored in reverse
    error = caught.value
    assert error.exit_code is ExitCode.LOAD
    assert error.file == theatre.history.location
    assert error.file.name == HISTORY_STORE_NAME
    assert "injected insert failure" in error.problem
    assert "restored" not in error.problem
    assert records == before
    assert snapshot_tree(directory) == snapshot
    assert _listing(config_dir) == listing


def test_history_failure_with_failing_restore_names_store_and_file(
    make_theatre: MakeTheatre, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Req 14.10, 15.6: recording fails and restoring t.txt fails too; the remaining restore of
    troupe.yml is still attempted (reverse order), exit 4 is kept, the error's file stays the store
    and the message names both the store and t.txt."""
    directory = _two_file_theatre(make_theatre)
    config_dir = directory / ".troupe"
    with _opened(directory) as theatre:
        before = step1(theatre.history.records())
        listing = _listing(config_dir)
        calls = _fail_replace(monkeypatch, {"t.txt": 2})
        _fail_inserts(monkeypatch)
        with pytest.raises(ConfigLoadError) as caught:
            _set(theatre, *P_SET)
        monkeypatch.undo()
        records = step1(theatre.history.records())

    assert calls == ["troupe.yml", "t.txt", "t.txt", "troupe.yml"]
    error = caught.value
    assert error.exit_code is ExitCode.LOAD
    assert error.file == theatre.history.location
    message = str(error)
    assert str(theatre.history.location) in message
    assert f"not restored: {config_dir / 't.txt'}" in message
    assert str(config_dir / "troupe.yml") not in message
    assert records == before
    assert_file_bytes(config_dir / "troupe.yml", P_ROOT)
    assert_file_bytes(config_dir / "t.txt", "new")
    assert _listing(config_dir) == listing
