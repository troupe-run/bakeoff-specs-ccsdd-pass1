"""End-to-end validation of merges, deletions and kind changes of `troupe config set` (5.7).

Every case runs the real ``troupe`` console script (``set_support``). PATH and VALUE are the
exact argv strings, written as Python literals; fixtures and expected texts are the exact bytes
of the theatre's files (``str`` as UTF-8). Success cases assert exit 0, empty stdout, empty
stderr, the exact bytes of every changed file and that no other file was added, removed or
changed; refusals assert exit 3, empty stdout, one stderr line ``<file>: <PATH>: <reason>`` and
an unchanged theatre.

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from set_support import TreeSnapshot, assert_file_bytes, run_config, run_set, snapshot_tree
from support import CliResult, MakeTheatre

ROOT = ".troupe/troupe.yml"


def _assert_success(result: CliResult) -> None:
    assert (result.exit_code, result.stdout, result.stderr) == (0, b"", ""), result.stderr


def _assert_changed(
    theatre: Path, before: TreeSnapshot, expected: Mapping[str, str | bytes]
) -> None:
    """Assert each file of ``expected`` holds exactly its bytes and no other file changed."""
    for relative, content in expected.items():
        assert_file_bytes(theatre / relative, content)
    after = snapshot_tree(theatre)
    assert set(after) == set(before)
    assert {k: v for k, v in after.items() if k not in expected} == {
        k: v for k, v in before.items() if k not in expected
    }


def _assert_refused(
    theatre: Path,
    before: TreeSnapshot,
    result: CliResult,
    *,
    file: str,
    path: str,
    names: tuple[str, ...] = (),
) -> None:
    """Assert exit 3, empty stdout, one stderr line ``<file>: <PATH>: <reason>``, unchanged theatre.

    ``names`` must appear in the (non-empty) reason.
    """
    assert result.exit_code == 3, (result.exit_code, result.stderr)
    assert result.stdout == b""
    assert result.stderr.endswith("\n") and result.stderr.count("\n") == 1, result.stderr
    prefix = f"{theatre / file}: {path}: "
    assert result.stderr.startswith(prefix), (prefix, result.stderr)
    reason = result.stderr[len(prefix) : -1]
    assert reason.strip(), result.stderr
    for name in names:
        assert name in reason, (name, result.stderr)
    assert snapshot_tree(theatre) == before


def _history(theatre: Path) -> list[dict[str, object]]:
    result = run_config("history", "--theatre", str(theatre), cwd=theatre)
    assert (result.exit_code, result.stderr) == (0, ""), result.stderr
    return [json.loads(line) for line in result.stdout.decode("utf-8").splitlines()]


def _set_and_check(
    make_theatre: MakeTheatre, fixture: str, path: str, value: str, expected: str
) -> None:
    theatre = make_theatre(fixture)
    before = snapshot_tree(theatre)
    result = run_set(theatre, path, value)
    _assert_success(result)
    _assert_changed(theatre, before, {ROOT: expected})


# ---- Requirement 5.1-5.4: merges of mappings and lists (the five 3.5 fixtures) ----

DB_FIXTURE = "db:\n  host: a   # primary\n  # port comment\n  port: 5432\n  user: x\n"
DB_EXPECTED = "db:\n  host: b   # primary\n  # port comment\n  port: 5432\n  pool: 3\n"


@pytest.mark.parametrize(
    ("fixture", "path", "value", "expected"),
    [
        pytest.param(
            DB_FIXTURE,
            "db",
            "{host: b, port: 5432, pool: 3}",
            DB_EXPECTED,
            id="block-mapping-merge",
        ),
        pytest.param(
            DB_FIXTURE,
            "db",
            "{port: 5432, host: b, pool: 3}",
            DB_EXPECTED,
            id="block-mapping-merge-keeps-existing-order",
        ),
        pytest.param(
            "l:\n  - a  # first\n  - b\n  - c\n",
            "l",
            "[a, z]",
            "l:\n  - a  # first\n  - z\n",
            id="block-list-by-index",
        ),
        pytest.param(
            "m: {a: 1, b: 2, c: 3}\n",
            "m",
            "{a: 1, c: 4}",
            "m: {a: 1, c: 4}\n",
            id="flow-remove-middle",
        ),
        pytest.param(
            "m: {a: 1, b: 2}\n",
            "m",
            "{b: 2}",
            "m: {b: 2}\n",
            id="flow-remove-first",
        ),
        pytest.param(
            "m: {a: 1, b: 2}\n",
            "m",
            "{x: 1}",
            "m: {x: 1}\n",
            id="flow-remove-all-add-new",
        ),
    ],
)
def test_collection_merge(
    make_theatre: MakeTheatre, fixture: str, path: str, value: str, expected: str
) -> None:
    """Req 5.1, 5.2, 5.3, 5.4: unchanged entries stay byte-identical (comments included), changed
    entries change in place, absent keys / surplus items are removed (block: through the last
    content line; flow: with one comma and its space), new keys are appended and the existing key
    order is kept whatever the order in VALUE."""
    _set_and_check(make_theatre, fixture, path, value, expected)


# ---- Requirement 5.1, 2.2, 2.3: text includes inside a merged mapping ----

TEXT_ROOT = "p:\n  t: file:t.txt\n  n: 1\n"
TEXT_FILE = ".troupe/t.txt"


@pytest.mark.parametrize(
    ("old", "new"),
    [
        pytest.param("old\n", "new\n", id="lf"),
        pytest.param(b"old\r\n", b"new\r\n", id="crlf"),
    ],
)
def test_merge_changes_only_the_text_include(
    make_theatre: MakeTheatre, old: str | bytes, new: str | bytes
) -> None:
    """Req 5.1, 2.2, 2.3: merging `{t: "new\\n", n: 1}` changes only t.txt (keeping its line
    ending); troupe.yml stays byte-identical because `n` is unchanged."""
    theatre = make_theatre(TEXT_ROOT, files={TEXT_FILE: old})
    before = snapshot_tree(theatre)

    result = run_set(theatre, "p", '{t: "new\\n", n: 1}')

    _assert_success(result)
    _assert_changed(theatre, before, {TEXT_FILE: new})


def test_two_file_set_writes_both_files_and_records_both_leaves(
    make_theatre: MakeTheatre,
) -> None:
    """Req 13.6, 5.1, 2.2, 15.1, 15.5, 16.2 (product-owner decision B): `p, '{t: new, n: 2}'`
    changes troupe.yml and t.txt byte-exactly, nothing else, and the history records `set p.t`
    then `set p.n` after the import events with consecutive seq."""
    theatre = make_theatre(TEXT_ROOT, files={TEXT_FILE: "old\n"})
    before = snapshot_tree(theatre)

    result = run_set(theatre, "p", "{t: new, n: 2}")

    _assert_success(result)
    _assert_changed(theatre, before, {ROOT: "p:\n  t: file:t.txt\n  n: 2\n", TEXT_FILE: "new"})
    assert _history(theatre) == [
        {"seq": 1, "op": "import", "path": "p.t", "old": None, "new": "old\n"},
        {"seq": 2, "op": "import", "path": "p.n", "old": None, "new": 1},
        {"seq": 3, "op": "set", "path": "p.t", "old": "old\n", "new": "new"},
        {"seq": 4, "op": "set", "path": "p.n", "old": 1, "new": 2},
    ]


# ---- Requirement 5.5, 5.6: kind changes ----


@pytest.mark.parametrize(
    ("fixture", "path", "value", "expected"),
    [
        pytest.param(
            "db:\n  host: a\n  # trailing\nnext: 1\n",
            "db",
            "5",
            "db: 5\n  # trailing\nnext: 1\n",
            id="scalar-replaces-block-mapping",
        ),
        pytest.param(
            "db:\n  host: a\nnext: 1\n",
            "db",
            "{}",
            "db: {}\nnext: 1\n",
            id="empty-mapping-replaces-block-mapping",
        ),
        pytest.param(
            "x:\n  - a\n",
            "x",
            "{k: v}",
            "x:\n  k: v\n",
            id="mapping-replaces-block-list",
        ),
    ],
)
def test_kind_change(
    make_theatre: MakeTheatre, fixture: str, path: str, value: str, expected: str
) -> None:
    """Req 5.5, 5.6, 8.1: a scalar or `{}` replacing a block collection goes on the key's line,
    the collection's lines go through its last content line and the trailing comment stays; a
    mapping replacing a list is written in block style."""
    _set_and_check(make_theatre, fixture, path, value, expected)


# ---- Requirement 5.7, 5.8, 5.9, 14.9: refusals and YAML includes inside merges ----


def test_deleting_compact_first_key_is_refused(make_theatre: MakeTheatre) -> None:
    """Req 5.7, 14.9: deleting the first key on a list item's `-` line (`a: 1`) would delete part
    of a line that keeps content, so the set exits 3 naming troupe.yml and PATH `l.0`."""
    theatre = make_theatre("l:\n  - a: 1\n    b: 2\n")
    before = snapshot_tree(theatre)

    result = run_set(theatre, "l.0", "{b: 2}")

    _assert_refused(
        theatre, before, result, file=ROOT, path="l.0", names=("shares its first line",)
    )


AGENTS_ROOT = "agents:\n  coder: file:coder.yaml\n  lead: x\n"
CODER_FILE = ".troupe/coder.yaml"


def test_changing_an_include_root_inside_a_merge_is_refused(make_theatre: MakeTheatre) -> None:
    """Req 5.8, 2.3: merging `agents` with a changed `coder` (a YAML include root) exits 3 naming
    the nested path `agents.coder` and coder.yaml; no file changes."""
    theatre = make_theatre(AGENTS_ROOT, files={CODER_FILE: "model: y\n"})
    before = snapshot_tree(theatre)

    result = run_set(theatre, "agents", "{coder: {model: z}, lead: x}")

    _assert_refused(
        theatre,
        before,
        result,
        file=CODER_FILE,
        path="agents.coder",
        names=("whole value comes from the include", "coder.yaml"),
    )


def test_dropping_an_included_entry_removes_only_the_directive(
    make_theatre: MakeTheatre,
) -> None:
    """Req 5.9, 5.1, 5.3: `agents, '{lead: y}'` removes the `coder` directive entry from
    troupe.yml and changes `lead`; coder.yaml stays byte-identical."""
    theatre = make_theatre(AGENTS_ROOT, files={CODER_FILE: "model: y\n"})
    before = snapshot_tree(theatre)

    result = run_set(theatre, "agents", "{lead: y}")

    _assert_success(result)
    _assert_changed(theatre, before, {ROOT: "agents:\n  lead: y\n"})
