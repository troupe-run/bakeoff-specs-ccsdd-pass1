"""Unit tests of the EditApplier (``troupe.config.writeback.edits``): application of text edits in
position order, refusal of overlapping edits, and the end-of-line comment column fix-up.

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

from pathlib import Path

import pytest

from troupe.config.errors import WriteRefusedError
from troupe.config.loader import load_config
from troupe.config.writeback.edits import EditKind, TextEdit, apply_edits
from troupe.config.writeback.layout import FileLayout


def _layout(tmp_path: Path, config: str | bytes) -> FileLayout:
    target = tmp_path / "troupe.yml"
    target.write_bytes(config.encode("utf-8") if isinstance(config, str) else config)
    document = load_config(target)
    return FileLayout.build(document.root_file, document)


def _replace(layout: FileLayout, old: str, new: str, *, occurrence: int = 0) -> TextEdit:
    """An INLINE edit replacing the ``occurrence``-th appearance of ``old`` with ``new``."""
    text = layout.file.text
    start = -1
    for _ in range(occurrence + 1):
        start = text.index(old, start + 1)
    return TextEdit(start, start + len(old), new, EditKind.INLINE)


def _apply(tmp_path: Path, config: str, old: str, new: str) -> str:
    layout = _layout(tmp_path, config)
    return apply_edits(layout, [_replace(layout, old, new)])


# --- comment columns -----------------------------------------------------------------------------


def test_longer_value_moves_comment_back_to_its_column(tmp_path: Path) -> None:
    """Req 12.1: a longer value keeps the comment at its previous column."""
    assert _apply(tmp_path, "a: 1    # c\n", "1", "100") == "a: 100  # c\n"


def test_shorter_value_moves_comment_back_to_its_column(tmp_path: Path) -> None:
    """Req 12.1: a shorter value keeps the comment at its previous column."""
    assert _apply(tmp_path, "a: 1000 # c\n", "1000", "1") == "a: 1    # c\n"


def test_value_reaching_the_column_gets_exactly_one_space(tmp_path: Path) -> None:
    """Req 12.2: a value ending exactly at the comment's column is separated by one space."""
    assert _apply(tmp_path, "a: 1  # c\n", "1", "123") == "a: 123 # c\n"


def test_value_passing_the_column_gets_exactly_one_space(tmp_path: Path) -> None:
    """Req 12.2: a value passing the comment's column is separated by one space."""
    assert _apply(tmp_path, "a: 1  # c\n", "1", "123456789") == "a: 123456789 # c\n"


def test_value_one_short_of_the_column_keeps_one_space(tmp_path: Path) -> None:
    """Req 12.1, 12.2: a value ending one column before the comment leaves one space."""
    assert _apply(tmp_path, "a: 1   # c\n", "1", "12") == "a: 12  # c\n"
    assert _apply(tmp_path, "a: 1   # c\n", "1", "123") == "a: 123 # c\n"


def test_columns_are_counted_in_code_points_in_the_value(tmp_path: Path) -> None:
    """Req 12.3: multibyte characters in the value count as one column each."""
    config = 'k: "日本"  # c\n'
    assert _apply(tmp_path, config, '"日本"', '"日本語語"') == 'k: "日本語語" # c\n'
    assert _apply(tmp_path, config, '"日本"', '"é"') == 'k: "é"   # c\n'


def test_columns_are_counted_in_code_points_before_the_value(tmp_path: Path) -> None:
    """Req 12.3: multibyte characters in the key count as one column each."""
    assert _apply(tmp_path, "ключ: 1    # c\n", "1", "100") == "ключ: 100  # c\n"
    assert _apply(tmp_path, "ключ: 1000 # c\n", "1000", "é") == "ключ: é    # c\n"


def test_byte_order_mark_is_not_a_column(tmp_path: Path) -> None:
    """Req 12.3: on the first line of a file with a BOM, columns start after the BOM."""
    layout = _layout(tmp_path, "﻿a: 1    # c\n".encode())
    edited = apply_edits(layout, [_replace(layout, "1", "100")])
    assert edited == "﻿a: 100  # c\n"


def test_comment_fixup_touches_no_other_line(tmp_path: Path) -> None:
    """Req 12.4: only the line holding the edited value changes; aligned comments elsewhere stay."""
    config = "a: 1    # c\nb: 2    # d\nc: 3    # e\n"
    assert _apply(tmp_path, config, "2", "2000000") == "a: 1    # c\nb: 2000000 # d\nc: 3    # e\n"


def test_line_without_comment_is_only_replaced(tmp_path: Path) -> None:
    """Req 12.4: a line without an end-of-line comment gets only the replacement."""
    assert _apply(tmp_path, "a: 1    \nb: 2  # d\n", "1", "100") == "a: 100    \nb: 2  # d\n"


def test_hash_inside_quoted_value_is_not_a_comment(tmp_path: Path) -> None:
    """Req 12.1: a ``#`` inside the quoted value is not moved; the real comment is."""
    config = 'a: "x # y"  # c\n'
    assert _apply(tmp_path, config, '"x # y"', '"x # yz"') == 'a: "x # yz" # c\n'


def test_line_insertions_never_move_comments(tmp_path: Path) -> None:
    """Req 12.4: whole-line edits leave every comment where it is."""
    config = "a: 1  # c\nb: 2  # d\n"
    layout = _layout(tmp_path, config)
    at = config.index("b:")
    insert = TextEdit(at, at, "a_very_long_key: a_very_long_value\n", EditKind.LINES)
    assert apply_edits(layout, [insert]) == (
        "a: 1  # c\na_very_long_key: a_very_long_value\nb: 2  # d\n"
    )


def test_line_deletion_and_inline_edit_together(tmp_path: Path) -> None:
    """Req 12.1, 12.4: a whole-line deletion does not disturb the fix-up on another line."""
    config = "a: 1    # c\nb: 2  # d\nc: 3    # e\n"
    layout = _layout(tmp_path, config)
    start = config.index("b:")
    delete = TextEdit(start, config.index("c:"), "", EditKind.LINES)
    edits = [_replace(layout, "3", "300"), delete]
    assert apply_edits(layout, edits) == "a: 1    # c\nc: 300  # e\n"


def test_several_inline_edits_on_different_lines(tmp_path: Path) -> None:
    """Req 12.1, 12.2: each edited line restores its own comment column."""
    config = "a: 1    # c\nb: 22222  # d\n"
    layout = _layout(tmp_path, config)
    edits = [_replace(layout, "22222", "2"), _replace(layout, "1", "1234567")]
    assert apply_edits(layout, edits) == "a: 1234567 # c\nb: 2      # d\n"


def test_several_inline_edits_on_one_line(tmp_path: Path) -> None:
    """Req 12.1: two edits on one flow line restore the single comment once."""
    config = "a: [1, 2]    # c\n"
    layout = _layout(tmp_path, config)
    edits = [_replace(layout, "1", "11"), _replace(layout, "2", "22")]
    assert apply_edits(layout, edits) == "a: [11, 22]  # c\n"


def test_comment_removed_by_an_edit_is_not_fixed(tmp_path: Path) -> None:
    """Req 12.4: when an edit removes the comment, nothing is re-aligned."""
    config = "a: 1    # c\nb: 2\n"
    layout = _layout(tmp_path, config)
    start = config.index("1")
    edit = TextEdit(start, config.index("\n"), "100", EditKind.INLINE)
    assert apply_edits(layout, [edit]) == "a: 100\nb: 2\n"


def test_inline_edit_on_crlf_file(tmp_path: Path) -> None:
    """Req 12.1, 12.4: CRLF line breaks are kept and do not affect the comment column."""
    layout = _layout(tmp_path, b"a: 1    # c\r\nb: 2    # d\r\n")
    edited = apply_edits(layout, [_replace(layout, "2", "200")])
    assert edited == "a: 1    # c\r\nb: 200  # d\r\n"


# --- application and overlap ---------------------------------------------------------------------


def test_no_edits_returns_the_text_unchanged(tmp_path: Path) -> None:
    """Req 13.1: without edits the text is unchanged."""
    layout = _layout(tmp_path, "a: 1  # c\n")
    assert apply_edits(layout, []) == "a: 1  # c\n"


def test_edits_are_applied_in_position_order(tmp_path: Path) -> None:
    """Req 13.1: edits given out of order are applied by position against the original text."""
    config = "a: 1\nb: 2\nc: 3\n"
    layout = _layout(tmp_path, config)
    edits = [
        _replace(layout, "3", "x"),
        TextEdit(len(config), len(config), "d: 4\n", EditKind.LINES),
        _replace(layout, "1", "yy"),
    ]
    assert apply_edits(layout, edits) == "a: yy\nb: 2\nc: x\nd: 4\n"


def test_insertions_at_one_position_keep_planner_order(tmp_path: Path) -> None:
    """Req 13.1: insertions at the same position appear in the order the planner gave them."""
    config = "a: 1\n"
    layout = _layout(tmp_path, config)
    end = len(config)
    edits = [
        TextEdit(end, end, "b: 2\n", EditKind.LINES),
        TextEdit(end, end, "c: 3\n", EditKind.LINES),
    ]
    assert apply_edits(layout, edits) == "a: 1\nb: 2\nc: 3\n"


def test_adjacent_edits_are_allowed(tmp_path: Path) -> None:
    """Req 14.9: edits that touch but do not overlap are applied."""
    config = "a: 12\n"
    layout = _layout(tmp_path, config)
    start = config.index("12")
    edits = [
        TextEdit(start + 1, start + 2, "y", EditKind.INLINE),
        TextEdit(start, start + 1, "x", EditKind.INLINE),
        TextEdit(start + 2, start + 2, "z", EditKind.INLINE),
    ]
    assert apply_edits(layout, edits) == "a: xyz\n"


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ((3, 5), (4, 6)),  # partial overlap
        ((3, 6), (4, 5)),  # nested
        ((3, 5), (3, 5)),  # identical
        ((3, 6), (4, 4)),  # insertion inside a replaced range
    ],
)
def test_overlapping_edits_are_refused(
    tmp_path: Path, first: tuple[int, int], second: tuple[int, int]
) -> None:
    """Req 14.9: overlapping edits raise a write-refused error in either order."""
    layout = _layout(tmp_path, "a: 1234\n")
    one = TextEdit(first[0], first[1], "x", EditKind.INLINE)
    two = TextEdit(second[0], second[1], "y", EditKind.INLINE)
    for edits in ([one, two], [two, one]):
        with pytest.raises(WriteRefusedError):
            apply_edits(layout, edits)


@pytest.mark.parametrize(("start", "end"), [(-1, 0), (3, 2), (8, 9)])
def test_edits_outside_the_text_are_refused(tmp_path: Path, start: int, end: int) -> None:
    """Req 14.9: an edit with an invalid range raises a write-refused error."""
    layout = _layout(tmp_path, "a: 1234\n")
    with pytest.raises(WriteRefusedError):
        apply_edits(layout, [TextEdit(start, end, "x", EditKind.INLINE)])


def test_comment_removed_by_another_edit_is_not_fixed(tmp_path: Path) -> None:
    """Req 12.4, 14.9: a comment deleted by a separate edit is not re-aligned or resurrected."""
    config = "a: 1    # c\nb: 2\n"
    layout = _layout(tmp_path, config)
    value_end = config.index("1") + 1
    remove_comment = TextEdit(value_end, config.index("\n"), "", EditKind.INLINE)
    edits = [_replace(layout, "1", "100"), remove_comment]
    assert apply_edits(layout, edits) == "a: 100\nb: 2\n"


def test_insertion_at_start_of_replacement_goes_before_it(tmp_path: Path) -> None:
    """Req 14.9: an insertion at a replacement's start does not overlap it, in either order."""
    config = "a: 12\n"
    layout = _layout(tmp_path, config)
    start = config.index("12")
    insert = TextEdit(start, start, "x", EditKind.INLINE)
    replace = TextEdit(start, start + 2, "y", EditKind.INLINE)
    for edits in ([insert, replace], [replace, insert]):
        assert apply_edits(layout, edits) == "a: xy\n"


def test_lines_edit_ending_on_a_commented_line_does_not_align(tmp_path: Path) -> None:
    """Req 12.4: only INLINE edits trigger the fix-up, even when a LINES edit ends mid-line."""
    config = "a: 1  # c\n"
    layout = _layout(tmp_path, config)
    edit = TextEdit(0, 1, "abc", EditKind.LINES)
    assert apply_edits(layout, [edit]) == "abc: 1  # c\n"
