"""LeafDiff: leaves before (as on disk) and after a set -> history changes in recording order."""

from troupe.config.history import LeafChange, RemoveChange, SetChange
from troupe.config.model import ConfigDocument
from troupe.config.resolve import flatten_leaves
from troupe.config.writeback.compare import scalar_equal


def diff_leaves(before: ConfigDocument, after: ConfigDocument) -> tuple[LeafChange, ...]:
    """Return the history changes that turn ``before`` into ``after`` (15.1-15.5, 15.7).

    ``before`` is the configuration loaded from disk for this command (never the history), so every
    ``old`` is the value currently in the files. Leaves are compared by dotted path and typed value
    (:func:`scalar_equal`). The result lists one :class:`SetChange` per leaf of ``after`` that is
    new (``old`` ``None``) or changed, in ``after`` document order, then one :class:`RemoveChange`
    per leaf of ``before`` whose path is gone, in ``before`` document order. Empty collections have
    no leaves, so ``{}`` -> ``[]`` yields nothing. Since ``after`` is the verified read-back, the
    leaves of a newly written YAML include are set changes and a text include is one string leaf.
    """
    old_leaves = {str(leaf.path): leaf for leaf in flatten_leaves(before.root)}
    new_leaves = flatten_leaves(after.root)
    new_paths = {str(leaf.path) for leaf in new_leaves}
    changes: list[LeafChange] = []
    for leaf in new_leaves:
        old = old_leaves.get(str(leaf.path))
        if old is None:
            changes.append(SetChange(leaf.path, None, leaf.value))
        elif not scalar_equal(old.value, leaf.value):
            changes.append(SetChange(leaf.path, old.value, leaf.value))
    changes.extend(
        RemoveChange(leaf.path, leaf.value)
        for key, leaf in old_leaves.items()
        if key not in new_paths
    )
    return tuple(changes)
