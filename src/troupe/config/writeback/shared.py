"""SharedPlaces: the intended configuration of new keys written into a file included at several
places (config-include-overrides 4.8, requirements choice 17, research.md Decision 11).

The override rule writes a new key into the file holding the include reference (choice 5). When
that file is itself included at more than one place, the written mapping (or the include site to
convert) is reached at more than one path of the merged configuration, and the new key appears at
each of them, like any other content of the file. :func:`with_other_places` adds the same new keys
to the intended configuration at every such other path, in merged position, so the strict ordered
read-back (:func:`~troupe.config.writeback.verify.verify_patch`) accepts the write and history
records the new leaves at every place.

Only placements made by the override rule (``KeyPlacement.by_override``) are widened. Changed
existing values, ``--in-include`` and the step 1 rule for list items are not, so the read-back
still refuses them in a shared file (config-set-writeback 14.2).

How the other places are found. Diamond includes are loaded once per place, as separate node
objects, so a written mapping is identified by its file's real path and its span, and an include
site by the file and span of its include string. At every node of the merged tree, its layer tree
(``layers.base``/``layers.over``, recursively, and the includes a node stands for) is searched for
the node that receives the keys after the write: the merged mapping whose override side is the
written mapping, the written mapping itself, or the content of the include whose ``$ref`` mapping
is the written mapping or whose include string is converted. Its descendant along the placement's
prefix is the layer gaining the new keys; the key order at the place is recomputed from the layer
tree with that layer's keys followed by the new ones (merged order, 1.3): after the keys of that
layer, before the override-only keys of every outer layer.

A place where the new keys are shadowed (the prefix is not a mapping there, or the layer is not
merged in) is left unchanged. A place where an outer layer already has one of the new keys is left
unchanged too, as an outer scalar keeps it; any other outcome there makes the read-back refuse the
set (exit 3) rather than guess the merge.
"""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from troupe.config.model import (
    ConfigMapping,
    ConfigNode,
    ConfigSequence,
    IncludeKind,
    IncludeRef,
    Origin,
)
from troupe.config.overrides import is_ref_key
from troupe.config.paths import ConfigPath
from troupe.config.writeback.compare import IncludeDirective, PlainMapping, PlainValue, new_to_plain
from troupe.config.writeback.target import KeyPlacement
from troupe.config.writeback.value import NewValue

type _Where = tuple[Path, int, int]
"""A written place: the real path of its file and the start and end of its span."""


@dataclass(frozen=True)
class PlacedKeys:
    """New keys the planner placed by the override rule."""

    placement: KeyPlacement
    entries: tuple[tuple[str, NewValue], ...]
    """The new keys below ``placement.prefix`` (without the prefix nesting)."""
    at: ConfigPath
    """The path of the mapping the keys are new in, where the intended configuration has them."""


def with_other_places(
    root: ConfigMapping, intended: PlainMapping, placed: Sequence[PlacedKeys]
) -> PlainMapping:
    """Return ``intended`` with the new keys of each placement in ``placed`` also at every other
    path of the merged configuration ``root`` that reaches the same written mapping or include
    site, in merged position (4.8)."""
    for record in placed:
        where = _target(record.placement)
        for place, holder in _places(root, (), where):
            at = (*place, *record.placement.prefix)
            if at == record.at.segments:
                continue
            layer = _descend(holder, record.placement.prefix)
            if layer is None:
                continue
            node = _node_at(root, at)
            if node is None:
                continue
            order = _order(node, layer, [key for key, _ in record.entries])
            if order is None:
                continue
            new = {key: new_to_plain(value) for key, value in record.entries}
            updated = _update(intended, at, new, order)
            if not isinstance(updated, dict):  # pragma: no cover - the root stays a mapping
                raise TypeError("the root must stay a mapping")
            intended = updated
    return intended


def _where(origin: Origin) -> _Where:
    return (origin.file.real_path, origin.span.start, origin.span.end)


def _target(placement: KeyPlacement) -> tuple[str, _Where]:
    """The identity of the placement's target: a written mapping, or an include string."""
    if placement.convert is not None:
        return ("include", _where(placement.convert.ref.directive))
    if placement.mapping is None:  # pragma: no cover - a placement always names one of them
        raise ValueError("the placement names neither a mapping nor an include")
    return ("mapping", _where(placement.mapping.origin))


def _places(
    node: ConfigNode, path: tuple[str, ...], target: tuple[str, _Where]
) -> Iterator[tuple[tuple[str, ...], ConfigMapping]]:
    """Every path of the merged tree below ``node`` whose layer tree holds the target, with the
    layer node that receives the new keys there."""
    if isinstance(node, ConfigMapping):
        for holder in _receivers(node, target, over=False):
            yield path, holder
        for entry in node.entries:
            yield from _places(entry.value, (*path, entry.key), target)
    elif isinstance(node, ConfigSequence):
        for index, item in enumerate(node.items):
            yield from _places(item, (*path, str(index)), target)


def _receivers(
    node: ConfigMapping, target: tuple[str, _Where], *, over: bool
) -> Iterator[ConfigMapping]:
    """The nodes of ``node``'s layer tree that receive the new keys written at ``target``.

    A merged mapping whose override side is the written mapping receives them (after its keys);
    an unmerged written mapping receives them itself (``over``: unless it is the override side
    just matched); a node standing for an include whose ``$ref`` mapping is the written mapping,
    or whose include string is converted, receives them as its content's new override.
    """
    kind, where = target
    layers = node.layers
    if layers is not None:
        if kind == "mapping" and layers.over.layers is None and _where(layers.over.origin) == where:
            yield node
        yield from _receivers(layers.base, target, over=False)
        yield from _receivers(layers.over, target, over=True)
        return
    if kind == "mapping" and not over and _where(node.origin) == where:
        yield node
    if any(_written_at(ref, kind) == where for ref in _chain(node.include)):
        yield node


def _written_at(ref: IncludeRef, kind: str) -> _Where | None:
    """Where ``ref`` is written as a target of ``kind``: its ``$ref`` mapping (``mapping``), or
    its include string when it is in scalar form (``include``)."""
    if kind == "mapping":
        return None if ref.form is None else _where(ref.form.origin)
    return _where(ref.directive) if ref.form is None else None


def _chain(ref: IncludeRef | None) -> Iterator[IncludeRef]:
    """The YAML includes a node stands for, outermost first."""
    while ref is not None:
        if ref.kind is IncludeKind.YAML:
            yield ref
        ref = ref.inner


def _descend(node: ConfigMapping, keys: tuple[str, ...]) -> ConfigMapping | None:
    """The mapping below ``node`` along ``keys`` (merged children), or ``None``."""
    current = node
    for key in keys:
        entry = current.get(key)
        if entry is None or not isinstance(entry.value, ConfigMapping):
            return None
        current = entry.value
    return current


def _node_at(root: ConfigMapping, segments: tuple[str, ...]) -> ConfigMapping | None:
    """The mapping of the merged tree at ``segments``, or ``None``."""
    node: ConfigNode = root
    for segment in segments:
        if isinstance(node, ConfigMapping):
            entry = node.get(segment)
            if entry is None:
                return None
            node = entry.value
        elif isinstance(node, ConfigSequence):
            node = node.items[int(segment)]
        else:
            return None
    return node if isinstance(node, ConfigMapping) else None


def _order(node: ConfigMapping, layer: ConfigMapping, new: list[str]) -> list[str] | None:
    """The merged key order of ``node`` once ``layer`` (a node of its layer tree) gains the keys
    ``new``; ``None`` when ``layer`` is not in the tree or a new key is already there."""
    existing = _keys(node)
    if any(key in existing for key in new):
        return None
    found, order = _ordered(node, layer, new)
    return order if found else None


def _ordered(node: ConfigMapping, layer: ConfigMapping, new: list[str]) -> tuple[bool, list[str]]:
    if node is layer:
        keys = _keys(node)
        return True, [*keys, *(key for key in new if key not in keys)]
    if node.layers is None:
        return False, _keys(node)
    found_base, base = _ordered(node.layers.base, layer, new)
    found_over, over = _ordered(node.layers.over, layer, new)
    return found_base or found_over, [*base, *(key for key in over if key not in base)]


def _keys(node: ConfigMapping) -> list[str]:
    return [entry.key for entry in node.entries if not is_ref_key(entry.key)]


def _update(
    plain: PlainValue, segments: tuple[str, ...], new: dict[str, PlainValue], order: list[str]
) -> PlainValue:
    """Return ``plain`` with the mapping at ``segments`` holding ``new`` in the key ``order``."""
    if isinstance(plain, IncludeDirective) and plain.content is not None:
        return replace(plain, content=_update(plain.content, segments, new, order))
    if segments:
        segment, rest = segments[0], segments[1:]
        if isinstance(plain, dict):
            return {**plain, segment: _update(plain[segment], rest, new, order)}
        if isinstance(plain, list):
            items = list(plain)
            items[int(segment)] = _update(items[int(segment)], rest, new, order)
            return items
        raise ValueError(f"segment {segment!r} lies below a scalar")  # pragma: no cover
    if not isinstance(plain, dict):  # pragma: no cover - the merged node there is a mapping
        raise TypeError("new keys go into a mapping")
    merged = {**plain, **new}
    if set(merged) != set(order):  # the set changes this mapping too: left to the read-back
        return plain
    return {key: merged[key] for key in order}
