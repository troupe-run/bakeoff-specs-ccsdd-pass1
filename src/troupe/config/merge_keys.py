"""Merge keys: the merged view of a mapping with a plain ``<<`` key
(config-aliases Requirements 2.1, 2.2, 2.4).

A mapping written with a merge key has as its value its own keys plus every key of the merged
mappings (the merge sources) that it does not write itself. The loader builds that **merged view**
with :func:`merge_mapping` and keeps the mapping as written in ``ConfigMapping.merge``
(research.md Decision 4).

This module is pure: it performs no I/O and never mutates its inputs.
"""

import dataclasses
from collections.abc import Sequence

from troupe.config.model import ConfigMapping, MappingEntry, MergeKey, Origin


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
    seen = {entry.key for entry in own}
    merged: list[MappingEntry] = []
    for source in sources:
        for entry in source.entries:
            if entry.key not in seen:
                seen.add(entry.key)
                merged.append(dataclasses.replace(entry, via_merge=True))
    entries = written.entries[:merge_index] + tuple(merged) + written.entries[merge_index + 1 :]
    return dataclasses.replace(written, entries=entries, merge=MergeKey(written, key_origin))
