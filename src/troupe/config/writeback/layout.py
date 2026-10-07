"""FileLayout: interpret the raw text of one file.

Lines, columns, line breaks, indentation step, list dash offset, comments, value slots (the key
``:`` and item ``-`` positions), content ends and insertion points.

Anchors, aliases and merge keys (config-aliases research.md, Decision 3 and "one node, one written
place"): an anchored value's ``&name`` property belongs to its slot's indicator (``indicator_end``
is after it) and is a token for comment detection. A ``!literal`` tag (written after any anchor)
belongs to its scalar: the scalar's inline extent starts at the tag, and the tag is a token for
comment detection (config-remote-schema 8.4). In every walk over the nodes written in a file
an alias place is a leaf written at its ``*name`` token, and a mapping with a merge key is walked
as written (its own entries and the ``<<`` entry, ``merge.written``), never as its merged view.

All positions are character (code point) indices into ``SourceFile.text``; all columns are counted
in code points from the start of the line. A byte order mark at the start of the file is not a
column: columns on the first line are counted from the character after it.
"""

from bisect import bisect_right
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from functools import partial
from typing import Final

from troupe.config.constants import LITERAL_TAG
from troupe.config.model import (
    CollectionStyle,
    ConfigDocument,
    ConfigMapping,
    ConfigNode,
    ConfigScalar,
    ConfigSequence,
    LineEnding,
    MappingEntry,
    ScalarStyle,
    SourceFile,
    Span,
)
from troupe.config.overrides import written_parts
from troupe.config.paths import ConfigPath

DEFAULT_INDENT_STEP: Final = 2
"""Indentation step of a file without a block mapping nested under a block mapping key (8.2)."""

_BOM: Final = "\ufeff"
_LF: Final = "\n"
_CRLF: Final = "\r\n"
_CR: Final = "\r"
_COMMENT: Final = "#"
_DASH: Final = "-"
_COLON: Final = ":"
_INLINE_SPACE: Final = " \t"
_BLOCK_HEADER_INDICATORS: Final = frozenset("0123456789+-")
_KEEP: Final = "+"


@dataclass(frozen=True)
class ValueSlot:
    """Where a value sits in its file: the owning key or ``-`` and the index just after ``:``/``-``.

    For an item of a flow sequence there is no indicator: ``owner_column`` is the column of the item
    and ``indicator_end`` its start. When the value is written with an anchor property after the
    ``:`` or ``-`` (``key: &name value``), ``indicator_end`` is just after the property, so every
    replacement from it keeps the anchor (config-aliases 4.3).
    """

    file: SourceFile
    path: ConfigPath
    parent_style: CollectionStyle  # style of the containing collection
    in_sequence: bool  # item of a sequence (True) or value of a mapping entry (False)
    owner_column: int  # key column or '-' column
    indicator_end: int  # index just after ':' or '-' (and after the value's anchor property)
    node: ConfigNode


@dataclass(frozen=True)
class InsertionPoint:
    """Where a new entry of a block collection is inserted (11.1, 11.2, 11.6).

    ``needs_leading_break`` is true when ``index`` is the end of a file whose last line has content
    but no line break: the inserted text then starts with a line break and ends without one.
    """

    index: int
    needs_leading_break: bool


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
        # Lazily filled caches keyed by node identity; the document keeps the nodes alive.
        self._dash_cache: dict[int, tuple[int, ...]] = {}
        self._key_columns: dict[int, int] = self._block_scalar_key_columns()
        self._floors: dict[int, int] = self._token_floors()

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

    # --- end-of-line comments ----------------------------------------------------------------

    def comment_start(self, line: int) -> int | None:
        """Index of the comment's ``#`` on ``line``; ``None`` when the line has no comment (12.x).

        The comment is the first ``#`` after the line's token-end floor (the greatest end of any
        key, non-empty flow scalar, ``file:`` directive, flow collection bracket, block scalar
        header, ``:`` or ``-`` indicator on the line) that starts the line's content or follows a
        space. A line inside a multi-line scalar or a block scalar's content has no comment. A TAB
        before a comment is out of contract. Whole comment lines report their ``#``.
        """
        start = self._line_starts[line]
        if line == 0 and self._text.startswith(_BOM):
            start += 1
        end = self._line_ends[line]
        index = self._text.find(_COMMENT, max(start, self._floors.get(line, start)), end)
        while index != -1:
            if index == start or self._text[index - 1] == " ":
                return index
            index = self._text.find(_COMMENT, index + 1, end)
        return None

    # --- value slots -------------------------------------------------------------------------

    def entry_slot(
        self, mapping: ConfigMapping, entry: MappingEntry, path: ConfigPath
    ) -> ValueSlot:
        """The slot of ``entry``'s value: the key column and the index just after its ``:``.

        On the merged view of a mapping with a merge key only its own entries have a slot here;
        an entry received from the merge key is written elsewhere (raises :class:`ValueError`).
        """
        if entry.via_merge:
            raise ValueError(f"the entry {entry.key!r} comes from a merge key")
        self._require_here(entry.key_origin.file, "key")
        return ValueSlot(
            file=self._file,
            path=path,
            parent_style=mapping.style,
            in_sequence=False,
            owner_column=self.column(entry.key_origin.span.start),
            indicator_end=self._property_end(entry.value, self._colon_end(entry)),
            node=entry.value,
        )

    def item_slot(self, sequence: ConfigSequence, index: int, path: ConfigPath) -> ValueSlot:
        """The slot of item ``index``: the column of its ``-`` and the index just after it.

        Dashes are found from the sequence start and the content end of the previous item, never
        from an empty item's span. A flow item's slot is the item itself (no indicator).
        """
        item = sequence.items[index]
        if sequence.style is CollectionStyle.BLOCK:
            self._require_here(sequence.origin.file, "sequence")
            dash = self._dashes(sequence)[index]
            owner, indicator_end = dash, self._property_end(item, dash + 1)
        else:
            owner = indicator_end = self._value_start(item)
        return ValueSlot(
            file=self._file,
            path=path,
            parent_style=sequence.style,
            in_sequence=True,
            owner_column=self.column(owner),
            indicator_end=indicator_end,
            node=item,
        )

    def has_property(self, slot: ValueSlot) -> bool:
        """Whether the slot's value is written with its own ``&name`` property after the slot's
        ``:`` or ``-`` (``indicator_end`` is just after it, config-aliases 4.3)."""
        span = self._property(slot.node)
        return span is not None and span.end == slot.indicator_end

    # --- content ends, extents and body lines ------------------------------------------------

    def content_end(self, node: ConfigNode, slot: ValueSlot | None) -> int:
        """The index just after the last character of ``node``'s content in this file.

        Non-empty flow scalars, ``file:`` directives and flow collections end at their span end; an
        empty null ends at its slot's indicator end (so it needs ``slot``); a block scalar ends at
        the end of its last non-blank content line for clip and strip chomping, and at the end of
        its last line (trailing blank lines included) for keep; a block collection ends where its
        last entry ends. Line breaks are never included.
        """
        if slot is not None and slot.node is not node:
            raise ValueError("the slot does not hold this node")
        return self._value_end(node, None if slot is None else slot.indicator_end)

    def inline_extent(self, slot: ValueSlot) -> Span:
        """The part of the slot's value on the slot's line.

        The span of a non-empty flow scalar, directive or flow collection; the empty span at the
        indicator end for an empty value or a block collection starting on a later line; the header
        indicators of a block scalar; for a scalar with a ``!literal`` tag written here, from the
        tag on (so a replacement removes the tag, while an anchor before it stays,
        config-remote-schema 8.4); for a block collection starting on the slot's line (compact
        list item) the text from its start to the line's content end without the end-of-line
        comment and the spaces before it; the ``*name`` token of an alias place.
        """
        node = self._local(slot.node)
        if isinstance(node, Span):
            return node
        span = node.origin.span
        if _is_empty_null(node):
            return Span(slot.indicator_end, slot.indicator_end)
        # A `!literal` scalar's extent starts at its tag (config-remote-schema 8.4).
        start = self._written_start(node)
        if _is_block_scalar(node):
            return Span(start, self._header_end(span.start))
        if isinstance(node, ConfigScalar):
            return Span(start, span.end)
        if node.style is CollectionStyle.FLOW:
            return span
        start = self._collection_start(node)
        line = self.line_of(start)
        if line != self.line_of(slot.indicator_end):
            return Span(slot.indicator_end, slot.indicator_end)
        comment = self.comment_start(line)
        end = self._line_ends[line] if comment is None else comment
        while end > start and self._text[end - 1] in _INLINE_SPACE:
            end -= 1
        return Span(start, end)

    def body_lines(self, slot: ValueSlot) -> Span | None:
        """The whole lines (with breaks) of a block value below its inline extent, or ``None``.

        Only block scalars and block collections have body lines: from the line after the inline
        extent's end through the line of the content end.
        """
        node = self._local(slot.node)
        if isinstance(node, Span):
            return None
        block = _is_block_scalar(node) or (
            not isinstance(node, ConfigScalar) and node.style is CollectionStyle.BLOCK
        )
        if not block:
            return None
        first = self.line_of(self.inline_extent(slot).end) + 1
        last = self.line_of(self.content_end(slot.node, slot))
        if first > last:
            return None
        return Span(self._line_starts[first], self._next_line_start(last))

    def entries_column(self, collection: ConfigMapping | ConfigSequence) -> int:
        """Column of the first key or ``-`` (of the first item of a flow sequence); 0 when empty."""
        if isinstance(collection, ConfigMapping):
            collection = _written_mapping(collection)
            if not collection.entries:
                return self.column(collection.origin.span.start)
            return self.column(collection.entries[0].key_origin.span.start)
        if not collection.items:
            return self.column(collection.origin.span.start)
        return self.column(self._collection_start(collection))

    # --- insertion points --------------------------------------------------------------------

    def insertion_point(self, collection: ConfigMapping | ConfigSequence) -> InsertionPoint:
        """Where a new entry of the block ``collection`` goes (11.1, 11.2, 11.6).

        Start at the line after the last entry's content end; skip blank lines tentatively and
        accept comment lines indented at least as far as the entries; stop at any other line. The
        insertion point is the start of the line after the last accepted line (blank lines not
        followed by an accepted comment stay below it). An empty root inserts at the end of file.
        At a file end without a final line break the point is the end of file with a leading break.
        """
        self._require_here(collection.origin.file, "collection")
        if collection.style is not CollectionStyle.BLOCK:
            raise ValueError("insertion points exist only for block collections")
        if isinstance(collection, ConfigMapping):
            collection = _written_mapping(collection)
        empty = (
            not collection.entries
            if isinstance(collection, ConfigMapping)
            else not collection.items
        )
        if empty:
            return self._end_of_file_point()
        column = self.entries_column(collection)
        insert_line = self.line_of(self.content_end(collection, None)) + 1
        line = insert_line
        while line < self.line_count:
            if self.is_blank(line):
                line += 1
            elif self.is_comment_line(line) and self.indentation(line) >= column:
                line += 1
                insert_line = line
            else:
                break
        if insert_line >= self.line_count:
            return self._end_of_file_point()
        return InsertionPoint(self._line_starts[insert_line], needs_leading_break=False)

    # --- internals ---------------------------------------------------------------------------

    def _end_of_file_point(self) -> InsertionPoint:
        last = self.line_count - 1
        return InsertionPoint(len(self._text), needs_leading_break=self._content(last) != "")

    def _next_line_start(self, line: int) -> int:
        return self._line_starts[line + 1] if line + 1 < self.line_count else len(self._text)

    def _here(self, file: SourceFile) -> bool:
        return file.key == self._file.key

    def _require_here(self, file: SourceFile, what: str) -> None:
        if not self._here(file):
            raise ValueError(f"the {what} is not written in {self._file.shown}")

    def _local(self, node: ConfigNode) -> ConfigNode | Span:
        """What is written in this file at the slot holding ``node``.

        An alias place gives its ``*name`` token span (when written here); a mapping-form include
        root gives its ``$ref`` mapping as written (when written here); a scalar-form include root
        gives its ``file:`` directive span (when here); a mapping with a merge key gives the mapping
        as written (when here); any other node gives itself (when here). Raises
        :class:`ValueError` otherwise.
        """
        alias = node.alias
        if alias is not None:
            if self._here(alias.token.file):
                return alias.token.span
            raise ValueError(f"the alias is not written in {self._file.shown}")
        include = node.include
        if (
            include is not None
            and include.form is not None
            and self._here(include.form.origin.file)
        ):
            return include.form
        if self._here(node.origin.file):
            return _as_written(node)
        if include is not None and self._here(include.directive.file):
            return include.directive.span
        raise ValueError(f"the node is not written in {self._file.shown}")

    def _colon_end(self, entry: MappingEntry) -> int:
        """Index just after the ``:`` following ``entry``'s key (spaces may come between)."""
        colon_end = self._find_colon_end(entry)
        if colon_end is None:
            raise ValueError(f"no ':' after key {entry.key!r} in {self._file.shown}")
        return colon_end

    def _find_colon_end(self, entry: MappingEntry) -> int | None:
        """Like :meth:`_colon_end`, but ``None`` for out-of-contract keys (``? k``, ``{k}``)."""
        index = entry.key_origin.span.end
        while index < len(self._text) and self._text[index] in _INLINE_SPACE:
            index += 1
        return index + 1 if self._text.startswith(_COLON, index) else None

    def _property_end(self, node: ConfigNode, indicator_end: int) -> int:
        """``indicator_end`` moved past the anchor property written on ``node`` after it (4.3)."""
        span = self._property(node)
        if span is None or span.start < indicator_end:
            return indicator_end
        return span.end

    def _property(self, node: ConfigNode) -> Span | None:
        """The span of the ``&name`` property written in this file for ``node``, or ``None``.

        An alias place has none (it also carries its anchored node's anchor). A plain node's
        anchor lies in its own file. The anchor of an include lies in the including file: on the
        ``$ref`` mapping as written, or before the ``file:`` directive (an anchor taken over from
        the included file's root lies in that file and is not before the directive).
        """
        if node.alias is not None:
            return None
        include = node.include
        if include is None:
            anchor = node.anchor
            here = anchor is not None and self._here(node.origin.file)
            return anchor.span if anchor is not None and here else None
        if include.form is not None:
            return self._property(include.form)
        anchor = node.anchor
        if anchor is None or not self._here(include.directive.file):
            return None
        span, directive = anchor.span, include.directive.span
        written = self._text[span.start : span.end] == f"&{anchor.name}"
        between = self._text[span.end : directive.start]
        if written and span.end <= directive.start and between.strip(" \t") == "":
            return span
        return None

    def _value_start(self, node: ConfigNode) -> int:
        local = self._local(node)
        return local.start if isinstance(local, Span) else self._written_start(local)

    def _written_start(self, node: ConfigNode) -> int:
        """Start of ``node`` as written here: its ``!literal`` tag when it has one written in this
        file (the node's own span excludes the tag), else its span start."""
        tag = self._literal_tag(node)
        return node.origin.span.start if tag is None else tag.start

    def _literal_tag(self, node: ConfigNode) -> Span | None:
        """The span of the ``!literal`` tag written in this file on scalar ``node``, or ``None``."""
        if not isinstance(node, ConfigScalar) or node.literal is None:
            return None
        return node.literal if self._here(node.origin.file) else None

    def _collection_start(self, node: ConfigMapping | ConfigSequence) -> int:
        """First key of a block mapping, first ``-`` of a block sequence, else the span start."""
        if isinstance(node, ConfigMapping):
            node = _written_mapping(node)
        if node.style is CollectionStyle.BLOCK:
            if isinstance(node, ConfigMapping) and node.entries:
                return node.entries[0].key_origin.span.start
            if isinstance(node, ConfigSequence) and node.items:
                return self._dashes(node)[0]
        return node.origin.span.start

    def _dashes(self, sequence: ConfigSequence) -> tuple[int, ...]:
        """Index of every item's ``-`` of a block sequence written in this file.

        The first ``-`` is the sequence start; every further ``-`` starts the first line after the
        previous item's content end that is neither blank nor a comment line.
        """
        cached = self._dash_cache.get(id(sequence))
        if cached is not None:
            return cached
        first = sequence.origin.span.start
        self._expect(first, _DASH)
        dashes = [first]
        for item in sequence.items[:-1]:
            end = self._value_end(item, self._property_end(item, dashes[-1] + 1))
            line = self.line_of(end) + 1
            while line < self.line_count and (self.is_blank(line) or self.is_comment_line(line)):
                line += 1
            if line >= self.line_count:
                raise ValueError("a list item's '-' was not found")
            dash = self._line_starts[line] + self.indentation(line)
            if line == 0 and self._text.startswith(_BOM):
                dash += 1
            self._expect(dash, _DASH)
            dashes.append(dash)
        result = tuple(dashes)
        self._dash_cache[id(sequence)] = result
        return result

    def _expect(self, index: int, char: str) -> None:
        if not self._text.startswith(char, index):
            raise ValueError(f"expected {char!r} at index {index} of {self._file.shown}")

    def _value_end(self, node: ConfigNode, indicator_end: int | None) -> int:
        """Content end of ``node``; ``indicator_end`` locates an empty null value."""
        local = self._local(node)
        if isinstance(local, Span):
            return local.end
        node = local
        if _is_empty_null(node):
            if indicator_end is None:
                raise ValueError("an empty value is located only through its slot")
            return indicator_end
        if _is_block_scalar(node):
            return self._block_scalar_end(node.origin.span)
        if isinstance(node, ConfigScalar) or node.style is CollectionStyle.FLOW:
            return node.origin.span.end
        if isinstance(node, ConfigMapping):
            if not node.entries:
                return node.origin.span.start
            last = node.entries[-1]
            empty = _is_empty_null(last.value)
            colon_end = self._property_end(last.value, self._colon_end(last)) if empty else None
            return self._value_end(last.value, colon_end)
        if not node.items:
            return node.origin.span.start
        last_item = node.items[-1]
        return self._value_end(last_item, self._property_end(last_item, self._dashes(node)[-1] + 1))

    def _header_end(self, start: int) -> int:
        """Index after a block scalar's header indicators (``|``/``>``, indentation, chomping)."""
        index = start + 1
        while index < len(self._text) and self._text[index] in _BLOCK_HEADER_INDICATORS:
            index += 1
        return index

    def _block_scalar_end(self, span: Span) -> int:
        """Content end of a block scalar from its lines and chomping (its span may overrun)."""
        header_end = self._header_end(span.start)
        header_line = self.line_of(span.start)
        last = self.line_of(max(span.end - 1, span.start))
        if last <= header_line:
            return header_end
        if _KEEP in self._text[span.start : header_end]:
            return self._line_ends[last]
        lines = range(header_line + 1, last + 1)
        content_indent = self._explicit_indentation(span.start)
        if content_indent is None:
            indents = [self.indentation(n) for n in lines if not self.is_blank(n)]
            if not indents:
                return header_end
            content_indent = min(indents)
        # A whitespace-only line longer than the content indentation is content (its spaces).
        for line in reversed(lines):
            if len(self.line_text(line)) > content_indent:
                return self._line_ends[line]
        return header_end

    def _explicit_indentation(self, header: int) -> int | None:
        """Content indentation set by the indentation indicator of the block header at ``header``.

        The indicator (``|2``) counts from the owner's column: the key of a mapping value, the
        ``-`` of a list item written on the header's line. ``None`` without an indicator or when
        the owner is not found (the indentation then follows from the content lines).
        """
        digits = [c for c in self._text[header : self._header_end(header)] if c.isdigit()]
        if not digits:
            return None
        owner = self._key_columns.get(header)
        if owner is None:
            index = header
            while index > 0 and self._text[index - 1] in _INLINE_SPACE:
                index -= 1
            if self._text.endswith(LITERAL_TAG, 0, index):
                # ``- !literal |2``: the tag stands between the ``-`` and the header.
                index -= len(LITERAL_TAG)
                while index > 0 and self._text[index - 1] in _INLINE_SPACE:
                    index -= 1
            if index == 0 or self._text[index - 1] != _DASH:
                return None
            owner = self.column(index - 1)
        return owner + int(digits[0])

    def _block_scalar_key_columns(self) -> dict[int, int]:
        """Header index of every block scalar mapping value written here -> its key's column."""
        columns: dict[int, int] = {}
        stack: list[ConfigNode] = list(_written(self._document.root))
        while stack:
            node = stack.pop()
            if isinstance(node, ConfigMapping):
                for entry in node.entries:
                    value = entry.value
                    written_here = value.alias is None and self._here(value.origin.file)
                    if _is_block_scalar(value) and written_here:
                        key = entry.key_origin.span.start
                        columns[value.origin.span.start] = self.column(key)
            stack.extend(_children(node))
        return columns

    def _token_floors(self) -> dict[int, int]:
        """For every line holding tokens, the greatest token end on it (see :meth:`comment_start`).

        Lines inside a multi-line token or a block scalar's content get their content end.
        """
        floors: dict[int, int] = {}

        def token(start: int, end: int) -> None:
            first, last = self.line_of(start), self.line_of(end - 1)
            for line in range(first, last):
                floors[line] = self._line_ends[line]
            floors[last] = max(floors.get(last, end), end)

        def visit(node: ConfigNode) -> None:
            if node.alias is not None:
                if self._here(node.alias.token.file):
                    alias = node.alias.token.span
                    token(alias.start, alias.end)
                return
            anchor = self._property(node)
            if anchor is not None:
                token(anchor.start, anchor.end)
            parts = _parts(node)
            if parts is not None:
                for part in parts:
                    visit(part)
                return
            if not self._here(node.origin.file):
                if node.include is not None and self._here(node.include.directive.file):
                    directive = node.include.directive.span
                    token(directive.start, directive.end)
                    return
                # Nodes of other files may hold include roots written in this file.
                for child in _children(node):
                    visit(child)
                return
            span = node.origin.span
            if isinstance(node, ConfigScalar):
                tag = self._literal_tag(node)
                if tag is not None:
                    token(tag.start, tag.end)
                if _is_empty_null(node):
                    return
                if _is_block_scalar(node):
                    token(span.start, self._header_end(span.start))
                    end_line = self.line_of(self._block_scalar_end(span))
                    for line in range(self.line_of(span.start) + 1, end_line + 1):
                        floors[line] = self._line_ends[line]
                    return
                token(span.start, span.end)
                return
            if node.style is CollectionStyle.FLOW and span.end > span.start:
                token(span.start, span.start + 1)
                token(span.end - 1, span.end)
            if isinstance(node, ConfigMapping):
                for entry in node.entries:
                    key = entry.key_origin.span
                    if key.end > key.start:
                        token(key.start, key.end)
                    colon_end = self._find_colon_end(entry)
                    if colon_end is not None:
                        token(colon_end - 1, colon_end)
                    visit(entry.value)
            elif node.style is CollectionStyle.BLOCK:
                for dash, item in zip(self._dashes(node), node.items, strict=True):
                    token(dash, dash + 1)
                    visit(item)
            else:
                for item in node.items:
                    visit(item)

        visit(self._document.root)
        return floors

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
    return node.origin.file.key == file.key


def _block_entries_in_file(
    document: ConfigDocument, file: SourceFile
) -> Iterator[tuple[int, ConfigNode]]:
    """Yield ``(key start, value)`` of every entry of a block mapping written in ``file``.

    The value is yielded only when it was also written in ``file`` (an include root's value lies in
    the included file and is skipped; an alias place is written as its token and is skipped). The
    order is not document order; see :func:`_ordered`.
    """
    stack: list[ConfigNode] = list(_written(document.root))
    while stack:
        node = stack.pop()
        if (
            node.alias is None
            and isinstance(node, ConfigMapping)
            and node.style is CollectionStyle.BLOCK
            and _in_file(node, file)
        ):
            for entry in node.entries:
                value = _local_value(entry.value, file)
                if value is not None:
                    yield entry.key_origin.span.start, value
        stack.extend(_children(node))


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


def _parts(node: ConfigNode) -> tuple[ConfigNode, ...] | None:
    """The written parts of a merged mapping or a bare mapping-form include, else ``None``."""
    parts = written_parts(node)
    return None if len(parts) == 1 and parts[0] is node else parts


def _written(node: ConfigNode) -> tuple[ConfigNode, ...]:
    """The nodes written in files for ``node``: its written parts, recursively.

    A merged mapping's entries are never all written in one file, so walks over files visit its
    two sides instead; a mapping-form include root gives its ``$ref`` mapping as well; a mapping
    with a merge key gives the mapping as written; an alias place is a leaf.
    """
    parts = _parts(node)
    if parts is None:
        return (node,)
    return tuple(written for part in parts for written in _written(part))


def _children(node: ConfigNode) -> tuple[ConfigNode, ...]:
    """The written children of a node written in one file (see :func:`_written`).

    An alias place is a leaf: its content is written at its anchor.
    """
    values: tuple[ConfigNode, ...]
    if node.alias is not None:
        return ()
    if isinstance(node, ConfigMapping):
        values = tuple(entry.value for entry in node.entries)
    elif isinstance(node, ConfigSequence):
        values = node.items
    else:
        return ()
    return tuple(written for value in values for written in _written(value))


def _local_value(node: ConfigNode, file: SourceFile) -> ConfigNode | None:
    """What ``file`` holds for an entry value: its ``$ref`` mapping, itself, or ``None``.

    An alias place gives ``None``: what is written for it is its token, not a collection.
    """
    if node.alias is not None:
        return None
    include = node.include
    if include is not None and include.form is not None and _in_file(include.form, file):
        return include.form
    return _as_written(node) if _in_file(node, file) else None


def _as_written(node: ConfigNode) -> ConfigNode:
    """What is written for ``node`` in its own file: see :func:`_written_mapping`."""
    return _written_mapping(node) if isinstance(node, ConfigMapping) else node


def _written_mapping(mapping: ConfigMapping) -> ConfigMapping:
    """The mapping as written behind the merged view of a mapping with a merge key, else itself.

    A merged mapping of a ``$ref`` include (``layers``) keeps its own treatment.
    """
    if mapping.merge is not None and mapping.layers is None:
        return mapping.merge.written
    return mapping


def _is_empty_null(node: ConfigNode) -> bool:
    """Whether ``node`` is a zero-length null (``key:``), whose span is unreliable."""
    return (
        isinstance(node, ConfigScalar)
        and node.include is None
        and node.style is ScalarStyle.PLAIN
        and node.value is None
        and node.origin.span.start == node.origin.span.end
    )


def _is_block_scalar(node: ConfigNode) -> bool:
    return (
        isinstance(node, ConfigScalar)
        and node.include is None
        and node.style in (ScalarStyle.LITERAL, ScalarStyle.FOLDED)
    )
