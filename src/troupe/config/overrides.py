"""Mapping-form include overrides: the deep merge and the written-mapping lookups
(config-include-overrides Requirements 1.2, 1.3, 1.5, 7.1, 7.2).

A mapping-form include ``{$ref: "file:...", <overrides>}`` with overrides has as its value the deep
merge of the included content with the overrides. The loader builds that merged mapping with
:func:`deep_merge`; every merged mapping keeps both sides in ``ConfigMapping.layers``. The writer
uses the lookups here to reach the mappings actually written in files behind a merged one:

* :func:`physical_entry`: the written mapping and entry that hold a key's merged value;
* :func:`physical_holders`: every written mapping holding a key, with the layers leading to it;
* :func:`holder_of`: the entry holding a given written mapping in a merged parent (for pruning
  emptied nested overrides, research.md Decision 4);
* :func:`written_parts`: what is written for a node, for walks over the files.

This module is pure: it performs no I/O and never mutates or rebuilds its inputs.
"""

import dataclasses
from dataclasses import dataclass
from enum import Enum

from troupe.config.constants import INCLUDE_REF_KEY
from troupe.config.model import ConfigMapping, ConfigNode, IncludeRef, MappingEntry, MergeLayers


def is_ref_key(key: str) -> bool:
    """Return whether ``key`` is the reserved key of a mapping-form include."""
    return key == INCLUDE_REF_KEY


def override_entries(mapping: ConfigMapping) -> tuple[MappingEntry, ...]:
    """Return the entries of ``mapping`` without its ``$ref`` entry, in written order."""
    return tuple(entry for entry in mapping.entries if not is_ref_key(entry.key))


def _override(mapping: ConfigMapping, key: str) -> MappingEntry | None:
    """Return the override entry of ``key`` in ``mapping``; the ``$ref`` entry is never one."""
    return None if is_ref_key(key) else mapping.get(key)


def deep_merge(
    base: ConfigMapping, over: ConfigMapping, *, include: IncludeRef | None
) -> ConfigMapping:
    """Return the deep merge of the included side ``base`` with the override side ``over`` (1.2).

    The entries are ``base``'s entries in their order, each overridden one replaced in place by the
    override entry, or by a merged entry when both values are mappings, followed by the entries
    only in ``over`` in their order (1.3). The ``$ref`` entry of ``over`` is skipped (1.5).

    The result has ``base``'s style, origin, anchor and alias (config-aliases: an alias place or
    anchored node behind overrides keeps its provenance), the given ``include`` and
    ``layers=MergeLayers(base, over)``. A nested merged mapping carries its ``base`` value's
    include. Scalars and lists are the very nodes of their layer, so their origins name their own
    file. Neither input is changed.
    """
    entries: list[MappingEntry] = []
    for entry in base.entries:
        overriding = _override(over, entry.key)
        if overriding is None:
            entries.append(entry)
        elif isinstance(entry.value, ConfigMapping) and isinstance(overriding.value, ConfigMapping):
            merged = deep_merge(entry.value, overriding.value, include=entry.value.include)
            entries.append(dataclasses.replace(entry, value=merged))
        else:
            entries.append(overriding)
    entries.extend(entry for entry in override_entries(over) if base.get(entry.key) is None)
    return ConfigMapping(
        entries=tuple(entries),
        style=base.style,
        origin=base.origin,
        include=include,
        layers=MergeLayers(base=base, over=over),
        anchor=base.anchor,
        alias=base.alias,
    )


def physical_entry(mapping: ConfigMapping, key: str) -> tuple[ConfigMapping, MappingEntry]:
    """Return the written mapping and entry that hold the value of ``key`` in ``mapping``.

    For a merged mapping this is the override side when it holds ``key`` (7.1), else the included
    side (7.2), recursively through merged layers. Otherwise it is ``mapping`` itself. The returned
    mapping is written in one file. Raises :class:`KeyError` when ``mapping`` has no ``key`` (a
    merged mapping never has ``$ref``).
    """
    layers = mapping.layers
    if layers is None:
        entry = mapping.get(key)
        if entry is None:
            raise KeyError(key)
        return mapping, entry
    if is_ref_key(key):
        raise KeyError(key)
    if _override(layers.over, key) is not None:
        return physical_entry(layers.over, key)
    return physical_entry(layers.base, key)


class Layer(Enum):
    """The side of a merged mapping that a lookup stepped into."""

    BASE = "base"  # the included side
    OVER = "over"  # the override side


@dataclass(frozen=True)
class LayerStep:
    """One step from a merged mapping into one of its layers."""

    merged: ConfigMapping
    layer: Layer


@dataclass(frozen=True)
class Holder:
    """A written mapping holding a key, and the layers leading to it from the merged mapping."""

    mapping: ConfigMapping  # written in one file
    entry: MappingEntry  # its entry for the key
    chain: tuple[LayerStep, ...]  # outermost first; empty when the mapping looked in holds the key

    @property
    def is_override(self) -> bool:
        """Whether ``mapping`` is an override mapping: the last step went into an override side.

        Only such a holder may be pruned when it loses every entry (research.md Decision 4); it is
        never pruned when it is a ``$ref`` mapping.
        """
        return bool(self.chain) and self.chain[-1].layer is Layer.OVER


def physical_holders(mapping: ConfigMapping, key: str) -> tuple[Holder, ...]:
    """Return every written mapping below ``mapping`` that holds ``key`` (7.1, 7.2, 7.4).

    The override side comes first, then the included side, recursively through merged layers.
    Each holder records the chain of layer steps that lead from ``mapping`` to it. An unmerged
    mapping holding ``key`` is its own single holder with an empty chain. The ``$ref`` entry of an
    override side is never a holder.
    """
    return _holders(mapping, key, ())


def _holders(mapping: ConfigMapping, key: str, chain: tuple[LayerStep, ...]) -> tuple[Holder, ...]:
    layers = mapping.layers
    if layers is None:
        entry = mapping.get(key)
        return () if entry is None else (Holder(mapping, entry, chain),)
    if is_ref_key(key):
        return ()
    return _holders(layers.over, key, (*chain, LayerStep(mapping, Layer.OVER))) + _holders(
        layers.base, key, (*chain, LayerStep(mapping, Layer.BASE))
    )


def holder_of(parent: ConfigMapping, key: str, written: ConfigMapping) -> Holder | None:
    """Return the holder of ``key`` in ``parent`` whose entry's value is the mapping ``written``.

    ``written`` is a holder's mapping found below the merged value of ``key`` in ``parent``. The
    result is its own entry in the written mapping that holds it, so that an emptied nested
    override can be deleted at that entry (research.md Decision 4). ``None`` when no written entry
    of ``key`` holds ``written``: then ``written`` is the top of its own written tree (a ``$ref``
    mapping or included content).
    """
    for holder in physical_holders(parent, key):
        if holder.entry.value is written:
            return holder
    return None


def written_parts(node: ConfigNode) -> tuple[ConfigNode, ...]:
    """Return what is written in files for ``node``, for walks over the written trees.

    A merged mapping gives ``(base, over)``. A bare mapping-form include gives its ``$ref``
    mapping and the node with ``include.form`` cleared (its content). Any other node gives
    ``(node,)``.
    """
    if isinstance(node, ConfigMapping) and node.layers is not None:
        return (node.layers.base, node.layers.over)
    if node.include is not None and node.include.form is not None:
        content = dataclasses.replace(node, include=dataclasses.replace(node.include, form=None))
        return (node.include.form, content)
    return (node,)
