"""``troupe config set`` writes at anchors, end to end (config-aliases task 4.2).

Every set runs the real ``troupe`` console script on a real theatre and checks the whole result:
exit 0 with empty stdout and stderr, the changed file byte for byte (only the requested change, the
``&name`` property and every ``*name`` alias and ``<<`` entry kept as written), every other file of
the theatre unchanged, ``get`` at the anchor and at each place that uses it, and the history events
the set recorded: one ``set`` or ``remove`` event per changed leaf path, at the anchor and at every
alias place and merge use, all in one change with cli lineage (Requirements 4.1-4.5, 9.1, 9.3,
10.1, 10.2).

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

import pytest

from set_support import assert_file_bytes, run_config, run_set, snapshot_tree
from support import MakeTheatre

ACTOR: Final = {"kind": "agent", "id": "writer"}
"""The actor every set in this file runs as (``TROUPE_ACTOR=agent:writer``)."""

OTHER_FILES: Final = {"notes/readme.md": "# not configuration\n", ".troupe/spare.yml": "x: 1\n"}
"""Files of the theatre that no set here may touch."""

Event = tuple[str, str, Any, Any]
"""One recorded event as ``(op, path, old, new)``."""


@dataclass(frozen=True)
class AnchorWrite:
    """One set at an anchor: the file before, the set, the file after, reads and events."""

    before: str
    path: str
    value: str
    after: str
    events: list[Event]
    reads: dict[str, str] = field(default_factory=dict)
    """``get PATH`` -> its exact stdout after the set."""


def _history(theatre: Path) -> list[dict[str, Any]]:
    result = run_config("history", "--theatre", str(theatre), cwd=theatre)
    assert (result.exit_code, result.stderr) == (0, ""), result.stderr
    return [json.loads(line) for line in result.stdout.decode("utf-8").splitlines()]


def _get(theatre: Path, path: str) -> str:
    result = run_config("get", path, "--theatre", str(theatre), cwd=theatre)
    assert (result.exit_code, result.stderr) == (0, ""), result.stderr
    return result.stdout.decode("utf-8")


def _assert_write(
    make_theatre: MakeTheatre,
    monkeypatch: pytest.MonkeyPatch,
    case: AnchorWrite,
    *,
    config_name: str = "troupe.yml",
) -> None:
    """Import, run the set, then assert the bytes, the untouched files, the reads and the events."""
    theatre = make_theatre(case.before, config_name=config_name, files=OTHER_FILES)
    imported = _history(theatre)
    assert {line["op"] for line in imported} == {"import"}
    before = snapshot_tree(theatre)
    monkeypatch.setenv("TROUPE_ACTOR", "agent:writer")

    result = run_set(theatre, case.path, case.value)

    assert (result.exit_code, result.stdout, result.stderr) == (0, b"", ""), result.stderr
    config = f".troupe/{config_name}"
    assert_file_bytes(theatre / config, case.after)
    after = snapshot_tree(theatre)
    assert after.keys() == before.keys()
    assert {name: data for name, data in after.items() if name != config} == {
        name: data for name, data in before.items() if name != config
    }
    for path, printed in case.reads.items():
        assert _get(theatre, path) == printed, path
    _assert_own_events(_history(theatre), imported, case.events)


def _assert_own_events(
    lines: list[dict[str, Any]], imported: list[dict[str, Any]], expected: list[Event]
) -> None:
    """The set appended exactly ``expected``, in one change of its own, with cli lineage."""
    assert lines[: len(imported)] == imported
    own = lines[len(imported) :]
    assert [(line["op"], line["path"], line["old"], line["new"]) for line in own] == expected
    assert [(line["actor"], line["via"], line["reason"]) for line in own] == [
        (ACTOR, "cli", None)
    ] * len(expected)
    assert len({line["change"] for line in own}) == 1
    assert own[0]["change"] not in {line["change"] for line in imported}


ISSUE_2: Final = "defaults: &d\n  model: opus\ncoder:\n  settings: *d\nreviewer:\n  settings: *d\n"
"""The reporter's file of tracker issue #2."""


# ---- a changed value at the anchor (4.1, 4.5) ----


@pytest.mark.parametrize("config_name", ["troupe.yml", "troupe.yaml"])
def test_changed_anchored_scalar_keeps_the_anchor_and_reaches_the_alias(
    make_theatre: MakeTheatre, monkeypatch: pytest.MonkeyPatch, config_name: str
) -> None:
    """Req 4.1, 4.5, 10.1, 10.2: `set name bar` on `name: &n foo` replaces only `foo` after `&n`,
    leaves `other: *n` as written, `get other` prints `bar`, and history records `set` at `name`
    and at `other`."""
    case = AnchorWrite(
        before="# names\nname: &n foo  # shared\n\nother: *n\nkept: 'q'\n",
        path="name",
        value="bar",
        after="# names\nname: &n bar  # shared\n\nother: *n\nkept: 'q'\n",
        events=[("set", "name", "foo", "bar"), ("set", "other", "foo", "bar")],
        reads={"name": "bar\n", "other": "bar\n"},
    )
    _assert_write(make_theatre, monkeypatch, case, config_name=config_name)


@pytest.mark.parametrize("config_name", ["troupe.yml", "troupe.yaml"])
def test_changed_value_in_an_anchored_mapping_reaches_every_alias(
    make_theatre: MakeTheatre, monkeypatch: pytest.MonkeyPatch, config_name: str
) -> None:
    """Req 4.1, 4.5, 10.1, 10.2: on the issue #2 file, `set defaults.model sonnet` changes only
    `opus` to `sonnet` at the anchor; both aliases read `sonnet`; history records `set` at
    `defaults.model`, `coder.settings.model` and `reviewer.settings.model`."""
    case = AnchorWrite(
        before=ISSUE_2,
        path="defaults.model",
        value="sonnet",
        after=ISSUE_2.replace("opus", "sonnet"),
        events=[
            ("set", "defaults.model", "opus", "sonnet"),
            ("set", "coder.settings.model", "opus", "sonnet"),
            ("set", "reviewer.settings.model", "opus", "sonnet"),
        ],
        reads={"coder.settings.model": "sonnet\n", "reviewer.settings.model": "sonnet\n"},
    )
    _assert_write(make_theatre, monkeypatch, case, config_name=config_name)


def test_changed_anchored_value_in_an_included_file_changes_only_that_file(
    make_theatre: MakeTheatre, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Req 4.1, 4.5, 10.1: an anchor written in an included file is changed in that file only;
    the root file and every other file are unchanged; both paths record a `set`."""
    root = "name: demo\ninc: file:inc.yml\n"
    theatre = make_theatre(
        root, files={**OTHER_FILES, ".troupe/inc.yml": "d: &d\n  m: 1  # c\nu: *d\n"}
    )
    imported = _history(theatre)
    before = snapshot_tree(theatre)
    monkeypatch.setenv("TROUPE_ACTOR", "agent:writer")

    result = run_set(theatre, "inc.d.m", "2")

    assert (result.exit_code, result.stdout, result.stderr) == (0, b"", ""), result.stderr
    assert_file_bytes(theatre / ".troupe/inc.yml", "d: &d\n  m: 2  # c\nu: *d\n")
    after = snapshot_tree(theatre)
    del before[".troupe/inc.yml"], after[".troupe/inc.yml"]
    assert after == before
    assert _get(theatre, "inc.u.m") == "2\n"
    _assert_own_events(
        _history(theatre), imported, [("set", "inc.d.m", 1, 2), ("set", "inc.u.m", 1, 2)]
    )


# ---- new keys and list items inside an anchored node (4.2, 4.5) ----

MERGED: Final = (
    "defaults: &d\n"
    "  model: opus\n"
    "coder:\n"
    "  settings: *d\n"
    "merged:\n"
    "  <<: *d\n"
    "  own: 1\n"
    "shadowing:\n"
    "  <<: *d\n"
    "  temperature: 0.9\n"
)
"""An anchored mapping used by an alias, by a merge key, and by a merge key whose mapping writes
``temperature`` itself."""


def test_new_key_in_an_anchored_mapping_is_seen_at_each_alias_and_merge_use(
    make_theatre: MakeTheatre, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Req 4.2, 4.5, 9.1, 10.1, 10.2: `set defaults.temperature 0.3` adds the key inside the
    anchored node only; the alias and the `<<: *d` mapping see it, the mapping whose own
    `temperature` shadows it does not; `set` events at the anchor, the alias place and the merge
    use only; every `<<` line unchanged."""
    case = AnchorWrite(
        before=MERGED,
        path="defaults.temperature",
        value="0.3",
        after=MERGED.replace("  model: opus\n", "  model: opus\n  temperature: 0.3\n"),
        events=[
            ("set", "defaults.temperature", None, 0.3),
            ("set", "coder.settings.temperature", None, 0.3),
            ("set", "merged.temperature", None, 0.3),
        ],
        reads={
            "coder.settings.temperature": "0.3\n",
            "merged.temperature": "0.3\n",
            "shadowing.temperature": "0.9\n",
        },
    )
    _assert_write(make_theatre, monkeypatch, case)


def test_list_append_in_an_anchored_list_is_seen_at_each_alias(
    make_theatre: MakeTheatre, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Req 4.2, 4.5, 10.1, 10.2: `set tools.1 grep` appends inside the anchored list only; both
    aliases see the item; `set` events at `tools.1` and at each alias place."""
    before = "tools: &t\n  - ls  # first\nlead:\n  tools: *t\nhelper:\n  tools: *t\n"
    case = AnchorWrite(
        before=before,
        path="tools.1",
        value="grep",
        after=before.replace("# first\n", "# first\n  - grep\n"),
        events=[
            ("set", "tools.1", None, "grep"),
            ("set", "lead.tools.1", None, "grep"),
            ("set", "helper.tools.1", None, "grep"),
        ],
        reads={"lead.tools.1": "grep\n", "helper.tools.1": "grep\n"},
    )
    _assert_write(make_theatre, monkeypatch, case)


MERGED_LIST: Final = (
    "defaults: &d\n"
    "  tools:\n"
    "    - ls\n"
    "coder:\n"
    "  settings: *d\n"
    "merged:\n"
    "  <<: *d\n"
    "  own: 1\n"
    "shadowing:\n"
    "  <<: *d\n"
    "  tools:\n"
    "    - cat\n"
)
"""An anchored mapping holding a block list, used by an alias, by a merge key, and by a merge key
whose mapping writes ``tools`` itself."""


def test_list_append_in_an_anchored_mapping_is_seen_at_each_alias_and_merge_use(
    make_theatre: MakeTheatre, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Req 4.2, 4.5, 9.1, 10.1, 10.2: `set defaults.tools.1 grep` inserts one `- grep` line in the
    anchored node's list only; the alias and the `<<: *d` mapping see it, the mapping whose own
    `tools` shadows the merged one does not; `set` events at the anchor, the alias place and the
    merge use only; every `*d` and `<<` line unchanged."""
    case = AnchorWrite(
        before=MERGED_LIST,
        path="defaults.tools.1",
        value="grep",
        after=MERGED_LIST.replace("    - ls\n", "    - ls\n    - grep\n"),
        events=[
            ("set", "defaults.tools.1", None, "grep"),
            ("set", "coder.settings.tools.1", None, "grep"),
            ("set", "merged.tools.1", None, "grep"),
        ],
        reads={
            "coder.settings.tools.1": "grep\n",
            "merged.tools.1": "grep\n",
            "shadowing.tools.0": "cat\n",
        },
    )
    _assert_write(make_theatre, monkeypatch, case)


# ---- replacing the anchored node itself (4.3, 4.5) ----

REPLACEMENTS: Final[dict[str, AnchorWrite]] = {
    "mapping-by-mapping": AnchorWrite(
        before=MERGED,
        path="defaults",
        value="{model: x}",
        after=MERGED.replace("model: opus", "model: x"),
        events=[
            ("set", "defaults.model", "opus", "x"),
            ("set", "coder.settings.model", "opus", "x"),
            ("set", "merged.model", "opus", "x"),
            ("set", "shadowing.model", "opus", "x"),
        ],
        reads={"coder.settings.model": "x\n", "merged.model": "x\n"},
    ),
    "scalar-by-scalar": AnchorWrite(
        before="n: &n foo\nother: *n\n",
        path="n",
        value="bar",
        after="n: &n bar\nother: *n\n",
        events=[("set", "n", "foo", "bar"), ("set", "other", "foo", "bar")],
        reads={"other": "bar\n"},
    ),
    "mapping-by-scalar": AnchorWrite(
        before="defaults: &d\n  model: opus\n  t: 1\ncoder:\n  settings: *d\n",
        path="defaults",
        value="5",
        after="defaults: &d 5\ncoder:\n  settings: *d\n",
        events=[
            ("set", "defaults", None, 5),
            ("set", "coder.settings", None, 5),
            ("remove", "defaults.model", "opus", None),
            ("remove", "defaults.t", 1, None),
            ("remove", "coder.settings.model", "opus", None),
            ("remove", "coder.settings.t", 1, None),
        ],
        reads={"coder.settings": "5\n"},
    ),
    "list-item-scalar-by-mapping": AnchorWrite(
        before="l:\n  - &d 1\nu: *d\n",
        path="l.0",
        value="{k: 1}",
        after="l:\n  - &d\n    k: 1\nu: *d\n",
        events=[
            ("set", "l.0.k", None, 1),
            ("set", "u.k", None, 1),
            ("remove", "l.0", 1, None),
            ("remove", "u", 1, None),
        ],
        reads={"l.0.k": "1\n", "u.k": "1\n"},
    ),
}


@pytest.mark.parametrize("case", list(REPLACEMENTS.values()), ids=list(REPLACEMENTS))
def test_replaced_anchored_node_keeps_its_anchor(
    make_theatre: MakeTheatre, monkeypatch: pytest.MonkeyPatch, case: AnchorWrite
) -> None:
    """Req 4.3, 4.5, 9.1, 10.1, 10.2: a VALUE replacing the anchored node itself (a mapping, a
    scalar, a mapping by a scalar, a block list item by a mapping on the lines after `- &d`) keeps
    `&d` on the new value, every alias and `<<` entry unchanged and seeing the new value, with one
    event per changed leaf path at the anchor and at every alias place and merge use."""
    _assert_write(make_theatre, monkeypatch, case)


def test_replacement_containing_an_anchored_node_changes_it_in_place(
    make_theatre: MakeTheatre, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Req 4.4, 4.3, 4.5, 10.1, 10.2: `set shared '{defaults: {model: x}, other: 1}'` where
    `shared.defaults: &d` changes only `opus` inside the anchored node, keeps `&d` and the alias,
    and records `set` at `shared.defaults.model` and at the alias place `coder.model`."""
    before = "shared:\n  defaults: &d\n    model: opus  # m\n  other: 1\ncoder: *d\n"
    case = AnchorWrite(
        before=before,
        path="shared",
        value="{defaults: {model: x}, other: 1}",
        after="shared:\n  defaults: &d\n    model: x     # m\n  other: 1\ncoder: *d\n",
        events=[("set", "shared.defaults.model", "opus", "x"), ("set", "coder.model", "opus", "x")],
        reads={"coder.model": "x\n"},
    )
    _assert_write(make_theatre, monkeypatch, case)


# ---- own keys of a mapping with a merge key (9.1, 9.3) ----

BASE: Final = "base: &b\n  a: 1\n  b: 2\n"

OWN_KEY_WRITES: Final[dict[str, AnchorWrite]] = {
    "changed-own-key": AnchorWrite(
        before=BASE + "m:\n  <<: *b\n  own: 1  # c\n",
        path="m.own",
        value="2",
        after=BASE + "m:\n  <<: *b\n  own: 2  # c\n",
        events=[("set", "m.own", 1, 2)],
        reads={"m.a": "1\n"},
    ),
    "changed-own-key-shadowing-a-merged-key": AnchorWrite(
        before=BASE + "m:\n  <<: *b\n  a: 9\n",
        path="m.a",
        value="3",
        after=BASE + "m:\n  <<: *b\n  a: 3\n",
        events=[("set", "m.a", 9, 3)],
        reads={"m.a": "3\n", "base.a": "1\n"},
    ),
    "new-own-key-after-the-merge-key": AnchorWrite(
        before=BASE + "m:\n  x: 0\n  <<: *b\nz: 0\n",
        path="m.new",
        value="2",
        after=BASE + "m:\n  x: 0\n  <<: *b\n  new: 2\nz: 0\n",
        events=[("set", "m.new", None, 2)],
        reads={"m.new": "2\n", "m.b": "2\n"},
    ),
}


@pytest.mark.parametrize("case", list(OWN_KEY_WRITES.values()), ids=list(OWN_KEY_WRITES))
def test_own_key_write_in_a_mapping_with_a_merge_key_leaves_the_merge_key(
    make_theatre: MakeTheatre, monkeypatch: pytest.MonkeyPatch, case: AnchorWrite
) -> None:
    """Req 9.3, 9.1, 10.1, 10.2: a changed own key or a new own key of a mapping with `<<` is
    written in that mapping under the usual rules (a new key after its last entry, `<<` included);
    the `<<` entry and the anchored source are unchanged; one `set` event at that path only."""
    _assert_write(make_theatre, monkeypatch, case)
