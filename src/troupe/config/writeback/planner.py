"""PatchPlanner: slot changes, merges, new entries, deletions and kind changes.

Turns a resolved :class:`~troupe.config.writeback.target.SetRequest` into the changed text of every
file the set must change, one :class:`FilePatch` per changed file, in the order the files appear in
the loaded document (root file first). Nothing is written here.

A replaced value goes through :meth:`_Planner.change_slot`, which follows the design's "slot change
decision":

* an unchanged value (typed plain equality) needs no edit;
* a YAML include root that would change is refused (2.5); a text include gets its target file's
  whole content replaced by the text-include content rule (2.2, 2.3), or is refused when VALUE is
  not a string (2.4);
* an existing literal or folded block changed to a string it can still hold is rewritten in place
  (9.4, 9.5, 10.x);
* an existing plain, quoted or empty scalar changed to a scalar is replaced in its slot in the
  chosen quote style (6.1-6.3); a multi-line string becomes a literal block in a block collection
  (9.1, 9.2) and a double-quoted scalar in a flow collection or where a literal block cannot hold
  it (9.3, 9.7).

End-of-line comments on the changed lines are moved back to their columns by the EditApplier
(12.x). Every refusal is a :class:`WriteRefusedError` naming the file and the path (14.9).
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final, NoReturn, TypeVar

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
from troupe.config.paths import ConfigPath
from troupe.config.writeback.block_scalar import (
    Chomping,
    literal_fits,
    required_chomping,
    rewrite_block,
)
from troupe.config.writeback.collection_render import RenderContext, render_block_value
from troupe.config.writeback.compare import new_to_plain, node_to_plain, plain_equal
from troupe.config.writeback.edits import EditKind, TextEdit, apply_edits
from troupe.config.writeback.layout import FileLayout, ValueSlot
from troupe.config.writeback.scalar_render import Context, quote_style_of, render_scalar
from troupe.config.writeback.target import ReplaceTarget, SetRequest, text_include_value
from troupe.config.writeback.value import NewMapping, NewScalar, NewValue

_QUOTED_OR_PLAIN: Final = frozenset(
    {ScalarStyle.PLAIN, ScalarStyle.SINGLE_QUOTED, ScalarStyle.DOUBLE_QUOTED}
)
_BLOCK_STYLES: Final = frozenset({ScalarStyle.LITERAL, ScalarStyle.FOLDED})
_LF: Final = "\n"

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
    if not isinstance(target, ReplaceTarget):
        _refuse(
            request.path,
            document.root_file,
            "this change cannot be written: new keys and list items are not supported yet",
        )
    planner.replace(target, request.value)
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

        self.change_slot(layout, target.node, slot, value, self._path)

    # --- slot change decision --------------------------------------------------------------------

    def change_slot(
        self,
        layout: FileLayout,
        node: ConfigNode,
        slot: Callable[[], ValueSlot],
        value: NewValue,
        path: ConfigPath,
    ) -> None:
        """Plan the change of ``node`` (whose slot ``slot`` locates in ``layout``) to ``value``."""
        if plain_equal(node_to_plain(node), new_to_plain(value), ordered=False):
            return
        if node.include is not None:
            self._change_include(node, value, path)
            return
        if not self._guard(
            layout.file, path, lambda: self._change_value(layout, node, slot(), value, path)
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
    ) -> bool:
        """Plan an in-place change of a value that is not an include root; ``False`` if none fits.

        Layout lookups raise ``ValueError`` for layouts outside the contract (``? key``); the
        caller turns that into a refusal (14.9).
        """
        if not (isinstance(node, ConfigScalar) and isinstance(value, NewScalar)):
            return False
        if node.style in _BLOCK_STYLES:
            return isinstance(value.value, str) and self._rewrite_block(
                layout, slot, node, value.value, path
            )
        if node.style in _QUOTED_OR_PLAIN:
            self._replace_scalar(layout, slot, node, value, path)
            return True
        return False  # pragma: no cover - only text includes have no style

    def _change_include(self, node: ConfigNode, value: NewValue, path: ConfigPath) -> None:
        """An include root that changes: rewrite a text include's target, refuse anything else."""
        include = node.include
        if include is None:  # pragma: no cover - only called for include roots
            raise ValueError("not an include root")
        target = include.target
        directive = f"file:{include.spec}"
        if include.kind is not IncludeKind.TEXT:
            _refuse(
                path,
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
        key = file.real_path
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
