"""PatchPlanner: slot changes, merges, new entries, deletions and kind changes.

Turns a resolved :class:`~troupe.config.writeback.target.SetRequest` into the changed text of every
file the set must change, one :class:`FilePatch` per changed file, in the order the files appear in
the loaded document (root file first). Nothing is written here.

A replaced value goes through :meth:`_Planner.change_slot`, which follows the design's "slot change
decision":

* an unchanged value (typed plain equality) needs no edit;
* a YAML include root that would change is refused naming its own path, which inside a merged
  collection is the nested path, and the included file (2.5, 5.8); a text include gets its target
  file's whole content replaced by the text-include content rule (2.2, 2.3), or is refused when
  VALUE is not a string (2.4);
* an existing literal or folded block changed to a string it can still hold is rewritten in place
  (9.4, 9.5, 10.x);
* an existing plain, quoted or empty scalar changed to a scalar is replaced in its slot in the
  chosen quote style (6.1-6.3); a multi-line string becomes a literal block in a block collection
  (9.1, 9.2) and a double-quoted scalar in a flow collection or where a literal block cannot hold
  it (9.3, 9.7);
* every other change of kind or block form (a scalar over a collection, a collection over a scalar
  or a collection of another kind, an empty collection, a non-string or a string no block holds
  over a block scalar) replaces the whole value in its slot: a flow rendering inside a flow parent
  (8.5), otherwise the new inline part on the key's (or ``-``'s) line and the new block lines in
  place of the old ones (5.5, 5.6, 9.6, 9.7, 11.4, 11.5);
* a non-empty mapping over a mapping or list over a list is merged entry-wise (5.1, 5.2): kept
  entries go through the slot change, absent ones are deleted (block: whole lines through the last
  content line, refused when the entry shares its first line with remaining content, 5.3, 5.7;
  flow: the entry with one adjoining separator on one line, 5.4) and new ones are added in one
  insertion; a flow mapping none of whose entries stays is rendered anew.

A new key (with any missing intermediate mappings nested under it) or an appended list item goes
through :meth:`_Planner.add_entries` into the file holding the collection (3.1, 3.2, 4.1): in a
block collection as whole lines at its insertion point and entries column with the quote styles of
its last remaining entry (7.1-7.3, 11.1, 11.2, 11.6), in a flow collection right after its last
entry on that entry's line (11.3). A new key whose placement is a scalar-form include goes through
:meth:`_Planner.convert_include`, which rewrites the include in mapping form with the key as its
override, in the file holding the include string (config-include-overrides 5.1-5.5).

End-of-line comments on the changed lines are moved back to their columns by the EditApplier
(12.x). Every refusal is a :class:`WriteRefusedError` naming the file and the path (14.9).
"""

from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Final, NoReturn, TypeVar

from troupe.config.constants import INCLUDE_REF_KEY
from troupe.config.errors import WriteRefusedError
from troupe.config.model import (
    CollectionStyle,
    ConfigDocument,
    ConfigMapping,
    ConfigNode,
    ConfigScalar,
    ConfigSequence,
    IncludeKind,
    ScalarStyle,
    SourceFile,
)
from troupe.config.overrides import physical_entry
from troupe.config.paths import ConfigPath
from troupe.config.writeback.block_scalar import (
    Chomping,
    literal_fits,
    required_chomping,
    rewrite_block,
)
from troupe.config.writeback.collection_render import (
    RenderContext,
    SiblingStyles,
    render_block_entry,
    render_block_value,
    render_flow_entry,
    render_flow_ref_form,
    render_flow_value,
    render_ref_form_lines,
)
from troupe.config.writeback.compare import new_to_plain, node_to_plain, plain_equal
from troupe.config.writeback.edits import EditKind, TextEdit, apply_edits
from troupe.config.writeback.layout import FileLayout, ValueSlot
from troupe.config.writeback.scalar_render import (
    Context,
    QuoteStyle,
    quote_style_of,
    render_scalar,
)
from troupe.config.writeback.target import (
    AddKeyTarget,
    AppendTarget,
    IncludeSite,
    ReplaceTarget,
    SetRequest,
    text_include_value,
)
from troupe.config.writeback.value import NewMapping, NewScalar, NewSequence, NewValue

_QUOTED_OR_PLAIN: Final = frozenset(
    {ScalarStyle.PLAIN, ScalarStyle.SINGLE_QUOTED, ScalarStyle.DOUBLE_QUOTED}
)
_BLOCK_STYLES: Final = frozenset({ScalarStyle.LITERAL, ScalarStyle.FOLDED})
_LF: Final = "\n"
_INLINE_SPACE: Final = " \t"
_WHITESPACE: Final = " \t\r\n"
_COMPACT_INDENT: Final = 2
"""Entries of a list-item mapping start this many columns right of its ``-`` (8.4, 5.3)."""

_T = TypeVar("_T")


@dataclass(frozen=True)
class FilePatch:
    """The complete new text of one changed file."""

    file: SourceFile
    new_text: str


def plan_patch(document: ConfigDocument, request: SetRequest) -> tuple[FilePatch, ...]:
    """Return one :class:`FilePatch` per file the set changes, in ``document.files`` order.

    Never two patches for the same file, and every patch changes its file. Raises
    :class:`WriteRefusedError` (exit 3) naming the file and PATH for every refusal: a YAML include
    root that would change (2.5), a non-string or another include over a text include (2.4), a
    layout the planner cannot write (14.9), or a set that would change nothing.
    """
    planner = _Planner(document, request.path)
    target = request.target
    if isinstance(target, ReplaceTarget):
        planner.replace(target, request.value)
    elif isinstance(target, AddKeyTarget):
        planner.add_key(target, request.value)
    else:
        planner.append(target, request.value)
    return planner.patches()


class _Planner:
    """Collects the edits of one set per file and turns them into file patches."""

    def __init__(self, document: ConfigDocument, path: ConfigPath) -> None:
        self._document = document
        self._path = path
        # Keyed by ``SourceFile.real_path``.
        self._layouts: dict[Path, FileLayout] = {}
        self._edits: dict[Path, list[TextEdit]] = {}
        self._whole: dict[Path, tuple[SourceFile, str]] = {}

    # --- entry point -----------------------------------------------------------------------------

    def replace(self, target: ReplaceTarget, value: NewValue) -> None:
        """Plan the change of the existing value ``target.node`` to ``value``."""
        parent = target.parent
        file = parent.origin.file
        layout = self._guard(file, self._path, lambda: self._layout(file))

        def slot() -> ValueSlot:
            if isinstance(parent, ConfigMapping):
                entry = parent.get(str(target.key))
                if entry is None:  # pragma: no cover - the target was resolved on this mapping
                    raise ValueError(f"no entry {target.key!r}")
                return layout.entry_slot(parent, entry, self._path)
            return layout.item_slot(parent, int(target.key), self._path)

        self.change_slot(layout, target.node, slot, value, self._path, self._path)

    def add_key(self, target: AddKeyTarget, value: NewValue) -> None:
        """Plan a new key at its placement, with the missing intermediate mappings (3.1, 3.2).

        A placement naming a written mapping (an override side, a ``$ref`` mapping or a plain
        mapping) receives the keys ``prefix + missing`` there (config-include-overrides 4.1,
        4.3-4.6). A scalar-form include to convert is turned into a mapping-form include holding
        them as its override (5.1-5.5).

        The first key is the new entry; the keys after it become nested new mappings with VALUE
        at the last one, rendered as one new collection value (7.4).
        """
        placement = target.placement
        first, *intermediates = (*placement.prefix, *target.missing)
        for key in reversed(intermediates):
            value = NewMapping(entries=((key, value),))
        if placement.convert is not None:
            self.convert_include(placement.convert, ((first, value),), self._path)
        elif placement.mapping is not None:
            self.add_entries(placement.mapping, ((first, value),), self._path)
        else:  # pragma: no cover - a placement always names a mapping or an include
            raise ValueError("the placement names neither a mapping nor an include")

    def append(self, target: AppendTarget, value: NewValue) -> None:
        """Plan VALUE as a new last item of ``target.sequence`` (4.1)."""
        self.add_entries(target.sequence, ((None, value),), self._path)

    # --- new entries -----------------------------------------------------------------------------

    def add_entries(
        self,
        collection: ConfigMapping | ConfigSequence,
        entries: Sequence[tuple[str | None, NewValue]],
        path: ConfigPath,
        deleted: Collection[int] = (),
    ) -> None:
        """Plan new entries (``key`` ``None``: list items) after the last entry of ``collection``.

        They go into the file the collection is written in, styled after its last remaining entry
        (7.1-7.3): ``deleted`` are the indices of the entries the same set deletes. A block
        collection gets one line insertion at its insertion point (11.1, 11.2, 11.6); a flow
        collection one insertion on its last entry's line (11.3).
        """
        file = collection.origin.file
        layout = self._guard(file, path, lambda: self._layout(file))
        siblings = _sibling_styles(collection, deleted)
        if collection.style is CollectionStyle.FLOW:
            edit = self._guard(
                file,
                path,
                lambda: self._flow_insertion(layout, collection, entries, siblings, path),
            )
        else:
            edit = self._guard(
                file,
                path,
                lambda: self._block_insertion(layout, collection, entries, siblings, path),
            )
        self._add(layout, [edit])

    def _block_insertion(
        self,
        layout: FileLayout,
        collection: ConfigMapping | ConfigSequence,
        entries: Sequence[tuple[str | None, NewValue]],
        siblings: SiblingStyles,
        path: ConfigPath,
    ) -> TextEdit:
        """New block entries at the entries column, inserted at the insertion point.

        At a file end without a final line break the text starts with a break and ends without
        one (11.6), unless its last line is content of a literal block that needs its break.
        """
        point = layout.insertion_point(collection)
        column = layout.entries_column(collection)
        ctx = RenderContext(
            step=layout.indent_step(),
            dash_offset=layout.dash_offset(),
            line_break=layout.line_break_at(point.index),
            path=path,
        )
        text = self._render(
            lambda: "".join(
                render_block_entry(key, value, column, siblings, ctx) for key, value in entries
            ),
            layout,
            path,
        )
        if point.needs_leading_break:
            text = ctx.line_break + text
            if not _needs_final_break(entries[-1][1]):
                text = text.removesuffix(ctx.line_break)
        return TextEdit(point.index, point.index, text, EditKind.LINES)

    def _flow_insertion(
        self,
        layout: FileLayout,
        collection: ConfigMapping | ConfigSequence,
        entries: Sequence[tuple[str | None, NewValue]],
        siblings: SiblingStyles,
        path: ConfigPath,
    ) -> TextEdit:
        """New flow entries right after the last entry (after the opening bracket when empty).

        Whatever follows the last entry (a trailing comma, the closing bracket, a comment) stays
        after the new entries (11.3).
        """
        separator = ", "
        if isinstance(collection, ConfigMapping):
            if collection.entries:
                last = collection.entries[-1]
                slot = layout.entry_slot(collection, last, path)
                at = layout.content_end(last.value, slot)
                extent = layout.inline_extent(slot)
                if extent.start == extent.end:
                    # An empty last value ends at its ':'; `b:,` would read as the key `b:`.
                    separator = " , "
            else:
                at = self._after_opening_bracket(layout, collection)
        elif collection.items:
            index = len(collection.items) - 1
            slot = layout.item_slot(collection, index, path)
            at = layout.content_end(collection.items[index], slot)
        else:
            at = self._after_opening_bracket(layout, collection)
        ctx = RenderContext(
            step=layout.indent_step(),
            dash_offset=layout.dash_offset(),
            line_break=layout.line_break_at(at),
            path=path,
        )
        text = self._render(
            lambda: ", ".join(
                render_flow_entry(key, value, siblings, ctx) for key, value in entries
            ),
            layout,
            path,
        )
        empty = at == collection.origin.span.start + 1
        return TextEdit(at, at, text if empty else separator + text, EditKind.INLINE)

    @staticmethod
    def _after_opening_bracket(
        layout: FileLayout, collection: ConfigMapping | ConfigSequence
    ) -> int:
        start = collection.origin.span.start
        bracket = "{" if isinstance(collection, ConfigMapping) else "["
        if not layout.file.text.startswith(bracket, start):
            raise ValueError(f"expected {bracket!r} at index {start} of {layout.file.path}")
        return start + 1

    # --- conversion to mapping form --------------------------------------------------------------

    def convert_include(
        self, site: IncludeSite, entries: Sequence[tuple[str, NewValue]], path: ConfigPath
    ) -> None:
        """Convert the scalar-form include at ``site`` to mapping form, ``entries`` as overrides.

        The edits go into the file holding the include string; the included file never changes
        (5.5). In a flow collection the include string becomes ``{$ref: <string>, k: v}`` (5.4).
        In a block collection the include string must end on its key's (or ``-``'s) line, else
        the layout is refused (research.md Decision 7). The owner's line then keeps its bytes
        through the ``:`` (or ``-``), followed by the whitespace before an end-of-line comment and
        the comment, which is not re-aligned (5.2, Decision 6); ``$ref: <string>`` and the
        entries follow on the next lines one indentation step deeper than the key (5.1), or two
        columns right of the ``-`` (5.3). A list item without a comment is compact: ``$ref:
        <string>`` replaces the include string on the ``-`` line.
        """
        include = site.node.include
        if include is None:  # pragma: no cover - the placement walk only converts include roots
            raise ValueError("not an include root")
        file = include.directive.file
        layout = self._guard(file, path, lambda: self._layout(file))
        slot = self._guard(file, path, lambda: self._include_slot(layout, site, path))
        extent = self._guard(file, path, lambda: layout.inline_extent(slot))
        raw = layout.file.text[extent.start : extent.end]
        siblings = SiblingStyles(key=ScalarStyle.PLAIN, value=_raw_style(raw))
        ctx = self._context(layout, extent.end, path)
        if slot.parent_style is CollectionStyle.FLOW:
            text = self._render(
                lambda: render_flow_ref_form(raw, entries, siblings, ctx), layout, path
            )
            self._add(layout, [TextEdit(extent.start, extent.end, text, EditKind.INLINE)])
            return
        line = layout.line_of(slot.indicator_end)
        if layout.line_of(extent.start) != line or layout.line_of(extent.end) != line:
            _refuse(
                path,
                layout.file,
                f"this layout cannot be written: the include string {raw} does not stay on the "
                "line of its key",
            )
        comment = layout.comment_start(line)
        compact = slot.in_sequence and comment is None
        column = slot.owner_column + (_COMPACT_INDENT if slot.in_sequence else ctx.step)
        ctx = self._context(layout, self._following_start(layout, extent.end), path)
        following = self._render(
            lambda: render_ref_form_lines(
                raw, entries, column, siblings, ctx, with_ref=not compact
            ),
            layout,
            path,
        )
        edits: list[TextEdit] = []
        if compact:
            edits.append(
                TextEdit(extent.start, extent.end, f"{INCLUDE_REF_KEY}: {raw}", EditKind.INLINE)
            )
        else:
            end = extent.end if comment is not None else layout.line_span(line).end
            edits.append(TextEdit(slot.indicator_end, end, "", EditKind.LINES))
        strip = not _needs_final_break(entries[-1][1])
        edits.append(
            self._following_edit(layout, extent.end, following, ctx.line_break, strip=strip)
        )
        self._add(layout, edits)

    @staticmethod
    def _include_slot(layout: FileLayout, site: IncludeSite, path: ConfigPath) -> ValueSlot:
        """The written slot of the include string at ``site`` (its parent may be merged)."""
        parent = site.parent
        if isinstance(parent, ConfigMapping):
            written, entry = physical_entry(parent, str(site.key))
            return layout.entry_slot(written, entry, path)
        return layout.item_slot(parent, int(site.key), path)

    # --- slot change decision --------------------------------------------------------------------

    def change_slot(
        self,
        layout: FileLayout,
        node: ConfigNode,
        slot: Callable[[], ValueSlot],
        value: NewValue,
        path: ConfigPath,
        at: ConfigPath,
    ) -> None:
        """Plan the change of ``node`` (whose slot ``slot`` locates in ``layout``) to ``value``.

        ``path`` is PATH; ``at`` is the path of ``node`` itself (PATH, or a path below it inside a
        merged collection), named when a YAML include root there would change (5.8).
        """
        if plain_equal(node_to_plain(node), new_to_plain(value), ordered=False):
            return
        if node.include is not None:
            self._change_include(node, value, path, at)
            return
        if not self._guard(
            layout.file, path, lambda: self._change_value(layout, node, slot(), value, path, at)
        ):
            _refuse(
                path,
                layout.file,
                f"this change cannot be written: {_kind(node)} cannot be replaced by "
                f"{_kind(value)}",
            )

    def _change_value(
        self,
        layout: FileLayout,
        node: ConfigNode,
        slot: ValueSlot,
        value: NewValue,
        path: ConfigPath,
        at: ConfigPath,
    ) -> bool:
        """Plan an in-place change of a value that is not an include root; ``False`` if none fits.

        Layout lookups raise ``ValueError`` for layouts outside the contract (``? key``); the
        caller turns that into a refusal (14.9).
        """
        preferred: QuoteStyle | None = None
        if isinstance(node, ConfigScalar) and isinstance(value, NewScalar):
            if node.style in _QUOTED_OR_PLAIN:
                self._replace_scalar(layout, slot, node, value, path)
                return True
            if isinstance(value.value, str):
                if self._rewrite_block(layout, slot, node, value.value, path):
                    return True
                # A string no block can hold is double-quoted instead (9.7).
                preferred = ScalarStyle.DOUBLE_QUOTED
        elif _merges(node, value):
            self._merge(layout, node, slot, value, path, at)
            return True
        self._replace_value(layout, slot, value, path, preferred)
        return True

    def _change_include(
        self, node: ConfigNode, value: NewValue, path: ConfigPath, at: ConfigPath
    ) -> None:
        """An include root that changes: rewrite a text include's target, refuse anything else.

        A YAML include root is refused naming its own path ``at`` and the included file (2.5,
        5.8); the text-include refusals name PATH and the target (2.4).
        """
        include = node.include
        if include is None:  # pragma: no cover - only called for include roots
            raise ValueError("not an include root")
        target = include.target
        directive = f"file:{include.spec}"
        if include.kind is not IncludeKind.TEXT:
            _refuse(
                at,
                target,
                f"the whole value comes from the include {directive}; "
                "change the included file instead",
            )
        if not (isinstance(value, NewScalar) and isinstance(value.value, str)):
            _refuse(
                path,
                target,
                f"the value is the text include {directive}, which can only be set to a string",
            )
        if not isinstance(new_to_plain(value), str):
            _refuse(
                path,
                target,
                f"the value is the text include {directive}; "
                "replacing it by another include is not supported",
            )
        content = text_include_value(value.value, target)
        try:
            content.encode("utf-8")
        except UnicodeEncodeError:
            _refuse(path, target, f"the string {value.value!r} cannot be written as UTF-8")
        if content != target.text:
            self._replace_file(target, content, path)

    # --- entry-wise merges and deletions --------------------------------------------------------

    def _merge(
        self,
        layout: FileLayout,
        node: ConfigNode,
        slot: ValueSlot,
        value: NewValue,
        path: ConfigPath,
        at: ConfigPath,
    ) -> None:
        """Merge a non-empty mapping or list into an existing one of the same kind (5.1, 5.2).

        Kept entries go through the slot change (unchanged ones need no edit), absent entries are
        deleted and new entries are added in one insertion after the old last entry. A flow
        mapping none of whose entries stays is rendered anew as a whole (8.5).
        """
        if isinstance(node, ConfigMapping) and isinstance(value, NewMapping):
            self._merge_mapping(layout, node, slot, value, path, at)
        elif isinstance(node, ConfigSequence) and isinstance(value, NewSequence):
            self._merge_sequence(layout, node, value, path, at)
        else:  # pragma: no cover - only called when ``_merges`` holds
            raise ValueError("only collections of the same kind are merged")

    def _merge_mapping(
        self,
        layout: FileLayout,
        node: ConfigMapping,
        slot: ValueSlot,
        value: NewMapping,
        path: ConfigPath,
        at: ConfigPath,
    ) -> None:
        new = dict(value.entries)
        deleted = [index for index, entry in enumerate(node.entries) if entry.key not in new]
        if node.style is CollectionStyle.FLOW and len(deleted) == len(node.entries):
            self._render_flow_anew(layout, slot, value, path)
            return
        for entry in node.entries:
            if entry.key in new:
                self.change_slot(
                    layout,
                    entry.value,
                    partial(layout.entry_slot, node, entry, path),
                    new[entry.key],
                    path,
                    at.child(entry.key),
                )
        self._delete_entries(layout, node, deleted, path)
        existing = {entry.key for entry in node.entries}
        added = [(key, item) for key, item in value.entries if key not in existing]
        if added:
            self.add_entries(node, added, path, deleted)

    def _merge_sequence(
        self,
        layout: FileLayout,
        node: ConfigSequence,
        value: NewSequence,
        path: ConfigPath,
        at: ConfigPath,
    ) -> None:
        for index, (item, new_item) in enumerate(zip(node.items, value.items, strict=False)):
            self.change_slot(
                layout,
                item,
                partial(layout.item_slot, node, index, path),
                new_item,
                path,
                at.child(index),
            )
        self._delete_entries(layout, node, range(len(value.items), len(node.items)), path)
        surplus = value.items[len(node.items) :]
        if surplus:  # no item is deleted, so the old last item stays the preceding sibling
            self.add_entries(node, [(None, item) for item in surplus], path)

    def _render_flow_anew(
        self, layout: FileLayout, slot: ValueSlot, value: NewValue, path: ConfigPath
    ) -> None:
        """Replace a one-line flow collection by the flow rendering of ``value``."""
        extent = self._guard(layout.file, path, lambda: layout.inline_extent(slot))
        if layout.line_of(extent.start) != layout.line_of(extent.end):
            _refuse(
                path,
                layout.file,
                "this change cannot be written: the flow collection spans several lines",
            )
        ctx = self._context(layout, extent.start, path)
        text = self._render(lambda: render_flow_value(value, ctx), layout, path)
        self._add(layout, [TextEdit(extent.start, extent.end, text, EditKind.INLINE)])

    def _delete_entries(
        self,
        layout: FileLayout,
        collection: ConfigMapping | ConfigSequence,
        indexes: Sequence[int],
        path: ConfigPath,
    ) -> None:
        """Delete the entries (or items) at ``indexes`` of ``collection`` (5.3, 5.4, 5.7)."""
        if not indexes:
            return
        if collection.style is CollectionStyle.FLOW:
            edits = self._guard(
                layout.file,
                path,
                lambda: self._flow_deletions(layout, collection, indexes, path),
            )
        else:
            edits = self._guard(
                layout.file,
                path,
                lambda: self._block_deletions(layout, collection, indexes, path),
            )
        self._add(layout, edits)

    def _entry_bounds(
        self,
        layout: FileLayout,
        collection: ConfigMapping | ConfigSequence,
        index: int,
        path: ConfigPath,
    ) -> tuple[int, int]:
        """Start (key or ``-``; flow item start) and content end of entry ``index``."""
        if isinstance(collection, ConfigMapping):
            entry = collection.entries[index]
            slot = layout.entry_slot(collection, entry, path)
            start = entry.key_origin.span.start
        else:
            slot = layout.item_slot(collection, index, path)
            flow = collection.style is CollectionStyle.FLOW
            # A flow item starts at its slot's indicator end; a block item at its ``-``.
            start = slot.indicator_end if flow else slot.indicator_end - 1
        return start, layout.content_end(slot.node, slot)

    def _block_deletions(
        self,
        layout: FileLayout,
        collection: ConfigMapping | ConfigSequence,
        indexes: Sequence[int],
        path: ConfigPath,
    ) -> list[TextEdit]:
        """Whole lines from each entry's first line through the break of its last content line.

        An entry that does not start its line (a compact first key on a ``-`` line) is refused
        (5.7). Adjacent ranges are joined; a range that ends a file without a final line break
        takes the break before it instead, so the file keeps ending without one (13.2).
        """
        ranges: list[tuple[int, int]] = []
        for index in indexes:
            start, end = self._entry_bounds(layout, collection, index, path)
            if not layout.starts_line(start):
                _refuse(
                    path,
                    layout.file,
                    "this change cannot be written: an entry to remove shares its first line "
                    "with content that remains",
                )
            first = layout.line_of(start)
            last = layout.line_of(end)
            line_end = layout.line_span(last).end + len(layout.line_break(last))
            if ranges and ranges[-1][1] == layout.line_start(first):
                ranges[-1] = (ranges[-1][0], line_end)
            else:
                ranges.append((layout.line_start(first), line_end))
        edits: list[TextEdit] = []
        text = layout.file.text
        for start, end in ranges:
            if end == len(text) and not text.endswith(("\n", "\r")):
                first = layout.line_of(start)
                if first > 0:
                    start = layout.line_span(first - 1).end
            edits.append(TextEdit(start, end, "", EditKind.LINES))
        return edits

    def _flow_deletions(
        self,
        layout: FileLayout,
        collection: ConfigMapping | ConfigSequence,
        indexes: Sequence[int],
        path: ConfigPath,
    ) -> list[TextEdit]:
        """Each entry with one adjoining separator, within one line (5.4).

        An entry after a kept one takes the ``,`` (and spaces) before it; an entry before the first
        kept one takes the ``,`` and the spaces after it. A range spanning lines is refused.
        """
        text = layout.file.text
        deleted = set(indexes)
        edits: list[TextEdit] = []
        for index in sorted(deleted):
            start, end = self._entry_bounds(layout, collection, index, path)
            if any(other not in deleted for other in range(index)):
                comma = start
                while comma > 0 and text[comma - 1] in _WHITESPACE:
                    comma -= 1
                if not text.startswith(",", comma - 1):
                    raise ValueError(f"no ',' before the entry at index {start}")
                start = comma - 1
            else:
                after = end
                while after < len(text) and text[after] in _WHITESPACE:
                    after += 1
                if not text.startswith(",", after):
                    raise ValueError(f"no ',' after the entry at index {start}")
                end = after + 1
                while end < len(text) and text[end] in _INLINE_SPACE:
                    end += 1
            if layout.line_of(start) != layout.line_of(end):
                _refuse(
                    path,
                    layout.file,
                    "this change cannot be written: a flow entry to remove spans several lines",
                )
            edits.append(TextEdit(start, end, "", EditKind.INLINE))
        return edits

    # --- block scalars ---------------------------------------------------------------------------

    def _rewrite_block(
        self,
        layout: FileLayout,
        slot: ValueSlot,
        node: ConfigScalar,
        value: str,
        path: ConfigPath,
    ) -> bool:
        """Rewrite an existing literal/folded block to ``value``; ``False`` when no block holds it.

        A literal block must fit the value (9.7); a folded block refuses more-indented lines itself
        (10.6, 10.7) and reports a value no block can hold with ``ValueError``.
        """
        if node.style is ScalarStyle.LITERAL and not literal_fits(value):
            return False
        try:
            edits = rewrite_block(layout, slot, node, value)
        except WriteRefusedError as error:
            raise _with_context(error, layout.file, path) from error
        except ValueError:
            return False
        self._add(layout, edits)
        return True

    # --- kind and block form changes ------------------------------------------------------------

    def _replace_value(
        self,
        layout: FileLayout,
        slot: ValueSlot,
        value: NewValue,
        path: ConfigPath,
        preferred: QuoteStyle | None,
    ) -> None:
        """Replace the slot's whole value, whatever its kind, by ``value`` (5.5, 5.6, 9.6, 9.7).

        In a flow parent the old text is replaced by the flow rendering (8.5). In a block parent
        the value's inline extent gets the new inline part, its body lines are removed through the
        last content line (5.5) and the new following lines take their place, or follow the line
        holding the inline extent when there were none (11.4). An inline part that disappears keeps
        an end-of-line comment at its column, and takes the spaces after the ``:`` or ``-`` with it
        when no comment follows (11.5). A list item gets a compact collection unless its line has
        an end-of-line comment (8.4).
        """
        extent = layout.inline_extent(slot)
        empty = extent.start == extent.end
        if slot.parent_style is CollectionStyle.FLOW:
            ctx = self._context(layout, extent.start, path)
            text = self._render(lambda: render_flow_value(value, ctx), layout, path)
            text = " " + text if empty else text
            self._add(layout, [TextEdit(extent.start, extent.end, text, EditKind.INLINE)])
            return
        line = layout.line_of(extent.end)
        comment = layout.comment_start(line)
        body = layout.body_lines(slot)
        following_at = body.start if body is not None else self._following_start(layout, extent.end)
        ctx = self._context(layout, following_at, path)
        inline, following = self._render(
            lambda: render_block_value(
                value,
                slot.owner_column,
                slot.in_sequence,
                ctx,
                compact=comment is None,
                preferred=preferred,
            ),
            layout,
            path,
        )
        edits: list[TextEdit] = []
        if inline:
            text = " " + inline if empty else inline
            edits.append(TextEdit(extent.start, extent.end, text, EditKind.INLINE))
        elif not empty:
            end = extent.end
            line_end = layout.line_span(line).end
            if comment is None and not layout.file.text[end:line_end].strip(_INLINE_SPACE):
                end = line_end
            start = extent.start if comment is not None else slot.indicator_end
            edits.append(TextEdit(start, end, "", EditKind.INLINE))
        strip = not _needs_final_break(value)
        if body is None:
            if following:
                edits.append(
                    self._following_edit(layout, extent.end, following, ctx.line_break, strip=strip)
                )
        elif layout.line_break(layout.line_of(body.end - 1)) == "":
            # The body ends the file without a final break; so does the new text (13.2).
            start = layout.line_span(line).end
            text = layout.line_break(line) + following if following else ""
            if strip:
                text = text.removesuffix(layout.line_break(line))
            edits.append(TextEdit(start, body.end, text, EditKind.LINES))
        else:
            edits.append(TextEdit(body.start, body.end, following, EditKind.LINES))
        self._add(layout, edits)

    @staticmethod
    def _context(layout: FileLayout, index: int, path: ConfigPath) -> RenderContext:
        return RenderContext(
            step=layout.indent_step(),
            dash_offset=layout.dash_offset(),
            line_break=layout.line_break_at(index),
            path=path,
        )

    # --- plain, quoted and empty scalars ---------------------------------------------------------

    def _replace_scalar(
        self,
        layout: FileLayout,
        slot: ValueSlot,
        node: ConfigScalar,
        value: NewScalar,
        path: ConfigPath,
    ) -> None:
        """Replace a plain, quoted or empty scalar in its slot (6.1-6.3, 9.1-9.3, 9.7)."""
        preferred = quote_style_of(node.style)
        extent = layout.inline_extent(slot)
        empty = extent.start == extent.end
        if slot.parent_style is CollectionStyle.FLOW:
            text = self._render(
                lambda: render_scalar(value, Context.FLOW_VALUE, preferred, path), layout, path
            )
            following = ""
        else:
            insert_at = self._following_start(layout, extent.end)
            ctx = RenderContext(
                step=layout.indent_step(),
                dash_offset=layout.dash_offset(),
                line_break=layout.line_break_at(insert_at),
                path=path,
            )
            text, following = self._render(
                lambda: render_block_value(
                    value, slot.owner_column, slot.in_sequence, ctx, preferred=preferred
                ),
                layout,
                path,
            )
        edits: list[TextEdit] = []
        if empty:
            if text:
                edits.append(TextEdit(extent.start, extent.end, " " + text, EditKind.INLINE))
        elif text or slot.parent_style is CollectionStyle.FLOW:
            edits.append(TextEdit(extent.start, extent.end, text, EditKind.INLINE))
        else:
            # An empty spelling (``{a: }`` in VALUE): drop the scalar and the spaces before it.
            edits.append(TextEdit(slot.indicator_end, extent.end, "", EditKind.INLINE))
        if following and isinstance(value.value, str):
            strip = required_chomping(value.value) is Chomping.STRIP
            edits.append(
                self._following_edit(layout, extent.end, following, ctx.line_break, strip=strip)
            )
        self._add(layout, edits)

    @staticmethod
    def _following_start(layout: FileLayout, index: int) -> int:
        """Start of the line after the one holding ``index`` (end of file when there is none)."""
        line = layout.line_of(index)
        if line + 1 < layout.line_count:
            return layout.line_start(line + 1)
        return len(layout.file.text)

    def _following_edit(
        self, layout: FileLayout, extent_end: int, following: str, line_break: str, *, strip: bool
    ) -> TextEdit:
        """Insert ``following`` lines after the line holding ``extent_end`` (11.4).

        At the end of a file without a final line break the lines start with a break. Strip
        content (``|-``) then leaves the file without a final break; clip and keep content needs
        its final break, which is part of the value.
        """
        line = layout.line_of(extent_end)
        at = self._following_start(layout, extent_end)
        if layout.line_break(line) == "":
            following = line_break + following
            if strip:
                following = following.removesuffix(line_break)
        return TextEdit(at, at, following, EditKind.LINES)

    # --- bookkeeping -----------------------------------------------------------------------------

    def _layout(self, file: SourceFile) -> FileLayout:
        layout = self._layouts.get(file.real_path)
        if layout is None:
            layout = FileLayout.build(file, self._document)
            self._layouts[file.real_path] = layout
        return layout

    def _add(self, layout: FileLayout, edits: list[TextEdit]) -> None:
        key = layout.file.real_path
        if key in self._whole:
            _refuse(self._path, layout.file, "the file would be changed in two ways")
        self._edits.setdefault(key, []).extend(edits)

    def _replace_file(self, file: SourceFile, text: str, path: ConfigPath) -> None:
        """Replace a text-include target's whole content; one file never gets two patches (13.6).

        A target included at several places may be given the same new content at each of them;
        different contents, or a whole replacement besides edits, are refused.
        """
        key = file.real_path
        if key in self._whole and self._whole[key][1] == text:
            return
        if key in self._whole or self._edits.get(key):
            _refuse(path, file, "the file would be changed in two ways")
        self._whole[key] = (file, text)

    def patches(self) -> tuple[FilePatch, ...]:
        """One patch per changed file, in ``document.files`` order (root file first)."""
        patches: list[FilePatch] = []
        for file in self._document.files:
            key = file.real_path
            if key in self._whole:
                new_text = self._whole[key][1]
            elif self._edits.get(key):
                layout = self._layouts[key]
                new_text = apply_edits(layout, self._edits[key])
            else:
                continue
            if new_text != file.text:
                patches.append(FilePatch(file=file, new_text=new_text))
        if not patches:
            _refuse(self._path, self._document.root_file, "the change would not modify any file")
        return tuple(patches)

    def _guard(self, file: SourceFile, path: ConfigPath, locate: Callable[[], _T]) -> _T:
        """Run a layout lookup; a layout it cannot interpret (``? key``) is refused (14.9)."""
        try:
            return locate()
        except ValueError as error:
            _refuse(path, file, f"the value's layout cannot be written ({error})")

    def _render(self, render: Callable[[], _T], layout: FileLayout, path: ConfigPath) -> _T:
        """Run a renderer; its refusals (a lone surrogate) are completed with file and path."""
        try:
            return render()
        except WriteRefusedError as error:
            raise _with_context(error, layout.file, path) from error


def _with_context(
    error: WriteRefusedError, file: SourceFile, path: ConfigPath
) -> WriteRefusedError:
    return WriteRefusedError(
        error.problem,
        file=error.file if error.file is not None else file.path,
        path=error.path if error.path is not None else str(path),
    )


def _refuse(path: ConfigPath, file: SourceFile, problem: str) -> NoReturn:
    raise WriteRefusedError(problem, file=file.path, path=str(path))


def _sibling_styles(
    collection: ConfigMapping | ConfigSequence, deleted: Collection[int] = ()
) -> SiblingStyles:
    """Quote styles of the last remaining entry's key and scalar value (7.1-7.3).

    Entries at the ``deleted`` indices are not in the written file, so they are no preceding
    sibling; with no remaining entry there is no preferred style (minimal style). A value that is
    a collection, a block scalar or a text include gives no preferred style.
    """
    if isinstance(collection, ConfigMapping):
        entries = [e for i, e in enumerate(collection.entries) if i not in deleted]
        if not entries:
            return SiblingStyles(key=None, value=None)
        last = entries[-1]
        return SiblingStyles(key=quote_style_of(last.key_style), value=_value_style(last.value))
    items = [item for i, item in enumerate(collection.items) if i not in deleted]
    if not items:
        return SiblingStyles(key=None, value=None)
    return SiblingStyles(key=None, value=_value_style(items[-1]))


def _raw_style(raw: str) -> QuoteStyle:
    """The quote style of an include string from its source text."""
    if raw.startswith("'"):
        return ScalarStyle.SINGLE_QUOTED
    if raw.startswith('"'):
        return ScalarStyle.DOUBLE_QUOTED
    return ScalarStyle.PLAIN


def _value_style(node: ConfigNode) -> QuoteStyle | None:
    return quote_style_of(node.style) if isinstance(node, ConfigScalar) else None


def _needs_final_break(value: NewValue) -> bool:
    """Whether the last line of ``value`` rendered in block style is content needing its break.

    That is a literal block (clip or keep chomping) as the last value: its final line break is
    part of the string (9.1). Everything else may end at a break-less file end (11.6).
    """
    while not isinstance(value, NewScalar):
        if isinstance(value, NewMapping):
            if not value.entries:
                return False
            value = value.entries[-1][1]
        elif not value.items:
            return False
        else:
            value = value.items[-1]
    text = value.value
    return (
        isinstance(text, str)
        and _LF in text
        and literal_fits(text)
        and required_chomping(text) is not Chomping.STRIP
    )


def _merges(node: ConfigNode, value: NewValue) -> bool:
    """Whether ``value`` is merged into ``node`` entry-wise: same collection kind, not empty."""
    if isinstance(node, ConfigMapping) and isinstance(value, NewMapping):
        return bool(value.entries)
    if isinstance(node, ConfigSequence) and isinstance(value, NewSequence):
        return bool(value.items)
    return False


def _kind(value: ConfigNode | NewValue) -> str:
    """A short description of a value's kind for refusal messages."""
    if isinstance(value, ConfigMapping):
        return "a mapping"
    if isinstance(value, ConfigSequence):
        return "a list"
    if isinstance(value, ConfigScalar):
        if value.style in _BLOCK_STYLES:
            return f"a {value.style.value} block scalar"
        return "a scalar"
    if isinstance(value, NewScalar):
        if isinstance(value.value, str):
            return "a multi-line string" if _LF in value.value else "a string"
        return "a non-string scalar"
    if isinstance(value, NewMapping):
        return "a mapping"
    return "a list"
