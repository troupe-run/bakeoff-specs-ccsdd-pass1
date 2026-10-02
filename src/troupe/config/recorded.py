"""The recorded configuration: the history folded into its live leaves (2.1, 2.2, 2.3, 2.6).

Starting from the import events, every later ``set`` event gives its leaf the value ``new`` and
every ``remove`` event deletes its leaf, in ``seq`` order. Each live leaf also carries the ``seq``
of the event that most recently created it (its ``import``, or a ``set`` recorded while the leaf
did not exist), which orders the ``remove`` events of a reconcile (2.6).

Whether a ``set`` creates a leaf is decided by the path's presence in the fold, never by ``old``
being null, because a leaf can exist with the value null.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from troupe.config.history import HistoryRecord
from troupe.config.scalars import ScalarValue


@dataclass(frozen=True)
class RecordedLeaf:
    """One live leaf of the recorded configuration."""

    path: str  # dotted, as stored in the history
    value: ScalarValue
    created_seq: int  # seq of the event that most recently created this leaf


def recorded_leaves(records: Sequence[HistoryRecord]) -> tuple[RecordedLeaf, ...]:
    """Fold ``records`` in ``seq`` order; return the live leaves ordered by ``created_seq``."""
    live: dict[str, RecordedLeaf] = {}
    for record in sorted(records, key=lambda record: record.seq):
        if record.op == "remove":
            live.pop(record.path, None)
            continue
        current = live.get(record.path)
        # An import always creates; a set creates only when its path is absent from the fold.
        if record.op == "import" or current is None:
            created_seq = record.seq
        else:
            created_seq = current.created_seq
        live[record.path] = RecordedLeaf(record.path, record.new, created_seq)
    return tuple(sorted(live.values(), key=lambda leaf: leaf.created_seq))
