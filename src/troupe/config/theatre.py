"""The theatre session: use cases shared by every subcommand (6.1-6.9, 7.4, 7.9, 8.3-8.5).

A command opens its theatre with ``with open_theatre(directory) as theatre:``. Opening follows a
fixed order: locate the configuration file, take the theatre lock on its directory, load it from
disk, open the history store, then import the configuration when it was never imported, or
otherwise reconcile the history with the files unless the caller opted out (config-file-sync 1.1,
1.3, 1.4). Nothing is created when locating or loading fails (7.4), and a load failure records
nothing and leaves the lock free (config-file-sync 1.5). Leaving the ``with`` block closes the
history store and then releases the lock, whether the block succeeded or failed (7.9;
config-file-sync 7.3). The lock is held from load to close, so the events of two commands on one
theatre never interleave (config-file-sync 1.7, 6.1-6.4).

Files are the truth (product-owner decision A): ``theatre.document`` is the configuration as read
from disk by this command, and :func:`get_value` (``get``) and :func:`node_at` (``show``) read
only that document (config-get-show 6.4). Step 2 supersedes the step 1 rule that hand edits are
never recorded (6.9, 7.3): every command except ``history`` reconciles first, so a hand edit is
recorded as ``set``/``remove`` events before the command's own work; ``history`` opens with
``reconcile=False`` and only imports a never-imported theatre.
"""

from collections.abc import Callable
from contextlib import ExitStack, suppress
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Self

from troupe.config import reconcile as reconciler
from troupe.config.errors import ConfigLoadError, WrongNodeKindError
from troupe.config.history import HistoryRecord
from troupe.config.loader import load_config
from troupe.config.locate import find_config_file
from troupe.config.lock import TheatreLock
from troupe.config.model import ConfigDocument, ConfigMapping, ConfigNode, ConfigScalar
from troupe.config.paths import ConfigPath, path_has_prefix
from troupe.config.resolve import flatten_leaves, resolve_path
from troupe.config.scalars import ScalarValue
from troupe.config.store import HistoryStore


@dataclass(frozen=True)
class Theatre:
    """An opened theatre: its configuration as on disk, its history store and its held lock."""

    directory: Path
    config_file: Path
    document: ConfigDocument  # the files as on disk when the command opened the theatre
    history: HistoryStore
    lock: TheatreLock

    def close(self) -> None:
        """Close the history store, then release the lock; calling it again does nothing.

        The lock is released even when closing the store fails.
        """
        try:
            self.history.close()
        finally:
            self.lock.release()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the store; never suppress the block's error, never replace it by a close error.

        After a successful block, a failure to close is raised as a :class:`ConfigLoadError`.
        """
        if exc_type is None:
            self.close()
            return
        with suppress(ConfigLoadError):  # the block's own error is the one reported
            self.close()


def open_theatre(directory: Path, *, reconcile: bool = True) -> Theatre:
    """Open the theatre in ``directory``: locate, lock, load, open the store, import or reconcile.

    A never-imported theatre is imported and records no reconcile events; an imported one is
    reconciled with the files unless ``reconcile`` is false (``history``). The reconcile events
    are committed before this returns, so they stay when the command then fails.

    Raises :class:`ConfigLoadError` when the configuration cannot be located or loaded (no history
    state is created then) or when the lock or the history store fails. On any failure everything
    already acquired is released in reverse order (store, then lock) and the original error is
    re-raised; a failure to release never replaces it. Raises :class:`RuntimeError` when this
    process already holds the theatre open.
    """
    config_file = find_config_file(directory)
    with ExitStack() as acquired:
        lock = TheatreLock.acquire(config_file.parent)
        acquired.callback(_quietly, lock.release)
        document = load_config(config_file)
        history = HistoryStore.open(config_file.parent)
        acquired.callback(_quietly, history.close)
        imported_now = history.ensure_imported(flatten_leaves(document.root))
        if reconcile and not imported_now:
            reconciler.reconcile(document, history)
        acquired.pop_all()  # success: the theatre owns the store and the lock now
    return Theatre(
        directory=directory, config_file=config_file, document=document, history=history, lock=lock
    )


def _quietly(release: Callable[[], None]) -> None:
    """Release a resource while an opening failure propagates; never mask that failure."""
    with suppress(Exception):
        release()


def get_value(theatre: Theatre, path: ConfigPath) -> ScalarValue:
    """Return the scalar at ``path`` in the loaded files (never the history); ``get``'s use case.

    Raises :class:`PathNotFoundError` when ``path`` does not exist and :class:`WrongNodeKindError`
    naming ``path`` and the file holding the node when it is a mapping or a list, with a hint to
    print it with ``troupe config show <PATH>`` (6.7, 6.8; config-get-show 1.1-1.3).
    """
    node = resolve_path(theatre.document, path)
    if not isinstance(node, ConfigScalar):
        kind = "a mapping" if isinstance(node, ConfigMapping) else "a list"
        raise WrongNodeKindError(
            f"is {kind}, not a value; print it with `troupe config show {path}`",
            file=node.origin.file.path,
            path=str(path),
        )
    return node.value


def node_at(theatre: Theatre, path: ConfigPath | None) -> ConfigNode:
    """Return the node at ``path`` in the loaded files, or the root mapping when ``path`` is None.

    The node may be a mapping, a list or a scalar. Raises :class:`PathNotFoundError` naming the
    deepest existing node's file when ``path`` does not exist (config-get-show 2.3, 2.4, 6.4).
    """
    if path is None:
        return theatre.document.root
    return resolve_path(theatre.document, path)


def list_history(theatre: Theatre, prefix: ConfigPath | None) -> tuple[HistoryRecord, ...]:
    """Return the history records, oldest first, optionally only those at or below ``prefix``.

    Filtering is textual (``prefix`` itself and paths starting with ``prefix.``) and keeps each
    record's original ``seq`` (8.3, 8.4).
    """
    records = theatre.history.records()
    if prefix is None:
        return records
    text = str(prefix)
    return tuple(record for record in records if path_has_prefix(record.path, text))
