"""Lineage of recorded changes: who, how, when, why and with what else.

config-lineage 1.2-1.6, 3.4, 4.3, 5.1, 5.3.

A caller describes a batch with a ``ChangeContext`` (actor, via, reason). The store stamps it once
per batch into a ``Lineage``, adding the current UTC time and a fresh random change id, so every
event of one batch shares ``at`` and ``change`` and no two batches share a ``change``.

Imports and file edits have no known author: their contexts carry ``UNKNOWN_ACTOR`` and no reason
(3.4). Only a CLI command's context carries a declared actor and, for ``set``, a reason.

This module is core: standard library only, and it never reads the environment. The CLI resolves
the command's actor and passes it in.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, Literal
from uuid import uuid4

type ActorKind = Literal["human", "agent", "unknown"]
type Via = Literal["import", "cli", "file"]


@dataclass(frozen=True)
class Actor:
    """Who made a change: a human, an agent, or unknown; ``id`` is null when it is not known."""

    kind: ActorKind
    id: str | None


UNKNOWN_ACTOR: Final = Actor(kind="unknown", id=None)


@dataclass(frozen=True)
class ChangeContext:
    """Who made a batch of changes, how, and why (a reason only for cli)."""

    actor: Actor
    via: Via
    reason: str | None = None


IMPORT_CONTEXT: Final = ChangeContext(actor=UNKNOWN_ACTOR, via="import")
FILE_CONTEXT: Final = ChangeContext(actor=UNKNOWN_ACTOR, via="file")


def cli_context(actor: Actor, reason: str | None) -> ChangeContext:
    """The context of a CLI command's own batch: the command's actor and its reason, if any."""
    return ChangeContext(actor=actor, via="cli", reason=reason)


@dataclass(frozen=True)
class Lineage:
    """The lineage of every event of one batch."""

    actor: Actor
    via: Via
    at: str  # formatted by format_utc
    reason: str | None
    change: str


def stamp(context: ChangeContext) -> Lineage:
    """The lineage of one batch recorded now: current UTC time, fresh change id (4.3, 5.1)."""
    return Lineage(
        actor=context.actor,
        via=context.via,
        at=format_utc(datetime.now(UTC)),
        reason=context.reason,
        change=str(uuid4()),
    )


def format_utc(moment: datetime) -> str:
    """``YYYY-MM-DDTHH:MM:SS.ffffffZ`` of an aware ``moment`` converted to UTC (1.4, 5.1, 5.3).

    A naive datetime has no known zone, so it is refused with ``ValueError``.
    """
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError(f"cannot format a naive datetime as UTC: {moment.isoformat()}")
    utc = moment.astimezone(UTC).replace(tzinfo=None)
    return utc.isoformat(timespec="microseconds") + "Z"
