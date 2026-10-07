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

Since config-aliases (step 6) the guard runs right after target resolution and before the no-op
check: a set reached through an alias or a merge key is refused (exit 3) even when it would change
nothing, and every planner refusal comes after it (config-aliases 5.1-5.3, 9.2; research.md
Decision 5).

With ``expand_alias`` (``set --expand-alias``) the first alias the guard meets is expanded in
memory, the configuration is reloaded from the new texts, the request is resolved again and the
guard asked again, until no alias remains, outermost first (config-aliases 7.7, 8.1, 8.2;
research.md Decision 6). The usual set then runs on the expanded configuration. Its patches are
merged with the expansion texts and paired with the original files, so verification reads back
the original document with the final texts, the history compares the original configuration with
the result (no event for the expansion itself, 8.6, 10.4) and every changed file is written once
(10.2, 10.3).
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final

from troupe.config.errors import TroupeError, WriteRefusedError
from troupe.config.history import LeafChange
from troupe.config.lineage import ChangeContext
from troupe.config.loader import load_config
from troupe.config.model import AliasRef, ConfigDocument, SourceFile
from troupe.config.paths import ConfigPath
from troupe.config.theatre import Theatre
from troupe.config.writeback import expand as expander
from troupe.config.writeback.aliases import alias_sites
from troupe.config.writeback.atomic import restore_file, write_atomically
from troupe.config.writeback.compare import plain_equal
from troupe.config.writeback.leaf_diff import diff_leaves
from troupe.config.writeback.planner import FilePatch, plan_set
from troupe.config.writeback.target import SetRequest, resolve_target
from troupe.config.writeback.value import NewValue
from troupe.config.writeback.verify import verify_patch

EXPANSION_CHANGES_VALUES: Final = (
    "expanding alias '*{name}' would change values elsewhere (an alias in its copy would refer "
    "to an anchor redefined before this place)"
)
"""The problem of an expansion whose copy does not load as the alias's value, because an alias
copied into it refers to another anchor of the same name (config-aliases 7.5, 10.2)."""

EXPANSION_STUCK: Final = "alias '*{name}' is still an alias after its expansion"
"""The problem of an expansion round that leaves its alias place an alias (a backstop that stops
the expansion loop)."""

REMOTE_SETS_REFUSED: Final = (
    "the configuration has a remote include ({url}); set does not change configurations with "
    "remote includes yet"
)
"""The temporary refusal (exit 3) of every set on a configuration that has a remote source, so that
no write can reach remote content before the remote write guard exists (config-remote-schema)."""


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
    expand_alias: bool = False,
) -> SetOutcome:
    """Set the value at ``path`` to ``value`` in the files of ``theatre`` and record the history.

    The changes are recorded as one batch with ``context`` (``set`` passes a cli context carrying
    the command's actor; config-lineage 3.3). ``in_include`` (``set --in-include``) writes new
    keys by the step 1 placement (config-include-overrides Requirement 10). ``expand_alias``
    (``set --expand-alias``) expands every alias the set reaches instead of refusing it, and
    writes the expansions with the set (config-aliases 7.7, 8.1, 8.2, 8.6).

    Raises the refusal of any pure step (exit 3 or 5) before any file is written (an expansion's
    refusal names PATH); a :class:`WriteRefusedError` naming the failed file and PATH when a file
    cannot be written (the already-written files restored, nothing recorded); the store's
    :class:`ConfigLoadError` when the changes cannot be recorded (every written file restored).
    """
    document = theatre.document
    _refuse_remote(document, path)
    request = resolve_target(document, path, value, in_include=in_include)
    # Before the no-op check, so a no-op through an alias is refused too (config-aliases 5.3).
    expanded, request, texts = _expanded(
        theatre.config_file, document, request, expand=expand_alias
    )
    if plain_equal(request.current, request.intended, ordered=False):
        if not texts:
            return SetOutcome(written=(), changes=())
        # Expanded but unchanged: the expansion alone is written, with no event (8.6, 10.4).
        texts_after = texts
    else:
        plan = plan_set(expanded, request)
        texts_after = texts | {patch.file.key: patch.new_text for patch in plan.patches}
        # A change written into a file included at several places appears at each (4.8, 11.2).
        request = replace(request, intended=plan.intended)
    patches = _final_patches(document, texts_after)
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


def _refuse_remote(document: ConfigDocument, path: ConfigPath) -> None:
    """Refuse (exit 3, before anything is planned) a set on a configuration with a remote source,
    naming the root file, PATH and the first remote include's URL."""
    for file in document.files:
        if file.url is not None:
            raise WriteRefusedError(
                REMOTE_SETS_REFUSED.format(url=file.url),
                file=document.root_file.path,
                path=str(path),
            )


def _expanded(
    config_file: Path, document: ConfigDocument, request: SetRequest, *, expand: bool
) -> tuple[ConfigDocument, SetRequest, dict[Path | str, str]]:
    """Run the guard and, with ``expand``, expand every alias it meets, one per round.

    Returns the expanded document, the request resolved on it and the expanded texts by source
    identity (``SourceFile.key``; empty when nothing was expanded). Each round expands the first
    alias the guard meets, reloads the configuration from the original texts overridden by the
    expanded ones and resolves the request again, so nested aliases are expanded outermost first
    (8.1, 8.2). An expander
    refusal is re-raised naming PATH, keeping its problem and file (8.4, 8.5, 9.2). A round that
    changes the configuration (:data:`EXPANSION_CHANGES_VALUES`, 7.5, 10.2) or leaves its alias
    place an alias (:data:`EXPANSION_STUCK`) is refused (exit 3) naming the alias's file and PATH.
    """
    original: dict[Path | str, str] = {file.key: file.text for file in document.files}
    path, current = request.path, request.current
    texts: dict[Path | str, str] = {}
    expanded_at: set[ConfigPath] = set()
    sites = alias_sites(document, request, expand=expand)
    while sites:
        site = sites[0]
        alias = site.place.alias
        if alias is None:  # pragma: no cover - the guard returns alias places only
            raise ValueError("an alias site is an alias place")
        # Each round turns one alias place into a copy, so a place met again means no progress.
        if site.path in expanded_at:
            raise _alias_refusal(EXPANSION_STUCK, alias, path)
        expanded_at.add(site.path)
        try:
            texts.update(expander.expand_alias(document, site))
        except WriteRefusedError as error:
            raise WriteRefusedError(error.problem, file=error.file, path=str(path)) from error
        document = load_config(config_file, original | texts)
        request = resolve_target(document, path, request.value, in_include=request.in_include)
        # An expansion changes no value: an alias in the copy may bind to a redefined anchor.
        if not plain_equal(request.current, current, ordered=True):
            raise _alias_refusal(EXPANSION_CHANGES_VALUES, alias, path)
        sites = alias_sites(document, request, expand=expand)
    return document, request, texts


def _alias_refusal(problem: str, alias: AliasRef, path: ConfigPath) -> WriteRefusedError:
    """The refusal (exit 3) of ``problem`` about ``alias``, naming its file and PATH."""
    return WriteRefusedError(
        problem.format(name=alias.name), file=alias.token.file.path, path=str(path)
    )


def _final_patches(document: ConfigDocument, texts: dict[Path | str, str]) -> tuple[FilePatch, ...]:
    """One patch per file of ``document`` whose final text differs from its text, in
    ``document.files`` order, each on the original :class:`SourceFile` (so a restore writes the
    original text back)."""
    files = {file.key for file in document.files}
    unknown = set(texts) - files
    if unknown:  # pragma: no cover - expansions and plans only patch loaded files
        raise ValueError(f"patches for files not in the configuration: {sorted(map(str, unknown))}")
    return tuple(
        FilePatch(file, texts[file.key])
        for file in document.files
        if file.key in texts and texts[file.key] != file.text
    )


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
