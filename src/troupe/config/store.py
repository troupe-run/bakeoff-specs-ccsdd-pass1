"""The persisted configuration history of one theatre (7.1, 7.3, 7.5-7.9, 8.1, 8.2, 9.1-9.4).

:class:`HistoryStore` wraps an eventsourcing application whose events live in one SQLite file,
``<theatre>/.troupe/history.sqlite`` (plus SQLite's ``-wal``/``-shm`` side files while it is open).
The application is configured only from the explicit settings below; the process environment is
never read, so it cannot redirect the store, switch its persistence module or compress/encrypt it.

Every write is one ``Application.save`` call, i.e. one SQLite transaction, so a failed import or a
failed batch of changes leaves no partial history. Every eventsourcing/SQLite failure is reported as
a :class:`ConfigLoadError` naming the store file. :meth:`HistoryStore.close` closes every SQLite
connection, after which SQLite checkpoints the write-ahead log and removes the side files.

Since step 4 the import and every batch of changes take the caller's
:class:`~troupe.config.lineage.ChangeContext`. A non-empty write is stamped once, right before its
save, so all its events share one ``at``, and one ``change`` that no other write has; an empty batch
records nothing and is not stamped (config-lineage 4.1, 4.3, 4.4, 5.2). Records read back carry
the stored lineage, the same in every later process (config-lineage 7.1).
"""

import sqlite3
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Self
from uuid import UUID

from eventsourcing.application import Application
from eventsourcing.domain import EventSourcingError
from eventsourcing.utils import Environment, TopicError

from troupe.config.constants import HISTORY_STORE_NAME
from troupe.config.errors import ConfigLoadError
from troupe.config.history import (
    HISTORY_AGGREGATE_ID,
    ConfigHistory,
    HistoryRecord,
    LeafChange,
    RemoveChange,
    to_history_record,
)
from troupe.config.lineage import ChangeContext, stamp
from troupe.config.resolve import Leaf

# Failures of the store itself: SQLite errors (some escape eventsourcing unwrapped, e.g. when the
# file is not a database), eventsourcing's persistence/topic errors, file-system errors, and
# decoding errors from corrupt stored state (``json.JSONDecodeError`` is a ``ValueError``).
_STORE_FAILURES = (sqlite3.Error, EventSourcingError, TopicError, OSError, ValueError)


class _HistoryApplication(Application[UUID]):
    """The eventsourcing application holding the single :class:`ConfigHistory` aggregate."""

    name = "TroupeHistory"

    def construct_env(self, name: str, env: Mapping[str, str] | None = None) -> Environment:
        # Unlike the base class, never merge ``os.environ``: only the explicit settings apply.
        return Environment(name, dict(env or {}))


def _settings(location: Path) -> dict[str, str]:
    return {
        "PERSISTENCE_MODULE": "eventsourcing.sqlite",
        "SQLITE_DBNAME": str(location),
        "CREATE_TABLE": "y",
        "COMPRESSOR_TOPIC": "",
        "CIPHER_TOPIC": "",
    }


class HistoryStore:
    """The history of one theatre, persisted at ``<theatre>/.troupe/history.sqlite``."""

    def __init__(self, application: _HistoryApplication, location: Path) -> None:
        self._application: _HistoryApplication | None = application
        self._location = location

    @classmethod
    def open(cls, config_dir: Path) -> Self:
        """Open (creating if needed) the store in ``config_dir``, which must already exist.

        The store never creates directories; a missing ``config_dir`` is a load error.
        """
        location = config_dir / HISTORY_STORE_NAME
        if not config_dir.is_dir():
            raise ConfigLoadError(
                f"cannot open the history store: {config_dir} is not a directory", file=location
            )
        try:
            application = _HistoryApplication(env=_settings(location))
        except _STORE_FAILURES as error:
            raise _store_error("cannot open the history store", location, error) from error
        return cls(application, location)

    @property
    def location(self) -> Path:
        """The store file, ``<theatre>/.troupe/history.sqlite``."""
        return self._location

    def is_imported(self) -> bool:
        """Whether the theatre's configuration has been imported (the history aggregate exists)."""
        return self._run(
            "cannot read the history store",
            lambda app: _first(app.events.get(HISTORY_AGGREGATE_ID, limit=1)) is not None,
        )

    def ensure_imported(self, leaves: Sequence[Leaf], context: ChangeContext) -> bool:
        """Import ``leaves`` unless the history exists; return whether an import happened.

        An import is the history-start event plus one leaf-imported event per leaf, in the given
        order, saved in one transaction. The import is stamped once with ``context``, so every
        leaf-imported event shares one lineage (config-lineage 3.1, 4.1, 5.2). An empty sequence
        still counts as an import (it records only the history-start event, so it is not stamped).
        When the history already exists nothing is recorded: importing is import-only. Reconciling
        the history with the files is a separate step (``troupe.config.reconcile``, config-file-sync
        Requirement 2) that the theatre runs after this returns ``False``.
        """
        if self.is_imported():
            return False
        history = ConfigHistory()
        if leaves:
            lineage = stamp(context)
            for leaf in leaves:
                history.import_leaf(
                    str(leaf.path),
                    leaf.value,
                    actor_kind=lineage.actor.kind,
                    actor_id=lineage.actor.id,
                    via=lineage.via,
                    at=lineage.at,
                    reason=lineage.reason,
                    change=lineage.change,
                )
        self._run("cannot import into the history store", lambda app: app.save(history))
        return True

    def record_changes(self, changes: Sequence[LeafChange], context: ChangeContext) -> None:
        """Append the changes reported by one ``set`` invocation or one reconcile (9.1-9.4).

        Precondition: :meth:`is_imported`. One leaf-value-set event per :class:`SetChange` and one
        leaf-removed event per :class:`RemoveChange`, in the given order, saved in one transaction
        (all or none). The batch is stamped once with ``context`` right before the save, so all
        its events share one lineage and no other batch shares its change id (config-lineage 4.1,
        4.2, 5.2). An empty sequence records nothing and is not stamped (config-lineage 4.4).
        Recording before the import, or any store failure, is a :class:`ConfigLoadError` naming
        the store file.
        """
        if not changes:
            return
        if not self.is_imported():
            raise ConfigLoadError(
                "cannot record changes in the history store: the configuration is not imported",
                file=self._location,
            )

        def record(app: _HistoryApplication) -> None:
            history: ConfigHistory = app.repository.get(HISTORY_AGGREGATE_ID)
            lineage = stamp(context)
            for change in changes:
                if isinstance(change, RemoveChange):
                    history.remove_leaf(
                        str(change.path),
                        change.old,
                        actor_kind=lineage.actor.kind,
                        actor_id=lineage.actor.id,
                        via=lineage.via,
                        at=lineage.at,
                        reason=lineage.reason,
                        change=lineage.change,
                    )
                else:
                    history.set_leaf(
                        str(change.path),
                        change.old,
                        change.new,
                        actor_kind=lineage.actor.kind,
                        actor_id=lineage.actor.id,
                        via=lineage.via,
                        at=lineage.at,
                        reason=lineage.reason,
                        change=lineage.change,
                    )
            app.save(history)

        self._run("cannot record changes in the history store", record)

    def records(self) -> tuple[HistoryRecord, ...]:
        """Return the history records, oldest first.

        Events stored before step 4 are read with their legacy lineage (upcast in
        ``troupe.config.history``; config-lineage 7.3); the stored rows are never rewritten.
        """

        def project(app: _HistoryApplication) -> tuple[HistoryRecord, ...]:
            projected = (to_history_record(event) for event in app.events.get(HISTORY_AGGREGATE_ID))
            return tuple(record for record in projected if record is not None)

        return self._run("cannot read the history store", project)

    def close(self) -> None:
        """Close the application and its SQLite connections; calling it again does nothing.

        A failure while closing is reported as a load error, unless another error is already being
        raised, which is never masked.
        """
        application, self._application = self._application, None
        if application is None:
            return
        in_flight = sys.exception() is not None
        try:
            application.close()
        except _STORE_FAILURES as error:
            if not in_flight:
                raise _store_error(
                    "cannot close the history store", self._location, error
                ) from error

    def _run[T](self, action: str, operation: Callable[[_HistoryApplication], T]) -> T:
        if self._application is None:
            raise ConfigLoadError(f"{action}: the history store is closed", file=self._location)
        try:
            return operation(self._application)
        except _STORE_FAILURES as error:
            raise _store_error(action, self._location, error) from error


def _first[T](items: Iterable[T]) -> T | None:
    return next(iter(items), None)


def _store_error(action: str, location: Path, error: BaseException) -> ConfigLoadError:
    return ConfigLoadError(f"{action}: {error}", file=location)
