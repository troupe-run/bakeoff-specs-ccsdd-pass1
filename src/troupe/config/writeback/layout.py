"""FileLayout: interpret the raw text of one file.

Lines, columns, line breaks, indentation step, list dash offset, comments, value slots (the key
``:`` and item ``-`` positions), content ends and insertion points.

All positions are character (code point) indices into ``SourceFile.text``; all columns are counted
in code points from the start of the line. A byte order mark at the start of the file is not a
column: columns on the first line are counted from the character after it.
"""

from bisect import bisect_right
from collections.abc import Callable, Iterator
from functools import partial
from typing import Final

from troupe.config.model import (
    CollectionStyle,
    ConfigDocument,
    ConfigMapping,
    ConfigNode,
    ConfigSequence,
    LineEnding,
    SourceFile,
    Span,
)

DEFAULT_INDENT_STEP: Final = 2
"""Indentation step of a file without a block mapping nested under a block mapping key (8.2)."""

_BOM: Final = "\ufeff"
_LF: Final = "\n"
_CRLF: Final = "\r\n"
_CR: Final = "\r"


class FileLayout:
    """The layout of one file's text: lines, columns, line breaks, indentation step, dash offset.

    Built once per file with :meth:`build`; pure and immutable after construction.
    """

    def __init__(
        self,
        file: SourceFile,
        document: ConfigDocument,
        *,
        line_starts: tuple[int, ...],
        line_ends: tuple[int, ...],
        indent_step: int,
        dash_offset: int,
    ) -> None:
        self._file = file
        self._document = document
        self._text = file.text
        self._line_starts = line_starts
        self._line_ends = line_ends
        self._indent_step = indent_step
        self._dash_offset = dash_offset

    @classmethod
    def build(cls, file: SourceFile, document: ConfigDocument) -> "FileLayout":
        """Analyse ``file`` (one of ``document.files``) using the nodes written in that file."""
        line_starts, line_ends = _index_lines(file.text)
        key_columns = partial(_column, file.text, line_starts)
        step = _detect_indent_step(file, document, key_columns)
        offset = _detect_dash_offset(file, document, key_columns)
        return cls(
            file,
            document,
            line_starts=line_starts,
            line_ends=line_ends,
            indent_step=step,
            dash_offset=step if offset is None else offset,
        )

    @property
    def file(self) -> SourceFile:
        """The analysed file."""
        return self._file

    # --- lines and columns -------------------------------------------------------------------

    @property
    def line_count(self) -> int:
        """Number of lines; the (possibly empty) text after the last line break is a line."""
        return len(self._line_starts)

    def line_start(self, line: int) -> int:
        """Index of the first character of ``line`` (0-based)."""
        return self._line_starts[line]

    def line_of(self, index: int) -> int:
        """The 0-based line holding ``index`` (a line break belongs to the line it ends)."""
        return bisect_right(self._line_starts, index) - 1

    def column(self, index: int) -> int:
        """Code-point column of ``index`` on its line (a leading BOM is not counted)."""
        return _column(self._text, self._line_starts, index)

    def line_span(self, line: int) -> Span:
        """The content of ``line`` without its line break."""
        return Span(self._line_starts[line], self._line_ends[line])

    def line_break(self, line: int) -> str:
        """The line break ending ``line``: ``"\\r\\n"``, ``"\\n"``, ``"\\r"`` or ``""`` (none)."""
        end = self._line_ends[line]
        following = self._line_starts[line + 1] if line + 1 < self.line_count else len(self._text)
        return self._text[end:following]

    def line_text(self, line: int) -> str:
        """The content of ``line`` without its line break."""
        return self._text[self._line_starts[line] : self._line_ends[line]]

    def indentation(self, line: int) -> int:
        """Number of leading spaces of ``line`` (a leading BOM is not counted)."""
        content = self.line_text(line)
        if line == 0:
            content = content.removeprefix(_BOM)
        return len(content) - len(content.lstrip(" "))

    def is_blank(self, line: int) -> bool:
        """Whether ``line`` holds only spaces and tabs (or nothing)."""
        return self._content(line).strip(" \t") == ""

    def is_comment_line(self, line: int) -> bool:
        """Whether the first non-space character of ``line`` is ``#``."""
        return self._content(line).lstrip(" ").startswith("#")

    def starts_line(self, index: int) -> bool:
        """Whether only spaces precede ``index`` on its line."""
        line = self.line_of(index)
        before = self._text[self._line_starts[line] : index]
        if line == 0:
            before = before.removeprefix(_BOM)
        return before.strip(" ") == ""

    # --- line breaks for insertions ----------------------------------------------------------

    def line_break_at(self, index: int) -> str:
        """The line break for text inserted at ``index`` (13.2).

        CRLF files get CRLF; LF files and files without any line break get LF; a mixed file gets
        the break of the line the insertion follows (the line holding the character just before
        ``index``), or LF when there is no such line or it has no break.
        """
        ending = self._file.line_ending
        if ending is LineEnding.CRLF:
            return _CRLF
        if ending is not LineEnding.MIXED or index <= 0:
            return _LF
        return self.line_break(self.line_of(index - 1)) or _LF

    # --- indentation step and dash offset ----------------------------------------------------

    def indent_step(self) -> int:
        """The file's indentation step (8.2): from its first nested block mapping, default 2."""
        return self._indent_step

    def dash_offset(self) -> int:
        """Offset of a new block list's ``-`` from its key (8.3); defaults to the step."""
        return self._dash_offset

    def _content(self, line: int) -> str:
        content = self.line_text(line)
        return content.removeprefix(_BOM) if line == 0 else content


def _index_lines(text: str) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """Return the start and content end of every line; CRLF, LF and a lone CR end a line."""
    starts = [0]
    ends: list[int] = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char in (_LF, _CR):
            ends.append(index)
            index += 2 if text.startswith(_CRLF, index) else 1
            starts.append(index)
        else:
            index += 1
    ends.append(length)
    return tuple(starts), tuple(ends)


def _column(text: str, line_starts: tuple[int, ...], index: int) -> int:
    line = bisect_right(line_starts, index) - 1
    start = line_starts[line]
    if line == 0 and text.startswith(_BOM) and index > 0:
        start += 1
    return index - start


def _in_file(node: ConfigNode, file: SourceFile) -> bool:
    return node.origin.file.real_path == file.real_path


def _block_entries_in_file(
    document: ConfigDocument, file: SourceFile
) -> Iterator[tuple[int, ConfigNode]]:
    """Yield ``(key start, value)`` of every entry of a block mapping written in ``file``.

    The value is yielded only when it was also written in ``file`` (an include root's value lies in
    the included file and is skipped). The order is not document order; see :func:`_ordered`.
    """
    stack: list[ConfigNode] = [document.root]
    while stack:
        node = stack.pop()
        children: list[ConfigNode]
        if isinstance(node, ConfigMapping):
            children = [entry.value for entry in node.entries]
            if node.style is CollectionStyle.BLOCK and _in_file(node, file):
                for entry in node.entries:
                    if _in_file(entry.value, file):
                        yield entry.key_origin.span.start, entry.value
        elif isinstance(node, ConfigSequence):
            children = list(node.items)
        else:
            children = []
        stack.extend(reversed(children))


def _detect_indent_step(
    file: SourceFile, document: ConfigDocument, column: Callable[[int], int]
) -> int:
    """Column of the first nested block mapping's keys minus its parent key's column (8.2)."""
    for key_start, value in _ordered(_block_entries_in_file(document, file)):
        if (
            isinstance(value, ConfigMapping)
            and value.style is CollectionStyle.BLOCK
            and value.entries
        ):
            return column(value.entries[0].key_origin.span.start) - column(key_start)
    return DEFAULT_INDENT_STEP


def _detect_dash_offset(
    file: SourceFile, document: ConfigDocument, column: Callable[[int], int]
) -> int | None:
    """Column of the first block list's ``-`` under a mapping key minus the key's column (8.3)."""
    for key_start, value in _ordered(_block_entries_in_file(document, file)):
        if (
            isinstance(value, ConfigSequence)
            and value.style is CollectionStyle.BLOCK
            and value.items
        ):
            return column(value.origin.span.start) - column(key_start)
    return None


def _ordered(entries: Iterator[tuple[int, ConfigNode]]) -> list[tuple[int, ConfigNode]]:
    """Sort entries by their key position, i.e. into document order within the file.

    Sorting also keeps the result correct when a YAML file is included at several places and its
    nodes are visited more than once.
    """
    return sorted(entries, key=lambda pair: pair[0])
