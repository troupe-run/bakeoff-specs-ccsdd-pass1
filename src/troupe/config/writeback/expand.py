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
  so an indentation indicator stays valid. The anchor's header comment is not copied (7.6).

Comments inside the anchored node are never copied and no other byte changes (7.6). Copies of
non-empty collections and of includes are not written yet (task 3.7): they are refused.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from troupe.config.errors import WriteRefusedError
from troupe.config.model import (
    CollectionStyle,
    ConfigDocument,
    ConfigMapping,
    ConfigNode,
    ConfigScalar,
    ConfigSequence,
    ScalarStyle,
)
from troupe.config.paths import ROOT, ConfigPath
from troupe.config.writeback.aliases import AliasSite, merge_problem
from troupe.config.writeback.collection_render import RenderContext
from troupe.config.writeback.edits import EditKind, TextEdit, apply_edits
from troupe.config.writeback.layout import FileLayout, ValueSlot
from troupe.config.writeback.scalar_render import Context, quote_style_of, render_scalar
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

COLLECTION_NOT_YET: Final = "expanding a collection alias is not supported yet"
"""Temporary (task 3.7 writes block copies): an alias of a non-empty collection."""

INCLUDE_NOT_YET: Final = "expanding an alias of an include is not supported yet"
"""Temporary (task 3.7 copies includes as written): an alias of an include."""

_COMMENT_GAP: Final = "  "
_INLINE_SPACE: Final = " \t"
_BLOCK_STYLES: Final = frozenset({ScalarStyle.LITERAL, ScalarStyle.FOLDED})


@dataclass(frozen=True)
class _Copy:
    """A copy of the anchored node: its text on the key's line and the lines after it."""

    inline: str
    """Written after the indicator and one space (empty: nothing but the comment)."""
    lines: tuple[str, ...]
    """Following lines without their line breaks."""


def expand_alias(document: ConfigDocument, site: AliasSite) -> dict[Path, str]:
    """Return ``{real_path: new text}`` of the file holding ``site``'s alias, with the alias
    replaced by a copy of its anchored node and marked with :data:`EXPANDED_COMMENT`.

    Raises :class:`WriteRefusedError` (exit 3) naming the alias's file and ``site.path`` for an
    alias inside a flow collection (8.4), a line that already has a comment (8.5), and an anchored
    node holding a merge key (9.1); and, until task 3.7, for an alias of a non-empty collection or
    of an include.
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
    if anchored.include is not None or site.place.include is not None:
        raise refuse(INCLUDE_NOT_YET)

    ctx = RenderContext(
        step=layout.indent_step(),
        dash_offset=layout.dash_offset(),
        line_break=layout.line_break_at(layout.line_span(last).end),
        path=site.path,
    )
    copy = _copy(document, layout, slot, anchored, ctx)
    if copy is None:
        raise refuse(COLLECTION_NOT_YET)

    end = token.end
    line_end = layout.line_span(last).end
    while end < line_end and file.text[end] in _INLINE_SPACE:
        end += 1
    comment = EXPANDED_COMMENT.format(name=alias.name)
    inline = f" {copy.inline}{_COMMENT_GAP}{comment}" if copy.inline else _COMMENT_GAP + comment
    edits = [TextEdit(slot.indicator_end, end, inline, EditKind.INLINE)]
    if copy.lines:
        edits.append(_lines_after(layout, last, copy.lines, ctx.line_break))
    return {file.real_path: apply_edits(layout, edits)}


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


def _copy(
    document: ConfigDocument,
    layout: FileLayout,
    slot: ValueSlot,
    anchored: ConfigNode,
    ctx: RenderContext,
) -> _Copy | None:
    """The copy of ``anchored`` written at ``slot``; ``None`` for a non-empty collection (task
    3.7)."""
    if isinstance(anchored, ConfigMapping):
        return _Copy("{}", ()) if not anchored.entries else None
    if isinstance(anchored, ConfigSequence):
        return _Copy("[]", ()) if not anchored.items else None
    if anchored.style in _BLOCK_STYLES:
        return _block_scalar_copy(document, layout, slot, anchored)
    span = anchored.origin.span
    source = anchored.origin.file.text[span.start : span.end]
    if span.end <= span.start or layout.line_of(span.start) == layout.line_of(span.end - 1):
        return _Copy(source, ())
    if not isinstance(anchored.value, str):  # pragma: no cover - multi-line plain is a string
        raise ValueError(f"multi-line scalar {source!r} is not a string")
    preferred = quote_style_of(anchored.style)
    rendered = render_scalar(
        NewScalar(anchored.value, None), Context.BLOCK_VALUE, preferred, ctx.path
    )
    return _Copy(rendered, ())


def _block_scalar_copy(
    document: ConfigDocument, layout: FileLayout, slot: ValueSlot, anchored: ConfigScalar
) -> _Copy:
    """A literal or folded block's header indicators and its content lines, shifted by the
    difference between ``slot``'s owner column and the anchored block's owner column."""
    anchored_slot = _anchored_slot(document, layout, anchored)
    header = layout.inline_extent(anchored_slot)
    text = layout.file.text
    header_line = layout.line_of(header.start)
    content_end = layout.content_end(anchored, anchored_slot)
    last = layout.line_of(content_end) if content_end > header.end else header_line
    shift = slot.owner_column - anchored_slot.owner_column
    lines = tuple(_shifted(layout.line_text(n), shift) for n in range(header_line + 1, last + 1))
    return _Copy(text[header.start : header.end], lines)


def _shifted(line: str, shift: int) -> str:
    """``line`` moved ``shift`` columns (left for a negative shift, never past its text); an empty
    line stays empty."""
    if not line:
        return line
    if shift >= 0:
        return " " * shift + line
    spaces = len(line) - len(line.lstrip(" "))
    return line[min(-shift, spaces) :]


def _lines_after(
    layout: FileLayout, line: int, lines: tuple[str, ...], line_break: str
) -> TextEdit:
    """Insert ``lines``, each ending with ``line_break``, after ``line``. At the end of a file
    without a final break a break comes first: the copied lines end with a break as at the anchor,
    so a clip or keep block keeps its final line break."""
    copied = "".join(f"{text}{line_break}" for text in lines)
    if layout.line_break(line):
        index = layout.line_start(line + 1)
        return TextEdit(index, index, copied, EditKind.LINES)
    index = layout.line_span(line).end
    return TextEdit(index, index, line_break + copied, EditKind.LINES)


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
