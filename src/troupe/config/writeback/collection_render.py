"""CollectionRender: new block and flow collections and new entries as text lines.

Block style (8.1-8.4, 8.6, 9.1, 9.2): a mapping entry is ``{column}{key}: {inline}`` and a list item
``{column}- {inline}``, each followed by a line break and the value's following lines. A nested
mapping is written one indentation step right of its key, a nested list with its ``-`` indicators at
the file's dash offset from its key, and a collection that is a list item compactly: its first entry
follows ``- `` and its further entries are two columns right of the ``-``. A multi-line string is a
literal block whose content is one step right of its key (or ``-``), or double-quoted when a literal
block cannot hold it (9.7). An empty collection is ``{}``/``[]`` inline.

Flow style (8.5, 8.6, 9.3): ``{k: v, k2: v2}`` and ``[a, b]``; multi-line strings are double-quoted.

Sibling styles (7.1-7.3) apply only to the first-level new key and value; every key and string
inside a new collection is written in the minimal style (7.4).

Mapping-form includes (config-include-overrides 5.1, 5.3, 5.4): the conversion of a scalar-form
include writes ``$ref: <include string as written>`` followed by the new override entries, as block
lines at one column or as the flow mapping ``{$ref: <string>, k: v}``.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from troupe.config.constants import INCLUDE_REF_KEY
from troupe.config.paths import ConfigPath
from troupe.config.writeback.block_scalar import literal_fits, render_literal
from troupe.config.writeback.scalar_render import Context, QuoteStyle, render_key, render_scalar
from troupe.config.writeback.value import NewMapping, NewScalar, NewSequence, NewValue

_COMPACT_INDENT: Final = 2
"""Further entries of a list-item collection start this many columns right of its ``-`` (8.4)."""


@dataclass(frozen=True)
class RenderContext:
    """The file's layout conventions for new text, and the PATH of the set for errors."""

    step: int
    """Indentation step of nested block mappings and of literal block content (8.1, 9.2)."""
    dash_offset: int
    """Offset of a block list's ``-`` from the key that holds it (8.3)."""
    line_break: str
    """Line break written after every new line (13.2)."""
    path: ConfigPath
    """The set's PATH, named by a refusal (a lone surrogate that cannot be written as UTF-8)."""


@dataclass(frozen=True)
class SiblingStyles:
    """Quote styles of the preceding sibling's key and value (``None``: no preferred style)."""

    key: QuoteStyle | None
    value: QuoteStyle | None


_MINIMAL: Final = SiblingStyles(key=None, value=None)


def render_block_entry(
    key: str | None, value: NewValue, column: int, siblings: SiblingStyles, ctx: RenderContext
) -> str:
    """Render a new block mapping entry (``key``) or list item (``key`` ``None``) at ``column``.

    The key and a string value use the sibling styles when they can represent them (7.1, 7.2);
    everything inside a collection value is minimal (7.4). The text ends with a line break.
    """
    if key is None:
        head = " " * column + "-"
        in_sequence = True
    else:
        head = " " * column + render_key(key, Context.BLOCK_KEY, siblings.key, path=ctx.path) + ":"
        in_sequence = False
    inline, following = render_block_value(
        value, column, in_sequence, ctx, preferred=siblings.value
    )
    return head + (" " + inline if inline else "") + ctx.line_break + following


def render_block_value(
    value: NewValue,
    owner_column: int,
    in_sequence: bool,
    ctx: RenderContext,
    *,
    compact: bool = True,
    preferred: QuoteStyle | None = None,
) -> tuple[str, str]:
    """Render a value in block context as ``(inline part, following lines)``.

    ``owner_column`` is the column of the value's key, or of its ``-`` when ``in_sequence``. The
    inline part goes after ``: `` or ``- `` on the owner's line (empty: nothing goes there); the
    following lines each end with a line break. A list-item collection is compact (its first entry
    is the inline part) unless ``compact`` is false, in which case all of its entries follow, two
    columns right of the ``-`` (8.4). ``preferred`` is the quote style for a string value; it never
    applies inside a collection (7.4).
    """
    if isinstance(value, NewScalar):
        return _block_scalar(value, owner_column, ctx, preferred)
    if isinstance(value, NewMapping) and not value.entries:
        return "{}", ""
    if isinstance(value, NewSequence) and not value.items:
        return "[]", ""
    if in_sequence:
        column = owner_column + _COMPACT_INDENT
    elif isinstance(value, NewMapping):
        column = owner_column + ctx.step
    else:
        column = owner_column + ctx.dash_offset
    lines = _block_collection(value, column, ctx)
    if not (in_sequence and compact):
        return "", lines
    first_end = lines.index(ctx.line_break)
    return lines[column:first_end], lines[first_end + len(ctx.line_break) :]


def render_flow_entry(
    key: str | None, value: NewValue, siblings: SiblingStyles, ctx: RenderContext
) -> str:
    """Render a new flow mapping entry ``key: value`` or flow list item (``key`` ``None``).

    The key and a string value use the sibling styles when they can represent them (7.1, 7.2);
    everything inside a collection value is minimal (7.4).
    """
    if key is None:
        return _flow(value, siblings.value, ctx)
    return _flow_pair(key, value, siblings, ctx)


def render_ref_form_lines(
    raw: str,
    entries: Sequence[tuple[str, NewValue]],
    column: int,
    siblings: SiblingStyles,
    ctx: RenderContext,
    *,
    with_ref: bool,
) -> str:
    """Render the block lines of a mapping-form include at ``column`` (5.1, 5.3).

    ``raw`` is the include string exactly as written (quotes kept). With ``with_ref`` the first
    line is ``$ref: <raw>``; without it (a compact list item that holds ``$ref`` on its ``-``
    line) only the new entries are rendered. The entries use the sibling styles (those of the
    ``$ref`` entry). Every line ends with a line break.
    """
    head = " " * column + INCLUDE_REF_KEY + ": " + raw + ctx.line_break if with_ref else ""
    return head + "".join(
        render_block_entry(key, value, column, siblings, ctx) for key, value in entries
    )


def render_flow_ref_form(
    raw: str,
    entries: Sequence[tuple[str, NewValue]],
    siblings: SiblingStyles,
    ctx: RenderContext,
) -> str:
    """Render the flow mapping ``{$ref: <raw>, k: v}`` of a mapping-form include (5.4).

    ``raw`` is the include string exactly as written; the entries use the sibling styles.
    """
    pairs = [INCLUDE_REF_KEY + ": " + raw]
    pairs.extend(render_flow_entry(key, value, siblings, ctx) for key, value in entries)
    return "{" + ", ".join(pairs) + "}"


def render_flow_value(value: NewValue, ctx: RenderContext) -> str:
    """Render a value in flow style with minimal quoting (8.5, 8.6, 9.3)."""
    return _flow(value, None, ctx)


def _block_scalar(
    value: NewScalar, owner_column: int, ctx: RenderContext, preferred: QuoteStyle | None
) -> tuple[str, str]:
    text = value.value
    if isinstance(text, str) and "\n" in text and literal_fits(text):
        return render_literal(text, owner_column + ctx.step, ctx.line_break)
    return render_scalar(value, Context.BLOCK_VALUE, preferred, ctx.path), ""


def _block_collection(value: NewMapping | NewSequence, column: int, ctx: RenderContext) -> str:
    """The lines of a non-empty block collection whose entries start at ``column``."""
    if isinstance(value, NewMapping):
        return "".join(
            render_block_entry(key, item, column, _MINIMAL, ctx) for key, item in value.entries
        )
    return "".join(render_block_entry(None, item, column, _MINIMAL, ctx) for item in value.items)


def _flow(value: NewValue, preferred: QuoteStyle | None, ctx: RenderContext) -> str:
    if isinstance(value, NewScalar):
        return render_scalar(value, Context.FLOW_VALUE, preferred, ctx.path)
    if isinstance(value, NewMapping):
        pairs = (_flow_pair(key, item, _MINIMAL, ctx) for key, item in value.entries)
        return "{" + ", ".join(pairs) + "}"
    return "[" + ", ".join(_flow(item, None, ctx) for item in value.items) + "]"


def _flow_pair(key: str, value: NewValue, styles: SiblingStyles, ctx: RenderContext) -> str:
    rendered_key = render_key(key, Context.FLOW_KEY, styles.key, path=ctx.path)
    return rendered_key + ": " + _flow(value, styles.value, ctx)
