"""TargetResolver: write-target rules 1-4, path errors (exit 5) and the intended configuration.

:func:`resolve_target` decides what PATH addresses for a set:

* an existing value is a :class:`ReplaceTarget` (rule 1, including include roots, whose refusals
  are the planner's);
* a missing key below an existing mapping is an :class:`AddKeyTarget` naming that mapping and the
  missing intermediate mappings (rule 2), with the :class:`KeyPlacement` of the placement walk
  (config-include-overrides Requirements 4 and 6, research.md Decision 2);
* an index equal to a list's length as the last segment is an :class:`AppendTarget` (rule 3);
* everything else raises :class:`PathNotFoundError` (exit 5) naming PATH and the file of the
  deepest existing node, so such paths never reach :func:`splice`.

A PATH with a ``$ref`` segment is refused (exit 3) before any of this (8.1).

It also computes the intended configuration with :func:`splice`, supplying the text-include content
rule (:func:`text_include_value`) so it applies at PATH and at any depth inside a replaced
collection.
"""

import dataclasses
import itertools
import re
from dataclasses import dataclass
from typing import Final, TypeAlias

from troupe.config.errors import PathNotFoundError, WriteRefusedError
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
from troupe.config.overrides import is_ref_key
from troupe.config.paths import ConfigPath, is_index_segment
from troupe.config.resolve import PathWalk, WalkStop, walk_path
from troupe.config.writeback.compare import (
    IncludeDirective,
    PlainMapping,
    PlainValue,
    new_to_plain,
    root_plain,
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
class IncludeSite:
    """A scalar-form YAML include root to convert to mapping form, where its directive is."""

    node: ConfigNode
    """The include root (the included content as loaded)."""
    parent: ConfigMapping | ConfigSequence
    """The (possibly merged) parent; its written entry is found through the overrides lookups."""
    key: str | int
    """The mapping key (``str``) or the list index (``int``) of ``node`` in ``parent``."""


@dataclass(frozen=True)
class KeyPlacement:
    """Where new keys below a mapping are written (research.md Decision 2).

    Exactly one of ``mapping`` and ``convert`` is set.
    """

    mapping: ConfigMapping | None
    """A written mapping receiving the entries: none of its entries comes from another layer."""
    convert: IncludeSite | None
    """A scalar-form YAML include to convert to mapping form, receiving the entries as overrides."""
    prefix: tuple[str, ...]
    """Existing keys (of the merged configuration) to create first as intermediate mappings."""


@dataclass(frozen=True)
class AddKeyTarget:
    """PATH continues below an existing mapping with keys it does not have."""

    mapping: ConfigMapping
    """The deepest existing (possibly merged) mapping, for messages and comparisons."""
    missing: tuple[str, ...]
    """The first missing key, then the intermediate keys; VALUE goes at the last."""
    placement: KeyPlacement
    """Where the new keys are written: ``prefix`` then ``missing`` below the placement."""


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

    Before any of that, raises :class:`WriteRefusedError` (exit 3) naming PATH and the root file
    when a segment of PATH is ``$ref`` (config-include-overrides 8.1). The current configuration
    is the root's plain form with a root include unwrapped.
    """
    if any(is_ref_key(segment) for segment in path.segments):
        raise WriteRefusedError(
            "`$ref` is reserved for includes; a set PATH cannot name it",
            file=document.root_file.path,
            path=str(path),
        )
    walk = walk_path(document.root, path)
    target = _target(walk)
    current = root_plain(document)
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
        placement = _place(_chain(walk.nodes, path.segments[: walk.consumed]))
        return AddKeyTarget(mapping=deepest, missing=remaining, placement=placement)
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


@dataclass(frozen=True)
class _Link:
    """One node of a placement chain, with how it is reached from the node before it."""

    node: ConfigNode
    parent: ConfigMapping | ConfigSequence | None  # None for the top of the chain
    key: str | int | None


def place_new_keys(document: ConfigDocument, mapping_path: ConfigPath) -> KeyPlacement:
    """Return where new keys below the mapping at ``mapping_path`` are written (4.1-4.4, 4.6, 6.2,
    6.3; design.md "Placement walk").

    ``mapping_path`` must name an existing mapping A of the merged configuration. The walk goes up
    from A and stops at the first of:

    * a merged mapping X: the walk continues in X's override side, descending the keys from X to
      A through what it already holds as mappings and rerunning the walk on that sub-chain
      (never above it); a sub-chain walk reaching its top places into its deepest mapping;
    * a bare mapping-form include X: its ``$ref`` mapping, prefix the keys from X to A;
    * a scalar-form YAML include X: X is converted where its directive is, same prefix;
    * a list item: A itself (the step 1 rule).

    Reaching the root places into A itself.
    """
    walk = walk_path(document.root, mapping_path)
    if walk.stop is not WalkStop.COMPLETE or not isinstance(walk.deepest, ConfigMapping):
        raise ValueError(f"{mapping_path} does not name an existing mapping")
    return _place(_chain(walk.nodes, mapping_path.segments))


def _chain(nodes: tuple[ConfigNode, ...], segments: tuple[str, ...]) -> tuple[_Link, ...]:
    """Return the links of a walked chain: ``nodes[i + 1]`` is reached by ``segments[i]``."""
    links = [_Link(node=nodes[0], parent=None, key=None)]
    for (parent, node), segment in zip(itertools.pairwise(nodes), segments, strict=True):
        if isinstance(parent, ConfigScalar):  # pragma: no cover - a walk never passes a scalar
            raise TypeError("a path walk never continues below a scalar")
        key: str | int = int(segment) if isinstance(parent, ConfigSequence) else segment
        links.append(_Link(node=node, parent=parent, key=key))
    return tuple(links)


def _place(chain: tuple[_Link, ...]) -> KeyPlacement:
    """Run the placement walk up ``chain`` (top first, A last), never above its top."""
    deepest = chain[-1].node
    if not isinstance(deepest, ConfigMapping):  # pragma: no cover - A is always a mapping
        raise TypeError("new keys are placed below a mapping")
    for index in range(len(chain) - 1, -1, -1):
        link = chain[index]
        node = link.node
        keys = tuple(str(later.key) for later in chain[index + 1 :])
        if isinstance(node, ConfigMapping) and node.layers is not None:
            held, rest = _override_chain(link, node.layers.over, keys)
            placement = _place(held)
            return dataclasses.replace(placement, prefix=placement.prefix + rest)
        include = node.include
        if include is not None and include.form is not None:
            return KeyPlacement(mapping=include.form, convert=None, prefix=keys)
        if include is not None and include.kind is IncludeKind.YAML and link.parent is not None:
            site = IncludeSite(node=node, parent=link.parent, key=_key(link))
            return KeyPlacement(mapping=None, convert=site, prefix=keys)
        if isinstance(link.parent, ConfigSequence):
            break
    return KeyPlacement(mapping=deepest, convert=None, prefix=())


def _override_chain(
    link: _Link, over: ConfigMapping, keys: tuple[str, ...]
) -> tuple[tuple[_Link, ...], tuple[str, ...]]:
    """Return the sub-chain of ``over``, the override side of ``link``'s merged mapping, and the
    keys it lacks.

    The sub-chain descends ``keys`` through what ``over`` already holds as mappings; its top is
    reached from ``link``'s parent by ``link``'s key (so an override written as a scalar-form
    include is converted at its entry). The keys after the deepest mapping held are returned as
    the rest, to be created as intermediate mappings.
    """
    links = [_Link(node=over, parent=link.parent, key=link.key)]
    current = over
    for key in keys:
        entry = current.get(key)
        if entry is None or not isinstance(entry.value, ConfigMapping):
            break
        links.append(_Link(node=entry.value, parent=current, key=key))
        current = entry.value
    return tuple(links), keys[len(links) - 1 :]


def _key(link: _Link) -> str | int:
    if link.key is None:  # pragma: no cover - only the top of a chain has no key
        raise TypeError("the top of a chain has no key")
    return link.key


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
