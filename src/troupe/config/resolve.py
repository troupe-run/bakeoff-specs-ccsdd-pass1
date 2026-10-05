"""Walk and resolve dotted PATHs; flatten the configuration into leaves (5.1-5.5, 6.8, 7.2).

Includes are transparent: included nodes are embedded in the tree, so a path passes through an
included YAML file exactly as if its content were written in place (5.4). A text include is a
scalar; no segment can be applied to it (5.5).

Since step 5 a mapping-form include is embedded as its merged value (config-include-overrides 1.2,
1.5): paths and leaves are those of the merge, never a ``$ref`` segment, so ``get``, ``show``,
import and reconcile all see the merged configuration (3.1, 3.3, 8.3).
"""

from dataclasses import dataclass
from enum import Enum

from troupe.config.errors import PathNotFoundError
from troupe.config.model import (
    ConfigDocument,
    ConfigMapping,
    ConfigNode,
    ConfigScalar,
    ConfigSequence,
)
from troupe.config.paths import ROOT, ConfigPath, is_index_segment
from troupe.config.scalars import ScalarValue


class WalkStop(Enum):
    """Why a path walk stopped."""

    COMPLETE = "complete"  # every segment consumed
    MISSING_KEY = "missing-key"  # mapping has no such key
    INDEX_OUT_OF_RANGE = "index-out-of-range"
    NOT_AN_INDEX = "not-an-index"  # non-digit segment applied to a list
    SCALAR = "scalar"  # segment applied to a scalar (incl. text include)


@dataclass(frozen=True)
class PathWalk:
    """The outcome of walking a path from the root."""

    path: ConfigPath
    nodes: tuple[ConfigNode, ...]  # root first, then one node per consumed segment
    consumed: int  # number of segments resolved
    stop: WalkStop

    @property
    def deepest(self) -> ConfigNode:
        """The deepest node reached (the root when no segment was resolved)."""
        return self.nodes[-1]


@dataclass(frozen=True)
class Leaf:
    """One scalar of the configuration at its full dotted path."""

    path: ConfigPath
    value: ScalarValue
    node: ConfigScalar


def walk_path(root: ConfigMapping, path: ConfigPath) -> PathWalk:
    """Apply ``path`` segment by segment from ``root`` and report how far it got and why."""
    nodes: list[ConfigNode] = [root]
    for segment in path.segments:
        node = nodes[-1]
        stop = _step(node, segment)
        if isinstance(stop, WalkStop):
            return PathWalk(path=path, nodes=tuple(nodes), consumed=len(nodes) - 1, stop=stop)
        nodes.append(stop)
    return PathWalk(path=path, nodes=tuple(nodes), consumed=len(nodes) - 1, stop=WalkStop.COMPLETE)


def _step(node: ConfigNode, segment: str) -> ConfigNode | WalkStop:
    """Return the child of ``node`` selected by ``segment``, or why there is none."""
    if isinstance(node, ConfigMapping):
        # A digit-only segment is looked up as a key (digit-only keys are out of contract).
        entry = node.get(segment)
        return WalkStop.MISSING_KEY if entry is None else entry.value
    if isinstance(node, ConfigSequence):
        if not is_index_segment(segment):
            return WalkStop.NOT_AN_INDEX
        index = int(segment)
        if index >= len(node.items):
            return WalkStop.INDEX_OUT_OF_RANGE
        return node.items[index]
    return WalkStop.SCALAR


_PROBLEMS = {
    WalkStop.MISSING_KEY: "not found: no such key",
    WalkStop.INDEX_OUT_OF_RANGE: "not found: list index out of range",
    WalkStop.NOT_AN_INDEX: "not found: a list can only be indexed by a number",
    WalkStop.SCALAR: "not found: a scalar value has no children",
}


def resolve_path(document: ConfigDocument, path: ConfigPath) -> ConfigNode:
    """Return the node at ``path``, or raise :class:`PathNotFoundError`.

    The error names ``path`` and the file holding the deepest part of ``path`` that exists (6.8).
    """
    walk = walk_path(document.root, path)
    if walk.stop is not WalkStop.COMPLETE:
        segment = path.segments[walk.consumed]
        raise PathNotFoundError(
            f"{_PROBLEMS[walk.stop]} (at segment {segment!r})",
            file=walk.deepest.origin.file.path,
            path=str(path),
        )
    return walk.deepest


def flatten_leaves(root: ConfigMapping) -> tuple[Leaf, ...]:
    """Return every scalar of the configuration, depth-first in document order (7.2).

    Text includes and nulls are leaves; list items are addressed by index; empty mappings and
    empty lists contribute nothing.
    """
    leaves: list[Leaf] = []
    _collect(root, ROOT, leaves)
    return tuple(leaves)


def _collect(node: ConfigNode, path: ConfigPath, leaves: list[Leaf]) -> None:
    if isinstance(node, ConfigScalar):
        leaves.append(Leaf(path=path, value=node.value, node=node))
    elif isinstance(node, ConfigMapping):
        for entry in node.entries:
            _collect(entry.value, path.child(entry.key), leaves)
    else:
        for index, item in enumerate(node.items):
            _collect(item, path.child(index), leaves)
