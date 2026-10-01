"""Unit tests of the BlockScalar rewrite of existing literal and folded blocks (task 2.6):
``troupe.config.writeback.block_scalar.rewrite_block``.

Each case loads a real file, rewrites the block scalar at a key or list item through its value slot,
applies the edits with the EditApplier, compares the result byte-exactly and re-loads it with the
real loader (``load_config``) to prove it reads back as the intended string.

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

from pathlib import Path

import pytest

from troupe.config.errors import WriteRefusedError
from troupe.config.loader import load_config
from troupe.config.model import ConfigDocument, ConfigMapping, ConfigNode, ConfigScalar
from troupe.config.paths import ConfigPath
from troupe.config.writeback.block_scalar import rewrite_block
from troupe.config.writeback.edits import TextEdit, apply_edits
from troupe.config.writeback.layout import FileLayout, ValueSlot

FOLDED = "text: >\n  The quick brown fox\n  jumps over.\n\n  Second para here.\nnext: 1\n"
"""The folded fixture F of the spec's byte-exact cases."""


def _write(tmp_path: Path, name: str, content: str | bytes) -> Path:
    target = tmp_path / name
    target.write_bytes(content.encode("utf-8") if isinstance(content, str) else content)
    return target


def _scalar_at(document: ConfigDocument, segments: tuple[str | int, ...]) -> ConfigScalar:
    node: ConfigNode = document.root
    for segment in segments:
        if isinstance(segment, int):
            assert not isinstance(node, ConfigScalar | ConfigMapping)
            node = node.items[segment]
        else:
            assert isinstance(node, ConfigMapping)
            entry = node.get(segment)
            assert entry is not None
            node = entry.value
    assert isinstance(node, ConfigScalar)
    return node


def _slot(
    layout: FileLayout, document: ConfigDocument, segments: tuple[str | int, ...]
) -> ValueSlot:
    parent: ConfigNode = document.root
    for segment in segments[:-1]:
        if isinstance(segment, int):
            assert not isinstance(parent, ConfigScalar | ConfigMapping)
            parent = parent.items[segment]
        else:
            assert isinstance(parent, ConfigMapping)
            found = parent.get(segment)
            assert found is not None
            parent = found.value
    path = ConfigPath(tuple(str(segment) for segment in segments))
    last = segments[-1]
    if isinstance(last, int):
        assert not isinstance(parent, ConfigScalar | ConfigMapping)
        return layout.item_slot(parent, last, path)
    assert isinstance(parent, ConfigMapping)
    entry = parent.get(last)
    assert entry is not None
    return layout.entry_slot(parent, entry, path)


def _edits(
    tmp_path: Path, text: str | bytes, value: str, *segments: str | int
) -> tuple[FileLayout, list[TextEdit]]:
    document = load_config(_write(tmp_path, "troupe.yml", text))
    layout = FileLayout.build(document.root_file, document)
    slot = _slot(layout, document, segments)
    return layout, rewrite_block(layout, slot, _scalar_at(document, segments), value)


def _rewrite(tmp_path: Path, text: str | bytes, value: str, *segments: str | int) -> str:
    """Rewrite the block at ``segments`` to ``value``; return the new text after re-loading it."""
    segments = segments or ("a",)
    layout, edits = _edits(tmp_path, text, value, *segments)
    result = apply_edits(layout, edits)
    reloaded = load_config(_write(tmp_path, "result.yml", result))
    assert _scalar_at(reloaded, segments).value == value
    return result


# --- U1 guard ------------------------------------------------------------------------------------


def test_u1_guard_loader_strips_fold_markers(tmp_path: Path) -> None:
    """Req 10.1 (U1 guard): the foundation loader returns folded values without fold markers."""
    document = load_config(_write(tmp_path, "troupe.yml", "t: >\n  a\n  b\n"))
    assert _scalar_at(document, ("t",)).value == "a b\n"


# --- literal blocks ------------------------------------------------------------------------------


def test_literal_rewrite_keeps_header_comment(tmp_path: Path) -> None:
    """Req 9.4, 9.5: indicator, chomping and the header comment are kept; content replaced."""
    text = "a: |  # hdr\n  old\nb: 1\n"
    assert _rewrite(tmp_path, text, "new\nlines\n") == "a: |  # hdr\n  new\n  lines\nb: 1\n"


def test_literal_rewrite_changes_chomping_only_when_required(tmp_path: Path) -> None:
    """Req 9.4: clip cannot hold a value without a final break, so the header becomes `|-`."""
    assert _rewrite(tmp_path, "a: |\n  old\n", "new") == "a: |-\n  new\n"


def test_literal_chomping_change_keeps_header_comment_column(tmp_path: Path) -> None:
    """Req 9.4, 9.5, 12.1: the header comment stays at its column when the chomping grows."""
    assert _rewrite(tmp_path, "a: |  # c\n  old\nb: 1\n", "x") == "a: |- # c\n  x\nb: 1\n"


def test_literal_rewrite_keeps_existing_content_indentation(tmp_path: Path) -> None:
    """Req 9.5: new lines are written at the block's existing content indentation."""
    assert _rewrite(tmp_path, "a: |-\n    deep\n", "x\ny") == "a: |-\n    x\n    y\n"


def test_literal_keep_is_kept_while_it_still_fits(tmp_path: Path) -> None:
    """Req 9.4: keep represents one trailing break; its old trailing empty lines are replaced."""
    assert _rewrite(tmp_path, "a: |+\n  x\n\nb: 1\n", "y\n") == "a: |+\n  y\nb: 1\n"


def test_literal_keep_to_strip(tmp_path: Path) -> None:
    """Req 9.4: keep cannot hold a value without a trailing break; strip is required."""
    assert _rewrite(tmp_path, "a: |+\n  x\n\nb: 1\n", "y") == "a: |-\n  y\nb: 1\n"


def test_literal_clip_to_keep_absorbs_following_blank_lines(tmp_path: Path) -> None:
    """Req 9.4: blank lines after a block become content under keep, so they are rewritten."""
    assert _rewrite(tmp_path, "a: |\n  x\n\nb: 1\n", "y\n\n") == "a: |+\n  y\n\nb: 1\n"


def test_literal_clip_to_clip_keeps_following_blank_lines(tmp_path: Path) -> None:
    """Req 9.5: blank lines after a clip block are not content and stay."""
    assert _rewrite(tmp_path, "a: |\n  x\n\nb: 1\n", "y\nz\n") == "a: |\n  y\n  z\n\nb: 1\n"


def test_literal_rewrite_keeps_indentation_indicator(tmp_path: Path) -> None:
    """Req 9.4, 9.5: `|2` is kept and the content goes at the key column plus 2."""
    text = "k:\n  a: |2\n    old\n"
    assert _rewrite(tmp_path, text, "new\n", "k", "a") == "k:\n  a: |2\n    new\n"


def test_literal_indentation_indicator_with_whitespace_content_line(tmp_path: Path) -> None:
    """Req 9.5: a spaces-only content line under `|2-` belongs to the replaced region."""
    assert _rewrite(tmp_path, "a: |2-\n    x\n   \nb: 1\n", "y") == "a: |2-\n  y\nb: 1\n"


def test_literal_indentation_indicator_gets_chomping_after_it(tmp_path: Path) -> None:
    """Req 9.4: a chomping indicator is added after an existing indentation indicator."""
    assert _rewrite(tmp_path, "a: |2\n  x\nb: 1\n", "y") == "a: |2-\n  y\nb: 1\n"


def test_literal_chomping_removed_before_indentation_indicator(tmp_path: Path) -> None:
    """Req 9.4: a chomping indicator before `2` is replaced in place (here removed for clip)."""
    assert _rewrite(tmp_path, "a: |-2\n  x\nb: 1\n", "y\n") == "a: |2\n  y\nb: 1\n"


def test_literal_rewrite_at_end_of_file_without_break(tmp_path: Path) -> None:
    """Req 9.5: content ending the file without a break is rewritten without a final break."""
    assert _rewrite(tmp_path, "a: |-\n  x", "y\nz") == "a: |-\n  y\n  z"


def test_literal_without_old_content_lines(tmp_path: Path) -> None:
    """Req 9.4, 9.5: an empty block gets content at the owner column plus the step."""
    assert _rewrite(tmp_path, "a: |-\nb: 1\n", "x") == "a: |-\n  x\nb: 1\n"


def test_literal_header_at_end_of_file_without_break(tmp_path: Path) -> None:
    """Req 9.5: an empty block whose header ends the file gets a break before its content."""
    assert _rewrite(tmp_path, "a: |-", "x\ny") == "a: |-\n  x\n  y"


def test_literal_rewrite_in_crlf_file(tmp_path: Path) -> None:
    """Req 9.5, 13.2: the content uses the file's CRLF line breaks."""
    text = b"a: |\r\n  old\r\nb: 1\r\n"
    assert _rewrite(tmp_path, text, "x\ny\n") == "a: |\r\n  x\r\n  y\r\nb: 1\r\n"


def test_literal_rewrite_of_a_list_item(tmp_path: Path) -> None:
    """Req 9.5: a list item's block keeps its content indentation."""
    text = "k:\n  - |\n    old\n  - z\n"
    assert _rewrite(tmp_path, text, "n\nm\n", "k", 0) == "k:\n  - |\n    n\n    m\n  - z\n"


def test_literal_to_empty_string(tmp_path: Path) -> None:
    """Req 9.4: the empty string needs strip and no content lines."""
    assert _rewrite(tmp_path, "a: |\n  x\nb: 1\n", "") == "a: |-\nb: 1\n"


def test_unchanged_value_has_no_edits(tmp_path: Path) -> None:
    """Req 10.1: an unchanged value produces no edit (even with more-indented lines)."""
    _, edits = _edits(tmp_path, "t: >\n  a\n    more\n  b\n", "a\n  more\nb\n", "t")
    assert edits == []


# --- folded blocks -------------------------------------------------------------------------------


def test_folded_changed_paragraph_is_rewrapped_at_its_width(tmp_path: Path) -> None:
    """Req 10.1, 10.2, 10.4, 10.5: the unchanged paragraph keeps its bytes; the changed one is
    wrapped at the width of the old second paragraph (17)."""
    value = "The quick brown fox jumps over.\nSecond para changed now and longer.\n"
    assert _rewrite(tmp_path, FOLDED, value, "text") == (
        "text: >\n  The quick brown fox\n  jumps over.\n\n"
        "  Second para\n  changed now and\n  longer.\nnext: 1\n"
    )


def test_folded_inserted_paragraph_uses_the_blocks_width(tmp_path: Path) -> None:
    """Req 10.1, 10.3: a new paragraph is wrapped at the old value's longest line (19)."""
    value = "The quick brown fox jumps over.\nSecond para here.\nExtra paragraph text that wraps.\n"
    assert _rewrite(tmp_path, FOLDED, value, "text") == (
        "text: >\n  The quick brown fox\n  jumps over.\n\n  Second para here.\n\n"
        "  Extra paragraph\n  text that wraps.\nnext: 1\n"
    )


def test_folded_unchanged_paragraph_keeps_its_unusual_wrapping(tmp_path: Path) -> None:
    """Req 10.1, 10.2: unchanged bytes are kept as written; a word longer than the width (2)
    takes a line of its own."""
    text = "text: >\n  aa\n  bb cc\n\n  dd\n"
    assert _rewrite(tmp_path, text, "aa bb cc\neee ff gg\n", "text") == (
        "text: >\n  aa\n  bb cc\n\n  eee\n  ff\n  gg\n"
    )


def test_folded_separator_bytes_kept_between_unchanged_neighbours(tmp_path: Path) -> None:
    """Req 10.1: an empty line holding spaces between paragraphs keeps its bytes."""
    text = "text: >\n  a\n  \n  b\n\n  c\n"
    assert _rewrite(tmp_path, text, "a\nb\nd\n", "text") == "text: >\n  a\n  \n  b\n\n  d\n"


def test_folded_deleted_paragraph(tmp_path: Path) -> None:
    """Req 10.1, 10.4: a paragraph absent from the new value is dropped with its separator."""
    assert _rewrite(tmp_path, FOLDED, "Second para here.\n", "text") == (
        "text: >\n  Second para here.\nnext: 1\n"
    )


def test_folded_changed_separator(tmp_path: Path) -> None:
    """Req 10.1, 10.4: two line breaks between paragraphs need two empty lines."""
    value = "The quick brown fox jumps over.\n\nSecond para here.\n"
    assert _rewrite(tmp_path, FOLDED, value, "text") == (
        "text: >\n  The quick brown fox\n  jumps over.\n\n\n  Second para here.\nnext: 1\n"
    )


def test_folded_more_new_than_old_paragraphs_pair_in_order(tmp_path: Path) -> None:
    """Req 10.2, 10.3, 10.4: between matched paragraphs the first new paragraph takes the old
    one's width (2) and the extra one the block's width (6)."""
    text = "t: >\n  aaaa\n\n  bb\n\n  cccccc\n"
    value = "aaaa\nx1 x2\ny1 y2 y3\ncccccc\n"
    assert _rewrite(tmp_path, text, value, "t") == (
        "t: >\n  aaaa\n\n  x1\n  x2\n\n  y1 y2\n  y3\n\n  cccccc\n"
    )


def test_folded_chomping_change(tmp_path: Path) -> None:
    """Req 9.4, 10.1: the folded indicator is kept and the chomping becomes strip."""
    value = "The quick brown fox jumps over.\nSecond para here."
    assert _rewrite(tmp_path, FOLDED, value, "text") == (
        "text: >-\n  The quick brown fox\n  jumps over.\n\n  Second para here.\nnext: 1\n"
    )


def test_folded_keep_with_trailing_empty_lines(tmp_path: Path) -> None:
    """Req 9.4, 10.1, 10.3: keep stays and the trailing breaks become empty lines."""
    text = "t: >+\n  a\n\nn: 1\n"
    assert _rewrite(tmp_path, text, "a\nb\n\n", "t") == "t: >+\n  a\n\n  b\n\nn: 1\n"


def test_folded_without_old_content_does_not_wrap(tmp_path: Path) -> None:
    """Req 10.3: without an old content line there is no width and nothing is wrapped."""
    assert _rewrite(tmp_path, "t: >-\nn: 1\n", "a b c", "t") == "t: >-\n  a b c\nn: 1\n"


def test_folded_leading_line_breaks(tmp_path: Path) -> None:
    """Req 10.1: leading line breaks of the value are leading empty lines of the block."""
    assert _rewrite(tmp_path, "t: >\n  a\n", "\nb\n", "t") == "t: >\n\n  b\n"


def test_folded_refuses_a_new_more_indented_line(tmp_path: Path) -> None:
    """Req 10.6: a paragraph starting with a space would be more-indented: exit 3."""
    value = "The quick brown fox jumps over.\n  indented\n"
    with pytest.raises(WriteRefusedError) as raised:
        _edits(tmp_path, FOLDED, value, "text")
    assert raised.value.exit_code == 3
    assert raised.value.path == "text"


def test_folded_refuses_a_new_tab_indented_line(tmp_path: Path) -> None:
    """Req 10.6: a paragraph starting with a TAB would be more-indented: exit 3."""
    with pytest.raises(WriteRefusedError):
        _edits(tmp_path, FOLDED, "x\n\ty\n", "text")


def test_folded_refuses_changing_a_block_with_more_indented_lines(tmp_path: Path) -> None:
    """Req 10.7: an existing folded value with more-indented lines cannot be changed: exit 3."""
    with pytest.raises(WriteRefusedError) as raised:
        _edits(tmp_path, "t: >\n  a\n    more\n  b\n", "x\n", "t")
    assert raised.value.exit_code == 3
    assert raised.value.path == "t"


def test_folded_leading_empty_line_bytes_are_kept(tmp_path: Path) -> None:
    """Req 10.1: unchanged leading empty lines (here holding spaces) keep their bytes."""
    text = "t: >\n  \n  a\n\n  b\n"
    assert _rewrite(tmp_path, text, "\na\nc\n", "t") == "t: >\n  \n  a\n\n  c\n"
