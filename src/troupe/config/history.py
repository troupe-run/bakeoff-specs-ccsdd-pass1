"""Event-sourced configuration history: aggregate, events and records (7.1, 7.3, 7.5, 9.1, 9.2).

One :class:`ConfigHistory` aggregate with the fixed identity :data:`HISTORY_AGGREGATE_ID` records
the history of one theatre. Its events are totally ordered by version: the created event
:class:`ConfigHistory.HistoryStarted` is version 1 and marks the theatre as imported (it is not a
history line), and every later event is one history line whose ``seq`` is its version minus one,
so ``seq`` starts at 1 and has no gaps.

The aggregate holds no derived leaf state: the files are the truth for current values, and the
history records their changes. Set and remove events come from ``set`` and, since step 2, from
reconcile, which records hand edits before every command except ``history`` (config-file-sync 1.1,
1.3, 2.1-2.3). Persistence lives in ``troupe.config.store``, which projects stored
events through :func:`to_history_record`, the single home of the event-to-record mapping.

Since step 4 every history event also stores its lineage as flat fields (``actor_kind``,
``actor_id``, ``via``, ``at``, ``reason``, ``change``; config-lineage 7.1): the store stamps each
recorded batch once and passes the same fields to every event of the batch. A record carries them
back as a :class:`~troupe.config.lineage.Lineage`; an unknown stored actor kind or via is a
``ValueError``, which the store reports as an unreadable store.

The three lineage-carrying events have schema version 2 (``class_version``). Events stored before
step 4 (version 1, no lineage fields) are upcast on read by ``upcast_v1_v2`` with a legacy lineage
(config-lineage 7.3, research Decisions 4 and 5): actor unknown, reason null, ``at`` from the time
the event was stored, via ``import`` with the one change ``legacy-import`` for imports, and via
``cli`` with its own change ``legacy-<seq>`` for every set and remove. Nothing is rewritten in
place, and the legacy lineage is deterministic, so every read gives the same records (7.2).
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Final, Literal, TypeAlias, get_args
from uuid import NAMESPACE_URL, UUID, uuid5

from eventsourcing.domain import Aggregate, DomainEventProtocol, event

from troupe.config.lineage import UNKNOWN_ACTOR, Actor, ActorKind, Lineage, Via, format_utc
from troupe.config.paths import ConfigPath
from troupe.config.scalars import ScalarValue

LEGACY_IMPORT_CHANGE: Final = "legacy-import"
"""The change id shared by all import events stored before step 4 (a theatre has one import)."""


def _upcast_legacy_lineage(state: dict[str, object], *, imported: bool) -> None:
    """Add the legacy lineage fields to the decoded ``state`` of a pre-step-4 event (7.3).

    eventsourcing runs upcasters on the decoded state, after it set ``originator_version``, so
    ``timestamp`` is an aware ``datetime`` and ``originator_version`` an ``int``.
    """
    timestamp = state.get("timestamp")
    version = state.get("originator_version")
    if not isinstance(timestamp, datetime) or not isinstance(version, int):
        raise ValueError("a stored history event has no valid timestamp or version")
    state["actor_kind"] = UNKNOWN_ACTOR.kind
    state["actor_id"] = UNKNOWN_ACTOR.id
    state["via"] = "import" if imported else "cli"
    state["at"] = format_utc(timestamp)
    state["reason"] = None
    state["change"] = LEGACY_IMPORT_CHANGE if imported else f"legacy-{version - 1}"


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

        actor_kind: str
        actor_id: str | None
        via: str
        at: str
        reason: str | None
        change: str

        class_version = 2

        @staticmethod
        def upcast_v1_v2(state: dict[str, object]) -> None:
            """Give an event stored before step 4 its legacy lineage (7.3)."""
            _upcast_legacy_lineage(state, imported=True)

    class LeafValueSet(Aggregate.Event):
        """A leaf changed or created by ``set`` or reconcile: op ``set``, ``old`` null if new."""

        path: str
        old: ScalarValue
        new: ScalarValue

        actor_kind: str
        actor_id: str | None
        via: str
        at: str
        reason: str | None
        change: str

        class_version = 2

        @staticmethod
        def upcast_v1_v2(state: dict[str, object]) -> None:
            """Give an event stored before step 4 its legacy lineage (7.3)."""
            _upcast_legacy_lineage(state, imported=False)

    class LeafRemoved(Aggregate.Event):
        """A leaf gone after ``set`` or found gone by reconcile: op ``remove``, ``new`` null."""

        path: str
        old: ScalarValue

        actor_kind: str
        actor_id: str | None
        via: str
        at: str
        reason: str | None
        change: str

        class_version = 2

        @staticmethod
        def upcast_v1_v2(state: dict[str, object]) -> None:
            """Give an event stored before step 4 its legacy lineage (7.3)."""
            _upcast_legacy_lineage(state, imported=False)

    @event(HistoryStarted)
    def __init__(self) -> None:
        pass

    @event(LeafImported)
    def import_leaf(
        self,
        path: str,
        new: ScalarValue,
        *,
        actor_kind: str,
        actor_id: str | None,
        via: str,
        at: str,
        reason: str | None,
        change: str,
    ) -> None:
        """Record that the leaf at ``path`` held ``new`` when the theatre was imported."""

    @event(LeafValueSet)
    def set_leaf(
        self,
        path: str,
        old: ScalarValue,
        new: ScalarValue,
        *,
        actor_kind: str,
        actor_id: str | None,
        via: str,
        at: str,
        reason: str | None,
        change: str,
    ) -> None:
        """Record that ``set`` changed (or created, ``old`` None) the leaf at ``path``."""

    @event(LeafRemoved)
    def remove_leaf(
        self,
        path: str,
        old: ScalarValue,
        *,
        actor_kind: str,
        actor_id: str | None,
        via: str,
        at: str,
        reason: str | None,
        change: str,
    ) -> None:
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
    lineage: Lineage


_ACTOR_KINDS: Final[tuple[ActorKind, ...]] = get_args(ActorKind.__value__)
_VIAS: Final[tuple[Via, ...]] = get_args(Via.__value__)

type _LineageEvent = (
    ConfigHistory.LeafImported | ConfigHistory.LeafValueSet | ConfigHistory.LeafRemoved
)


def _lineage(event: _LineageEvent) -> Lineage:
    """Rebuild the lineage stored on ``event``; an unknown actor kind or via is a ``ValueError``."""
    kind = next((known for known in _ACTOR_KINDS if known == event.actor_kind), None)
    if kind is None:
        raise ValueError(f"unknown actor kind in a stored history event: {event.actor_kind!r}")
    via = next((known for known in _VIAS if known == event.via), None)
    if via is None:
        raise ValueError(f"unknown via in a stored history event: {event.via!r}")
    return Lineage(
        actor=Actor(kind=kind, id=event.actor_id),
        via=via,
        at=event.at,
        reason=event.reason,
        change=event.change,
    )


def to_history_record(event: DomainEventProtocol[UUID]) -> HistoryRecord | None:
    """Map a history event to its history record; ``None`` for :class:`HistoryStarted`.

    An unknown stored actor kind or via raises :class:`ValueError` (config-lineage 7.1).
    The parameter is the library's event protocol (what ``Aggregate.collect_events`` and the
    event store return) rather than ``ConfigHistory.Event``, so callers need no casts; any event
    that is not one of the four ``ConfigHistory`` events raises :class:`TypeError`.
    """
    seq = event.originator_version - 1
    if isinstance(event, ConfigHistory.LeafImported):
        lineage = _lineage(event)
        return HistoryRecord(seq, "import", event.path, None, event.new, lineage)
    if isinstance(event, ConfigHistory.LeafValueSet):
        lineage = _lineage(event)
        return HistoryRecord(seq, "set", event.path, event.old, event.new, lineage)
    if isinstance(event, ConfigHistory.LeafRemoved):
        lineage = _lineage(event)
        return HistoryRecord(seq, "remove", event.path, event.old, None, lineage)
    if isinstance(event, ConfigHistory.HistoryStarted):
        return None
    raise TypeError(f"unknown configuration history event: {type(event).__name__}")
