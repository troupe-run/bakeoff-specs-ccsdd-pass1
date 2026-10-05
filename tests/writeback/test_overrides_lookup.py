"""Unit tests of the written-mapping lookups below merged mappings (task 1.1):
``physical_entry``, ``physical_holders``, ``holder_of`` and ``written_parts`` in
``troupe.config.overrides``.

The loader does not recognise ``$ref`` yet (task 2.1), so each case loads real files from a
temporary theatre with the existing loader and builds the merged mappings with ``deep_merge``, as
the loader will do for ``{$ref: ..., <overrides>}``.

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

import dataclasses
from collections.abc import Mapping
from pathlib import Path

import pytest

from troupe.config.loader import load_config
from troupe.config.model import ConfigDocument, ConfigMapping, ConfigNode, ConfigScalar
from troupe.config.overrides import (
    Layer,
    deep_merge,
    holder_of,
    physical_entry,
    physical_holders,
    written_parts,
)

ROOT = "troupe.yml"

CODER = """\
model: small
params:
  temperature: 0.7
  top_p: 0.9
retries: 2
"""

CONFIG = """\
coder: file:coder.yaml
over:
  $ref: file:coder.yaml
  params:
    temperature: 0.3
    seed: 7
  extra: 1
second:
  retries: 5
  params:
    top_p: 0.5
"""


def _mapping(node: ConfigNode | None) -> ConfigMapping:
    assert isinstance(node, ConfigMapping)
    return node


def _value(mapping: ConfigMapping, key: str) -> ConfigNode:
    entry = mapping.get(key)
    assert entry is not None, key
    return entry.value


def _load(tmp_path: Path, files: Mapping[str, str]) -> ConfigDocument:
    for name, text in files.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    return load_config(tmp_path / ROOT)


def _parts(tmp_path: Path) -> tuple[ConfigMapping, ConfigMapping, ConfigMapping, ConfigMapping]:
    """Return ``(merged, base, over, second)``: the merge of coder.yaml with ``over``."""
    document = _load(tmp_path, {"coder.yaml": CODER, ROOT: CONFIG})
    coder = _mapping(_value(document.root, "coder"))
    assert coder.include is not None
    over = _mapping(_value(document.root, "over"))
    base = dataclasses.replace(coder, include=None)
    merged = deep_merge(base, over, include=dataclasses.replace(coder.include, form=over))
    return merged, base, over, _mapping(_value(document.root, "second"))


def test_physical_entry_of_an_override_only_key_is_in_the_override(tmp_path: Path) -> None:
    """Req 7.1: a key only in the overrides is written in the override mapping."""
    merged, _base, over, _second = _parts(tmp_path)
    assert physical_entry(merged, "extra") == (over, over.get("extra"))
    assert physical_entry(merged, "extra")[0] is over


def test_physical_entry_of_an_included_only_key_is_in_the_included_file(tmp_path: Path) -> None:
    """Req 7.2: a key that is not overridden is written in the included mapping."""
    merged, base, _over, _second = _parts(tmp_path)
    mapping, entry = physical_entry(merged, "model")
    assert mapping is base
    assert entry is base.get("model")
    assert entry.key_origin.file.real_path == (tmp_path / "coder.yaml").resolve()


def test_physical_entry_of_a_two_sided_key_is_the_override_first(tmp_path: Path) -> None:
    """Req 7.1, 7.2: a key on both sides is written (for the merged value) in the override."""
    merged, _base, over, _second = _parts(tmp_path)
    mapping, entry = physical_entry(merged, "params")
    assert mapping is over
    assert entry is over.get("params")


def test_physical_entry_inside_a_nested_merged_mapping(tmp_path: Path) -> None:
    """Req 7.1, 7.2: below a nested merged mapping each key resolves to its own written side."""
    merged, base, over, _second = _parts(tmp_path)
    params = _mapping(_value(merged, "params"))
    over_params = _mapping(_value(over, "params"))
    base_params = _mapping(_value(base, "params"))
    assert physical_entry(params, "temperature") == (
        over_params,
        over_params.get("temperature"),
    )
    assert physical_entry(params, "temperature")[0] is over_params
    assert physical_entry(params, "seed")[0] is over_params
    assert physical_entry(params, "top_p")[0] is base_params


def test_physical_entry_recurses_through_merged_layers(tmp_path: Path) -> None:
    """Req 7.1, 7.2: when the included side is itself merged, the lookup reaches its layers."""
    merged, base, over, second = _parts(tmp_path)
    outer = deep_merge(merged, second, include=None)
    assert physical_entry(outer, "retries")[0] is second
    assert physical_entry(outer, "extra")[0] is over
    assert physical_entry(outer, "model")[0] is base
    outer_params = _mapping(_value(outer, "params"))
    assert physical_entry(outer_params, "top_p")[0] is _value(second, "params")
    assert physical_entry(outer_params, "temperature")[0] is _value(over, "params")


def test_physical_entry_of_an_unmerged_mapping_is_the_mapping_itself(tmp_path: Path) -> None:
    """Req 7.2: an unmerged mapping is written as it is."""
    _merged, _base, _over, second = _parts(tmp_path)
    assert physical_entry(second, "retries") == (second, second.get("retries"))


def test_physical_entry_refuses_a_missing_key(tmp_path: Path) -> None:
    """Req 7.1: asking for a key the merged mapping does not hold is an error, never a guess."""
    merged, _base, _over, _second = _parts(tmp_path)
    with pytest.raises(KeyError):
        physical_entry(merged, "absent")
    with pytest.raises(KeyError):
        physical_entry(merged, "$ref")


def test_physical_holders_of_a_two_sided_key_are_override_then_included(tmp_path: Path) -> None:
    """Req 7.1, 7.2: every written mapping holding the key, the override side first."""
    merged, base, over, _second = _parts(tmp_path)
    holders = physical_holders(merged, "params")
    assert [holder.mapping for holder in holders] == [over, base]
    assert holders[0].mapping is over
    assert holders[1].mapping is base
    assert holders[0].entry is over.get("params")
    assert holders[1].entry is base.get("params")
    assert [step.layer for step in holders[0].chain] == [Layer.OVER]
    assert [step.layer for step in holders[1].chain] == [Layer.BASE]
    assert all(step.merged is merged for holder in holders for step in holder.chain)
    assert holders[0].is_override
    assert not holders[1].is_override


def test_physical_holders_of_one_sided_keys(tmp_path: Path) -> None:
    """Req 7.1, 7.2: an override-only key has one override holder, an included-only key one
    included holder, and an absent key none."""
    merged, base, over, _second = _parts(tmp_path)
    (extra,) = physical_holders(merged, "extra")
    assert extra.mapping is over
    assert extra.is_override
    (model,) = physical_holders(merged, "model")
    assert model.mapping is base
    assert not model.is_override
    assert physical_holders(merged, "absent") == ()
    assert physical_holders(merged, "$ref") == ()


def test_physical_holders_of_an_unmerged_mapping_have_an_empty_chain(tmp_path: Path) -> None:
    """Req 7.2: an unmerged mapping is its own single holder, reached by no layer."""
    _merged, _base, _over, second = _parts(tmp_path)
    (holder,) = physical_holders(second, "retries")
    assert holder.mapping is second
    assert holder.entry is second.get("retries")
    assert holder.chain == ()
    assert not holder.is_override


def test_physical_holders_follow_nested_layers_with_their_chain(tmp_path: Path) -> None:
    """Req 7.1, 7.2: through two merges, each holder records the layers that lead to it."""
    merged, base, over, second = _parts(tmp_path)
    outer = deep_merge(merged, second, include=None)
    holders = physical_holders(_mapping(_value(outer, "params")), "top_p")
    second_params = _value(second, "params")
    base_params = _value(base, "params")
    assert [holder.mapping for holder in holders] == [second_params, base_params]
    assert [step.layer for step in holders[0].chain] == [Layer.OVER]
    assert [step.layer for step in holders[1].chain] == [Layer.BASE, Layer.BASE]
    holders = physical_holders(_mapping(_value(outer, "params")), "temperature")
    assert [holder.mapping for holder in holders] == [_value(over, "params"), base_params]
    assert [step.layer for step in holders[0].chain] == [Layer.BASE, Layer.OVER]
    assert holders[0].is_override


def test_holder_of_finds_the_entry_holding_a_nested_override(tmp_path: Path) -> None:
    """Req 7.1 (Decision 4): the entry that holds a nested override mapping is found in the
    merged parent, so an emptied override can be pruned at its own entry."""
    merged, base, over, _second = _parts(tmp_path)
    params = _mapping(_value(merged, "params"))
    (seed,) = physical_holders(params, "seed")
    parent = holder_of(merged, "params", seed.mapping)
    assert parent is not None
    assert parent.mapping is over
    assert parent.entry is over.get("params")
    assert parent.is_override
    (top_p,) = physical_holders(params, "top_p")
    included = holder_of(merged, "params", top_p.mapping)
    assert included is not None
    assert included.mapping is base
    assert holder_of(merged, "model", seed.mapping) is None


def test_written_parts_of_a_merged_mapping_are_its_layers(tmp_path: Path) -> None:
    """Req 1.2: a merged mapping is written as its included side and its override side."""
    merged, base, over, _second = _parts(tmp_path)
    parts = written_parts(merged)
    assert len(parts) == 2
    assert parts[0] is base
    assert parts[1] is over


def test_written_parts_of_a_bare_mapping_form_include(tmp_path: Path) -> None:
    """Req 1.4: a bare mapping-form include is written as its ``$ref`` mapping and its content."""
    document = _load(
        tmp_path, {"coder.yaml": CODER, ROOT: "coder: file:coder.yaml\nform:\n  $ref: x\n"}
    )
    coder = _mapping(_value(document.root, "coder"))
    form = _mapping(_value(document.root, "form"))
    assert coder.include is not None
    bare = dataclasses.replace(coder, include=dataclasses.replace(coder.include, form=form))
    form_part, content = written_parts(bare)
    assert form_part is form
    assert content == coder
    assert isinstance(content, ConfigMapping)
    assert content.include is not None
    assert content.include.form is None


def test_written_parts_of_any_other_node_is_the_node(tmp_path: Path) -> None:
    """Req 1.4: scalar-form includes, unmerged mappings and scalars are written as they are."""
    _merged, base, _over, second = _parts(tmp_path)
    document = load_config(tmp_path / ROOT)
    coder = _value(document.root, "coder")
    retries = _value(second, "retries")
    assert isinstance(retries, ConfigScalar)
    for node in (coder, second, retries, base):
        (part,) = written_parts(node)
        assert part is node
