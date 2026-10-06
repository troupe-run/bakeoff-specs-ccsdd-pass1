"""Unit tests of the merge-key merge and the alias provenance fields of the model (task 1.1):
``troupe.config.merge_keys.merge_mapping`` and ``Anchor``, ``AliasRef``, ``MergeKey`` and
``MappingEntry.via_merge`` in ``troupe.config.model``.

The loader does not accept a plain ``<<`` key yet (task 2.3), so each case loads a real theatre
file in which the merge key is written quoted (an ordinary key), and the test turns that entry into
the plain ``<<`` entry of the mapping as written, with the merge sources as its value, as the
loader will.

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

import copy
import dataclasses

import pytest

from support import MakeTheatre
from troupe.config.loader import load_config
from troupe.config.merge_keys import merge_mapping
from troupe.config.model import (
    AliasRef,
    Anchor,
    CollectionStyle,
    ConfigDocument,
    ConfigMapping,
    ConfigNode,
    ConfigScalar,
    ConfigSequence,
    MappingEntry,
    MergeKey,
    Origin,
    ScalarStyle,
    Span,
)

MERGE_KEY = "<<"  # the merge key's text, as written in the files below (quoted there)

CONFIG = """\
defaults:
  model: opus
  tools: [read]
  retries: 1
extra:
  retries: 2
  timeout: 30
  model: haiku
coder:
  name: coder
  "<<": slot
  model: sonnet
flowed: {"<<": slot, own: 1}
"""


def _load(make_theatre: MakeTheatre) -> ConfigDocument:
    theatre = make_theatre(CONFIG)
    return load_config(theatre / ".troupe" / "troupe.yml")


def _mapping(node: ConfigNode | None) -> ConfigMapping:
    assert isinstance(node, ConfigMapping)
    return node


def _value(mapping: ConfigMapping, key: str) -> ConfigNode:
    entry = mapping.get(key)
    assert entry is not None, key
    return entry.value


def _keys(mapping: ConfigMapping) -> list[str]:
    return [entry.key for entry in mapping.entries]


def _written(mapping: ConfigMapping, value: ConfigNode) -> tuple[ConfigMapping, int, Origin]:
    """Turn the quoted ``"<<"`` entry of ``mapping`` into a plain ``<<`` entry holding ``value``.

    Returns the mapping as written, the index of its ``<<`` entry and the ``<<`` key's origin.
    """
    index = _keys(mapping).index(MERGE_KEY)
    quoted = mapping.entries[index]
    assert quoted.key_style is ScalarStyle.DOUBLE_QUOTED
    merge_entry = dataclasses.replace(quoted, key_style=ScalarStyle.PLAIN, value=value)
    entries = (*mapping.entries[:index], merge_entry, *mapping.entries[index + 1 :])
    return dataclasses.replace(mapping, entries=entries), index, quoted.key_origin


def _sources(document: ConfigDocument) -> tuple[ConfigMapping, ConfigMapping]:
    return _mapping(_value(document.root, "defaults")), _mapping(_value(document.root, "extra"))


def _merged_coder(document: ConfigDocument) -> tuple[ConfigMapping, ConfigMapping]:
    """Return the written ``coder`` mapping with ``<<: [defaults, extra]`` and its merged view."""
    defaults, extra = _sources(document)
    coder = _mapping(_value(document.root, "coder"))
    origin = Origin(document.root_file, Span(0, 0))
    source_list = ConfigSequence(items=(defaults, extra), style=CollectionStyle.FLOW, origin=origin)
    written, index, key_origin = _written(coder, source_list)
    return written, merge_mapping(written, index, (defaults, extra), key_origin)


def _scalar(mapping: ConfigMapping, key: str) -> object:
    node = _value(mapping, key)
    assert isinstance(node, ConfigScalar)
    return node.value


def test_new_model_fields_default_to_none_on_every_loaded_node(
    make_theatre: MakeTheatre,
) -> None:
    """Req 2.1, 2.4: nodes built without anchors, aliases or merge keys record none of them."""
    document = _load(make_theatre)

    def walk(node: ConfigNode) -> None:
        assert node.anchor is None
        assert node.alias is None
        if isinstance(node, ConfigMapping):
            assert node.merge is None
            for entry in node.entries:
                assert entry.via_merge is False
                walk(entry.value)
        elif isinstance(node, ConfigSequence):
            for item in node.items:
                walk(item)

    walk(document.root)


def test_model_records_anchor_alias_place_and_merge_key(make_theatre: MakeTheatre) -> None:
    """Req 2.1: a node records its anchor and alias, and a mapping the mapping behind its ``<<``."""
    document = _load(make_theatre)
    defaults, _extra = _sources(document)
    anchor = Anchor(name="d", span=Span(9, 11))
    anchored = dataclasses.replace(defaults, anchor=anchor)
    token = Origin(document.root_file, Span(20, 22))
    place = dataclasses.replace(defaults, alias=AliasRef(name="d", token=token, anchored=anchored))
    assert anchored.anchor == anchor
    assert place.alias is not None
    assert place.alias.anchored is anchored
    assert place.alias.token == token
    written, merged = _merged_coder(document)
    assert merged.merge == MergeKey(written=written, key_origin=written.entries[1].key_origin)
    with pytest.raises(dataclasses.FrozenInstanceError):
        anchor.name = "e"  # type: ignore[misc]


def test_own_keys_take_precedence_over_merged_keys(make_theatre: MakeTheatre) -> None:
    """Req 2.1: a key the mapping writes itself keeps its own value over every merged one."""
    _written_coder, merged = _merged_coder(_load(make_theatre))
    assert _scalar(merged, "model") == "sonnet"
    model = merged.get("model")
    assert model is not None
    assert model.via_merge is False
    assert _keys(merged).count("model") == 1


def test_earlier_source_takes_precedence_over_later_source(make_theatre: MakeTheatre) -> None:
    """Req 2.1: with a list of merged mappings, the earlier one in the list is used."""
    document = _load(make_theatre)
    _written_coder, merged = _merged_coder(document)
    assert _scalar(merged, "retries") == 1
    defaults, _extra = _sources(document)
    retries = merged.get("retries")
    source_retries = defaults.get("retries")
    assert retries is not None and source_retries is not None
    assert retries.value is source_retries.value
    assert _keys(merged).count("retries") == 1


def test_merged_keys_take_the_place_of_the_merge_entry(make_theatre: MakeTheatre) -> None:
    """Req 2.2: own keys in written order, merged keys where ``<<`` is written, sources in order."""
    _written_coder, merged = _merged_coder(_load(make_theatre))
    assert _keys(merged) == ["name", "tools", "retries", "timeout", "model"]


def test_merge_entry_first_puts_merged_keys_before_own_keys(make_theatre: MakeTheatre) -> None:
    """Req 2.2: a ``<<`` entry written first places the merged keys before the own keys."""
    document = _load(make_theatre)
    _defaults, extra = _sources(document)
    flowed = _mapping(_value(document.root, "flowed"))
    written, index, key_origin = _written(flowed, extra)
    merged = merge_mapping(written, index, (extra,), key_origin)
    assert index == 0
    assert _keys(merged) == ["retries", "timeout", "model", "own"]
    assert merged.style is CollectionStyle.FLOW


def test_only_merged_entries_are_marked(make_theatre: MakeTheatre) -> None:
    """Req 2.1: entries from a merge key say so; entries the mapping writes itself do not."""
    _written_coder, merged = _merged_coder(_load(make_theatre))
    marks = {entry.key: entry.via_merge for entry in merged.entries}
    assert marks == {
        "name": False,
        "tools": True,
        "retries": True,
        "timeout": True,
        "model": False,
    }


def test_merged_entries_keep_their_source_key_and_value(make_theatre: MakeTheatre) -> None:
    """Req 2.1: a merged entry is its source's entry (key, key origin and value), only marked."""
    document = _load(make_theatre)
    _defaults, extra = _sources(document)
    _written_coder, merged = _merged_coder(document)
    timeout = merged.get("timeout")
    source = extra.get("timeout")
    assert timeout is not None and source is not None
    assert timeout == dataclasses.replace(source, via_merge=True)
    assert timeout.value is source.value


def test_merged_view_has_no_merge_key_entry(make_theatre: MakeTheatre) -> None:
    """Req 2.4: no ``<<`` key is in the merged view; the written mapping keeps it behind it."""
    written, merged = _merged_coder(_load(make_theatre))
    assert merged.get(MERGE_KEY) is None
    assert merged.merge is not None
    assert merged.merge.written is written
    assert merged.merge.written.get(MERGE_KEY) is written.entries[1]
    assert merged.merge.key_origin == written.entries[1].key_origin


def test_merged_view_keeps_the_written_mapping_style_and_origin(
    make_theatre: MakeTheatre,
) -> None:
    """Req 2.1: the merged view has the written mapping's style, origin, anchor and alias."""
    document = _load(make_theatre)
    defaults, extra = _sources(document)
    coder = _mapping(_value(document.root, "coder"))
    written, index, key_origin = _written(coder, defaults)
    token = Origin(document.root_file, Span(1, 2))
    written = dataclasses.replace(
        written,
        anchor=Anchor(name="c", span=Span(3, 5)),
        alias=AliasRef(name="c", token=token, anchored=written),
    )
    merged = merge_mapping(written, index, (defaults, extra), key_origin)
    assert merged.style is written.style
    assert merged.origin is written.origin
    assert merged.anchor is written.anchor
    assert merged.alias is written.alias


def test_merge_does_not_change_its_inputs(make_theatre: MakeTheatre) -> None:
    """Req 2.1, 2.4: the merge is pure; the written mapping and the sources are unchanged."""
    document = _load(make_theatre)
    defaults, extra = _sources(document)
    coder = _mapping(_value(document.root, "coder"))
    written, index, key_origin = _written(coder, defaults)
    before = copy.deepcopy((written, defaults, extra))
    merge_mapping(written, index, (defaults, extra), key_origin)
    assert (written, defaults, extra) == before
    assert _keys(written) == ["name", MERGE_KEY, "model"]
    assert all(not entry.via_merge for entry in (*defaults.entries, *extra.entries))


def test_merge_without_sources_only_drops_the_merge_entry(make_theatre: MakeTheatre) -> None:
    """Req 2.2, 2.4: with nothing to merge, the view is the own entries in written order."""
    document = _load(make_theatre)
    defaults, _extra = _sources(document)
    coder = _mapping(_value(document.root, "coder"))
    written, index, key_origin = _written(coder, defaults)
    merged = merge_mapping(written, index, (), key_origin)
    assert merged.entries == (written.entries[0], written.entries[2])


def test_nodes_built_without_the_new_fields_record_none(make_theatre: MakeTheatre) -> None:
    """Req 2.1: the new fields default to none, so existing constructions are plain own nodes."""
    document = _load(make_theatre)
    origin = Origin(document.root_file, Span(0, 1))
    scalar = ConfigScalar(value=1, style=ScalarStyle.PLAIN, origin=origin)
    entry = MappingEntry(key="a", key_origin=origin, key_style=ScalarStyle.PLAIN, value=scalar)
    mapping = ConfigMapping(entries=(entry,), style=CollectionStyle.BLOCK, origin=origin)
    sequence = ConfigSequence(items=(scalar,), style=CollectionStyle.BLOCK, origin=origin)
    assert entry.via_merge is False
    assert mapping.merge is None
    for node in (scalar, mapping, sequence):
        assert node.anchor is None
        assert node.alias is None
