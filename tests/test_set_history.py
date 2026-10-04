"""End-to-end validation of the history of sets (5.10).

Every set runs the real ``troupe`` console script (``set_support.run_set``) and the history is
read back with ``troupe config history`` through the same script; the injected store failure runs
the CLI in this process (``set_support.run_config_in_process``) so the SQLite boundary can be
monkeypatched. PATH and VALUE are the exact argv strings, written as Python literals; fixtures are
the exact bytes of the files.

Each history line is parsed with strict JSON (no NaN/Infinity constants) and asserted for ``seq``,
``op``, ``path``, ``old`` and ``new``, with the JSON type of every value checked too (``1``,
``1.0`` and ``"1"`` are different values).

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
    assert_file_bytes,
    run_config,
    run_config_in_process,
    run_set,
    set_args,
)
from support import CliResult, MakeTheatre, step1_members
from troupe.config.constants import HISTORY_STORE_NAME

ROOT = ".troupe/troupe.yml"

Event = tuple[int, str, str, object, object]
"""One history line as ``(seq, op, path, old, new)``."""


def _reject_constant(name: str) -> object:
    raise ValueError(f"non-strict JSON constant {name}")


def _typed(value: object) -> object:
    """``value`` paired with its exact JSON type, so ``1``, ``1.0``, ``True`` and ``"1"`` differ."""
    if isinstance(value, list):
        return ("list", [_typed(item) for item in value])
    if isinstance(value, dict):
        return ("dict", {key: _typed(item) for key, item in value.items()})
    return (type(value).__name__, value)


def _events(theatre: Path, path: str | None = None) -> list[Event]:
    """Every ``troupe config history [PATH]`` line, strictly parsed, oldest first."""
    args = ("history", *(() if path is None else (path,)), "--theatre", str(theatre))
    result = run_config(*args, cwd=theatre)
    assert (result.exit_code, result.stderr) == (0, ""), result.stderr
    events: list[Event] = []
    for line in result.stdout.decode("utf-8").splitlines():
        parsed = json.loads(line, parse_constant=_reject_constant)
        assert isinstance(parsed, dict)
        assert list(parsed)[5:] == ["actor", "via", "at", "reason", "change"], line
        row = step1_members(parsed)
        assert type(row["seq"]) is int, line
        assert row["op"] in ("import", "set", "remove"), line
        assert isinstance(row["path"], str), line
        events.append((row["seq"], row["op"], row["path"], row["old"], row["new"]))
    assert [seq for seq, *_ in events] == sorted(seq for seq, *_ in events)
    return events


def _assert_events(actual: list[Event], expected: list[Event]) -> None:
    """Compare events value by value AND JSON type by JSON type."""
    assert [(s, op, p, _typed(o), _typed(n)) for s, op, p, o, n in actual] == [
        (s, op, p, _typed(o), _typed(n)) for s, op, p, o, n in expected
    ]


def _assert_set_ok(result: CliResult) -> None:
    assert (result.exit_code, result.stdout, result.stderr) == (0, b"", ""), result.stderr


# --- first set, created keys, merges ---------------------------------------------------------


def test_first_set_on_a_fresh_theatre_imports_then_records_the_set(
    make_theatre: MakeTheatre,
) -> None:
    """Req 15.1, 15.5: a set as the very first command imports every leaf (old null, seq from 1),
    then records its own `set` event; a new key records `old` null; `history PATH` filters."""
    theatre = make_theatre("a: 1\nl: [p, q]\n")

    _assert_set_ok(run_set(theatre, "b", "two"))

    assert_file_bytes(theatre / ROOT, "a: 1\nl: [p, q]\nb: two\n")
    _assert_events(
        _events(theatre),
        [
            (1, "import", "a", None, 1),
            (2, "import", "l.0", None, "p"),
            (3, "import", "l.1", None, "q"),
            (4, "set", "b", None, "two"),
        ],
    )
    _assert_events(_events(theatre, "b"), [(4, "set", "b", None, "two")])


def test_mapping_merge_records_sets_in_new_order_then_removes(make_theatre: MakeTheatre) -> None:
    """Req 15.1, 15.2, 15.5: the mapping merge fixture of 3.5 records `set db.host`, `set db.pool`
    (created, old null), then `remove db.user` (new null), with consecutive `seq`."""
    theatre = make_theatre(
        "db:\n  host: a   # primary\n  # port comment\n  port: 5432\n  user: x\n"
    )
    assert len(_events(theatre)) == 3  # import first

    _assert_set_ok(run_set(theatre, "db", "{host: b, port: 5432, pool: 3}"))

    assert_file_bytes(
        theatre / ROOT, "db:\n  host: b   # primary\n  # port comment\n  port: 5432\n  pool: 3\n"
    )
    _assert_events(
        _events(theatre),
        [
            (1, "import", "db.host", None, "a"),
            (2, "import", "db.port", None, 5432),
            (3, "import", "db.user", None, "x"),
            (4, "set", "db.host", "a", "b"),
            (5, "set", "db.pool", None, 3),
            (6, "remove", "db.user", "x", None),
        ],
    )


# --- typed comparison, no-ops, refusals ------------------------------------------------------


def test_typed_changes_each_record_a_set_event_with_json_typed_values(
    make_theatre: MakeTheatre,
) -> None:
    """Req 15.1, 15.3: `1` -> `"1"` -> `1.0` are three different typed values; each change
    records one `set` event whose `old` and `new` keep their JSON types."""
    theatre = make_theatre("a: 1\n")

    _assert_set_ok(run_set(theatre, "a", '"1"'))
    assert_file_bytes(theatre / ROOT, "a: '1'\n")
    _assert_set_ok(run_set(theatre, "a", "1.0"))
    assert_file_bytes(theatre / ROOT, "a: 1.0\n")

    _assert_events(
        _events(theatre),
        [
            (1, "import", "a", None, 1),
            (2, "set", "a", 1, "1"),
            (3, "set", "a", "1", 1.0),
        ],
    )


def test_empty_mapping_to_empty_list_changes_the_file_and_records_nothing(
    make_theatre: MakeTheatre,
) -> None:
    """Req 15.4: `{}` -> `[]` changes the file but no leaf, so no event is recorded."""
    theatre = make_theatre("x: {}\n")
    before = _events(theatre)

    _assert_set_ok(run_set(theatre, "x", "[]"))

    assert_file_bytes(theatre / ROOT, "x: []\n")
    assert before == []
    assert _events(theatre) == before


def test_no_op_and_refused_sets_record_nothing(make_theatre: MakeTheatre) -> None:
    """Req 15.4: a no-op set (exit 0, file untouched) and a refused set (exit 3) record no event."""
    theatre = make_theatre("a: 1\nl:\n  - a: 1\n    b: 2\n")
    before = _events(theatre)
    assert [op for _, op, *_ in before] == ["import", "import", "import"]

    _assert_set_ok(run_set(theatre, "a", "1"))
    refused = run_set(theatre, "l.0", "{b: 2}")

    assert (refused.exit_code, refused.stdout) == (3, b""), refused.stderr
    assert refused.stderr.startswith(f"{theatre / ROOT}: l.0: "), refused.stderr
    assert_file_bytes(theatre / ROOT, "a: 1\nl:\n  - a: 1\n    b: 2\n")
    assert _events(theatre) == before


# --- new includes ----------------------------------------------------------------------------


def test_new_yaml_include_records_each_included_leaf_then_removes_the_old_one(
    make_theatre: MakeTheatre,
) -> None:
    """Req 15.7, 15.1, 15.2, 15.5: a new YAML include is recorded from the configuration as read
    back: `set b.k` and `set b.l.0` (old null), then `remove b` (old "x")."""
    theatre = make_theatre("b: x\n", files={".troupe/inc.yaml": "k: 1\nl: [p]\n"})
    assert len(_events(theatre)) == 1

    _assert_set_ok(run_set(theatre, "b", "file:inc.yaml"))

    assert_file_bytes(theatre / ROOT, "b: file:inc.yaml\n")
    _assert_events(
        _events(theatre),
        [
            (1, "import", "b", None, "x"),
            (2, "set", "b.k", None, 1),
            (3, "set", "b.l.0", None, "p"),
            (4, "remove", "b", "x", None),
        ],
    )


def test_new_text_include_records_one_string_leaf(make_theatre: MakeTheatre) -> None:
    """Req 15.7, 15.1: a new text include is recorded as one string leaf: one `set c` whose `new`
    is the file's text."""
    theatre = make_theatre("c: x\n", files={".troupe/notes.txt": "hi\n"})
    assert len(_events(theatre)) == 1

    _assert_set_ok(run_set(theatre, "c", "file:notes.txt"))

    assert_file_bytes(theatre / ROOT, "c: file:notes.txt\n")
    _assert_events(
        _events(theatre),
        [(1, "import", "c", None, "x"), (2, "set", "c", "x", "hi\n")],
    )


# --- files are the truth (product-owner decision A) ------------------------------------------


def test_set_after_hand_edit_records_the_reconcile_set_first_then_its_own_with_old_chained(
    make_theatre: MakeTheatre,
) -> None:
    """Req 1.7, 1.3 (config-file-sync), 15.1: a hand edit after the import is not recorded by
    `history`; the next set first records the reconcile `set a` (old 1, new 5), then its own
    `set a` with `old` 5 (the hand-edited value), not the imported 1."""
    theatre = make_theatre("a: 1\n")
    _assert_events(_events(theatre), [(1, "import", "a", None, 1)])

    (theatre / ROOT).write_bytes(b"a: 5\n")
    _assert_events(_events(theatre), [(1, "import", "a", None, 1)])

    _assert_set_ok(run_set(theatre, "a", "7"))

    assert_file_bytes(theatre / ROOT, "a: 7\n")
    _assert_events(
        _events(theatre),
        [(1, "import", "a", None, 1), (2, "set", "a", 1, 5), (3, "set", "a", 5, 7)],
    )


# --- store failure while recording ----------------------------------------------------------


def _listing(root: Path) -> dict[str, list[str]]:
    """Every directory under ``root`` with all its entries (hidden files, history store too)."""
    listing: dict[str, list[str]] = {}
    for directory, subdirectories, names in os.walk(root):
        relative = Path(directory).relative_to(root).as_posix()
        listing[relative] = sorted([*subdirectories, *names])
    return dict(sorted(listing.items()))


def test_store_failure_while_recording_restores_the_file(
    make_theatre: MakeTheatre, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Req 15.6 (product-owner decision E): the recorder's SQLite insert raises after the file was
    written; the file is restored byte-identically, exit 4 names history.sqlite, stdout is empty,
    the directory listing is unchanged (no temporary file) and no event was recorded."""
    original_bytes = b"a: 1   # keep\nb: two\n"
    theatre = make_theatre(original_bytes)
    before_events = _events(theatre)
    before_listing = _listing(theatre)

    original_execute = SQLiteCursor.execute
    inserts: list[str] = []

    def execute(self: SQLiteCursor, *args: Any, **kwargs: Any) -> None:
        statement = str(args[0]) if args else str(kwargs.get("statement", ""))
        if statement.lstrip().upper().startswith("INSERT"):
            inserts.append(statement)
            raise sqlite3.OperationalError("injected insert failure")
        original_execute(self, *args, **kwargs)

    monkeypatch.setattr(SQLiteCursor, "execute", execute)
    result = run_config_in_process(*set_args(theatre, "a", "2"), cwd=theatre)
    monkeypatch.undo()

    assert inserts, "the failure was not injected at the SQLite boundary"
    assert (result.exit_code, result.stdout) == (4, b""), result.stderr
    assert result.stderr.startswith(f"{theatre / '.troupe' / HISTORY_STORE_NAME}: "), result.stderr
    assert_file_bytes(theatre / ROOT, original_bytes)
    assert _listing(theatre) == before_listing
    assert _events(theatre) == before_events
