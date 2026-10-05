"""End-to-end validation of write safety, no-ops and refusals (5.9).

No-ops, refusals, the read-only write failure and the successful two-file set run the real
``troupe`` console script (``set_support.run_set``); the injected failures (a failing second
``os.replace`` for the multi-file rollback, a failing SQLite ``INSERT`` for the history failure) run
the CLI in this process (``set_support.run_config_in_process``) so the OS or SQLite boundary can be
monkeypatched. PATH and VALUE are the exact argv strings, written as Python literals; fixtures and
expected contents are the exact bytes of the files.

Every refusal or failure asserts the exit code, empty stdout, a stderr starting with
``f"{theatre / file}: {PATH}: "`` followed by the reason, the exact bytes of every file, the full
listing of every directory of the theatre (hidden files and the history store included: no
temporary or other extra file, product-owner decision E) and the ``history`` output, all identical
to before.

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from eventsourcing.sqlite import SQLiteCursor

from set_support import (
    TreeSnapshot,
    assert_file_bytes,
    run_config,
    run_config_in_process,
    run_set,
    set_args,
    snapshot_tree,
)
from support import CliResult, MakeTheatre
from troupe.config.constants import HISTORY_STORE_NAME

ROOT = ".troupe/troupe.yml"
TEXT = ".troupe/t.txt"
TWO_FILE_ROOT = "p:\n  t: file:t.txt\n  n: 1\n"
TWO_FILE_SET = ("p", "{t: new, n: 2}")

Listing = dict[str, list[str]]
"""Relative POSIX path of every directory under a root mapped to its sorted entry names."""


def _listing(root: Path) -> Listing:
    """Every directory under ``root`` with all its entries (hidden files, history store too)."""
    listing: Listing = {}
    for directory, subdirectories, names in os.walk(root):
        relative = Path(directory).relative_to(root).as_posix()
        listing[relative] = sorted([*subdirectories, *names])
    return dict(sorted(listing.items()))


def _history(theatre: Path) -> bytes:
    """The exact ``troupe config history`` output (importing on a fresh theatre)."""
    result = run_config("history", "--theatre", str(theatre), cwd=theatre)
    assert (result.exit_code, result.stderr) == (0, ""), result.stderr
    return result.stdout


def _ops(history: bytes) -> list[tuple[str, str, object, object]]:
    rows = [json.loads(line) for line in history.decode("utf-8").splitlines()]
    return [(row["op"], row["path"], row["old"], row["new"]) for row in rows]


class _State:
    """The observable state of a theatre: file bytes, inodes, directory listing, history."""

    def __init__(self, theatre: Path) -> None:
        self.files: TreeSnapshot = snapshot_tree(theatre)
        self.inodes = {name: (theatre / name).stat().st_ino for name in self.files}
        self.listing = _listing(theatre)
        self.history = _history(theatre)


def _prepared(theatre: Path) -> _State:
    """Import the theatre (first ``history``), then capture its state."""
    _history(theatre)
    return _State(theatre)


def _assert_unchanged(theatre: Path, before: _State) -> None:
    after = _State(theatre)
    assert after.files == before.files
    assert after.listing == before.listing
    assert after.history == before.history


def _assert_refused(result: CliResult, theatre: Path, file: str, path: str, reason: str) -> None:
    prefix = f"{theatre / file}: {path}: "
    assert (result.exit_code, result.stdout) == (3, b""), result.stderr
    assert result.stderr.startswith(prefix), result.stderr
    assert reason in result.stderr[len(prefix) :], result.stderr


def _fail_second_replace(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Make the second ``os.replace`` call fail; return every call's target name in order."""
    original = os.replace
    calls: list[str] = []

    def replace(src: Any, dst: Any, *args: Any, **kwargs: Any) -> None:
        calls.append(Path(dst).name)
        if len(calls) == 2:
            raise PermissionError(13, "Permission denied (injected)")
        original(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "replace", replace)
    return calls


def _count_replaces(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    original = os.replace
    calls: list[str] = []

    def replace(src: Any, dst: Any, *args: Any, **kwargs: Any) -> None:
        calls.append(Path(dst).name)
        original(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "replace", replace)
    return calls


def _fail_inserts(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Make every SQLite ``INSERT`` fail (the recorder's insert raises)."""
    original = SQLiteCursor.execute
    inserts: list[str] = []

    def execute(self: SQLiteCursor, *args: Any, **kwargs: Any) -> None:
        statement = str(args[0]) if args else str(kwargs.get("statement", ""))
        if statement.lstrip().upper().startswith("INSERT"):
            inserts.append(statement)
            raise sqlite3.OperationalError("injected insert failure")
        original(self, *args, **kwargs)

    monkeypatch.setattr(SQLiteCursor, "execute", execute)
    return inserts


# --- no-ops ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("root", "files", "path", "value"),
    [
        pytest.param("a: 1.0\n", {}, "a", "1.00", id="equal-float-other-spelling"),
        pytest.param(
            "agents:\n  coder: file:agents/coder.yaml\n",
            {".troupe/agents/coder.yaml": "model: small\n"},
            "agents.coder",
            "file:agents/coder.yaml",
            id="same-yaml-include-directive",
        ),
        pytest.param(
            "p:\n  t: file:t.txt\n",
            {TEXT: "old\n"},
            "p",
            '{t: "old\\n"}',
            id="equal-text-include-content",
        ),
    ],
)
def test_no_op_changes_no_file_and_records_nothing(
    make_theatre: MakeTheatre, root: str, files: dict[str, str], path: str, value: str
) -> None:
    """Req 14.6, 2.8: VALUE equal to the current value exits 0 with empty output; every file
    keeps its bytes and its inode (not even rewritten); listing and history are unchanged."""
    theatre = make_theatre(root, files=files)
    before = _prepared(theatre)

    result = run_set(theatre, path, value)

    assert (result.exit_code, result.stdout, result.stderr) == (0, b"", ""), result.stderr
    _assert_unchanged(theatre, before)
    assert {name: (theatre / name).stat().st_ino for name in before.files} == before.inodes


def test_no_op_goes_before_a_check_that_would_refuse(make_theatre: MakeTheatre) -> None:
    """Req 14.6, 2.5: the same `file:` directive over a YAML include root (refused with any other
    VALUE) is a no-op; a different VALUE there is refused (exit 3) naming the included file."""
    theatre = make_theatre(
        "agents:\n  coder: file:agents/coder.yaml\n",
        files={".troupe/agents/coder.yaml": "model: small\n"},
    )
    before = _prepared(theatre)

    refused = run_set(theatre, "agents.coder", "file:agents/other.yaml")

    _assert_refused(
        refused,
        theatre,
        ".troupe/agents/coder.yaml",
        "agents.coder",
        "the whole value comes from the include file:agents/coder.yaml",
    )
    _assert_unchanged(theatre, before)


# --- verification refusals -------------------------------------------------------------------


def test_file_included_twice_is_written_once_and_changes_both_places(
    make_theatre: MakeTheatre,
) -> None:
    """Req 14.2 (superseded since step 5 by config-include-overrides 11.1-11.3): changing a file
    included at two places is no longer refused; the value is written once in that file, both
    places read it back and one `set` event per place is recorded."""
    theatre = make_theatre(
        "x: file:shared.yaml\ny: file:shared.yaml\n", files={".troupe/shared.yaml": "k: 1\n"}
    )
    before = _prepared(theatre)

    result = run_set(theatre, "x.k", "2")

    assert (result.exit_code, result.stdout, result.stderr) == (0, b"", "")
    assert_file_bytes(theatre / ".troupe/shared.yaml", "k: 2\n")
    assert_file_bytes(theatre / ROOT, "x: file:shared.yaml\ny: file:shared.yaml\n")
    assert _listing(theatre) == before.listing
    assert _ops(_history(theatre))[len(_ops(before.history)) :] == [
        ("set", "x.k", 1, 2),
        ("set", "y.k", 1, 2),
    ]


def test_new_include_with_missing_target_is_refused(make_theatre: MakeTheatre) -> None:
    """Req 14.8, 14.2, 14.7, 16.2: a new include whose target is missing cannot be read back;
    exit 3 names the root file, PATH and the include target; nothing changes or is recorded."""
    theatre = make_theatre("a: 1\nb: x\n")
    before = _prepared(theatre)

    result = run_set(theatre, "b", "file:missing.yaml")

    _assert_refused(
        result, theatre, ROOT, "b", f"include target {theatre / '.troupe/missing.yaml'} does not"
    )
    _assert_unchanged(theatre, before)
    assert_file_bytes(theatre / ROOT, "a: 1\nb: x\n")
    assert not (theatre / ".troupe/missing.yaml").exists()


# --- write failures --------------------------------------------------------------------------


@pytest.mark.skipif(os.geteuid() == 0, reason="root can write into a read-only directory")
def test_include_in_read_only_directory_cannot_be_written(make_theatre: MakeTheatre) -> None:
    """Req 14.5, 14.7, 14.4, 16.2: the include target sits in a read-only directory, so no
    temporary file can be created; exit 3 names the target, PATH and why; nothing changes, no
    temporary file is left and nothing is recorded."""
    theatre = make_theatre("r: file:ro/inc.yaml\n", files={".troupe/ro/inc.yaml": "k: 1\n"})
    read_only = theatre / ".troupe/ro"
    before = _prepared(theatre)
    read_only.chmod(0o555)
    try:
        result = run_set(theatre, "r.k", "2")
    finally:
        read_only.chmod(0o755)

    _assert_refused(result, theatre, ".troupe/ro/inc.yaml", "r.k", "cannot write file: ")
    _assert_unchanged(theatre, before)
    assert_file_bytes(theatre / ".troupe/ro/inc.yaml", "k: 1\n")


def test_second_write_failure_restores_the_first_file(
    make_theatre: MakeTheatre, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Req 13.6, 14.5, 14.7, 14.4, 16.2 (product-owner decisions B, E): the second `os.replace`
    (onto t.txt) fails after troupe.yml was written; troupe.yml is restored (third rename), exit 3
    names t.txt, PATH and why; both files keep their bytes, no temporary file is left and the
    history shows no `set` event."""
    theatre = make_theatre(TWO_FILE_ROOT, files={TEXT: "old\n"})
    before = _prepared(theatre)

    calls = _fail_second_replace(monkeypatch)
    result = run_config_in_process(*set_args(theatre, *TWO_FILE_SET), cwd=theatre)
    monkeypatch.undo()

    assert calls == ["troupe.yml", "t.txt", "troupe.yml"]
    _assert_refused(result, theatre, TEXT, "p", "cannot write file: Permission denied (injected)")
    assert "not restored" not in result.stderr
    _assert_unchanged(theatre, before)
    assert_file_bytes(theatre / ROOT, TWO_FILE_ROOT)
    assert_file_bytes(theatre / TEXT, "old\n")
    assert [op for op, *_ in _ops(_history(theatre))] == ["import", "import"]


def test_history_failure_restores_both_files_and_leaves_no_extra_file(
    make_theatre: MakeTheatre, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Req 15.6, 14.7, 14.4, 16.1 (product-owner decision E): recording fails at the SQLite
    boundary after both files were written; both are restored, exit 4 names history.sqlite,
    stdout is empty and every listing and the history are unchanged."""
    theatre = make_theatre(TWO_FILE_ROOT, files={TEXT: "old\n"})
    before = _prepared(theatre)

    calls = _count_replaces(monkeypatch)
    inserts = _fail_inserts(monkeypatch)
    result = run_config_in_process(*set_args(theatre, *TWO_FILE_SET), cwd=theatre)
    monkeypatch.undo()

    assert inserts, "the failure was not injected at the SQLite boundary"
    assert calls == ["troupe.yml", "t.txt", "t.txt", "troupe.yml"]
    assert (result.exit_code, result.stdout) == (4, b""), result.stderr
    assert result.stderr.startswith(f"{theatre / '.troupe' / HISTORY_STORE_NAME}: "), result.stderr
    _assert_unchanged(theatre, before)


# --- success: no extra file ------------------------------------------------------------------


def test_successful_two_file_set_adds_no_file(make_theatre: MakeTheatre) -> None:
    """Req 13.6, 14.3, 14.4 (product-owner decision E): a successful two-file set changes exactly
    troupe.yml and t.txt; every directory listing is identical to before."""
    theatre = make_theatre(TWO_FILE_ROOT, files={TEXT: "old\n"})
    before = _prepared(theatre)

    result = run_set(theatre, *TWO_FILE_SET)

    assert (result.exit_code, result.stdout, result.stderr) == (0, b"", ""), result.stderr
    assert _listing(theatre) == before.listing
    assert snapshot_tree(theatre) == {
        **before.files,
        ROOT: b"p:\n  t: file:t.txt\n  n: 2\n",
        TEXT: b"new",
    }
    assert _ops(_history(theatre))[2:] == [("set", "p.t", "old\n", "new"), ("set", "p.n", 1, 2)]
