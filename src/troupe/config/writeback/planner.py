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
  insertion; a flow mapping none of whose entries stays is rendered anew. New keys go through the
  placement walk (config-include-overrides 7.3), so below an include they become overrides;
* a mapping over a mapping merged from an include and its overrides is merged across the layers
  (7.3, 7.4): kept keys change at their written entry, removed keys are deleted from every written
  mapping holding them, an override mapping that would lose every entry is pruned from its holder
  instead, never past a ``$ref`` mapping (research.md Decision 4), any other written mapping
  losing every key and gaining none becomes ``{}`` (never a null), and ``{}`` removes every key
  (the override pruned, the included mapping written as ``{}``) or is refused.

A new key (with any missing intermediate mappings nested under it) or an appended list item goes
through :meth:`_Planner.add_entries` into the written collection its target names (3.1, 3.2,
4.1): for a new key below an include that is the override mapping in the including file chosen by
the placement walk (config-include-overrides 4.1-4.6, 6.2, 6.3), with ``set --in-include`` the
included side of the deepest existing mapping (step 1 rule 2, 10.1), and otherwise the file holding
the mapping or list as in step 1 (4.6, 6.1). In a
block collection as whole lines at its insertion point and entries column with the quote styles of
its last remaining entry (7.1-7.3, 11.1, 11.2, 11.6), in a flow collection right after its last
entry on that entry's line (11.3). A new key whose placement is a scalar-form include goes through
:meth:`_Planner.convert_include`, which rewrites the include in mapping form with the key as its
override, in the file holding the include string (config-include-overrides 5.1-5.5).

New keys placed by the override rule are recorded; :func:`plan_set` gives, with the patches, the
intended configuration holding them also at every other place reaching the same written mapping or
include site, when the file holding it is included at several places
(:func:`~troupe.config.writeback.shared.with_other_places`, config-include-overrides 4.8).

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
    IncludeRef,
    MappingEntry,
    ScalarStyle,
    SourceFile,
)
from troupe.config.overrides import (
    holder_of,
    is_ref_key,
    physical_entry,
    physical_holders,
)
from troupe.config.paths import ConfigPath
from troupe.config.resolve import walk_path
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
from troupe.config.writeback.compare import PlainMapping, new_to_plain, node_to_plain, plain_equal
from troupe.config.writeback.edits import EditKind, TextEdit, apply_edits
from troupe.config.writeback.layout import FileLayout, ValueSlot
from troupe.config.writeback.scalar_render import (
    Context,
    QuoteStyle,
    quote_style_of,
    render_scalar,
)
from troupe.config.writeback.shared import PlacedKeys, with_other_places
from troupe.config.writeback.target import (
    AddKeyTarget,
    AppendTarget,
    IncludeSite,
    KeyPlacement,
    ReplaceTarget,
    SetRequest,
    place_new_keys,
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


@dataclass
class _Removal:
    """Entries of one written mapping that a layered merge removes (deleted when planning ends).

    ``at`` is the path of the merged node of which ``mapping`` is a written layer and
    ``is_override`` whether that layer was reached through an override side: such a mapping
    losing every entry may be pruned (research.md Decision 4).
    """

    mapping: ConfigMapping
    indexes: set[int]
    at: ConfigPath
    is_override: bool
    pruned: bool = False


@dataclass(frozen=True)
class FilePatch:
    """The complete new text of one changed file."""

    file: SourceFile
    new_text: str


@dataclass(frozen=True)
class Plan:
    """The planned set: its patches and the configuration they must read back as."""

    patches: tuple[FilePatch, ...]
    intended: PlainMapping
    """``request.intended`` with the new keys placed by the override rule also at every other
    place reaching their written mapping or include site (config-include-overrides 4.8)."""


def plan_set(document: ConfigDocument, request: SetRequest) -> Plan:
    """Plan the set: one :class:`FilePatch` per file it changes, in ``document.files`` order, and
    the intended configuration the read-back compares with.

    Never two patches for the same file, and every patch changes its file. Raises
    :class:`WriteRefusedError` (exit 3) naming the file and PATH for every refusal: a YAML include
    root that would change (2.5), a non-string or another include over a text include (2.4), a
    layout the planner cannot write (14.9), or a set that would change nothing.

    New keys placed by the override rule into a file included at several places appear at each
    of them (requirements choice 17): the intended configuration gains them there, in merged
    order. Every other change keeps ``request.intended`` (a shared file is refused by the
    read-back, config-set-writeback 14.2).
    """
    planner = _Planner(document, request.path, in_include=request.in_include)
    target = request.target
    if isinstance(target, ReplaceTarget):
        planner.replace(target, request.value)
    elif isinstance(target, AddKeyTarget):
        planner.add_key(target, request.value)
    else:
        planner.append(target, request.value)
    patches = planner.patches()
    intended = with_other_places(document.root, request.intended, planner.placed)
    return Plan(patches=patches, intended=intended)


def plan_patch(document: ConfigDocument, request: SetRequest) -> tuple[FilePatch, ...]:
    """Return the patches of :func:`plan_set` (the intended configuration left out)."""
    return plan_set(document, request).patches


class _Planner:
    """Collects the edits of one set per file and turns them into file patches."""

    def __init__(self, document: ConfigDocument, path: ConfigPath, *, in_include: bool) -> None:
        self._document = document
        self._path = path
        # `set --in-include`: new keys of a collection replacement use the step 1 rule (10.3).
        self._in_include = in_include
        # Keyed by ``SourceFile.real_path``.
        self._layouts: dict[Path, FileLayout] = {}
        self._edits: dict[Path, list[TextEdit]] = {}
        self._whole: dict[Path, tuple[SourceFile, str]] = {}
        # Layered-merge removals and the mappings receiving new entries, keyed by ``id``.
        self._removals: dict[int, _Removal] = {}
        self._receivers: set[int] = set()
        # New entries for written mappings, added once pruning is known (sibling styles).
        self._additions: dict[int, tuple[ConfigMapping, list[tuple[str, NewValue]]]] = {}
        # Scalar-form includes to convert, keyed by the ``id`` of their ``IncludeRef``, with their
        # new entries merged, converted once planning ends (one conversion per include).
        self._conversions: dict[int, tuple[IncludeSite, list[tuple[str, NewValue]]]] = {}
        # New keys placed by the override rule, for the intended configuration (4.8).
        self.placed: list[PlacedKeys] = []

    # --- entry point -----------------------------------------------------------------------------

    def replace(self, target: ReplaceTarget, value: NewValue) -> None:
        """Plan the change of the existing value ``target.node`` to ``value`` where it is written.

        Below a mapping-form include the parent's written entry comes from
        :func:`~troupe.config.overrides.physical_entry`: the override when the key is overridden,
        else the entry in the file it comes from (config-include-overrides 7.1, 7.2). The slot is
        in that entry's file; ``target.node`` (the merged value) drives comparisons and kind
        decisions, while the slot holds the written value (the override's own mapping when a
        mapping present in both layers is replaced).
        """
        parent = target.parent
        if isinstance(parent, ConfigMapping):
            written, entry = physical_entry(parent, str(target.key))
            file = written.origin.file
        else:
            file = parent.origin.file
        layout = self._guard(file, self._path, lambda: self._layout(file))

        def slot() -> ValueSlot:
            if isinstance(parent, ConfigMapping):
                return layout.entry_slot(written, entry, self._path)
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
        first, *intermediates = target.missing
        for key in reversed(intermediates):
            value = NewMapping(entries=((key, value),))
        segments = self._path.segments[: len(self._path.segments) - len(target.missing)]
        self._place(target.placement, ((first, value),), self._path, ConfigPath(segments))

    def _place(
        self,
        placement: KeyPlacement,
        entries: Sequence[tuple[str, NewValue]],
        path: ConfigPath,
        at: ConfigPath,
    ) -> None:
        """Write new ``entries`` at ``placement``, below its ``prefix`` keys created as mappings.

        A written mapping receives them after its last remaining entry (config-include-overrides
        4.5) when planning ends, so that its sibling styles skip the entries removed or pruned by
        the same set; a scalar-form include is converted to mapping form holding them (5.1-5.5),
        also when planning ends. The entries of every placement sharing a target (one written
        mapping, or one include) are merged first (:func:`_merge_new`), so a mapping VALUE adding
        keys below several included mappings gets one insertion or one conversion holding all of
        them (4.4, 7.3).

        ``at`` is the path of the mapping the entries are new in; a placement made by the override
        rule is recorded for the intended configuration (:attr:`placed`, 4.8).
        """
        self._record(placement, entries, at)
        entries = _nest(placement.prefix, entries)
        if placement.convert is not None:
            site = placement.convert
            _merge_new(self._conversions.setdefault(id(site.ref), (site, []))[1], entries)
        elif placement.mapping is not None:
            mapping = placement.mapping
            self._receivers.add(id(mapping))
            _merge_new(self._additions.setdefault(id(mapping), (mapping, []))[1], entries)
        else:  # pragma: no cover - a placement always names a mapping or an include
            raise ValueError("the placement names neither a mapping nor an include")

    def _record(
        self, placement: KeyPlacement, entries: Sequence[tuple[str, NewValue]], at: ConfigPath
    ) -> None:
        """Record new keys placed by the override rule (their file may be included at several
        places, 4.8)."""
        if placement.by_override:
            self.placed.append(PlacedKeys(placement=placement, entries=tuple(entries), at=at))

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
        self._receivers.add(id(collection))
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

        A site with no parent is an include string that is its file's whole root
        (:meth:`_convert_root`).
        """
        include = site.ref
        if site.parent is None:
            self._convert_root(include, entries, path)
            return
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

    def _convert_root(
        self, include: IncludeRef, entries: Sequence[tuple[str, NewValue]], path: ConfigPath
    ) -> None:
        """Convert an include string that is its file's whole root to a root ``$ref`` mapping.

        ``$ref: `` is inserted before the include string on its line, so the string, the
        whitespace before an end-of-line comment and the comment keep their bytes (the comment is
        not re-aligned, as in 5.2), and the entries follow on the next lines at the string's
        column (0 when the string starts its line), as for a compact list item (5.1, 5.3). An
        include string that
        does not stay on one line or shares its line with anything before it (``--- file:...``)
        is refused (exit 3, research.md Decision 7).
        """
        file = include.directive.file
        layout = self._guard(file, path, lambda: self._layout(file))
        span = include.directive.span
        raw = file.text[span.start : span.end]
        line = layout.line_of(span.start)
        start = layout.line_start(line)
        if layout.line_of(span.end) != line or file.text[start : span.start].strip(" "):
            _refuse(
                path,
                file,
                f"this layout cannot be written: the include string {raw} is not alone on its "
                "line as the file's root",
            )
        siblings = SiblingStyles(key=ScalarStyle.PLAIN, value=_raw_style(raw))
        ctx = self._context(layout, self._following_start(layout, span.end), path)
        column = span.start - start
        following = self._render(
            lambda: render_ref_form_lines(raw, entries, column, siblings, ctx, with_ref=False),
            layout,
            path,
        )
        strip = not _needs_final_break(entries[-1][1])
        self._add(
            layout,
            [
                TextEdit(span.start, span.start, f"{INCLUDE_REF_KEY}: ", EditKind.LINES),
                self._following_edit(layout, span.end, following, ctx.line_break, strip=strip),
            ],
        )

    @staticmethod
    def _include_slot(layout: FileLayout, site: IncludeSite, path: ConfigPath) -> ValueSlot:
        """The written slot of the include string at ``site`` (its parent may be merged)."""
        parent = site.parent
        if isinstance(parent, ConfigMapping):
            written, entry = physical_entry(parent, str(site.key))
            return layout.entry_slot(written, entry, path)
        if parent is None or site.key is None:  # pragma: no cover - see _convert_root
            raise ValueError("a root include string has no slot")
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
        if isinstance(node, ConfigMapping) and node.layers is not None:
            if isinstance(value, NewMapping):
                # Kept, removed and new keys of a merged mapping live in several files.
                if value.entries:
                    self._merge_layered(node, value, path, at)
                else:
                    self._empty_layered(layout, node, path, at)
                return True
            # Another kind replaces the override's own value only; the merged value is then it.
            self._replace_value(layout, slot, value, path, preferred)
            return True
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
        """Merge into an unmerged mapping; its new keys go through the placement walk (7.3).

        When the placement is the mapping itself, they are added in one insertion styled after
        its last remaining entry (step 1). A flow mapping none of whose entries stays is rendered
        anew as ``value`` when the placement is the mapping itself. A mapping none of whose
        entries stays while its new keys are written at another placement becomes ``{}`` (a
        block mapping emptied of its lines would read back as null).
        """
        new = dict(value.entries)
        deleted = [index for index, entry in enumerate(node.entries) if entry.key not in new]
        existing = {entry.key for entry in node.entries}
        added = [(key, item) for key, item in value.entries if key not in existing]
        placement = None
        if added:
            placement = place_new_keys(self._document, at, in_include=self._in_include)
            if placement.mapping is node and not placement.prefix:
                # The mapping itself: the step 1 insertion below, styled with ``deleted``.
                self._record(placement, added, at)
                placement = None
        if len(deleted) == len(node.entries):
            empty = NewMapping(entries=())
            if placement is not None:
                if node.style is CollectionStyle.FLOW:
                    self._render_flow_anew(layout, slot, empty, path)
                else:
                    self._replace_value(layout, slot, empty, path, None)
                self._place(placement, added, path, at)
                return
            if node.style is CollectionStyle.FLOW:
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
        if placement is None:
            if added:
                self.add_entries(node, added, path, deleted)
        else:
            self._place(placement, added, path, at)

    # --- mappings merged across layers -----------------------------------------------------------

    def _merge_layered(
        self, node: ConfigMapping, value: NewMapping, path: ConfigPath, at: ConfigPath
    ) -> None:
        """Merge a non-empty mapping into the merged mapping ``node`` at ``at`` (7.3, 7.4).

        Each removed key is removed from every written mapping holding it (the override, the
        included side, or both); the deletions are made when planning ends, so that an override
        mapping losing every entry and gaining none is pruned instead (research.md Decision 4).
        Each kept key changes at its physical entry (7.1, 7.2). New keys go through the placement
        walk (Requirement 4): they are written last in the override, so they come last in the
        merged order as in the intended configuration.
        """
        new = dict(value.entries)
        for entry in node.entries:
            if entry.key not in new:
                for holder in physical_holders(node, entry.key):
                    index = _index_of(holder.mapping, holder.entry)
                    self._remove(holder.mapping, index, at, is_override=holder.is_override)
        for entry in node.entries:
            if entry.key in new:
                written, physical = physical_entry(node, entry.key)
                file = written.origin.file
                layout = self._guard(file, path, partial(self._layout, file))
                self.change_slot(
                    layout,
                    entry.value,
                    partial(layout.entry_slot, written, physical, path),
                    new[entry.key],
                    path,
                    at.child(entry.key),
                )
        existing = {entry.key for entry in node.entries}
        added = [(key, item) for key, item in value.entries if key not in existing]
        if added:
            placement = place_new_keys(self._document, at, in_include=self._in_include)
            self._place(placement, added, path, at)

    def _empty_layered(
        self, layout: FileLayout, node: ConfigMapping, path: ConfigPath, at: ConfigPath
    ) -> None:
        """Replace the merged mapping ``node`` by ``{}``: remove every key from every layer.

        The override is pruned (its own entry deleted, upward as for a merge) and the included
        mapping is written as ``{}``: emptied lines would leave a null there, which does not read
        back as ``{}``. Only a merge of two plain written mappings can be emptied this way; any
        other (an override or included side that is itself an include or a merge, or an override
        that cannot be pruned) is refused (exit 3) naming PATH.
        """
        layers = node.layers
        if layers is None:  # pragma: no cover - only called for merged mappings
            raise ValueError("not a merged mapping")
        base, over = layers.base, layers.over
        parent = self._merged_parent(at)
        base_holder = None if parent is None else holder_of(parent, at.segments[-1], base)
        prune = self._prune_link(over, at, is_override=True)
        if (
            any(side.layers is not None or side.include is not None for side in (base, over))
            or base_holder is None
            or prune is None
        ):
            _refuse(
                path,
                layout.file,
                "this change cannot be written: this mapping merged from an include and its "
                "overrides cannot be replaced by an empty mapping",
            )
        holding, index, parent_at, is_override = prune
        self._remove(holding, index, parent_at, is_override=is_override)
        if base.entries:
            self._replace_written(base_holder.mapping, base_holder.entry, NewMapping(entries=()))

    def _replace_written(
        self, holding: ConfigMapping, entry: MappingEntry, value: NewMapping
    ) -> None:
        """Replace the value of the written ``entry`` of ``holding`` by the mapping ``value``.

        A flow mapping is rendered anew in place; any other value is replaced in its slot.
        """
        file = holding.origin.file
        path = self._path
        layout = self._guard(file, path, partial(self._layout, file))
        slot = self._guard(file, path, lambda: layout.entry_slot(holding, entry, path))
        if isinstance(entry.value, ConfigMapping) and entry.value.style is CollectionStyle.FLOW:
            self._render_flow_anew(layout, slot, value, path)
        else:
            self._guard(file, path, lambda: self._replace_value(layout, slot, value, path, None))

    def _remove(
        self, mapping: ConfigMapping, index: int, at: ConfigPath, *, is_override: bool
    ) -> None:
        """Record the removal of entry ``index`` of the written ``mapping`` (:class:`_Removal`)."""
        removal = self._removals.get(id(mapping))
        if removal is None:
            removal = _Removal(mapping=mapping, indexes=set(), at=at, is_override=is_override)
            self._removals[id(mapping)] = removal
        removal.indexes.add(index)

    def _removed(self, mapping: ConfigMapping) -> Collection[int]:
        """Indices of the entries of ``mapping`` that a layered merge removes."""
        removal = self._removals.get(id(mapping))
        return () if removal is None else removal.indexes

    def _flush_removals(self) -> None:
        """Make the recorded removals and additions, pruning emptied overrides (Decision 4).

        A mapping reached through an override side that loses every entry and receives none is
        not emptied; its own entry is deleted from the mapping holding it instead, which may in
        turn be pruned. A ``$ref`` mapping keeps its ``$ref`` entry, so pruning never passes it.

        Then each scalar-form include receiving new entries is converted once, and each receiving
        mapping gets its new entries in one insertion, styled after its last
        entry that remains (4.5). A flow mapping that loses every entry is rendered anew with
        them. Each other mapping gets one deletion (5.3, 5.4), except that a mapping losing
        every entry and receiving none becomes ``{}`` at its written entry: emptied lines would
        read back as null, which an override could hide from the read-back. Such a mapping
        without a written entry holding it is refused (exit 3) naming PATH.
        """
        pending = list(self._removals.values())
        while pending:
            removal = pending.pop()
            mapping = removal.mapping
            if (
                removal.pruned
                or id(mapping) in self._receivers
                or len(removal.indexes) < len(mapping.entries)
            ):
                continue
            link = self._prune_link(mapping, removal.at, is_override=removal.is_override)
            if link is None:
                continue
            removal.pruned = True
            holding, index, parent_at, is_override = link
            self._remove(holding, index, parent_at, is_override=is_override)
            pending.append(self._removals[id(holding)])
        for site, converted in self._conversions.values():
            self.convert_include(site, converted, self._path)
        rendered: set[int] = set()
        for key, (mapping, entries) in self._additions.items():
            losing = self._removals.get(key)
            emptied = losing is not None and len(losing.indexes) == len(mapping.entries)
            if losing is not None and emptied and mapping.style is CollectionStyle.FLOW:
                holder = self._written_entry(losing)
                self._replace_written(holder[0], holder[1], NewMapping(entries=tuple(entries)))
                rendered.add(key)
            else:
                self.add_entries(mapping, entries, self._path, self._removed(mapping))
        for key, removal in list(self._removals.items()):
            if removal.pruned or key in rendered:
                continue
            mapping = removal.mapping
            if key not in self._receivers and len(removal.indexes) == len(mapping.entries):
                holding, entry = self._written_entry(removal)
                self._replace_written(holding, entry, NewMapping(entries=()))
                continue
            file = mapping.origin.file
            layout = self._guard(file, self._path, partial(self._layout, file))
            self._delete_entries(layout, mapping, sorted(removal.indexes), self._path)

    def _written_entry(self, removal: _Removal) -> tuple[ConfigMapping, MappingEntry]:
        """The written mapping and entry holding ``removal.mapping``, or a refusal (exit 3)."""
        parent = self._merged_parent(removal.at)
        holder = (
            None if parent is None else holder_of(parent, removal.at.segments[-1], removal.mapping)
        )
        if holder is None:
            _refuse(
                self._path,
                removal.mapping.origin.file,
                "this change cannot be written: a mapping that loses every key has no written "
                "entry to hold an empty mapping",
            )
        return holder.mapping, holder.entry

    def _prune_link(
        self, mapping: ConfigMapping, at: ConfigPath, *, is_override: bool
    ) -> tuple[ConfigMapping, int, ConfigPath, bool] | None:
        """The written entry holding the override ``mapping`` (a layer of the merged node at
        ``at``): its mapping and index, that mapping's merged path and whether it is an override.

        ``None`` when ``mapping`` is not an override, is a ``$ref`` mapping, or is the top of its
        own written tree.
        """
        if not is_override or any(is_ref_key(entry.key) for entry in mapping.entries):
            return None
        parent = self._merged_parent(at)
        if parent is None:
            return None
        holder = holder_of(parent, at.segments[-1], mapping)
        if holder is None:
            return None
        parent_at = ConfigPath(at.segments[:-1])
        index = _index_of(holder.mapping, holder.entry)
        return holder.mapping, index, parent_at, holder.is_override

    def _merged_parent(self, at: ConfigPath) -> ConfigMapping | None:
        """The (merged) mapping holding the node at ``at``; ``None`` for the root or a list."""
        if not at.segments:
            return None
        parent = walk_path(self._document.root, ConfigPath(at.segments[:-1])).deepest
        return parent if isinstance(parent, ConfigMapping) else None

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
        self._flush_removals()
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


def _nest(
    prefix: Sequence[str], entries: Sequence[tuple[str, NewValue]]
) -> Sequence[tuple[str, NewValue]]:
    """``entries`` below the keys ``prefix`` created as nested new mappings (4.4)."""
    if not prefix:
        return entries
    first, *rest = prefix
    value = NewMapping(entries=tuple(entries))
    for key in reversed(rest):
        value = NewMapping(entries=((key, value),))
    return ((first, value),)


def _merge_new(merged: list[tuple[str, NewValue]], entries: Sequence[tuple[str, NewValue]]) -> None:
    """Merge new ``entries`` into ``merged`` (new entries of one placement), in place.

    A key already there whose two values are mappings gets their entries merged the same way;
    any other key is appended, so the first-seen order is kept. Keys created by one set never
    meet with two values that are not both mappings (they come from one VALUE).
    """
    for key, value in entries:
        index = next((i for i, (seen, _) in enumerate(merged) if seen == key), None)
        if index is None:
            merged.append((key, value))
            continue
        existing = merged[index][1]
        if not (isinstance(existing, NewMapping) and isinstance(value, NewMapping)):
            raise ValueError(f"the new key {key!r} is created twice")  # pragma: no cover
        nested = list(existing.entries)
        _merge_new(nested, value.entries)
        merged[index] = (key, NewMapping(entries=tuple(nested)))


def _index_of(mapping: ConfigMapping, entry: object) -> int:
    """The index of ``entry`` (by identity) in ``mapping``."""
    for index, candidate in enumerate(mapping.entries):
        if candidate is entry:
            return index
    raise ValueError("the entry is not in the mapping")  # pragma: no cover


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
