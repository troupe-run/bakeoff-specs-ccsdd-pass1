"""TargetResolver: write-target rules 1-4, path errors (exit 5) and the intended configuration.

:func:`resolve_target` decides what PATH addresses for a set:

* an existing value is a :class:`ReplaceTarget` (rule 1, including include roots, whose refusals
  are the planner's);
* a missing key below an existing mapping is an :class:`AddKeyTarget` in that mapping's file, with
  the missing intermediate mappings listed (rule 2);
* an index equal to a list's length as the last segment is an :class:`AppendTarget` (rule 3);
* everything else raises :class:`PathNotFoundError` (exit 5) naming PATH and the file of the
  deepest existing node, so such paths never reach :func:`splice`.

It also computes the intended configuration with :func:`splice`, supplying the text-include content
rule (:func:`text_include_value`) so it applies at PATH and at any depth inside a replaced
collection.
"""

import re
from dataclasses import dataclass
from typing import Final, TypeAlias

from troupe.config.errors import PathNotFoundError
from troupe.config.model import (
    ConfigDocument,
    ConfigMapping,
    ConfigNode,
    ConfigScalar,
    ConfigSequence,
    IncludeKind,
    LineEnding,
    SourceFile,
)
from troupe.config.paths import ConfigPath, is_index_segment
from troupe.config.resolve import PathWalk, WalkStop, walk_path
from troupe.config.writeback.compare import (
    IncludeDirective,
    PlainMapping,
    PlainValue,
    new_to_plain,
    node_to_plain,
    splice,
)
from troupe.config.writeback.value import NewValue


@dataclass(frozen=True)
class ReplaceTarget:
    """PATH names an existing value: ``node``, reached from ``parent`` through ``key``."""

    node: ConfigNode
    parent: ConfigMapping | ConfigSequence
    key: str | int
    """The mapping key (``str``) or the list index (``int``) of ``node`` in ``parent``."""


@dataclass(frozen=True)
class AddKeyTarget:
    """PATH continues below an existing mapping with keys it does not have."""

    mapping: ConfigMapping
    missing: tuple[str, ...]
    """The first missing key, then the intermediate keys; VALUE goes at the last."""


@dataclass(frozen=True)
class AppendTarget:
    """PATH ends with an index equal to the length of ``sequence``."""

    sequence: ConfigSequence


# A runtime union (not a ``type`` statement) so callers can also use it with ``isinstance``.
WriteTarget: TypeAlias = ReplaceTarget | AddKeyTarget | AppendTarget  # noqa: UP040
"""Where a set writes."""


@dataclass(frozen=True)
class SetRequest:
    """A resolved set: the target, VALUE and the current and intended configurations."""

    path: ConfigPath
    target: WriteTarget
    value: NewValue
    current: PlainMapping
    """The plain tree of the configuration as loaded."""
    intended: PlainMapping
    """The plain tree the configuration must become (include directives kept)."""


_BARE_LINE_FEED: Final = re.compile(r"(?<!\r)\n")


def text_include_value(value: str, target: SourceFile) -> str:
    """Return the new content of the text-include ``target`` when ``value`` is set over it.

    VALUE exactly, except that every line feed not already preceded by a carriage return is written
    as CRLF when ``target`` uses CRLF for every line break (2.2, 2.3).
    """
    if target.line_ending is LineEnding.CRLF:
        return _BARE_LINE_FEED.sub("\r\n", value)
    return value


def resolve_target(document: ConfigDocument, path: ConfigPath, value: NewValue) -> SetRequest:
    """Resolve PATH on ``document`` and compute the intended configuration for VALUE.

    Raises :class:`PathNotFoundError` (exit 5) naming PATH and the file holding the deepest
    existing node when PATH lies below a scalar (including null and a text include), applies a
    non-index segment to a list, uses an index beyond a list's length, continues after an index
    equal to the length, or would create a digit-only key (3.4-3.6, 4.2, 4.3, 16.3).
    """
    walk = walk_path(document.root, path)
    target = _target(walk)
    current = node_to_plain(document.root)
    if not isinstance(current, dict):  # pragma: no cover - the root is never an include
        raise TypeError("the root of a configuration must be a mapping")
    text_targets = _text_include_targets(document.root, current)

    def text_content(text: str, directive: IncludeDirective) -> PlainValue:
        return text_include_value(text, text_targets[id(directive)])

    intended = splice(current, path, new_to_plain(value), text_content)
    return SetRequest(path=path, target=target, value=value, current=current, intended=intended)


def _target(walk: PathWalk) -> WriteTarget:
    """Map a path walk to a write target, or raise the exit-5 error for it."""
    path = walk.path
    deepest = walk.deepest
    if walk.stop is WalkStop.COMPLETE:
        parent = walk.nodes[-2]
        if isinstance(parent, ConfigScalar):  # pragma: no cover - a walk never passes a scalar
            raise TypeError("a path walk never continues below a scalar")
        segment = path.segments[-1]
        key: str | int = int(segment) if isinstance(parent, ConfigSequence) else segment
        return ReplaceTarget(node=deepest, parent=parent, key=key)
    remaining = path.segments[walk.consumed :]
    segment = remaining[0]
    if walk.stop is WalkStop.MISSING_KEY and isinstance(deepest, ConfigMapping):
        digit_keys = [key for key in remaining if is_index_segment(key)]
        if digit_keys:
            raise _not_found(
                walk,
                f"not found: key {digit_keys[0]!r} would have to be created, "
                "but a key of digits only cannot be created (it reads as a list index)",
            )
        return AddKeyTarget(mapping=deepest, missing=remaining)
    if walk.stop is WalkStop.INDEX_OUT_OF_RANGE and isinstance(deepest, ConfigSequence):
        index = int(segment)
        length = len(deepest.items)
        if index > length:
            raise _not_found(
                walk,
                f"not found: list index {index} is beyond the end of a list of {length} "
                f"(use index {length} to append)",
            )
        if len(remaining) > 1:
            raise _not_found(
                walk,
                f"not found: list index {index} appends a new item, "
                f"so no segment may follow it (at segment {remaining[1]!r})",
            )
        return AppendTarget(sequence=deepest)
    if walk.stop is WalkStop.NOT_AN_INDEX:
        raise _not_found(
            walk, f"not found: a list can only be indexed by a number (at segment {segment!r})"
        )
    raise _not_found(
        walk,
        f"not found: {_scalar_kind(walk)} has no children (at segment {segment!r})",
    )


def _scalar_kind(walk: PathWalk) -> str:
    node = walk.deepest
    if node.include is not None and node.include.kind is IncludeKind.TEXT:
        return "a text include"
    if isinstance(node, ConfigScalar) and node.value is None:
        return "a null value"
    return "a scalar value"


def _not_found(walk: PathWalk, problem: str) -> PathNotFoundError:
    return PathNotFoundError(problem, file=walk.deepest.origin.file.path, path=str(walk.path))


def _text_include_targets(node: ConfigNode, plain: PlainValue) -> dict[int, SourceFile]:
    """Map each text-include directive object of ``plain`` (by identity) to its target file.

    ``plain`` is ``node_to_plain(node)``; :func:`splice` hands the existing directive objects of
    that tree to the content callback, so identity finds the target the directive loads.
    """
    targets: dict[int, SourceFile] = {}
    _collect_text_targets(node, plain, targets)
    return targets


def _collect_text_targets(
    node: ConfigNode, plain: PlainValue, targets: dict[int, SourceFile]
) -> None:
    if isinstance(plain, IncludeDirective):
        if node.include is not None and node.include.kind is IncludeKind.TEXT:
            targets[id(plain)] = node.include.target
            return
        if plain.content is None:  # pragma: no cover - loaded directives always have content
            return
        plain = plain.content
    if isinstance(node, ConfigMapping) and isinstance(plain, dict):
        for entry in node.entries:
            _collect_text_targets(entry.value, plain[entry.key], targets)
    elif isinstance(node, ConfigSequence) and isinstance(plain, list):
        for item, item_plain in zip(node.items, plain, strict=True):
            _collect_text_targets(item, item_plain, targets)
