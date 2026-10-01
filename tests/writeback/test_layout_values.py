"""Unit tests of the FileLayout's value analysis (``troupe.config.writeback.layout``): end-of-line
comments, value slots, content ends, inline extents, body lines and insertion points.

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

from pathlib import Path

import pytest

from troupe.config.loader import load_config
from troupe.config.model import (
    ConfigDocument,
    ConfigMapping,
    ConfigNode,
    ConfigSequence,
    SourceFile,
    Span,
)
from troupe.config.paths import ConfigPath
from troupe.config.writeback.layout import FileLayout, InsertionPoint, ValueSlot


def _write(tmp_path: Path, name: str, content: str | bytes) -> Path:
    target = tmp_path / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content.encode("utf-8") if isinstance(content, str) else content)
    return target


def _load(
    tmp_path: Path, config: str | bytes, files: dict[str, str | bytes] | None = None
) -> ConfigDocument:
    for name, text in (files or {}).items():
        _write(tmp_path, name, text)
    return load_config(_write(tmp_path, "troupe.yml", config))


def _file(document: ConfigDocument, name: str) -> SourceFile:
    for source in document.files:
        if source.path.name == name:
            return source
    raise AssertionError(f"{name} was not read")


def _setup(
    tmp_path: Path, config: str | bytes, files: dict[str, str | bytes] | None = None
) -> tuple[FileLayout, ConfigDocument]:
    document = _load(tmp_path, config, files)
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


def _sequence(document: ConfigDocument, *segments: str | int) -> ConfigSequence:
    node = _node(document, *segments)
    assert isinstance(node, ConfigSequence)
    return node


def _entry_slot(layout: FileLayout, document: ConfigDocument, *segments: str) -> ValueSlot:
    parent = _mapping(document, *segments[:-1])
    entry = parent.get(segments[-1])
    assert entry is not None
    return layout.entry_slot(parent, entry, ConfigPath(tuple(segments)))


def _item_slot(
    layout: FileLayout, document: ConfigDocument, index: int, *segments: str | int
) -> ValueSlot:
    sequence = _sequence(document, *segments)
    path = ConfigPath(tuple(str(s) for s in (*segments, index)))
    return layout.item_slot(sequence, index, path)


def _comments(layout: FileLayout) -> list[int | None]:
    return [layout.comment_start(line) for line in range(layout.line_count)]


# --- end-of-line comments --------------------------------------------------------------------


def test_comment_after_quoted_scalar_containing_hash(tmp_path: Path) -> None:
    """Req 12.1: a `#` inside a quoted scalar is not a comment; the one after the value is."""
    text = 'a: "x # y"  # c\n'
    layout, _ = _setup(tmp_path, text)
    assert layout.comment_start(0) == text.rindex("#")
    assert _comments(layout) == [12, None]


def test_line_without_comment_has_none(tmp_path: Path) -> None:
    """Req 12.1: quoted `#` text alone is no comment, nor is `#` inside a plain scalar."""
    layout, _ = _setup(tmp_path, "a: \"x # y\"\nb: 'p #q'\nc: a#b\n")
    assert _comments(layout) == [None, None, None, None]


def test_comment_after_plain_scalar_with_inner_hash(tmp_path: Path) -> None:
    """Req 12.1: a `#` not preceded by a space belongs to the plain scalar."""
    text = "k: a#b  # c\n"
    layout, _ = _setup(tmp_path, text)
    assert layout.comment_start(0) == text.rindex("#")


def test_comment_after_quoted_key_containing_hash(tmp_path: Path) -> None:
    """Req 12.1: keys are tokens too; a quoted key's `#` is not a comment."""
    text = '"a #b": 1  # c\n'
    layout, _ = _setup(tmp_path, text)
    assert layout.comment_start(0) == text.rindex("#")


def test_comment_after_empty_value_and_after_block_header(tmp_path: Path) -> None:
    """Req 12.1: the `:` indicator and a block scalar header are tokens before a comment."""
    text = "a: # c\nk: |- # h\n  x # not\n  # not\nm: 1 # d\n"
    layout, _ = _setup(tmp_path, text)
    assert _comments(layout) == [
        3,
        text.index("# h"),
        None,  # block scalar content
        None,  # block scalar content
        text.index("# d"),
        None,
    ]


def test_token_floor_with_flow_collections(tmp_path: Path) -> None:
    """Req 12.1: quoted `#` inside flow collections is skipped; comments inside them count."""
    text = 'k: [a, "#b"]  # c\nm: {a: "b #c"}\nn: [a, # c1\n  b]  # c2\n'
    layout, _ = _setup(tmp_path, text)
    assert _comments(layout) == [
        text.index("# c\n"),
        None,
        text.index("# c1"),
        text.index("# c2"),
        None,
    ]


def test_multi_line_quoted_scalar_hides_hash_on_its_lines(tmp_path: Path) -> None:
    """Req 12.1: a `#` on a continuation line of a quoted scalar is text, not a comment."""
    text = 'k: "a\n  # b"  # c\n'
    layout, _ = _setup(tmp_path, text)
    assert _comments(layout) == [None, text.index("# c"), None]


def test_whole_comment_lines_report_their_hash(tmp_path: Path) -> None:
    """Req 12.1: a comment line's comment starts at its `#`."""
    text = "# top\na:\n  b: 1\n    # deep\n"
    layout, _ = _setup(tmp_path, text)
    assert _comments(layout) == [0, None, None, text.index("# deep"), None]


def test_comments_in_crlf_and_multibyte_text(tmp_path: Path) -> None:
    """Req 12.1, 12.3: CRLF breaks and multibyte characters do not shift comment detection."""
    text = 'näme: "é # x"  # c\r\nb: 2\r\n'
    layout, _ = _setup(tmp_path, text.encode("utf-8"))
    start = layout.comment_start(0)
    assert start == text.index("# c")
    assert layout.column(start) == 15
    assert layout.comment_start(1) is None


def test_comment_after_include_directive(tmp_path: Path) -> None:
    """Req 12.1: a `file:` directive written in the file is a token of that file."""
    text = "t: file:x#y.txt  # c\nn: 1\n"
    layout, _ = _setup(tmp_path, text, {"x#y.txt": "hello\n"})
    assert layout.comment_start(0) == text.index("# c")


def test_comment_with_byte_order_mark(tmp_path: Path) -> None:
    """Req 12.1: a comment line right after a BOM still starts a comment."""
    text = "\ufeff# c\na: 1\n"
    layout, _ = _setup(tmp_path, text)
    assert layout.comment_start(0) == 1


# --- value slots -----------------------------------------------------------------------------


@pytest.mark.parametrize("text", ["a:\n", "a: # c\n", "a:   \n  # c\nb: 1\n", "a:"])
def test_empty_null_slot_is_found_from_the_colon(tmp_path: Path, text: str) -> None:
    """Req 11.1: an empty null value's slot comes from its key's `:`, never from its span."""
    layout, document = _setup(tmp_path, text)
    slot = _entry_slot(layout, document, "a")
    assert slot.owner_column == 0
    assert slot.indicator_end == 2
    assert slot.in_sequence is False
    assert slot.path == ConfigPath(("a",))
    assert slot.node is _node(document, "a")
    assert layout.inline_extent(slot) == Span(2, 2)
    assert layout.content_end(slot.node, slot) == 2
    assert layout.body_lines(slot) is None


def test_entry_slot_of_nested_and_quoted_keys(tmp_path: Path) -> None:
    """Req 11.1, 12.3: the key column and the index after `:` for nested and quoted keys."""
    text = 'x:\n  "é k" :  1\n  b:\n'
    layout, document = _setup(tmp_path, text)
    slot = _entry_slot(layout, document, "x", "é k")
    assert slot.owner_column == 2
    assert slot.indicator_end == text.index(":  1") + 1
    assert layout.inline_extent(slot) == Span(text.index("1"), text.index("1") + 1)
    empty = _entry_slot(layout, document, "x", "b")
    assert empty.indicator_end == len(text) - 1
    assert layout.inline_extent(empty) == Span(len(text) - 1, len(text) - 1)


def test_item_slots_of_empty_items_come_from_their_dashes(tmp_path: Path) -> None:
    """Req 11.1: empty list items are located from their `-`."""
    text = "k:\n  - a\n  -\n  - \n# c\n\n"
    layout, document = _setup(tmp_path, text)
    slots = [_item_slot(layout, document, n, "k") for n in range(3)]
    assert [s.owner_column for s in slots] == [2, 2, 2]
    assert [s.indicator_end for s in slots] == [6, 12, 16]
    assert all(s.in_sequence for s in slots)
    assert [layout.inline_extent(s) for s in slots] == [Span(7, 8), Span(12, 12), Span(16, 16)]


def test_empty_first_item_before_a_further_item(tmp_path: Path) -> None:
    """Req 11.1: an empty first item does not borrow the next item's dash."""
    text = "l:\n- \n- x\n"
    layout, document = _setup(tmp_path, text)
    assert [_item_slot(layout, document, n, "l").indicator_end for n in range(2)] == [4, 7]


def test_compact_nested_list_items(tmp_path: Path) -> None:
    """Req 11.1: a compact nested list's inline extent and body lines; dashes after it."""
    text = "l:\n- - x\n  - y  # c\n- \n- z"
    layout, document = _setup(tmp_path, text)
    first = _item_slot(layout, document, 0, "l")
    assert (first.owner_column, first.indicator_end) == (0, 4)
    assert layout.inline_extent(first) == Span(5, 8)
    assert layout.body_lines(first) == Span(9, text.index("- \n"))
    second = _item_slot(layout, document, 1, "l")
    assert second.indicator_end == text.index("- \n") + 1
    assert layout.inline_extent(second) == Span(second.indicator_end, second.indicator_end)
    third = _item_slot(layout, document, 2, "l")
    assert layout.inline_extent(third) == Span(len(text) - 1, len(text))
    inner = _item_slot(layout, document, 1, "l", 0)
    assert (inner.owner_column, inner.indicator_end) == (2, text.index("- y") + 1)


def test_compact_mapping_item_extent_excludes_the_comment(tmp_path: Path) -> None:
    """Req 11.1, 12.1: a compact mapping item's inline extent stops before its comment."""
    text = "l:\n  - a: 1   # c\n    b: 2\n"
    layout, document = _setup(tmp_path, text)
    slot = _item_slot(layout, document, 0, "l")
    assert layout.inline_extent(slot) == Span(text.index("a:"), text.index("1") + 1)
    assert layout.body_lines(slot) == Span(text.index("    b"), len(text))


def test_block_collection_on_a_later_line(tmp_path: Path) -> None:
    """Req 11.1: a block collection starting below its `-` or key has an empty inline extent."""
    text = "l:\n-\n  a: 1\n  b: 2\n- x\nm:  # c\n  n: 1\n"
    layout, document = _setup(tmp_path, text)
    item = _item_slot(layout, document, 0, "l")
    assert item.indicator_end == 4
    assert layout.inline_extent(item) == Span(4, 4)
    assert layout.body_lines(item) == Span(5, 19)
    assert _item_slot(layout, document, 1, "l").indicator_end == 20
    entry = _entry_slot(layout, document, "m")
    assert layout.inline_extent(entry) == Span(text.index("m:") + 2, text.index("m:") + 2)
    assert layout.body_lines(entry) == Span(text.index("  n"), len(text))


def test_flow_items_and_entries(tmp_path: Path) -> None:
    """Req 11.1: flow items are located at their own start; flow values span exactly."""
    text = "k: [a, {b: c}]\nm: {p: , q: [1]}\n"
    layout, document = _setup(tmp_path, text)
    item = _item_slot(layout, document, 1, "k")
    assert item.indicator_end == text.index("{")
    assert item.owner_column == text.index("{")
    assert layout.inline_extent(item) == Span(text.index("{"), text.index("]"))
    empty = _entry_slot(layout, document, "m", "p")
    assert empty.indicator_end == text.index("p:") + 2
    assert layout.inline_extent(empty) == Span(empty.indicator_end, empty.indicator_end)
    nested = _entry_slot(layout, document, "m", "q")
    assert layout.inline_extent(nested) == Span(text.index("[1]"), text.index("[1]") + 3)


def test_include_root_slot_uses_the_directive(tmp_path: Path) -> None:
    """Req 11.1: an include root's value in the including file is its `file:` directive."""
    text = "a:\n  t: file:t.txt  # c\n  y: file:y.yml\n# end\n"
    layout, document = _setup(tmp_path, text, {"t.txt": "hi\n", "y.yml": "z: 1\n"})
    slot = _entry_slot(layout, document, "a", "t")
    directive = Span(text.index("file:t"), text.index("  # c"))
    assert layout.inline_extent(slot) == directive
    assert layout.content_end(slot.node, slot) == directive.end
    yaml_slot = _entry_slot(layout, document, "a", "y")
    assert layout.inline_extent(yaml_slot) == Span(text.index("file:y"), text.index("\n# end"))
    assert layout.body_lines(yaml_slot) is None
    assert layout.content_end(_node(document, "a"), None) == text.index("\n# end")


# --- content ends and block scalars ----------------------------------------------------------


def test_clip_block_scalar_content_end_excludes_trailing_blank_lines(tmp_path: Path) -> None:
    """Req 11.1: clip chomping ends at the last non-blank content line."""
    text = "k: |\n  a\n  b\n\n\nz: 1\n"
    layout, document = _setup(tmp_path, text)
    slot = _entry_slot(layout, document, "k")
    end = text.index("b\n") + 1
    assert layout.content_end(slot.node, slot) == end
    assert layout.inline_extent(slot) == Span(3, 4)
    assert layout.body_lines(slot) == Span(5, end + 1)


def test_keep_block_scalar_content_end_includes_trailing_blank_lines(tmp_path: Path) -> None:
    """Req 11.1: keep chomping ends after the trailing blank lines that are content."""
    text = "k: |+\n  a\n\n\nz: 1\n"
    layout, document = _setup(tmp_path, text)
    slot = _entry_slot(layout, document, "k")
    assert layout.content_end(slot.node, slot) == text.index("z") - 1
    assert layout.inline_extent(slot) == Span(3, 5)
    assert layout.body_lines(slot) == Span(6, text.index("z"))


def test_block_scalar_headers_with_indicators_and_comment(tmp_path: Path) -> None:
    """Req 11.1, 12.1: the inline extent of a block scalar is its header indicators only."""
    text = "a: |2-\n   x\n\nb: >+ # h\n  y\nc: |\n\nd: 1\n"
    layout, document = _setup(tmp_path, text)
    a = _entry_slot(layout, document, "a")
    assert layout.inline_extent(a) == Span(3, 6)
    assert layout.content_end(a.node, a) == text.index("x") + 1
    b = _entry_slot(layout, document, "b")
    assert layout.inline_extent(b) == Span(text.index(">+"), text.index(">+") + 2)
    assert layout.content_end(b.node, b) == text.index("y") + 1
    c = _entry_slot(layout, document, "c")
    assert layout.content_end(c.node, c) == text.index("|\n\nd") + 1
    assert layout.body_lines(c) is None


def test_block_scalar_at_end_of_file_without_break(tmp_path: Path) -> None:
    """Req 11.1, 11.6: a keep block scalar ending the file without a break ends at end of file."""
    text = "k: |+\n  a"
    layout, document = _setup(tmp_path, text)
    slot = _entry_slot(layout, document, "k")
    assert layout.content_end(slot.node, slot) == len(text)
    assert layout.body_lines(slot) == Span(6, len(text))


def test_block_collection_content_end_is_its_last_entry(tmp_path: Path) -> None:
    """Req 11.1: a block collection ends with its last entry, not with its composer span."""
    text = "a:\n  b: 1\n  c: |\n    x\n\n# t\nz:\n  - p\n  -\n\n"
    layout, document = _setup(tmp_path, text)
    assert layout.content_end(_node(document, "a"), None) == text.index("x") + 1
    assert layout.content_end(_node(document, "z"), None) == text.index("-\n\n") + 1
    assert layout.content_end(document.root, None) == text.index("-\n\n") + 1


def test_block_mapping_ending_with_empty_value(tmp_path: Path) -> None:
    """Req 11.1: a last entry with an empty value ends at its `:`."""
    text = "a:\n  b:\n\n# c\nz: 1\n"
    layout, document = _setup(tmp_path, text)
    assert layout.content_end(_node(document, "a"), None) == text.index("b:") + 2


def test_empty_null_content_end_needs_its_slot(tmp_path: Path) -> None:
    """Req 11.1: an empty null has no reliable span, so its content end requires a slot."""
    layout, document = _setup(tmp_path, "a:\nb: 1\n")
    with pytest.raises(ValueError):
        layout.content_end(_node(document, "a"), None)


def test_entries_column(tmp_path: Path) -> None:
    """Req 11.1: the entries column is the column of the first key or `-`."""
    text = "a:\n    b: 1\nl:\n- x\nm:\n   - y\n"
    layout, document = _setup(tmp_path, text)
    assert layout.entries_column(_mapping(document, "a")) == 4
    assert layout.entries_column(_sequence(document, "l")) == 0
    assert layout.entries_column(_sequence(document, "m")) == 3
    assert layout.entries_column(document.root) == 0


# --- insertion points ------------------------------------------------------------------------


def test_insertion_below_trailing_comment_lines(tmp_path: Path) -> None:
    """Req 11.1: a new entry goes below comment lines indented like the entries."""
    text = "a:\n  b: 1\n  # about b\n      # deeper\nc: 2\n"
    layout, document = _setup(tmp_path, text)
    point = layout.insertion_point(_mapping(document, "a"))
    assert point == InsertionPoint(text.index("c: 2"), False)


def test_insertion_above_less_indented_comment(tmp_path: Path) -> None:
    """Req 11.2: a comment indented less than the entries ends the collection."""
    text = "a:\n  b: 1\n  # about b\n# about c\nc: 2\n"
    layout, document = _setup(tmp_path, text)
    point = layout.insertion_point(_mapping(document, "a"))
    assert point == InsertionPoint(text.index("# about c"), False)


def test_insertion_above_blank_lines_without_a_following_comment(tmp_path: Path) -> None:
    """Req 11.2: blank lines not followed by a qualifying comment stay below the new entry."""
    text = "a:\n  b: 1\n\n\nc: 2\n"
    layout, document = _setup(tmp_path, text)
    assert layout.insertion_point(_mapping(document, "a")) == InsertionPoint(10, False)
    text2 = "a:\n  b: 1\n\n# c\nc: 2\n"
    layout2, document2 = _setup(tmp_path / "two", text2)
    assert layout2.insertion_point(_mapping(document2, "a")) == InsertionPoint(10, False)


def test_insertion_below_comment_after_blank_line(tmp_path: Path) -> None:
    """Req 11.1, 11.2: blank lines followed by a qualifying comment are passed."""
    text = "a:\n  b: 1\n\n  # t\n\nc: 2\n"
    layout, document = _setup(tmp_path, text)
    point = layout.insertion_point(_mapping(document, "a"))
    assert point == InsertionPoint(text.index("\nc: 2"), False)


def test_insertion_into_a_list(tmp_path: Path) -> None:
    """Req 11.1, 11.2: list items follow the same rules with the dash column."""
    text = "l:\n  - a\n  # t\n# u\nm: 1\n"
    layout, document = _setup(tmp_path, text)
    point = layout.insertion_point(_sequence(document, "l"))
    assert point == InsertionPoint(text.index("# u"), False)


def test_insertion_after_block_scalar_last_value(tmp_path: Path) -> None:
    """Req 11.1: a block scalar's trailing blank lines do not hide a following comment."""
    text = "a:\n  b: |\n    x\n\n  # t\nc: 1\n"
    layout, document = _setup(tmp_path, text)
    point = layout.insertion_point(_mapping(document, "a"))
    assert point == InsertionPoint(text.index("c: 1"), False)


def test_insertion_into_root_with_trailing_comment(tmp_path: Path) -> None:
    """Req 11.1: the root's trailing comments at column 0 stay above a new root key."""
    text = "a: 1\n\n# trailing\n"
    layout, document = _setup(tmp_path, text)
    assert layout.insertion_point(document.root) == InsertionPoint(len(text), False)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", InsertionPoint(0, False)),
        ("# only\n", InsertionPoint(7, False)),
        ("# only", InsertionPoint(6, True)),
        ("\ufeff", InsertionPoint(1, False)),
    ],
)
def test_insertion_into_empty_root_is_at_end_of_file(
    tmp_path: Path, text: str, expected: InsertionPoint
) -> None:
    """Req 11.1, 11.6: an empty root inserts at end of file."""
    layout, document = _setup(tmp_path, text)
    assert document.root.entries == ()
    assert layout.insertion_point(document.root) == expected


@pytest.mark.parametrize(
    ("text", "segments"),
    [
        ("a: 1", ()),
        ("a: 1\n# c", ()),
        ("a:\n  b: 1\n  # c", ("a",)),
        ("l:\n- x", ("l",)),
    ],
)
def test_insertion_without_final_line_break_needs_a_leading_break(
    tmp_path: Path, text: str, segments: tuple[str, ...]
) -> None:
    """Req 11.6: at a file end without a final line break the new entry needs a leading break."""
    layout, document = _setup(tmp_path, text)
    node = _node(document, *segments)
    assert isinstance(node, ConfigMapping | ConfigSequence)
    assert layout.insertion_point(node) == InsertionPoint(len(text), True)


def test_insertion_point_in_crlf_file(tmp_path: Path) -> None:
    """Req 11.1, 13.2: CRLF lines are skipped and accepted whole."""
    text = "a:\r\n  b: 1\r\n  # t\r\n\r\nc: 2\r\n"
    layout, document = _setup(tmp_path, text.encode("utf-8"))
    point = layout.insertion_point(_mapping(document, "a"))
    assert point == InsertionPoint(text.index("\r\nc: 2"), False)


def test_insertion_point_refuses_flow_collections(tmp_path: Path) -> None:
    """Req 11.1: insertion points are defined for block collections only."""
    layout, document = _setup(tmp_path, "k: [a]\n")
    with pytest.raises(ValueError):
        layout.insertion_point(_sequence(document, "k"))


def test_insertion_point_in_an_included_file(tmp_path: Path) -> None:
    """Req 11.1: an included YAML file's root inserts by that file's own text."""
    document = _load(tmp_path, "a: file:inc.yml\n", {"inc.yml": "x: 1\n# t\n"})
    included = _mapping(document, "a")
    source = _file(document, "inc.yml")
    layout = FileLayout.build(source, document)
    assert layout.insertion_point(included) == InsertionPoint(len(source.text), False)


def test_out_of_contract_keys_do_not_break_the_analysis(tmp_path: Path) -> None:
    """Req 12.1: explicit `? k` keys and flow keys without `:` still allow comment detection."""
    text = "? a\n: 1  # c\nk: {b, c: 1}  # d\n"
    layout, document = _setup(tmp_path, text)
    assert layout.comment_start(1) == text.index("# c")
    assert layout.comment_start(2) == text.index("# d")
    with pytest.raises(ValueError):
        _entry_slot(layout, document, "a")


def test_whitespace_line_longer_than_the_indentation_is_block_scalar_content(
    tmp_path: Path,
) -> None:
    """Req 11.1: a spaces-only line deeper than the content indentation is content."""
    text = "k: |\n  a\n     \n\nz: 1\n"
    layout, document = _setup(tmp_path, text)
    slot = _entry_slot(layout, document, "k")
    assert layout.content_end(slot.node, slot) == text.index("     \n") + 5
