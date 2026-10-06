"""SetCommand: the ``set_value`` use case (no-op, plan, verify, write all, roll back, record).

Everything up to the first write is pure: the target is resolved on ``theatre.document`` (the files
as on disk, product-owner decision A), a no-op returns before planning (14.6), every patch is
planned and verified together (13.6) and the history changes are derived, so a refusal changes no
file (14.7). Then every patch is written atomically in order; when a write fails, the files already
written are restored in reverse order and the failure is re-raised with PATH (14.5, 16.2). The
changes are recorded in one batch only after every write succeeded; when recording fails, every
written file is restored and the store error is re-raised (15.6). A failing restore never stops the
remaining restores; the original error keeps its exit code and names each unrestored file (14.10).

Since step 4 the batch is recorded with the caller's context (``handle_set`` passes a cli context
with the command's actor and ``--reason``), so the set's own events form one change, separate from
the reconcile change recorded when the theatre was opened (config-lineage 3.3, 4.1, 4.2, 6.1).

Temporary (config-aliases tasks.md, "Safety window"; removed by task 4.1): since task 2.1 the
loader accepts anchors, but writing at anchors, through aliases and next to merge keys is only
taught to ``set`` by task 4.1. Until then ``set_value`` refuses (exit 3, before any other step) a
configuration that contains an anchor, an alias or a merge key anywhere.
"""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from troupe.config.errors import TroupeError, WriteRefusedError
from troupe.config.history import LeafChange
from troupe.config.lineage import ChangeContext
from troupe.config.model import (
    ConfigDocument,
    ConfigMapping,
    ConfigNode,
    ConfigSequence,
    SourceFile,
)
from troupe.config.paths import ConfigPath
from troupe.config.theatre import Theatre
from troupe.config.writeback.atomic import restore_file, write_atomically
from troupe.config.writeback.compare import plain_equal
from troupe.config.writeback.leaf_diff import diff_leaves
from troupe.config.writeback.planner import FilePatch, plan_set
from troupe.config.writeback.target import resolve_target
from troupe.config.writeback.value import NewValue
from troupe.config.writeback.verify import verify_patch


@dataclass(frozen=True)
class SetOutcome:
    """The result of a set: the written files (in write order) and the recorded changes."""

    written: tuple[Path, ...]  # empty for a no-op
    changes: tuple[LeafChange, ...]


def set_value(
    theatre: Theatre,
    path: ConfigPath,
    value: NewValue,
    context: ChangeContext,
    *,
    in_include: bool = False,
) -> SetOutcome:
    """Set the value at ``path`` to ``value`` in the files of ``theatre`` and record the history.

    The changes are recorded as one batch with ``context`` (``set`` passes a cli context carrying
    the command's actor; config-lineage 3.3). ``in_include`` (``set --in-include``) writes new
    keys by the step 1 placement (config-include-overrides Requirement 10).

    Raises the refusal of any pure step (exit 3 or 5) before any file is written; a
    :class:`WriteRefusedError` naming the failed file and PATH when a file cannot be written (the
    already-written files restored, nothing recorded); the store's :class:`ConfigLoadError` when
    the changes cannot be recorded (every written file restored).
    """
    document = theatre.document
    _refuse_alias_constructs(document, path)  # temporary: task 4.1 removes this refusal
    request = resolve_target(document, path, value, in_include=in_include)
    if plain_equal(request.current, request.intended, ordered=False):
        return SetOutcome(written=(), changes=())
    plan = plan_set(document, request)
    patches = plan.patches
    # A change written into a file included at several places appears at each (4.8, 11.2).
    request = replace(request, intended=plan.intended)
    after = verify_patch(theatre.config_file, document, patches, request)
    changes = diff_leaves(document, after)
    write_patches(patches, path)
    try:
        theatre.history.record_changes(changes, context)
    except Exception as error:
        unrestored = _restore([patch.file for patch in patches])
        if unrestored:
            raise _naming_unrestored(error, unrestored) from error
        raise
    return SetOutcome(written=tuple(patch.file.path for patch in patches), changes=changes)


def _refuse_alias_constructs(document: ConfigDocument, path: ConfigPath) -> None:
    """Temporary (removed by config-aliases task 4.1): refuse a set on a configuration that
    contains an anchor, an alias or a merge key, naming the file that holds the first one."""
    for node in _all_nodes(document.root):
        construct = None
        file = node.origin.file.path
        if node.alias is not None:
            construct, file = f"alias '*{node.alias.name}'", node.alias.token.file.path
        elif node.anchor is not None:
            construct = f"anchor '&{node.anchor.name}'"
            if node.include is not None:
                file = node.include.directive.file.path
        elif isinstance(node, ConfigMapping) and node.merge is not None:
            construct, file = "merge key '<<'", node.merge.key_origin.file.path
        if construct is not None:
            raise WriteRefusedError(
                f"{construct} is written here; set cannot change a configuration with anchors, "
                "aliases or merge keys yet",
                file=file,
                path=str(path),
            )


def _all_nodes(node: ConfigNode) -> Iterator[ConfigNode]:
    """Yield ``node`` and every node below it, including the written sides that a merged view
    hides (override layers, ``$ref`` mappings, mappings written behind a merge key)."""
    yield node
    if node.include is not None and node.include.form is not None:
        yield from _all_nodes(node.include.form)
    if isinstance(node, ConfigSequence):
        for item in node.items:
            yield from _all_nodes(item)
    elif isinstance(node, ConfigMapping):
        for entry in node.entries:
            yield from _all_nodes(entry.value)
        if node.layers is not None:
            yield from _all_nodes(node.layers.base)
            yield from _all_nodes(node.layers.over)
        if node.merge is not None:
            yield from _all_nodes(node.merge.written)


def write_patches(patches: Sequence[FilePatch], path: ConfigPath) -> None:
    """Write each patch atomically, in order; on a failure restore the written files, then raise.

    The failure is re-raised as a :class:`WriteRefusedError` naming the failed file, PATH and the
    reason, plus every already-written file that could not be restored (14.5, 14.10, 16.2).
    """
    written: list[SourceFile] = []
    for patch in patches:
        try:
            write_atomically(patch.file, patch.new_text)
        except WriteRefusedError as error:
            unrestored = _restore(written)
            problem = error.problem + _unrestored_note(unrestored)
            raise WriteRefusedError(problem, file=error.file, path=str(path)) from error
        written.append(patch.file)


def _restore(files: Sequence[SourceFile]) -> tuple[Path, ...]:
    """Restore ``files`` to their original text in reverse order; return those that failed."""
    unrestored: list[Path] = []
    for file in reversed(files):
        try:
            restore_file(file)
        except WriteRefusedError:
            unrestored.append(file.path)
    return tuple(unrestored)


def _unrestored_note(unrestored: Sequence[Path]) -> str:
    if not unrestored:
        return ""
    return f"; not restored: {', '.join(str(file) for file in unrestored)}"


def _naming_unrestored(error: Exception, unrestored: Sequence[Path]) -> Exception:
    """Return ``error`` with the unrestored files named, keeping its type (exit code) and file."""
    note = _unrestored_note(unrestored)
    if isinstance(error, TroupeError):
        return type(error)(error.problem + note, file=error.file, path=error.path)
    error.add_note(note.removeprefix("; "))
    return error
