"""Event-sourced configuration history: aggregate, events and records (7.1, 7.3, 7.5, 9.1, 9.2).

One :class:`ConfigHistory` aggregate with the fixed identity :data:`HISTORY_AGGREGATE_ID` records
the history of one theatre. Its events are totally ordered by version: the created event
:class:`ConfigHistory.HistoryStarted` is version 1 and marks the theatre as imported (it is not a
history line), and every later event is one history line whose ``seq`` is its version minus one,
so ``seq`` starts at 1 and has no gaps.

The aggregate holds no derived leaf state: the files are the truth for current values, and the
history is change history only. Persistence lives in ``troupe.config.store``, which projects stored
events through :func:`to_history_record`, the single home of the event-to-record mapping.
"""

from dataclasses import dataclass
from typing import Final, Literal, TypeAlias
from uuid import NAMESPACE_URL, UUID, uuid5

from eventsourcing.domain import Aggregate, DomainEventProtocol, event

from troupe.config.paths import ConfigPath
from troupe.config.scalars import ScalarValue

HISTORY_AGGREGATE_ID: Final[UUID] = uuid5(NAMESPACE_URL, "troupe:config-history")
"""The fixed identity of the one history aggregate in every theatre's store."""


class ConfigHistory(Aggregate):
    """The history of one theatre; event class names are the single home of event names."""

    @classmethod
    def create_id(cls) -> UUID:
        # The library declares ``create_id(*_: Any, **__: Any)`` and passes it only the keyword
        # arguments named in this signature (none here); the fixed ID makes the history unique.
        return HISTORY_AGGREGATE_ID

    class HistoryStarted(Aggregate.Created):
        """The theatre's configuration was imported (version 1); not a history line."""

    class LeafImported(Aggregate.Event):
        """A leaf found at import time: op ``import``, ``old`` null."""

        path: str
        new: ScalarValue

    class LeafValueSet(Aggregate.Event):
        """A leaf changed or created by ``set``: op ``set`` (``old`` null when created)."""

        path: str
        old: ScalarValue
        new: ScalarValue

    class LeafRemoved(Aggregate.Event):
        """A leaf that no longer exists after ``set``: op ``remove``, ``new`` null."""

        path: str
        old: ScalarValue

    @event(HistoryStarted)
    def __init__(self) -> None:
        pass

    @event(LeafImported)
    def import_leaf(self, path: str, new: ScalarValue) -> None:
        """Record that the leaf at ``path`` held ``new`` when the theatre was imported."""

    @event(LeafValueSet)
    def set_leaf(self, path: str, old: ScalarValue, new: ScalarValue) -> None:
        """Record that ``set`` changed (or created, ``old`` None) the leaf at ``path``."""

    @event(LeafRemoved)
    def remove_leaf(self, path: str, old: ScalarValue) -> None:
        """Record that the leaf at ``path``, which held ``old``, no longer exists."""


@dataclass(frozen=True)
class SetChange:
    """A leaf changed or created by one ``set`` invocation (writer-facing, 9.1)."""

    path: ConfigPath
    old: ScalarValue  # None when the leaf is newly created
    new: ScalarValue


@dataclass(frozen=True)
class RemoveChange:
    """A leaf that no longer exists after one ``set`` invocation (writer-facing, 9.2)."""

    path: ConfigPath
    old: ScalarValue


# Runtime unions (not ``type`` statements) so callers can also use them with ``isinstance``.
LeafChange: TypeAlias = SetChange | RemoveChange  # noqa: UP040
HistoryOp: TypeAlias = Literal["import", "set", "remove"]  # noqa: UP040


@dataclass(frozen=True)
class HistoryRecord:
    """One history line: ``seq`` is the event version minus one, so the first record is 1."""

    seq: int
    op: HistoryOp
    path: str
    old: ScalarValue
    new: ScalarValue


def to_history_record(event: DomainEventProtocol[UUID]) -> HistoryRecord | None:
    """Map a history event to its history record; ``None`` for :class:`HistoryStarted`.

    The parameter is the library's event protocol (what ``Aggregate.collect_events`` and the
    event store return) rather than ``ConfigHistory.Event``, so callers need no casts; any event
    that is not one of the four ``ConfigHistory`` events raises :class:`TypeError`.
    """
    seq = event.originator_version - 1
    if isinstance(event, ConfigHistory.LeafImported):
        return HistoryRecord(seq=seq, op="import", path=event.path, old=None, new=event.new)
    if isinstance(event, ConfigHistory.LeafValueSet):
        return HistoryRecord(seq=seq, op="set", path=event.path, old=event.old, new=event.new)
    if isinstance(event, ConfigHistory.LeafRemoved):
        return HistoryRecord(seq=seq, op="remove", path=event.path, old=event.old, new=None)
    if isinstance(event, ConfigHistory.HistoryStarted):
        return None
    raise TypeError(f"unknown configuration history event: {type(event).__name__}")
