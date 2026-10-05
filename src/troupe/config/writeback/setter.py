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
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from troupe.config.errors import TroupeError, WriteRefusedError
from troupe.config.history import LeafChange
from troupe.config.lineage import ChangeContext
from troupe.config.model import SourceFile
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
    request = resolve_target(document, path, value, in_include=in_include)
    if plain_equal(request.current, request.intended, ordered=False):
        return SetOutcome(written=(), changes=())
    plan = plan_set(document, request)
    patches = plan.patches
    # New keys written into a file included at several places appear at each (4.8).
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
