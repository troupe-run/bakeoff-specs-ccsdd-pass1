"""Unit tests of the LeafDiff (task 4.2): ``troupe.config.writeback.leaf_diff.diff_leaves``.

Each case loads a real "before" configuration and a real "after" configuration from disk (two
separate directories) and checks the exact list of history changes derived from them.

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

import math
from collections.abc import Mapping
from pathlib import Path

from troupe.config.history import LeafChange, RemoveChange, SetChange
from troupe.config.loader import load_config
from troupe.config.model import ConfigDocument
from troupe.config.paths import ConfigPath
from troupe.config.scalars import ScalarValue
from troupe.config.writeback.leaf_diff import diff_leaves


def _load(directory: Path, config: str, files: Mapping[str, str] | None = None) -> ConfigDocument:
    directory.mkdir(parents=True, exist_ok=True)
    for name, text in (files or {}).items():
        (directory / name).write_bytes(text.encode("utf-8"))
    root = directory / "troupe.yml"
    root.write_bytes(config.encode("utf-8"))
    return load_config(root)


def _diff(
    tmp_path: Path,
    before: str,
    after: str,
    *,
    before_files: Mapping[str, str] | None = None,
    after_files: Mapping[str, str] | None = None,
) -> tuple[LeafChange, ...]:
    return diff_leaves(
        _load(tmp_path / "before", before, before_files),
        _load(tmp_path / "after", after, after_files),
    )


def _set(path: str, old: ScalarValue, new: ScalarValue) -> SetChange:
    return SetChange(ConfigPath.parse(path), old, new)


def _remove(path: str, old: ScalarValue) -> RemoveChange:
    return RemoveChange(ConfigPath.parse(path), old)


def _typed(changes: tuple[LeafChange, ...]) -> list[tuple[object, ...]]:
    """Changes with the type of every value, so ``1 == 1.0 == True`` cannot hide a mismatch."""
    result: list[tuple[object, ...]] = []
    for change in changes:
        if isinstance(change, SetChange):
            result.append(
                (
                    "set",
                    str(change.path),
                    type(change.old),
                    change.old,
                    type(change.new),
                    change.new,
                )
            )
        else:
            result.append(("remove", str(change.path), type(change.old), change.old))
    return result


def test_changed_leaf_gives_one_set_with_old_and_new(tmp_path: Path) -> None:
    """Req 15.1: a changed leaf is one set event with its on-disk old value; others are silent."""
    changes = _diff(tmp_path, "a: 1\nb: x\n", "a: 2\nb: x\n")
    assert changes == (_set("a", 1, 2),)


def test_created_leaf_has_old_null(tmp_path: Path) -> None:
    """Req 15.1: a created leaf is a set event with old null, including leaves of new
    intermediates."""
    changes = _diff(tmp_path, "a: 1\n", "a: 1\nb:\n  c: x\n  d: null\nl: [p]\n")
    assert changes == (_set("b.c", None, "x"), _set("b.d", None, None), _set("l.0", None, "p"))
    assert _typed(changes)[1] == ("set", "b.d", type(None), None, type(None), None)


def test_removed_leaves_give_remove_events_with_old(tmp_path: Path) -> None:
    """Req 15.2, 15.5: leaves that no longer exist are remove events (new null) in before order."""
    changes = _diff(
        tmp_path,
        "db:\n  host: a\n  port: 5432\n  user: x\nl: [a, b, c]\n",
        "db:\n  host: a\n  port: 5432\nl: [a]\n",
    )
    assert changes == (_remove("db.user", "x"), _remove("l.1", "b"), _remove("l.2", "c"))


def test_sets_in_new_order_then_removes_in_old_order(tmp_path: Path) -> None:
    """Req 15.5: set events in the new document order first, then remove events in the old order."""
    changes = _diff(
        tmp_path,
        "z: 1\ndb:\n  host: a\n  port: 5432\n  user: x\n  name: n\n",
        "z: 1\ndb:\n  host: b\n  port: 5432\n  pool: 3\nm: {k: v}\n",
    )
    assert changes == (
        _set("db.host", "a", "b"),
        _set("db.pool", None, 3),
        _set("m.k", None, "v"),
        _remove("db.user", "x"),
        _remove("db.name", "n"),
    )


def test_type_change_counts_as_changed(tmp_path: Path) -> None:
    """Req 15.3: `1` -> `"1"`, `1` -> `1.0`, `1` -> `true` and `0.0` -> `-0.0` are changes."""
    changes = _diff(
        tmp_path,
        "a: 1\nb: 1\nc: 1\nd: 0.0\n",
        "a: '1'\nb: 1.0\nc: true\nd: -0.0\n",
    )
    assert _typed(changes) == [
        ("set", "a", int, 1, str, "1"),
        ("set", "b", int, 1, float, 1.0),
        ("set", "c", int, 1, bool, True),
        ("set", "d", float, 0.0, float, -0.0),
    ]
    assert math.copysign(1.0, changes[3].new) == -1.0  # type: ignore[union-attr,arg-type]


def test_typed_equal_leaves_give_no_event(tmp_path: Path) -> None:
    """Req 15.3, 15.4: same typed values (incl. nan, differently spelled) produce no event."""
    changes = _diff(
        tmp_path,
        "a: .nan\nb: 0x1F\nc: true\nd: '1'\ne: ~\n",
        'a: .NaN\nb: 31\nc: True\nd: "1"\ne: null\n',
    )
    assert changes == ()


def test_no_changes_between_empty_mapping_and_empty_list(tmp_path: Path) -> None:
    """Req 15.4: `{}` -> `[]` changes no leaf, so nothing is recorded."""
    assert _diff(tmp_path, "a: {}\nb: 1\n", "a: []\nb: 1\n") == ()


def test_identical_documents_give_no_event(tmp_path: Path) -> None:
    """Req 15.4: a no-op produces no event."""
    config = "a: 1\nl:\n  - x\n  - {k: v}\n"
    assert _diff(tmp_path, config, config) == ()


def test_scalar_replaced_by_collection_and_back(tmp_path: Path) -> None:
    """Req 15.1, 15.2, 15.5: a scalar becoming a mapping creates its leaves and removes the
    scalar."""
    assert _diff(tmp_path, "a: 5\n", "a:\n  x: 1\n") == (_set("a.x", None, 1), _remove("a", 5))
    assert _diff(tmp_path / "back", "a:\n  x: 1\n", "a: 5\n") == (
        _set("a", None, 5),
        _remove("a.x", 1),
    )


def test_new_yaml_include_leaves_are_set_events(tmp_path: Path) -> None:
    """Req 15.7: the leaves of a newly embedded YAML include are created or changed leaves."""
    changes = _diff(
        tmp_path,
        "a:\n  x: 0\n  old: o\n",
        "a: file:inc.yaml\n",
        after_files={"inc.yaml": "x: 1\ny:\n  - 2\n"},
    )
    assert changes == (_set("a.x", 0, 1), _set("a.y.0", None, 2), _remove("a.old", "o"))


def test_new_text_include_is_one_string_leaf(tmp_path: Path) -> None:
    """Req 15.7: a newly written text include is one string leaf holding the target's text."""
    changes = _diff(
        tmp_path,
        "p: x\n",
        "p: x\nq: file:t.txt\n",
        after_files={"t.txt": "hello\nworld\n"},
    )
    assert changes == (_set("q", None, "hello\nworld\n"),)


def test_scalar_replaced_by_text_include_is_one_set(tmp_path: Path) -> None:
    """Req 15.1, 15.7: a plain string replaced by a text include changes one leaf."""
    changes = _diff(tmp_path, "p: old\n", "p: file:t.txt\n", after_files={"t.txt": "new\n"})
    assert changes == (_set("p", "old", "new\n"),)


def test_old_values_come_from_the_before_document(tmp_path: Path) -> None:
    """Req 15.1, 15.2: old values are those of the before document (as on disk, incl. hand
    edits)."""
    changes = _diff(tmp_path, "a: hand-edited\nb: gone\n", "a: new\n")
    assert changes == (_set("a", "hand-edited", "new"), _remove("b", "gone"))
