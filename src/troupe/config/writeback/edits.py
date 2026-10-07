"""EditApplier: text edits on one file, non-overlap check, application, comment-column fix-up.

Edits are character (code point) ranges on one file's original text. They are applied in position
order; overlapping edits are a planner bug and refused with :class:`WriteRefusedError` (14.9).
After INLINE edits, each end-of-line comment on a line holding the end of such an edit is moved back
to its original column, separated from the value by exactly one space when the value reaches that
column (12.1, 12.2). Columns are counted in code points (12.3); no other line is touched (12.4).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Final

from troupe.config.errors import WriteRefusedError
from troupe.config.writeback.layout import FileLayout

_BOM: Final = "﻿"
_LINE_BREAKS: Final = "\r\n"


class EditKind(Enum):
    """Whether an edit stays within line content or works on whole lines."""

    INLINE = "inline"  # replaces characters within line content; triggers comment fix-up
    LINES = "lines"  # inserts or deletes whole lines; never triggers comment fix-up


@dataclass(frozen=True)
class TextEdit:
    """Replace ``[start, end)`` of the original text with ``text`` (``start == end`` inserts)."""

    start: int
    end: int
    text: str
    kind: EditKind


def apply_edits(layout: FileLayout, edits: Sequence[TextEdit]) -> str:
    """Return the text of ``layout``'s file with ``edits`` applied and comment columns restored.

    Edits are sorted by position; at equal positions insertions come before a replacement starting
    there and otherwise the planner's order is kept. Raises :class:`WriteRefusedError` for an edit
    outside the text or for overlapping edits.
    """
    text = layout.file.text
    ordered = sorted(edits, key=lambda edit: (edit.start, edit.end))
    _check(layout, ordered)
    comments = _comments_to_fix(layout, ordered)

    pieces: list[str] = []
    new_comments: dict[int, int] = {}  # new index of the ``#`` -> its original column
    position = 0
    shift = 0
    pending = sorted(comments)
    for edit in ordered:
        while pending and pending[0] < edit.end:
            index = pending.pop(0)
            new_comments[index + shift] = comments[index]
        pieces.append(text[position : edit.start])
        pieces.append(edit.text)
        shift += len(edit.text) - (edit.end - edit.start)
        position = edit.end
    for index in pending:
        new_comments[index + shift] = comments[index]
    pieces.append(text[position:])
    edited = "".join(pieces)

    # Fix from the end so earlier indices stay valid.
    for index in sorted(new_comments, reverse=True):
        edited = _align(edited, index, new_comments[index])
    return edited


def _check(layout: FileLayout, ordered: Sequence[TextEdit]) -> None:
    length = len(layout.file.text)
    previous_end = 0
    for edit in ordered:
        if not 0 <= edit.start <= edit.end <= length:
            raise WriteRefusedError(
                f"internal error: edit [{edit.start}, {edit.end}) lies outside the text",
                file=layout.file.shown,
            )
        if edit.start < previous_end:
            raise WriteRefusedError(
                f"internal error: overlapping edits at index {edit.start}", file=layout.file.shown
            )
        previous_end = edit.end


def _comments_to_fix(layout: FileLayout, ordered: Sequence[TextEdit]) -> dict[int, int]:
    """Original index of each comment ``#`` to re-align -> its original column."""
    comments: dict[int, int] = {}
    for edit in ordered:
        if edit.kind is not EditKind.INLINE:
            continue
        comment = layout.comment_start(layout.line_of(edit.end))
        if comment is None or comment <= edit.end:
            continue
        if any(other.start <= comment < other.end for other in ordered):
            continue  # the comment itself is removed by an edit
        comments[comment] = layout.column(comment)
    return comments


def _align(text: str, comment: int, column: int) -> str:
    """Resize the spaces before the ``#`` at ``comment`` so it lands at ``column`` (min. 1)."""
    run_start = comment
    while run_start > 0 and text[run_start - 1] == " ":
        run_start -= 1
    line_start = max(text.rfind(char, 0, run_start) for char in _LINE_BREAKS) + 1
    run_column = run_start - line_start
    if line_start == 0 and text.startswith(_BOM):
        run_column -= 1
    return text[:run_start] + " " * max(1, column - run_column) + text[comment:]
