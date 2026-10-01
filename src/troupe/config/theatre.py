"""The theatre session: use cases shared by every subcommand (6.1-6.9, 7.4, 7.9, 8.3-8.5).

A command opens its theatre with ``with open_theatre(directory) as theatre:``. Opening follows a
fixed order: locate the configuration file, load it from disk, open the history store, import the
configuration once. Nothing is created when locating or loading fails (7.4). Leaving the ``with``
block closes the history store, whether the block succeeded or failed (7.9).

Files are the truth (product-owner decision A): ``theatre.document`` is the configuration as read
from disk by this command, :func:`show_value` reads only that document, and the history is change
history only. A hand edit made since the import is shown and never recorded (6.9, 7.3).
"""

from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Self

from troupe.config.errors import ConfigLoadError, WrongNodeKindError
from troupe.config.history import HistoryRecord
from troupe.config.loader import load_config
from troupe.config.locate import find_config_file
from troupe.config.model import ConfigDocument, ConfigMapping, ConfigScalar
from troupe.config.paths import ConfigPath, path_has_prefix
from troupe.config.resolve import flatten_leaves, resolve_path
from troupe.config.scalars import ScalarValue
from troupe.config.store import HistoryStore


@dataclass(frozen=True)
class Theatre:
    """An opened theatre: its configuration as on disk and its (imported) history store."""

    directory: Path
    config_file: Path
    document: ConfigDocument  # the files as on disk when the command opened the theatre
    history: HistoryStore

    def close(self) -> None:
        """Close the history store; calling it again does nothing."""
        self.history.close()

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


def open_theatre(directory: Path) -> Theatre:
    """Open the theatre in ``directory``: locate, load, open the store, import once.

    Raises :class:`ConfigLoadError` when the configuration cannot be located or loaded (no history
    state is created then) or when the history store fails (the store is closed before the error
    is re-raised).
    """
    config_file = find_config_file(directory)
    document = load_config(config_file)
    history = HistoryStore.open(config_file.parent)
    try:
        history.ensure_imported(flatten_leaves(document.root))
    except BaseException:
        history.close()  # never masks the error being raised
        raise
    return Theatre(directory=directory, config_file=config_file, document=document, history=history)


def show_value(theatre: Theatre, path: ConfigPath) -> ScalarValue:
    """Return the scalar at ``path`` in the loaded files (never the history).

    Raises :class:`PathNotFoundError` when ``path`` does not exist and :class:`WrongNodeKindError`
    naming ``path`` and the file holding the node when it is a mapping or a list (6.7, 6.8).
    """
    node = resolve_path(theatre.document, path)
    if not isinstance(node, ConfigScalar):
        kind = "a mapping" if isinstance(node, ConfigMapping) else "a list"
        raise WrongNodeKindError(
            f"is {kind}, not a value", file=node.origin.file.path, path=str(path)
        )
    return node.value


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
