"""Merge keys: the merged view of a mapping with a plain ``<<`` key
(config-aliases Requirements 2.1, 2.2, 2.4).

A mapping written with a merge key has as its value its own keys plus every key of the merged
mappings (the merge sources) that it does not write itself. The loader builds that **merged view**
with :func:`merge_mapping` and keeps the mapping as written in ``ConfigMapping.merge``
(research.md Decision 4).

This module is pure: it performs no I/O and never mutates its inputs.
"""

import dataclasses
from collections.abc import Collection, Iterable, Sequence

from troupe.config.model import ConfigMapping, MergeKey, Origin


def merged_pairs[V](
    own: Collection[str], sources: Iterable[Iterable[tuple[str, V]]]
) -> list[tuple[str, V]]:
    """The ``(key, value)`` pairs a merge key brings into a mapping whose own keys are ``own``
    (2.1, 2.2): the pairs of ``sources``, earlier sources first and each in its order, whose keys
    are neither own keys nor given by an earlier pair. This is the single home of the merge-key
    precedence and order rule; the loader's merged view and the writer's rebuilt plain view both
    use it. Pure: neither input is changed."""
    seen = set(own)
    merged: list[tuple[str, V]] = []
    for source in sources:
        for key, value in source:
            if key not in seen:
                seen.add(key)
                merged.append((key, value))
    return merged


def merge_mapping(
    written: ConfigMapping,
    merge_index: int,
    sources: Sequence[ConfigMapping],
    key_origin: Origin,
) -> ConfigMapping:
    """Return the merged view of ``written``, whose ``<<`` entry is at ``merge_index`` (2.1, 2.2).

    Precondition: ``merge_index`` is the index of the plain ``<<`` entry in ``written.entries``.

    The entries are ``written``'s own entries in written order, and at ``merge_index`` (in place
    of the ``<<`` entry) the entries of ``sources``, earlier sources first and each in its order,
    whose keys are neither written in the mapping nor in an earlier source. Those entries are the
    sources' entries marked ``via_merge=True``. The result has no ``<<`` entry (2.4), has
    ``merge=MergeKey(written, key_origin)`` and otherwise the fields of ``written`` (style, origin,
    include, anchor and alias). Neither ``written`` nor ``sources`` is changed.
    """
    own = written.entries[:merge_index] + written.entries[merge_index + 1 :]
    pairs = merged_pairs(
        {entry.key for entry in own},
        (((entry.key, entry) for entry in source.entries) for source in sources),
    )
    merged = [dataclasses.replace(entry, via_merge=True) for _, entry in pairs]
    entries = written.entries[:merge_index] + tuple(merged) + written.entries[merge_index + 1 :]
    return dataclasses.replace(written, entries=entries, merge=MergeKey(written, key_origin))
