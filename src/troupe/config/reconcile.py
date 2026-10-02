"""Reconcile: the files against the recorded configuration (2.1-2.8, 3.1-3.3, 5.5).

:func:`reconcile_changes` is pure. Every leaf of the loaded files, in document order, that is
missing from the recorded configuration or whose recorded value differs under the typed comparison
(:func:`~troupe.config.scalars.scalar_equal`) gives a :class:`SetChange`; then every recorded leaf
missing from the files, in creation-``seq`` order, gives a :class:`RemoveChange`. Formatting,
comments, quoting, key order and whether a value is written in place or through an include never
change a leaf, so they give no change (2.4).

:func:`reconcile` records those changes with the store's one-transaction append, so a reconcile is
recorded all or nothing (2.7). It never reads or writes user files.
"""

from collections.abc import Sequence

from troupe.config.constants import PATH_SEPARATOR
from troupe.config.history import LeafChange, RemoveChange, SetChange
from troupe.config.model import ConfigDocument
from troupe.config.paths import ConfigPath
from troupe.config.recorded import RecordedLeaf, recorded_leaves
from troupe.config.resolve import flatten_leaves
from troupe.config.scalars import scalar_equal
from troupe.config.store import HistoryStore


def reconcile_changes(
    document: ConfigDocument, recorded: Sequence[RecordedLeaf]
) -> tuple[LeafChange, ...]:
    """Return the changes that make ``recorded`` equal to the leaves of ``document``.

    ``recorded`` must be ordered by ``created_seq`` (as :func:`recorded_leaves` returns it): sets
    come in the files' document order, then removes in that order (2.6).
    """
    recorded_by_path = {leaf.path: leaf for leaf in recorded}
    changes: list[LeafChange] = []
    file_paths: set[str] = set()
    for leaf in flatten_leaves(document.root):
        path = str(leaf.path)
        file_paths.add(path)
        current = recorded_by_path.get(path)
        if current is None:
            changes.append(SetChange(path=leaf.path, old=None, new=leaf.value))
        elif not scalar_equal(current.value, leaf.value):
            changes.append(SetChange(path=leaf.path, old=current.value, new=leaf.value))
    for gone in sorted(recorded, key=lambda leaf: leaf.created_seq):
        if gone.path not in file_paths:
            changes.append(RemoveChange(path=_stored_path(gone.path), old=gone.value))
    return tuple(changes)


def _stored_path(text: str) -> ConfigPath:
    """Rebuild the path of a recorded leaf from its stored dotted text, so ``str`` round-trips.

    Unlike :meth:`ConfigPath.parse` (which validates a user-given PATH), this accepts any stored
    text, because a leaf may sit under a key that is empty or contains the separator, and removing
    such a leaf must not fail with a usage error.
    """
    return ConfigPath(tuple(text.split(PATH_SEPARATOR)))


def reconcile(document: ConfigDocument, store: HistoryStore) -> tuple[LeafChange, ...]:
    """Record and return the changes between ``document`` and the configuration ``store`` records.

    Precondition: the store is imported and the caller holds the theatre lock. The changes are
    appended in one transaction (nothing for an empty diff); a store failure raises
    :class:`~troupe.config.errors.ConfigLoadError` naming the store.
    """
    changes = reconcile_changes(document, recorded_leaves(store.records()))
    store.record_changes(changes)
    return changes
