"""Expander: replace one alias by a copy of its anchored node (config-aliases Requirements 7,
8.3-8.5, 9.1; research.md Decision 7 and "Rendering a copy of the anchored node").

:func:`expand_alias` plans the text edits of one alias site in its file and returns the new text;
it loads nothing (only the setter reloads). The copy is rendered from the anchored node **as
written** (``AliasRef.anchored``), so styles and line breaks are the file's own.

Preconditions, each refused with :class:`WriteRefusedError` (exit 3) naming the alias's file and
the alias site's path:

* the alias is not inside a flow collection (8.4): a block copy cannot be written there;
* no line from the key's (or item's) indicator to the alias token has a comment (8.5): the line
  would get a second comment, and a comment between them would be lost;
* the anchored node's written structure has no mapping with a merge key (9.1): a copy would write
  ``<<``. Inner alias places and includes are not part of that structure (they are copied as
  written).

The key's line (7.1, 7.3, 8.3): the text from the slot's indicator end (after ``:`` or ``-``) to
the end of the alias token, plus the spaces after it, is replaced by `` <inline>  <comment>``, or
by ``  <comment>`` when the copy has no inline part, where the comment is
:data:`EXPANDED_COMMENT`. The inline part is:

* a single-line scalar's source text, verbatim (keeps its quoting, 7.4), and nothing for an
  empty null;
* a multi-line flow scalar rendered by :func:`render_scalar` with its own style preferred (7.4);
* ``{}``/``[]`` for an empty collection;
* a literal or folded block scalar's header indicators; its content lines follow the key's line,
  shifted by the difference between the alias's and the anchor's owner columns (the key or ``-``),
  so an indentation indicator stays valid. The anchor's header comment is not copied (7.6);
* an include string's directive token as written (7.4).

A scalar written with the ``!literal`` tag is copied with it (``!literal`` and one space before
the text above; a block scalar's header indicators start at the tag), so the copy still loads as
a string and never as an include (config-remote-schema 10.2).

A non-empty mapping or list (7.2, 8.3) has no inline part: its copy is written in block style on
the lines after the key's line (or the list item's line), under the column rules of
``collection_render.render_block_value`` with the file's indentation step, dash offset and line
break: a mapping one step right of its key, a list with its ``-`` at the dash offset, a list item's
collection two columns right of its ``-`` (compact below the copy's root, so a nested item's first
entry follows ``- ``). Every nested collection is in block style (flow ones too), and an empty one
is ``{}``/``[]``. Keys and single-line scalars copy their source token text (a key written over
several lines is rendered in its own style), multi-line flow scalars and block scalars are written
as on the key's line, an include as its directive token or its ``$ref`` mapping (``include.form``)
in block style, and an inner alias place as ``*name`` (7.5). Only the text after an anchor
property is ever copied, so no anchor is repeated (7.5).

Comments inside the anchored node are never copied and no other byte changes (7.6).
"""

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from troupe.config.constants import LITERAL_TAG
from troupe.config.errors import WriteRefusedError
from troupe.config.model import (
    CollectionStyle,
    ConfigDocument,
    ConfigMapping,
    ConfigNode,
    ConfigScalar,
    ConfigSequence,
    MappingEntry,
    Origin,
    ScalarStyle,
)
from troupe.config.paths import ROOT, ConfigPath
from troupe.config.writeback.aliases import AliasSite, merge_problem
from troupe.config.writeback.collection_render import RenderContext
from troupe.config.writeback.edits import EditKind, TextEdit, apply_edits
from troupe.config.writeback.layout import FileLayout, ValueSlot
from troupe.config.writeback.scalar_render import (
    Context,
    quote_style_of,
    render_key,
    render_scalar,
)
from troupe.config.writeback.value import NewScalar

EXPANDED_COMMENT: Final = "# expanded from *{name} by troupe"
"""The end-of-line comment marking an expanded alias (7.1)."""

FLOW_REFUSAL: Final = "alias '*{name}' cannot be expanded inside a flow collection"
"""The problem of an alias to expand inside a flow collection (8.4)."""

COMMENT_REFUSAL: Final = "the line of alias '*{name}' already has an end-of-line comment"
"""The problem of an alias whose line already has an end-of-line comment (8.5)."""

MERGE_PREFIX: Final = "alias '*{name}' cannot be expanded: "
"""Put before the merge-key problem of :func:`merge_problem` when the anchored node holds a merge
key (9.1)."""

_COMMENT_GAP: Final = "  "
_COMPACT_INDENT: Final = 2
"""Entries of a list item's collection start this many columns right of its ``-`` (as in
``collection_render``)."""
_INLINE_SPACE: Final = " \t"
_STRIP: Final = "-"
"""The strip chomping indicator of a block scalar header: its value has no final line break."""
_BLOCK_STYLES: Final = frozenset({ScalarStyle.LITERAL, ScalarStyle.FOLDED})


@dataclass(frozen=True)
class _Copy:
    """A copy of the anchored node: its text on the key's line and the lines after it."""

    inline: str
    """Written after the indicator and one space (empty: nothing but the comment)."""
    lines: tuple[str, ...]
    """Following lines without their line breaks."""
    final_break: bool = False
    """The last of ``lines`` ends the content of a clip or keep block scalar, so it needs a line
    break even at the end of a file without one (or its value would change)."""


def expand_alias(document: ConfigDocument, site: AliasSite) -> dict[Path | str, str]:
    """Return ``{key: new text}`` (``SourceFile.key``) of the file holding ``site``'s alias, with
    the alias replaced by a copy of its anchored node and marked with :data:`EXPANDED_COMMENT`.

    Raises :class:`WriteRefusedError` (exit 3) naming the alias's file and ``site.path`` for an
    alias inside a flow collection (8.4), a line that already has a comment (8.5), and an anchored
    node holding a merge key (9.1).
    """
    alias = site.place.alias
    if alias is None:
        raise ValueError("an alias site is an alias place")
    file = alias.token.file
    layout = FileLayout.build(file, document)

    def refuse(problem: str) -> WriteRefusedError:
        return WriteRefusedError(problem, file=file.path, path=str(site.path))

    if site.parent.style is CollectionStyle.FLOW:
        raise refuse(FLOW_REFUSAL.format(name=alias.name))
    slot = _slot(layout, site.parent, site.key, site.path)
    token = alias.token.span
    first, last = layout.line_of(slot.indicator_end), layout.line_of(token.end)
    if any(layout.comment_start(line) is not None for line in range(first, last + 1)):
        raise refuse(COMMENT_REFUSAL.format(name=alias.name))
    anchored = alias.anchored
    holder = _merge_holder(anchored, site.path)
    if holder is not None:
        raise refuse(MERGE_PREFIX.format(name=alias.name) + merge_problem(holder))

    ctx = RenderContext(
        step=layout.indent_step(),
        dash_offset=layout.dash_offset(),
        line_break=layout.line_break_at(layout.line_span(last).end),
        path=site.path,
    )
    copy = _Copier(document, layout, ctx).value(
        anchored, slot.owner_column, slot.in_sequence, compact=False
    )

    end = token.end
    line_end = layout.line_span(last).end
    while end < line_end and file.text[end] in _INLINE_SPACE:
        end += 1
    comment = EXPANDED_COMMENT.format(name=alias.name)
    inline = f" {copy.inline}{_COMMENT_GAP}{comment}" if copy.inline else _COMMENT_GAP + comment
    edits = [TextEdit(slot.indicator_end, end, inline, EditKind.INLINE)]
    if copy.lines:
        edits.append(_lines_after(layout, last, copy, ctx.line_break))
    return {file.key: apply_edits(layout, edits)}


def _slot(
    layout: FileLayout, parent: ConfigMapping | ConfigSequence, key: str | int, path: ConfigPath
) -> ValueSlot:
    """The slot of child ``key`` of the written collection ``parent``."""
    if isinstance(parent, ConfigSequence):
        return layout.item_slot(parent, int(key), path)
    entry = parent.get(str(key))
    if entry is None:
        raise ValueError(f"no entry {key!r} at {path}")
    return layout.entry_slot(parent, entry, path)


@dataclass(frozen=True)
class _Copier:
    """Renders nodes of the anchored node as written, in block style, under the column rules of
    :func:`~troupe.config.writeback.collection_render.render_block_value`."""

    document: ConfigDocument
    layout: FileLayout
    ctx: RenderContext

    def value(
        self, node: ConfigNode, owner_column: int, in_sequence: bool, *, compact: bool = True
    ) -> _Copy:
        """The copy of ``node`` as the value of a key (or ``-``) at ``owner_column``. A list item's
        collection is compact (its first entry is the inline part) unless ``compact`` is false."""
        if node.alias is not None:  # before ``include`` and ``anchor`` (an alias place has them)
            return _Copy("*" + node.alias.name, ())
        if node.include is not None:
            if node.include.form is None:
                return _Copy(_source(node.include.directive), ())
            node = node.include.form
        if isinstance(node, ConfigScalar):
            return self._scalar(node, owner_column)
        if isinstance(node, ConfigMapping) and node.merge is not None:  # pragma: no cover
            raise ValueError("a mapping with a merge key is never copied")
        if isinstance(node, ConfigMapping) and not node.entries:
            return _Copy("{}", ())
        if isinstance(node, ConfigSequence) and not node.items:
            return _Copy("[]", ())
        if in_sequence:
            column = owner_column + _COMPACT_INDENT
        elif isinstance(node, ConfigMapping):
            column = owner_column + self.ctx.step
        else:
            column = owner_column + self.ctx.dash_offset
        lines = self._collection(node, column)
        if not (in_sequence and compact):
            return lines
        return _Copy(lines.lines[0][column:], lines.lines[1:], lines.final_break)

    def _collection(self, node: ConfigMapping | ConfigSequence, column: int) -> _Copy:
        """The lines of a non-empty block collection whose entries start at ``column``; its
        final break is that of its last entry."""
        entries: list[_Copy] = []
        if isinstance(node, ConfigMapping):
            for entry in node.entries:
                head = " " * column + self._key(entry) + ":"
                entries.append(self._entry(head, entry.value, column, in_sequence=False))
        else:
            for item in node.items:
                entries.append(self._entry(" " * column + "-", item, column, in_sequence=True))
        lines = tuple(line for copy in entries for line in (copy.inline, *copy.lines))
        return _Copy("", lines, entries[-1].final_break)

    def _entry(self, head: str, node: ConfigNode, column: int, *, in_sequence: bool) -> _Copy:
        """An entry as a copy whose ``inline`` is its first line (the head and inline part)."""
        copy = self.value(node, column, in_sequence)
        first = head + (" " + copy.inline if copy.inline else "")
        return _Copy(first, copy.lines, copy.final_break)

    def _key(self, entry: MappingEntry) -> str:
        """A key's source text, or the key rendered in its own style when written over several
        lines."""
        source = _source(entry.key_origin)
        if _single_line(source):
            return source
        preferred = quote_style_of(entry.key_style)
        return render_key(entry.key, Context.BLOCK_KEY, preferred, path=self.ctx.path)

    def _scalar(self, node: ConfigScalar, owner_column: int) -> _Copy:
        if node.style in _BLOCK_STYLES:
            # The header indicators start at a `!literal` tag, so the copy keeps it.
            return _block_scalar_copy(self.document, self.layout, owner_column, node)
        source = _source(node.origin)
        if _single_line(source):
            if node.literal is None:
                return _Copy(source, ())
            # A copy keeps the tag, so it still loads as a string (config-remote-schema 10.2).
            return _Copy(LITERAL_TAG + (" " + source if source else ""), ())
        if not isinstance(node.value, str):  # pragma: no cover - multi-line plain is a string
            raise ValueError(f"multi-line scalar {source!r} is not a string")
        preferred = quote_style_of(node.style)
        rendered = render_scalar(
            NewScalar(node.value, None), Context.BLOCK_VALUE, preferred, self.ctx.path
        )
        if node.literal is not None:
            rendered = LITERAL_TAG + " " + rendered
        return _Copy(rendered, ())


def _source(origin: Origin) -> str:
    """The text written at ``origin``."""
    return origin.file.text[origin.span.start : origin.span.end]


def _single_line(text: str) -> bool:
    return "\n" not in text and "\r" not in text


def _block_scalar_copy(
    document: ConfigDocument, layout: FileLayout, owner_column: int, anchored: ConfigScalar
) -> _Copy:
    """A literal or folded block's header indicators and its content lines, shifted by the
    difference between ``owner_column`` (of the copy's key or ``-``) and the block's own owner
    column."""
    anchored_slot = _anchored_slot(document, layout, anchored)
    header = layout.inline_extent(anchored_slot)
    text = layout.file.text
    header_line = layout.line_of(header.start)
    content_end = layout.content_end(anchored, anchored_slot)
    last = layout.line_of(content_end) if content_end > header.end else header_line
    shift = owner_column - anchored_slot.owner_column
    lines = tuple(_shifted(layout.line_text(n), shift) for n in range(header_line + 1, last + 1))
    indicators = text[header.start : header.end]
    return _Copy(indicators, lines, final_break=bool(lines) and _STRIP not in indicators)


def _shifted(line: str, shift: int) -> str:
    """``line`` moved ``shift`` columns (left for a negative shift, never past its text); an empty
    line stays empty."""
    if not line:
        return line
    if shift >= 0:
        return " " * shift + line
    spaces = len(line) - len(line.lstrip(" "))
    return line[min(-shift, spaces) :]


def _lines_after(layout: FileLayout, line: int, copy: _Copy, line_break: str) -> TextEdit:
    """Insert ``copy``'s lines after ``line``, each ending with ``line_break``. At the end of a
    file without a final break, a break comes first and the last line gets none, so the file
    still ends without one (config-set-writeback 11.6); unless the copy's last line ends a clip
    or keep block scalar (``final_break``), whose value needs the break, as at the anchor."""
    if layout.line_break(line):
        index = layout.line_start(line + 1)
        copied = "".join(f"{text}{line_break}" for text in copy.lines)
        return TextEdit(index, index, copied, EditKind.LINES)
    index = layout.line_span(line).end
    copied = line_break + line_break.join(copy.lines) + (line_break if copy.final_break else "")
    return TextEdit(index, index, copied, EditKind.LINES)


def _anchored_slot(document: ConfigDocument, layout: FileLayout, anchored: ConfigNode) -> ValueSlot:
    """The slot where ``anchored`` (the node built at its anchor) is written in ``layout``'s
    file."""
    for parent, key, node, path in _written_children(document.root, ROOT):
        if node is anchored:
            return _slot(layout, parent, key, path)
    raise ValueError("the anchored node is not written in the configuration")  # pragma: no cover


def _written_children(
    node: ConfigNode, path: ConfigPath
) -> Iterator[tuple[ConfigMapping | ConfigSequence, str | int, ConfigNode, ConfigPath]]:
    """Every node written below ``node`` with the written collection holding it, its key there
    and its path, in document order: the sides of merged mappings, ``$ref`` mappings and mappings
    as written behind a merge key; never below an alias place."""
    if node.alias is not None:
        return
    if node.include is not None and node.include.form is not None:
        yield from _written_children(node.include.form, path)
    if isinstance(node, ConfigMapping):
        if node.layers is not None:
            yield from _written_children(node.layers.base, path)
            yield from _written_children(node.layers.over, path)
            return
        written = node if node.merge is None else node.merge.written
        for entry in written.entries:
            child = path.child(entry.key)
            yield written, entry.key, entry.value, child
            yield from _written_children(entry.value, child)
    elif isinstance(node, ConfigSequence):
        for index, item in enumerate(node.items):
            child = path.child(index)
            yield node, index, item, child
            yield from _written_children(item, child)


def _merge_holder(node: ConfigNode, path: ConfigPath) -> ConfigPath | None:
    """The path (below the alias place at ``path``) of the first mapping with a merge key in the
    written structure of ``node``, or ``None``. Inner alias places and includes are copied as
    written, so they are not searched."""
    if node.alias is not None or node.include is not None:
        return None
    if isinstance(node, ConfigMapping):
        if node.merge is not None:
            return path
        for entry in node.entries:
            found = _merge_holder(entry.value, path.child(entry.key))
            if found is not None:
                return found
    elif isinstance(node, ConfigSequence):
        for index, item in enumerate(node.items):
            found = _merge_holder(item, path.child(index))
            if found is not None:
                return found
    return None
