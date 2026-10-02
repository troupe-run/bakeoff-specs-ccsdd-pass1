"""Computing and recording the reconcile changes (2.1-2.8, 3.1-3.3, 5.5), on real theatres.

Each test imports a real theatre into a real store, rewrites the files on disk by hand, loads them
again and reconciles against the recorded configuration.
"""

import math
from collections.abc import Iterator
from pathlib import Path
from typing import Protocol

import pytest

from support import MakeTheatre
from troupe.config.constants import CONFIG_DIR_NAME, HISTORY_STORE_NAME
from troupe.config.errors import ConfigLoadError
from troupe.config.history import LeafChange, RemoveChange, SetChange
from troupe.config.loader import load_config
from troupe.config.locate import find_config_file
from troupe.config.model import ConfigDocument
from troupe.config.paths import ConfigPath
from troupe.config.reconcile import reconcile, reconcile_changes
from troupe.config.recorded import recorded_leaves
from troupe.config.resolve import flatten_leaves
from troupe.config.scalars import ScalarValue, scalar_equal
from troupe.config.store import HistoryStore


class Theatre:
    """A real theatre on disk with its imported history store."""

    def __init__(self, directory: Path, store: HistoryStore) -> None:
        self.directory = directory
        self.store = store

    def write(self, relative: str, content: str) -> None:
        (self.directory / CONFIG_DIR_NAME / relative).write_text(content, encoding="utf-8")

    def remove(self, relative: str) -> None:
        (self.directory / CONFIG_DIR_NAME / relative).unlink()

    def load(self) -> ConfigDocument:
        return load_config(find_config_file(self.directory))

    def changes(self) -> tuple[LeafChange, ...]:
        """The pure diff of the files on disk against the recorded configuration."""
        return reconcile_changes(self.load(), recorded_leaves(self.store.records()))


class MakeTheatreWithStore(Protocol):
    """Signature of the ``open_theatre`` fixture."""

    def __call__(self, config: str, files: dict[str, str] | None = None) -> Theatre: ...


@pytest.fixture
def open_theatre(make_theatre: MakeTheatre) -> Iterator[MakeTheatreWithStore]:
    stores: list[HistoryStore] = []

    def _open(config: str, files: dict[str, str] | None = None) -> Theatre:
        directory = make_theatre(
            config, files={f".troupe/{name}": text for name, text in (files or {}).items()}
        )
        store = HistoryStore.open(directory / CONFIG_DIR_NAME)
        stores.append(store)
        store.ensure_imported(flatten_leaves(load_config(find_config_file(directory)).root))
        return Theatre(directory, store)

    yield _open
    for store in stores:
        store.close()


def _set(path: str, old: ScalarValue, new: ScalarValue) -> SetChange:
    return SetChange(path=ConfigPath.parse(path), old=old, new=new)


def _remove(path: str, old: ScalarValue) -> RemoveChange:
    return RemoveChange(path=ConfigPath.parse(path), old=old)


def _assert_recorded_equals_files(theatre: Theatre) -> None:
    files = [(str(leaf.path), leaf.value) for leaf in flatten_leaves(theatre.load().root)]
    recorded = {leaf.path: leaf.value for leaf in recorded_leaves(theatre.store.records())}
    assert sorted(recorded) == sorted(path for path, _ in files)
    for path, value in files:
        assert scalar_equal(recorded[path], value), path


def _user_files(config_dir: Path) -> dict[str, bytes]:
    return {
        path.name: path.read_bytes()
        for path in config_dir.iterdir()
        if not path.name.startswith(HISTORY_STORE_NAME)
    }


BASE = """\
name: demo
count: 3
limits:
  rate: 1.5
  burst: 10
tags:
  - a
  - b
"""


def test_changed_created_and_removed_leaves_in_order(open_theatre: MakeTheatreWithStore) -> None:
    """Req 2.1, 2.2, 2.3, 2.6: sets in document order, then removes in creation-seq order."""
    theatre = open_theatre(BASE)
    theatre.write(
        "troupe.yml",
        "zeta: new\nname: renamed\nlimits:\n  rate: 1.5\nextra: true\ntags:\n  - a\n",
    )

    assert theatre.changes() == (
        _set("zeta", None, "new"),
        _set("name", "demo", "renamed"),
        _set("extra", None, True),
        _remove("count", 3),
        _remove("limits.burst", 10),
        _remove("tags.1", "b"),
    )


@pytest.mark.parametrize(
    ("written", "expected"),
    [("1.0", 1.0), ("'1'", "1"), ("true", True), ("null", None)],
)
def test_type_change_is_a_set(
    open_theatre: MakeTheatreWithStore, written: str, expected: ScalarValue
) -> None:
    """Req 2.5: a value whose type changes (1 → 1.0, "1", true, null) is a set."""
    theatre = open_theatre("value: 1\n")
    theatre.write("troupe.yml", f"value: {written}\n")

    changes = theatre.changes()

    assert changes == (_set("value", 1, expected),)
    assert isinstance(changes[0], SetChange)
    assert type(changes[0].new) is type(expected)


def test_same_typed_value_written_differently_gives_nothing(
    open_theatre: MakeTheatreWithStore,
) -> None:
    """Req 2.4, 2.5: `16` rewritten as `0x10` is the same int, so nothing differs."""
    theatre = open_theatre("value: 16\n")
    theatre.write("troupe.yml", "value: 0x10\n")

    assert theatre.changes() == ()


def test_null_valued_leaf_set_and_null_leaf_kept(open_theatre: MakeTheatreWithStore) -> None:
    """Req 2.1, 2.4: a null leaf that stays null is unchanged; null → value is a set from null."""
    theatre = open_theatre("a: null\nb: ~\n")
    theatre.write("troupe.yml", "a:\nb: 2\n")

    assert theatre.changes() == (_set("b", None, 2),)


def test_nan_and_inf_unchanged_give_nothing(open_theatre: MakeTheatreWithStore) -> None:
    """Req 2.4, 2.5: nan equals nan and inf equals inf under the typed comparison."""
    theatre = open_theatre("a: .nan\nb: .inf\nc: -0.0\n")
    theatre.write("troupe.yml", "a: .NaN\nb: .Inf\nc: 0.0\n")

    changes = theatre.changes()

    assert changes == (_set("c", -0.0, 0.0),)
    assert isinstance(changes[0], SetChange)
    assert isinstance(changes[0].old, float)
    assert math.copysign(1.0, changes[0].old) == -1.0


@pytest.mark.parametrize(
    "rewritten",
    [
        pytest.param(
            "name:    demo\ncount: 3\nlimits: {rate: 1.5, burst: 10}\ntags: [a, b]\n",
            id="formatting",
        ),
        pytest.param(
            "# top\nname: demo  # the name\n\ncount: 3\nlimits:\n  # inner\n  rate: 1.5\n"
            "  burst: 10\ntags:\n  - a\n  - b\n# end\n",
            id="comments-and-blank-lines",
        ),
        pytest.param(
            "name: 'demo'\ncount: 3\nlimits:\n  \"rate\": 1.5\n  burst: 10\n"
            'tags:\n  - "a"\n  - b\n',
            id="quoting",
        ),
        pytest.param(
            "tags:\n  - a\n  - b\nlimits:\n  burst: 10\n  rate: 1.5\ncount: 3\nname: demo\n",
            id="key-order",
        ),
        pytest.param(BASE.replace("\n", "\r\n"), id="line-endings"),
    ],
)
def test_format_only_rewrites_give_nothing(
    open_theatre: MakeTheatreWithStore, rewritten: str
) -> None:
    """Req 2.4: formatting, comment, quoting, key-order, blank-line, line-ending edits: no event."""
    theatre = open_theatre(BASE)
    theatre.write("troupe.yml", rewritten)

    assert theatre.changes() == ()
    assert reconcile(theatre.load(), theatre.store) == ()


def test_inline_versus_include_rewrite_gives_nothing(open_theatre: MakeTheatreWithStore) -> None:
    """Req 2.4: moving a value between inline and a YAML include records nothing, both ways."""
    theatre = open_theatre("a: {b: 1}\n")
    theatre.write("troupe.yml", "a: file:a.yml\n")
    theatre.write("a.yml", "b: 1\n")

    assert theatre.changes() == ()

    theatre.write("troupe.yml", "a:\n  b: 1\n")
    assert theatre.changes() == ()


@pytest.mark.parametrize(("before", "after"), [("{}", "[]"), ("[]", "{}")])
def test_empty_mapping_versus_empty_list_gives_nothing(
    open_theatre: MakeTheatreWithStore, before: str, after: str
) -> None:
    """Req 2.4: empty collections contribute no leaf, so `{}` ↔ `[]` records nothing."""
    theatre = open_theatre(f"a: {before}\nb: 1\n")
    theatre.write("troupe.yml", f"a: {after}\nb: 1\n")

    assert theatre.changes() == ()


def test_text_include_content_change_is_one_set(open_theatre: MakeTheatreWithStore) -> None:
    """Req 2.8: a text include's new content is one set on the including leaf."""
    theatre = open_theatre(
        "prompt: file:prompt.md\nother: 1\n", files={"prompt.md": "Hello\nworld\n"}
    )
    theatre.write("prompt.md", "Hello\nthere\n")

    assert theatre.changes() == (_set("prompt", "Hello\nworld\n", "Hello\nthere\n"),)


def test_hand_added_text_include_is_one_set(open_theatre: MakeTheatreWithStore) -> None:
    """Req 3.2: a hand-added text include records the included file's exact text."""
    theatre = open_theatre("prompt: inline\n")
    theatre.write("prompt.md", "from file\n")
    theatre.write("troupe.yml", "prompt: file:prompt.md\n")

    assert theatre.changes() == (_set("prompt", "inline", "from file\n"),)


def test_hand_added_yaml_include_gives_a_set_per_contributed_leaf(
    open_theatre: MakeTheatreWithStore,
) -> None:
    """Req 3.1, 2.2: each leaf of a hand-added YAML include that differs is a set."""
    theatre = open_theatre("name: demo\n")
    theatre.write("agents.yml", "lead: alice\nteam:\n  - bob\n  - carol\nnote: file:note.txt\n")
    theatre.write("note.txt", "hi")
    theatre.write("troupe.yml", "name: demo\nagents: file:agents.yml\n")

    assert theatre.changes() == (
        _set("agents.lead", None, "alice"),
        _set("agents.team.0", None, "bob"),
        _set("agents.team.1", None, "carol"),
        _set("agents.note", None, "hi"),
    )


def test_hand_added_yaml_include_only_sets_differing_leaves(
    open_theatre: MakeTheatreWithStore,
) -> None:
    """Req 3.1: leaves an include contributes with unchanged values record nothing."""
    theatre = open_theatre("agents:\n  lead: alice\n  size: 2\n")
    theatre.write("agents.yml", "lead: alice\nsize: 3\n")
    theatre.write("troupe.yml", "agents: file:agents.yml\n")

    assert theatre.changes() == (_set("agents.size", 2, 3),)


def test_removed_include_gives_a_remove_per_vanished_leaf(
    open_theatre: MakeTheatreWithStore,
) -> None:
    """Req 3.3, 2.3, 2.6: removing an include removes each leaf it contributed, oldest first."""
    theatre = open_theatre(
        "name: demo\nagents: file:agents.yml\n",
        files={"agents.yml": "lead: alice\nteam:\n  - bob\n"},
    )
    theatre.write("troupe.yml", "name: demo\n")
    theatre.remove("agents.yml")

    assert theatre.changes() == (
        _remove("agents.lead", "alice"),
        _remove("agents.team.0", "bob"),
    )


def test_removes_follow_creation_seq_not_document_order(
    open_theatre: MakeTheatreWithStore,
) -> None:
    """Req 2.6: a leaf re-created by a later set is removed after the imported leaves."""
    theatre = open_theatre("a: 1\nb: 2\nc: 3\n")
    # Recreate `a` after the import: removed, then set again (created_seq now newest).
    theatre.store.record_changes([_remove("a", 1)])
    theatre.store.record_changes([_set("a", None, 1)])
    theatre.write("troupe.yml", "z: 0\n")

    assert theatre.changes() == (
        _set("z", None, 0),
        _remove("b", 2),
        _remove("c", 3),
        _remove("a", 1),
    )


def test_reconcile_records_one_consecutive_batch_and_returns_it(
    open_theatre: MakeTheatreWithStore,
) -> None:
    """Req 2.6, 2.7: the changes are recorded together, in order, with consecutive seq numbers."""
    theatre = open_theatre(BASE)
    before = theatre.store.records()
    theatre.write("troupe.yml", "name: renamed\ncount: 3\nnew: x\n")

    changes = reconcile(theatre.load(), theatre.store)

    assert changes == (
        _set("name", "demo", "renamed"),
        _set("new", None, "x"),
        _remove("limits.rate", 1.5),
        _remove("limits.burst", 10),
        _remove("tags.0", "a"),
        _remove("tags.1", "b"),
    )
    added = theatre.store.records()[len(before) :]
    last = before[-1].seq
    assert [record.seq for record in added] == list(range(last + 1, last + 1 + len(changes)))
    assert [(record.op, record.path, record.old, record.new) for record in added] == [
        ("set", "name", "demo", "renamed"),
        ("set", "new", None, "x"),
        ("remove", "limits.rate", 1.5, None),
        ("remove", "limits.burst", 10, None),
        ("remove", "tags.0", "a", None),
        ("remove", "tags.1", "b", None),
    ]


def test_after_reconcile_recorded_equals_files_and_second_reconcile_records_nothing(
    open_theatre: MakeTheatreWithStore,
) -> None:
    """Req 2.1-2.3, 5.5: reconcile makes the recorded configuration equal the files; idempotent."""
    theatre = open_theatre(BASE, files={"p.txt": "one"})
    theatre.write("troupe.yml", "name: 1.0\nprompt: file:p.txt\nlimits: {rate: .nan}\nx: null\n")
    reconcile(theatre.load(), theatre.store)

    _assert_recorded_equals_files(theatre)
    count = len(theatre.store.records())
    assert reconcile(theatre.load(), theatre.store) == ()
    assert len(theatre.store.records()) == count


def test_net_difference_of_several_edits(open_theatre: MakeTheatreWithStore) -> None:
    """Req 5.5: several edits reconciled together record only the net difference."""
    theatre = open_theatre("a: 1\nb: 2\n")
    theatre.write("troupe.yml", "a: 5\nb: 2\n")
    theatre.write("troupe.yml", "a: 1\nb: 3\n")

    assert reconcile(theatre.load(), theatre.store) == (_set("b", 2, 3),)
    _assert_recorded_equals_files(theatre)


def test_empty_diff_records_nothing(open_theatre: MakeTheatreWithStore) -> None:
    """Req 2.4: an unchanged theatre records no event."""
    theatre = open_theatre(BASE)
    before = theatre.store.records()

    assert reconcile(theatre.load(), theatre.store) == ()
    assert theatre.store.records() == before


def test_reconcile_never_writes_user_files(open_theatre: MakeTheatreWithStore) -> None:
    """Req 4.9: reconcile never writes user files; they are byte-identical after it."""
    theatre = open_theatre("a: 1\nb: file:b.yml\n", files={"b.yml": "c: 2\n"})
    theatre.write("b.yml", "c: 3  # edited\n")
    config_dir = theatre.directory / CONFIG_DIR_NAME
    snapshot = _user_files(config_dir)

    reconcile(theatre.load(), theatre.store)

    assert _user_files(config_dir) == snapshot


def test_store_failure_is_a_load_error_naming_the_store(
    open_theatre: MakeTheatreWithStore,
) -> None:
    """Req 2.7 (design: store failures raise ConfigLoadError naming the store), nothing recorded."""
    theatre = open_theatre("a: 1\n")
    theatre.write("troupe.yml", "a: 2\n")
    document = theatre.load()
    theatre.store.close()

    with pytest.raises(ConfigLoadError) as raised:
        reconcile(document, theatre.store)

    assert raised.value.file == theatre.store.location


def test_removing_leaves_under_unusual_keys_round_trips_their_paths(
    open_theatre: MakeTheatreWithStore,
) -> None:
    """Req 2.3: leaves under an empty key or a key with the separator are removed by their path."""
    theatre = open_theatre('"": 1\n"a.": 2\nb: 3\n')
    theatre.write("troupe.yml", "b: 3\n")

    changes = reconcile(theatre.load(), theatre.store)

    assert [(str(change.path), change.old) for change in changes] == [("", 1), ("a.", 2)]
    assert all(isinstance(change, RemoveChange) for change in changes)
    _assert_recorded_equals_files(theatre)
