"""Unit tests of the intended configuration of a set at an anchor (config-aliases task 3.2).

A change at an anchored node is written once, at the anchor. Rebuilding the configuration from
written locations replicates it to every alias place (an alias place carries the anchored node's
origins) and, by re-applying merge keys, to every mapping that merges the anchored node, unless
that mapping writes the key itself. ``with_other_places`` therefore widens the intended
configuration to every such place.

Each case loads a real theatre configuration from disk, resolves PATH with the real
TargetResolver for its intended configuration along PATH, records the planned change at the
anchor's written location by hand (the planner learns anchors in later tasks) and widens it.

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

from collections.abc import Callable, Mapping
from pathlib import Path

import pytest

from troupe.config.loader import load_config
from troupe.config.model import ConfigDocument, ConfigMapping, ConfigNode, ConfigSequence
from troupe.config.paths import ConfigPath
from troupe.config.writeback.compare import (
    IncludeDirective,
    PlainMapping,
    PlainValue,
    node_to_plain,
)
from troupe.config.writeback.shared import WrittenChanges, rebuilt_plain, with_other_places
from troupe.config.writeback.target import resolve_target
from troupe.config.writeback.value import NewValue, parse_value

ROOT = "troupe.yml"
SHARED = (
    "defaults: &d\n"
    "  model: opus\n"
    "  t: 1\n"
    "coder:\n"
    "  settings: *d\n"
    "reviewer:\n"
    "  <<: *d\n"
    "  t: 2\n"
    "plain:\n"
    "  <<: *d\n"
)
"""`defaults` is anchored as `&d`, used by a plain alias (`coder.settings`), by a merge key with a
shadowing own key `t` (`reviewer`) and by a merge key without one (`plain`)."""


def _loaded(tmp_path: Path, files: Mapping[str, str]) -> ConfigDocument:
    for name, text in files.items():
        (tmp_path / name).write_bytes(text.encode("utf-8"))
    return load_config(tmp_path / ROOT)


def _node(document: ConfigDocument, path: str) -> ConfigNode:
    node: ConfigNode = document.root
    for segment in path.split("."):
        if isinstance(node, ConfigSequence):
            node = node.items[int(segment)]
            continue
        assert isinstance(node, ConfigMapping)
        entry = node.get(segment)
        assert entry is not None
        node = entry.value
    return node


def _mapping(document: ConfigDocument, path: str) -> ConfigMapping:
    node = _node(document, path)
    assert isinstance(node, ConfigMapping)
    return node


def _new(text: str) -> NewValue:
    return parse_value(text, ConfigPath.parse("x"))


def _widen(
    document: ConfigDocument, path: str, value: str, record: Callable[[WrittenChanges], None]
) -> PlainMapping:
    """The intended configuration of setting ``path`` to ``value`` whose planned changes
    ``record`` adds, widened to every other place that reaches them."""
    config_path = ConfigPath.parse(path)
    request = resolve_target(document, config_path, parse_value(value, config_path))
    changes = WrittenChanges(path=config_path)
    record(changes)
    return with_other_places(document, request.intended, config_path, changes)


def _ordered(plain: PlainValue) -> object:
    """``plain`` with key order made part of equality."""
    if isinstance(plain, IncludeDirective):
        return ("directive", plain.text, _ordered(plain.content))
    if isinstance(plain, dict):
        return [(key, _ordered(value)) for key, value in plain.items()]
    if isinstance(plain, list):
        return ("list", [_ordered(item) for item in plain])
    return (type(plain), plain)


def test_changed_value_at_an_anchor_appears_at_every_alias_and_merge(tmp_path: Path) -> None:
    """Req 4.1, 10.2: `defaults.model` changed at `&d` is intended at the anchor, at the plain
    alias `coder.settings` and in both mappings that merge `*d`."""
    document = _loaded(tmp_path, {ROOT: SHARED})
    defaults = _mapping(document, "defaults")
    intended = _widen(
        document,
        "defaults.model",
        "sonnet",
        lambda changes: changes.replace(defaults, "model", _new("sonnet")),
    )
    assert _ordered(intended) == _ordered(
        {
            "defaults": {"model": "sonnet", "t": 1},
            "coder": {"settings": {"model": "sonnet", "t": 1}},
            "reviewer": {"model": "sonnet", "t": 2},
            "plain": {"model": "sonnet", "t": 1},
        }
    )


def test_changed_value_shadowed_by_an_own_key_keeps_the_own_key(tmp_path: Path) -> None:
    """Req 4.1, 10.2: `defaults.t` changed at `&d` reaches the alias and the merge without an own
    `t`; `reviewer` writes `t: 2` itself, so its `t` stays 2."""
    document = _loaded(tmp_path, {ROOT: SHARED})
    defaults = _mapping(document, "defaults")
    intended = _widen(
        document, "defaults.t", "5", lambda changes: changes.replace(defaults, "t", _new("5"))
    )
    assert _ordered(intended) == _ordered(
        {
            "defaults": {"model": "opus", "t": 5},
            "coder": {"settings": {"model": "opus", "t": 5}},
            "reviewer": {"model": "opus", "t": 2},
            "plain": {"model": "opus", "t": 5},
        }
    )


def test_added_key_at_an_anchor_appears_in_merged_order(tmp_path: Path) -> None:
    """Req 4.2, 10.2: a key added inside `&d` is intended at the alias and in both merging
    mappings, in merged order: at the place of the `<<` entry, before `reviewer`'s own `t`."""
    document = _loaded(tmp_path, {ROOT: SHARED})
    defaults = _mapping(document, "defaults")
    intended = _widen(
        document, "defaults.n", "3", lambda changes: changes.add(defaults, [("n", _new("3"))])
    )
    assert _ordered(intended) == _ordered(
        {
            "defaults": {"model": "opus", "t": 1, "n": 3},
            "coder": {"settings": {"model": "opus", "t": 1, "n": 3}},
            "reviewer": {"model": "opus", "n": 3, "t": 2},
            "plain": {"model": "opus", "t": 1, "n": 3},
        }
    )


def test_removed_key_at_an_anchor_is_removed_at_every_alias_and_merge(tmp_path: Path) -> None:
    """Req 4.4, 10.2: replacing `defaults` by `{t: 1}` removes `model` inside `&d`; it is gone at
    the alias and from both merging mappings."""
    document = _loaded(tmp_path, {ROOT: SHARED})
    defaults = _mapping(document, "defaults")
    intended = _widen(
        document, "defaults", "{t: 1}", lambda changes: changes.remove(defaults, "model")
    )
    assert _ordered(intended) == _ordered(
        {
            "defaults": {"t": 1},
            "coder": {"settings": {"t": 1}},
            "reviewer": {"t": 2},
            "plain": {"t": 1},
        }
    )


def test_removed_key_shadowed_by_an_own_key_keeps_the_own_key(tmp_path: Path) -> None:
    """Req 4.4, 10.2: removing `t` inside `&d` removes it at the alias and from `plain`;
    `reviewer` keeps its own `t: 2`."""
    document = _loaded(tmp_path, {ROOT: SHARED})
    defaults = _mapping(document, "defaults")
    intended = _widen(
        document, "defaults", "{model: opus}", lambda changes: changes.remove(defaults, "t")
    )
    assert _ordered(intended) == _ordered(
        {
            "defaults": {"model": "opus"},
            "coder": {"settings": {"model": "opus"}},
            "reviewer": {"model": "opus", "t": 2},
            "plain": {"model": "opus"},
        }
    )


def test_appended_item_of_an_anchored_list_appears_at_every_alias(tmp_path: Path) -> None:
    """Req 4.2, 10.2: an item appended to the anchored list `&l` is intended at each alias."""
    files = {ROOT: "tools: &l\n  - a\ncoder:\n  tools: *l\nreviewer:\n  tools: *l\n"}
    document = _loaded(tmp_path, files)
    tools = _node(document, "tools")
    assert isinstance(tools, ConfigSequence)
    intended = _widen(
        document, "tools.1", "b", lambda changes: changes.add(tools, [(None, _new("b"))])
    )
    assert intended == {
        "tools": ["a", "b"],
        "coder": {"tools": ["a", "b"]},
        "reviewer": {"tools": ["a", "b"]},
    }


def test_change_reaches_merges_of_several_sources_and_nested_merges(tmp_path: Path) -> None:
    """Req 4.1, 10.2: a change at `&a` reaches a merge of `[*a, *b]` (where `*a` comes first) and
    a mapping merging `*b`, itself a mapping that merges `*a`; a key of `b` shadows `a`'s."""
    files = {
        ROOT: (
            "a: &a {k: 1, m: 1}\n"
            "b: &b\n"
            "  <<: *a\n"
            "  m: 2\n"
            "both:\n"
            "  <<: [*a, *b]\n"
            "nested:\n"
            "  <<: *b\n"
        )
    }
    document = _loaded(tmp_path, files)
    a = _mapping(document, "a")

    def record(changes: WrittenChanges) -> None:
        changes.replace(a, "k", _new("5"))
        changes.replace(a, "m", _new("6"))

    intended = _widen(document, "a", "{k: 5, m: 6}", record)
    assert _ordered(intended) == _ordered(
        {
            "a": {"k": 5, "m": 6},
            "b": {"k": 5, "m": 2},
            "both": {"k": 5, "m": 6},
            "nested": {"k": 5, "m": 2},
        }
    )


def test_change_at_an_anchor_in_an_included_file_reaches_its_merges(tmp_path: Path) -> None:
    """Req 4.1, 10.2: in an included file, a change at `&d` reaches the file's own `<<: *d`."""
    files = {
        ROOT: "agents: file:agents.yaml\n",
        "agents.yaml": "base: &d {model: opus}\ncoder:\n  <<: *d\n  t: 1\n",
    }
    document = _loaded(tmp_path, files)
    base = _mapping(document, "agents.base")
    intended = _widen(
        document,
        "agents.base.model",
        "sonnet",
        lambda changes: changes.replace(base, "model", _new("sonnet")),
    )
    agents = intended["agents"]
    assert isinstance(agents, IncludeDirective)
    assert agents.content == {"base": {"model": "sonnet"}, "coder": {"model": "sonnet", "t": 1}}


def test_own_key_of_a_merging_mapping_changes_only_there(tmp_path: Path) -> None:
    """Req 10.2: changing `reviewer`'s own `t` changes nothing at the anchor or elsewhere."""
    document = _loaded(tmp_path, {ROOT: SHARED})
    reviewer = _mapping(document, "reviewer")
    intended = _widen(
        document, "reviewer.t", "3", lambda changes: changes.replace(reviewer, "t", _new("3"))
    )
    assert _ordered(intended) == _ordered(
        {
            "defaults": {"model": "opus", "t": 1},
            "coder": {"settings": {"model": "opus", "t": 1}},
            "reviewer": {"model": "opus", "t": 3},
            "plain": {"model": "opus", "t": 1},
        }
    )


FAITHFUL_CASES = {
    "alias-and-merges": {ROOT: SHARED},
    "inline-merge": {ROOT: "a:\n  x: 0\n  <<: {k: 1, x: 9}\n  z: 2\n"},
    "several-sources": {ROOT: "a: &a {k: 1}\nb: &b {k: 2, m: 2}\nc:\n  <<: [*a, *b]\n"},
    "nested-merge": {ROOT: "a: &a {k: 1}\nb: &b {<<: *a, m: 1}\nc: {<<: *b, n: 1}\n"},
    "alias-of-include": {ROOT: "a: &i file:s.yaml\nb: *i\n", "s.yaml": "k: 1\n"},
    "merge-in-include": {ROOT: "a: file:s.yaml\n", "s.yaml": "d: &d {k: 1}\ne: {<<: *d}\n"},
}


@pytest.mark.parametrize("files", FAITHFUL_CASES.values(), ids=FAITHFUL_CASES.keys())
def test_unedited_recomputation_with_aliases_equals_the_loaded_plain_tree(
    tmp_path: Path, files: Mapping[str, str]
) -> None:
    """Req 10.2: with no change, the recomputation of a configuration with aliases and merge keys
    equals ``node_to_plain`` of the loaded root, in order."""
    document = _loaded(tmp_path, files)
    assert _ordered(rebuilt_plain(document.root, WrittenChanges())) == _ordered(
        node_to_plain(document.root)
    )
