"""Unit tests of the FileLayout in files with anchors, aliases and merge keys (task 3.1).

Each case loads a real theatre configuration with the real loader and analyses one of its files.
An anchored value's slot ends after its ``&name`` property, and the property is a token for comment
detection. In walks over the nodes written in a file an alias place is a leaf at its ``*name``
token, and a mapping with a merge key is walked as written (own entries and the ``<<`` entry).

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

from collections.abc import Mapping
from pathlib import Path

import pytest

from troupe.config.loader import load_config
from troupe.config.model import (
    ConfigDocument,
    ConfigMapping,
    ConfigNode,
    ConfigSequence,
    Span,
)
from troupe.config.paths import ConfigPath
from troupe.config.writeback.layout import FileLayout, InsertionPoint, ValueSlot

ROOT = "troupe.yml"
CODER = "model: m\ntemperature: 0.7\n"


def _setup(
    tmp_path: Path, config: str, files: Mapping[str, str] | None = None
) -> tuple[FileLayout, ConfigDocument]:
    for name, text in {**(files or {"c.yaml": CODER}), ROOT: config}.items():
        (tmp_path / name).write_bytes(text.encode("utf-8"))
    document = load_config(tmp_path / ROOT)
    return FileLayout.build(document.root_file, document), document


def _node(document: ConfigDocument, *segments: str | int) -> ConfigNode:
    node: ConfigNode = document.root
    for segment in segments:
        if isinstance(segment, int):
            assert isinstance(node, ConfigSequence)
            node = node.items[segment]
        else:
            assert isinstance(node, ConfigMapping)
            entry = node.get(segment)
            assert entry is not None
            node = entry.value
    return node


def _mapping(document: ConfigDocument, *segments: str | int) -> ConfigMapping:
    node = _node(document, *segments)
    assert isinstance(node, ConfigMapping)
    return node


def _entry_slot(layout: FileLayout, document: ConfigDocument, *segments: str | int) -> ValueSlot:
    parent = _mapping(document, *segments[:-1])
    key = segments[-1]
    assert isinstance(key, str)
    entry = parent.get(key)
    assert entry is not None
    return layout.entry_slot(parent, entry, ConfigPath(tuple(str(s) for s in segments)))


def _item_slot(
    layout: FileLayout, document: ConfigDocument, index: int, *segments: str | int
) -> ValueSlot:
    sequence = _node(document, *segments)
    assert isinstance(sequence, ConfigSequence)
    return layout.item_slot(sequence, index, ConfigPath((*(str(s) for s in segments), str(index))))


def _after(text: str, needle: str) -> int:
    return text.index(needle) + len(needle)


def _text(text: str, span: Span) -> str:
    return text[span.start : span.end]


# --- anchor properties: indicator end ----------------------------------------------------------


def test_indicator_end_of_an_anchored_scalar_is_after_the_property(tmp_path: Path) -> None:
    """Req 4.3: the slot of `name: &n foo` ends after `&n`, and its inline extent is `foo` alone,
    so replacing the extent keeps the property byte for byte."""
    text = "name: &n foo\nother: 1\n"
    layout, document = _setup(tmp_path, text)
    slot = _entry_slot(layout, document, "name")
    assert slot.indicator_end == _after(text, "&n")
    extent = layout.inline_extent(slot)
    assert _text(text, extent) == "foo"
    assert text[: extent.start] == "name: &n "
    replaced = text[: extent.start] + "bar" + text[extent.end :]
    assert replaced == "name: &n bar\nother: 1\n"


def test_indicator_end_of_an_anchored_empty_value_is_after_the_property(tmp_path: Path) -> None:
    """Req 4.3: the empty value of `a: &x` sits after the property: its inline extent and content
    end are at the end of `&x`, so a value written there follows the anchor."""
    text = "a: &x\nb: 1\n"
    layout, document = _setup(tmp_path, text)
    slot = _entry_slot(layout, document, "a")
    end = _after(text, "&x")
    assert slot.indicator_end == end
    assert layout.inline_extent(slot) == Span(end, end)
    assert layout.content_end(slot.node, slot) == end
    replaced = text[:end] + " 5" + text[end:]
    assert replaced == "a: &x 5\nb: 1\n"


def test_indicator_end_of_an_anchored_block_mapping_is_after_the_property(tmp_path: Path) -> None:
    """Req 4.3: for `defaults: &d` followed by a block mapping, the inline extent is empty after the
    property and the body lines are the mapping's lines."""
    text = "defaults: &d\n  model: opus\ncoder: 1\n"
    layout, document = _setup(tmp_path, text)
    slot = _entry_slot(layout, document, "defaults")
    end = _after(text, "&d")
    assert slot.indicator_end == end
    assert layout.inline_extent(slot) == Span(end, end)
    assert layout.body_lines(slot) == Span(text.index("  model"), text.index("coder"))


def test_indicator_end_of_an_anchored_list_item_is_after_the_property(tmp_path: Path) -> None:
    """Req 4.3: the slot of the item `- &a foo` ends after `&a`; the next item's dash is found."""
    text = "list:\n  - &a foo\n  - &b\n  - 3\n"
    layout, document = _setup(tmp_path, text)
    first = _item_slot(layout, document, 0, "list")
    assert first.indicator_end == _after(text, "&a")
    assert _text(text, layout.inline_extent(first)) == "foo"
    second = _item_slot(layout, document, 1, "list")
    assert second.indicator_end == _after(text, "&b")
    assert layout.inline_extent(second) == Span(second.indicator_end, second.indicator_end)
    third = _item_slot(layout, document, 2, "list")
    assert third.indicator_end == _after(text, "- 3") - 2


def test_indicator_end_of_an_anchored_include_string_is_after_the_property(tmp_path: Path) -> None:
    """Req 4.3: an anchor on an include string lies in the including file; the slot ends after it
    and the inline extent is the directive."""
    text = "coder: &i file:c.yaml\nother: *i\n"
    layout, document = _setup(tmp_path, text)
    slot = _entry_slot(layout, document, "coder")
    assert slot.indicator_end == _after(text, "&i")
    assert _text(text, layout.inline_extent(slot)) == "file:c.yaml"


def test_indicator_end_of_an_anchored_ref_mapping_is_after_the_property(tmp_path: Path) -> None:
    """Req 4.3: an anchor on a `$ref` mapping lies in the including file; the slot ends after it
    and the inline extent is the flow mapping."""
    text = "coder: &i {$ref: file:c.yaml, model: x}\nother: 1\n"
    layout, document = _setup(tmp_path, text)
    slot = _entry_slot(layout, document, "coder")
    assert slot.indicator_end == _after(text, "&i")
    assert _text(text, layout.inline_extent(slot)) == "{$ref: file:c.yaml, model: x}"


# --- comments after a property -----------------------------------------------------------------


def test_comment_after_an_anchor_property_is_found(tmp_path: Path) -> None:
    """Req 8.5: the end-of-line comment after `defaults: &d  # c` is found, and a `#` inside the
    anchor name is no comment."""
    text = "defaults: &d  # c\n  model: opus\nname: &n#x foo  # n\nlist:\n  - &i#j 1\n"
    layout, _ = _setup(tmp_path, text)
    assert [layout.comment_start(line) for line in range(layout.line_count)] == [
        text.index("# c"),
        None,
        text.index("# n"),
        None,
        None,
        None,
    ]


def test_comment_after_an_alias_and_an_anchored_include_is_found(tmp_path: Path) -> None:
    """Req 8.5: comments after an alias token, after an anchored include string and after a merge
    key's alias are found; the alias places add no tokens on the anchor's lines."""
    text = (
        "base: &b\n"
        "  x: 1\n"
        "inc: &i file:c.yaml  # inc\n"
        "copy: *b  # copy\n"
        "m:\n"
        "  <<: *b  # merge\n"
        "  y: 2\n"
    )
    layout, _ = _setup(tmp_path, text)
    assert [layout.comment_start(line) for line in range(layout.line_count)] == [
        None,
        None,
        text.index("# inc"),
        text.index("# copy"),
        None,
        text.index("# merge"),
        None,
        None,
    ]


# --- alias places are leaves at their token ----------------------------------------------------


def test_end_of_an_entry_whose_value_is_an_alias(tmp_path: Path) -> None:
    """Req 10.1: an alias value ends at its `*name` token: the mapping's content end and insertion
    point follow the alias's line, and the slot's inline extent is the token."""
    text = "d: &d\n  k: 1\nm:\n  a: *d\nn: 1\n"
    layout, document = _setup(tmp_path, text)
    slot = _entry_slot(layout, document, "m", "a")
    token = Span(text.index("*d"), _after(text, "*d"))
    assert slot.indicator_end == _after(text, "  a:")
    assert layout.inline_extent(slot) == token
    assert layout.body_lines(slot) is None
    assert layout.content_end(slot.node, slot) == token.end
    m = _mapping(document, "m")
    assert layout.content_end(m, None) == token.end
    assert layout.insertion_point(m) == InsertionPoint(text.index("n: 1"), False)


def test_end_of_a_list_whose_last_item_is_an_alias(tmp_path: Path) -> None:
    """Req 10.1: the dashes and the content end of a block list with alias items follow the alias
    tokens, not the anchored node."""
    text = "d: &d\n  k: 1\nl:\n  - *d\n  - *d\nn: 1\n"
    layout, document = _setup(tmp_path, text)
    second = _item_slot(layout, document, 1, "l")
    assert second.indicator_end == text.index("- *d\nn") + 1
    assert layout.inline_extent(second) == Span(text.index("*d\nn"), text.index("*d\nn") + 2)
    sequence = _node(document, "l")
    assert isinstance(sequence, ConfigSequence)
    assert layout.insertion_point(sequence) == InsertionPoint(text.index("n: 1"), False)


def test_alias_in_a_flow_list_is_a_leaf_at_its_token(tmp_path: Path) -> None:
    """Req 10.1: an alias item of a flow list has its token as slot and inline extent."""
    text = "l: [&a 1, *a]\n"
    layout, document = _setup(tmp_path, text)
    slot = _item_slot(layout, document, 1, "l")
    assert slot.indicator_end == text.index("*a")
    assert _text(text, layout.inline_extent(slot)) == "*a"


def test_block_scalar_owner_column_ignores_alias_places(tmp_path: Path) -> None:
    """Req 10.1: an alias of a block scalar with an indentation indicator does not change the
    owner column used for the anchored scalar's content."""
    text = "a: &s |2\n   x\nb:\n    c: *s\n"
    layout, document = _setup(tmp_path, text)
    slot = _entry_slot(layout, document, "a")
    assert layout.content_end(slot.node, slot) == _after(text, "   x")


# --- merge-key mappings are walked as written --------------------------------------------------


def test_insertion_point_in_a_mapping_whose_last_entry_is_a_merge_key(tmp_path: Path) -> None:
    """Req 10.1: a new key of a mapping whose last written entry is `<<` goes after the `<<` line;
    its content end is the end of the merge key's alias."""
    text = "base: &b\n  x: 1\nm:\n  a: 1\n  <<: *b\nother: 2\n"
    layout, document = _setup(tmp_path, text)
    m = _mapping(document, "m")
    assert [entry.key for entry in m.entries] == ["a", "x"]
    assert layout.content_end(m, None) == _after(text, "<<: *b")
    assert layout.insertion_point(m) == InsertionPoint(text.index("other"), False)
    assert layout.entries_column(m) == 2


def test_merge_key_first_mapping_columns_and_slots(tmp_path: Path) -> None:
    """Req 10.1: with `<<` as the first written entry the entries column is the `<<` key's; an own
    entry's slot is found, and a merged entry has no slot in this mapping."""
    text = "base: &b\n  x: 1\nm:\n    <<: *b\n    a: 1\n"
    layout, document = _setup(tmp_path, text)
    m = _mapping(document, "m")
    assert [entry.key for entry in m.entries] == ["x", "a"]
    assert layout.entries_column(m) == 4
    slot = _entry_slot(layout, document, "m", "a")
    assert slot.indicator_end == _after(text, "    a:")
    merged = m.get("x")
    assert merged is not None and merged.via_merge
    with pytest.raises(ValueError, match="merge key"):
        layout.entry_slot(m, merged, ConfigPath(("m", "x")))


def test_merge_key_with_inline_mapping_ends_at_its_flow_mapping(tmp_path: Path) -> None:
    """Req 10.1: a mapping whose last entry is `<<: {x: 1}` ends at the inline mapping's brace."""
    text = "m:\n  a: 1\n  <<: {x: 1}  # c\nn: 1\n"
    layout, document = _setup(tmp_path, text)
    m = _mapping(document, "m")
    assert layout.content_end(m, None) == _after(text, "{x: 1}")
    assert layout.insertion_point(m) == InsertionPoint(text.index("n: 1"), False)
    assert layout.comment_start(2) == text.index("# c")


# --- indentation step and dash offset ----------------------------------------------------------


def test_indentation_step_of_a_file_with_anchors(tmp_path: Path) -> None:
    """Req 10.1: an anchored nested mapping gives the step from its first key, not its property."""
    text = "defaults: &d\n    model: opus\ncoder:\n    settings: *d\n"
    layout, _ = _setup(tmp_path, text)
    assert layout.indent_step() == 4


def test_indentation_step_ignores_alias_places(tmp_path: Path) -> None:
    """Req 10.1: an alias of a block mapping is no nested block mapping written under its key, so
    the step comes from the first mapping written under a key."""
    text = "list:\n- &d\n  k: 1\ncopy: *d\nnested:\n   z: 1\n"
    layout, _ = _setup(tmp_path, text)
    assert layout.indent_step() == 3


def test_dash_offset_of_an_anchored_list(tmp_path: Path) -> None:
    """Req 10.1: an anchored block list's dash offset is counted from its first `-`."""
    text = "a: &l\n   - 1\nb: *l\n"
    layout, _ = _setup(tmp_path, text)
    assert layout.dash_offset() == 3
