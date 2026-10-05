"""TargetResolver: write-target rules 1-4, path errors (exit 5) and the intended configuration.

:func:`resolve_target` decides what PATH addresses for a set:

* an existing value is a :class:`ReplaceTarget` (rule 1, including include roots, whose refusals
  are the planner's);
* a missing key below an existing mapping is an :class:`AddKeyTarget` naming that mapping and the
  missing intermediate mappings (rule 2), with the :class:`KeyPlacement` of the placement walk
  over the layers holding that mapping's content (config-include-overrides Requirements 4 and 6,
  choice 5, research.md Decision 2);
* an index equal to a list's length as the last segment is an :class:`AppendTarget` (rule 3);
* everything else raises :class:`PathNotFoundError` (exit 5) naming PATH and the file of the
  deepest existing node, so such paths never reach :func:`splice`.

A PATH with a ``$ref`` segment is refused (exit 3) before any of this (8.1).

With ``in_include`` (``set --in-include``, config-include-overrides Requirement 10) new keys use the
step 1 placement instead of the walk: the included side of the deepest existing mapping, and the
intended configuration lists keys written into the included side of a merged mapping before its
override-only keys (merged order, 1.3, 10.2).

It also computes the intended configuration with :func:`splice`, supplying the text-include content
rule (:func:`text_include_value`) so it applies at PATH and at any depth inside a replaced
collection.
"""

import dataclasses
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
    IncludeRef,
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
from troupe.config.writeback.value import NewMapping, NewValue


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
    parent: ConfigMapping | ConfigSequence | None
    """The (possibly merged) parent; its written entry is found through the overrides lookups.
    ``None`` when the directive is the whole root of its file (a root file, or an included file,
    whose root is ``file:...``)."""
    key: str | int | None
    """The mapping key (``str``) or the list index (``int``) of ``node`` in ``parent``; ``None``
    with no parent."""
    include: IncludeRef | None = None
    """The include to convert when it is not ``node.include`` (a chained include, or the include
    kept on the included side of a merge); ``None`` for ``node.include``."""

    @property
    def ref(self) -> IncludeRef:
        """The include to convert."""
        ref = self.include if self.include is not None else self.node.include
        if ref is None:  # pragma: no cover - a site is always made for an include
            raise ValueError("not an include root")
        return ref


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
    in_include: bool = False
    """Whether new keys use the step 1 placement (``set --in-include``, Requirement 10); the
    planner uses the same choice for the new keys of a collection replacement (10.3)."""


_BARE_LINE_FEED: Final = re.compile(r"(?<!\r)\n")


def text_include_value(value: str, target: SourceFile) -> str:
    """Return the new content of the text-include ``target`` when ``value`` is set over it.

    VALUE exactly, except that every line feed not already preceded by a carriage return is written
    as CRLF when ``target`` uses CRLF for every line break (2.2, 2.3).
    """
    if target.line_ending is LineEnding.CRLF:
        return _BARE_LINE_FEED.sub("\r\n", value)
    return value


def resolve_target(
    document: ConfigDocument, path: ConfigPath, value: NewValue, *, in_include: bool = False
) -> SetRequest:
    """Resolve PATH on ``document`` and compute the intended configuration for VALUE.

    Raises :class:`PathNotFoundError` (exit 5) naming PATH and the file holding the deepest
    existing node when PATH lies below a scalar (including null and a text include), applies a
    non-index segment to a list, uses an index beyond a list's length, continues after an index
    equal to the length, or would create a digit-only key (3.4-3.6, 4.2, 4.3, 16.3).

    Before any of that, raises :class:`WriteRefusedError` (exit 3) naming PATH and the root file
    when a segment of PATH is ``$ref`` (config-include-overrides 8.1). The current configuration
    is the root's plain form with a root include unwrapped.

    With ``in_include`` new keys are placed by the step 1 rule (:func:`place_new_keys`), and in
    the intended configuration the keys written into the included side of a merged mapping (at
    the deepest existing mapping, or at any merged mapping a mapping VALUE is merged into) come
    after its included keys and before its override-only keys (10.1-10.3, 10.5).
    """
    if any(is_ref_key(segment) for segment in path.segments):
        raise WriteRefusedError(
            "`$ref` is reserved for includes; a set PATH cannot name it",
            file=document.root_file.path,
            path=str(path),
        )
    walk = walk_path(document.root, path)
    target = _target(walk, in_include=in_include)
    current = root_plain(document)
    text_targets = _text_include_targets(document.root, current)

    def text_content(text: str, directive: IncludeDirective) -> PlainValue:
        return text_include_value(text, text_targets[id(directive)])

    intended = splice(current, path, new_to_plain(value), text_content)
    if in_include:
        for at, merged in _merged_receivers(walk, target, value):
            intended = _reorder_at(intended, at, merged)
    return SetRequest(
        path=path,
        target=target,
        value=value,
        current=current,
        intended=intended,
        in_include=in_include,
    )


def _target(walk: PathWalk, *, in_include: bool) -> WriteTarget:
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
        if in_include:
            placement = _included_placement(deepest)
        else:
            placement = _place(document_root(walk), path.segments[: walk.consumed])
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
class _Holder:
    """One element of a holder chain (design.md "Placement walk"): a node on the way to the layer
    that holds A's content, or one of the YAML includes such a node stands for."""

    node: ConfigNode
    include: IncludeRef | None
    """The YAML include this element stands for (``node.include`` or a chained ``inner``)."""
    parent: ConfigMapping | ConfigSequence | None
    """Where the node's directive is looked up; ``None`` when it is its file's root."""
    key: str | int | None
    over: bool
    """Whether the node is reached as the override side of a merged mapping."""
    depth: int
    """How many keys lead to the node: ``keys[depth:]`` lead from it down to A."""


def place_new_keys(
    document: ConfigDocument, mapping_path: ConfigPath, *, in_include: bool
) -> KeyPlacement:
    """Return where new keys below the mapping at ``mapping_path`` are written (4.1-4.4, 4.6, 6.2,
    6.3; design.md "Placement walk").

    With ``in_include`` the walk is not run: the step 1 placement is used instead
    (:func:`_included_placement`, 10.1).

    ``mapping_path`` must name an existing mapping A of the merged configuration. The walk follows
    the layers that hold A's content (requirements choice 5: the file holding the reference to the
    include that A's content comes from, even when that file is itself included):

    1. the holder chain goes from the root along the keys to A; a node is listed once per YAML
       include it stands for (a chained ``inner`` include after the outer one), and at a merged
       mapping the chain continues with its override side when that holds every remaining key
       (A written in an override), else with its included side;
    2. walking that chain up from A's layer, the first element that is a YAML include root places
       the keys in its ``$ref`` mapping (descended through the keys it already holds as
       mappings, the walk rerun there, never above it) or converts it (scalar form, also a
       file's whole root), prefix the keys from it to A; an override side or a list item places
       them into the written mapping holding A's content, with no prefix (4.3, 6.2).

    Reaching the root places into that written mapping.
    """
    walk = walk_path(document.root, mapping_path)
    if walk.stop is not WalkStop.COMPLETE or not isinstance(walk.deepest, ConfigMapping):
        raise ValueError(f"{mapping_path} does not name an existing mapping")
    if in_include:
        return _included_placement(walk.deepest)
    return _place(document.root, mapping_path.segments)


def _included_side(mapping: ConfigMapping) -> ConfigMapping:
    """The included side of ``mapping``: its base layer, repeatedly while that is merged; the
    mapping itself when it is not merged (written in one file, the innermost one)."""
    while mapping.layers is not None:
        mapping = mapping.layers.base
    return mapping


def _included_placement(deepest: ConfigMapping) -> KeyPlacement:
    """The step 1 placement for ``set --in-include`` (10.1, 10.5; requirements choice 13).

    The included side of the deepest existing mapping A, with no prefix. An A written only in an
    override is not merged, so it is A itself in the including file; below no include it is A,
    as without the option.
    """
    return KeyPlacement(mapping=_included_side(deepest), convert=None, prefix=())


def _merged_receivers(
    walk: PathWalk, target: WriteTarget, value: NewValue
) -> list[tuple[tuple[str, ...], ConfigMapping]]:
    """The merged mappings whose new keys go into their included side with ``in_include``, each
    with its path segments: the deepest existing mapping of an added key, or every merged mapping
    a mapping VALUE is merged into (at PATH and below it, 10.3)."""
    if isinstance(target, AddKeyTarget):
        if target.mapping.layers is None:
            return []
        return [(walk.path.segments[: walk.consumed], target.mapping)]
    if isinstance(target, ReplaceTarget):
        found: list[tuple[tuple[str, ...], ConfigMapping]] = []
        _collect_merged(target.node, value, walk.path.segments, found)
        return found
    return []


def _collect_merged(
    node: ConfigNode,
    value: NewValue,
    at: tuple[str, ...],
    found: list[tuple[tuple[str, ...], ConfigMapping]],
) -> None:
    """Collect the merged mappings below ``at`` into which the mapping ``value`` is merged.

    A YAML include root is replaced, never merged (2.5, 7.5), so the walk stops there.
    """
    if not (isinstance(node, ConfigMapping) and isinstance(value, NewMapping)):
        return
    if node.include is not None and node.layers is None:
        return
    if node.layers is not None:
        found.append((at, node))
    new = dict(value.entries)
    for entry in node.entries:
        if entry.key in new:
            _collect_merged(entry.value, new[entry.key], (*at, entry.key), found)


def _reorder_at(
    plain: PlainValue, segments: tuple[str, ...], merged: ConfigMapping
) -> PlainMapping:
    """Return the intended root ``plain`` with the new keys of the mapping at ``segments`` moved
    just before its first key that is not on the included side of ``merged`` (merged order 1.3,
    10.2): keys written into the included side are listed after its keys after the reload."""
    result = _reordered(plain, segments, merged)
    if not isinstance(result, dict):  # pragma: no cover - the root stays a mapping
        raise TypeError("the root must stay a mapping")
    return result


def _reordered(plain: PlainValue, segments: tuple[str, ...], merged: ConfigMapping) -> PlainValue:
    if isinstance(plain, IncludeDirective) and plain.content is not None:
        return dataclasses.replace(plain, content=_reordered(plain.content, segments, merged))
    if segments:
        segment, rest = segments[0], segments[1:]
        if isinstance(plain, dict):
            return {**plain, segment: _reordered(plain[segment], rest, merged)}
        if isinstance(plain, list):
            items = list(plain)
            items[int(segment)] = _reordered(items[int(segment)], rest, merged)
            return items
        raise ValueError(f"segment {segment!r} lies below a scalar")  # pragma: no cover
    if not isinstance(plain, dict):  # pragma: no cover - a merged mapping stays a mapping here
        return plain
    existing = {entry.key for entry in merged.entries}
    included = {entry.key for entry in _included_side(merged).entries}
    added = [key for key in plain if key not in existing]
    kept = [key for key in plain if key in existing]
    split = next((i for i, key in enumerate(kept) if key not in included), len(kept))
    order = [*kept[:split], *added, *kept[split:]]
    return {key: plain[key] for key in order}


def document_root(walk: PathWalk) -> ConfigMapping:
    """The root mapping a path walk started at."""
    root = walk.nodes[0]
    if not isinstance(root, ConfigMapping):  # pragma: no cover - the root is always a mapping
        raise TypeError("the root is a mapping")
    return root


def _place(root: ConfigMapping, keys: tuple[str, ...]) -> KeyPlacement:
    """Run the placement walk for new keys below the mapping A that ``keys`` lead to from
    ``root`` (design.md "Placement walk", research.md Decision 2)."""
    holders: list[_Holder] = []
    _holders(root, keys, holders, parent=None, key=None)
    return _walk(holders, keys)


def _include_chain(node: ConfigNode) -> list[IncludeRef]:
    """The YAML includes ``node`` stands for, outermost first (``include`` then its ``inner``s).

    A nested merged mapping carries its included side's include; that include is reached through
    the included side, so it is not listed twice.
    """
    ref = node.include
    if (
        isinstance(node, ConfigMapping)
        and node.layers is not None
        and ref is node.layers.base.include
    ):
        ref = None
    refs: list[IncludeRef] = []
    while ref is not None:
        if ref.kind is IncludeKind.YAML:
            refs.append(ref)
        ref = ref.inner
    return refs


def _holders(
    node: ConfigNode,
    keys: tuple[str, ...],
    out: list[_Holder],
    *,
    parent: ConfigMapping | ConfigSequence | None,
    key: str | int | None,
    over: bool = False,
    depth: int = 0,
    lookup: ConfigMapping | None = None,
    partial: bool = False,
) -> int:
    """Append to ``out`` the holder chain from ``node`` along ``keys[depth:]``; return how many
    keys it consumed.

    Each node is appended once per YAML include it stands for (outermost first), or once. A merged
    mapping continues with the side holding A's content: its override side when that holds every
    remaining key (A written in the override, 4.3), else its included side, whose own include (if
    any) has its directive at its file's root. Below the override side the directive of a child is
    looked up through the merged mapping (``lookup``). With ``partial`` the descent stops at the
    first key missing or not leading to a mapping (an override side descended as far as it holds
    the keys); without it every key exists.
    """
    refs = _include_chain(node)
    if refs:
        for index, ref in enumerate(refs):
            first = index == 0
            out.append(
                _Holder(
                    node=node,
                    include=ref,
                    parent=parent if first else None,
                    key=key if first else None,
                    over=over and first,
                    depth=depth,
                )
            )
    else:
        out.append(_Holder(node=node, include=None, parent=parent, key=key, over=over, depth=depth))
    rest = keys[depth:]
    if isinstance(node, ConfigMapping) and node.layers is not None:
        layers = node.layers
        if _holds(layers.over, rest):
            return _holders(
                layers.over,
                keys,
                out,
                parent=parent,
                key=key,
                over=True,
                depth=depth,
                lookup=node,
                partial=partial,
            )
        return _holders(layers.base, keys, out, parent=None, key=None, depth=depth, partial=partial)
    if not rest:
        return depth
    segment = rest[0]
    child: ConfigNode
    child_key: str | int
    if isinstance(node, ConfigMapping):
        entry = node.get(segment)
        if entry is None or (partial and not isinstance(entry.value, ConfigMapping)):
            if partial:
                return depth
            raise ValueError(f"no key {segment!r} on the way to A")  # pragma: no cover
        child, child_key = entry.value, segment
    elif isinstance(node, ConfigSequence) and not partial:
        child, child_key = node.items[int(segment)], int(segment)
    elif partial:
        return depth
    else:  # pragma: no cover - a path walk never continues below a scalar
        raise TypeError("a path walk never continues below a scalar")
    return _holders(
        child,
        keys,
        out,
        parent=lookup if lookup is not None else node,
        key=child_key,
        depth=depth + 1,
        partial=partial,
    )


def _holds(mapping: ConfigMapping, keys: tuple[str, ...]) -> bool:
    """Whether ``keys`` all exist below ``mapping`` (A's content is written there)."""
    node: ConfigNode = mapping
    for segment in keys:
        if isinstance(node, ConfigMapping):
            entry = node.get(segment)
            if entry is None:
                return False
            node = entry.value
        elif isinstance(node, ConfigSequence) and is_index_segment(segment):
            index = int(segment)
            if index >= len(node.items):
                return False
            node = node.items[index]
        else:
            return False
    return True


def _walk(holders: list[_Holder], keys: tuple[str, ...]) -> KeyPlacement:
    """Walk the holder chain up from A's layer (innermost first) and stop at the first element
    that is a YAML include root (its ``$ref`` mapping, or a conversion, prefix the keys from it to
    A), an override side or a list item (the written mapping holding A's content, no prefix).
    Reaching the top of the chain also places into that mapping."""
    last = holders[-1].node
    if not isinstance(last, ConfigMapping):  # pragma: no cover - A is always a mapping
        raise TypeError("new keys are placed below a mapping")
    for holder in reversed(holders):
        prefix = keys[holder.depth :]
        include = holder.include
        if include is not None:
            if include.form is not None:
                return _into_form(include.form, prefix)
            site = IncludeSite(
                node=holder.node,
                parent=holder.parent,
                key=holder.key,
                include=None if include is holder.node.include else include,
            )
            return KeyPlacement(mapping=None, convert=site, prefix=prefix)
        if holder.over or isinstance(holder.parent, ConfigSequence):
            break
    return KeyPlacement(mapping=last, convert=None, prefix=())


def _into_form(form: ConfigMapping, prefix: tuple[str, ...]) -> KeyPlacement:
    """Place new keys, below the keys ``prefix``, into the ``$ref`` mapping ``form``.

    The form is descended through the prefix keys it already holds as mappings and the walk is
    rerun on that sub-chain, never above the form (4.3, 4.4: an existing override mapping takes
    the keys instead of a duplicate); the keys it lacks stay the prefix.
    """
    holders: list[_Holder] = []
    consumed = _holders(form, prefix, holders, parent=None, key=None, partial=True)
    placement = _walk(holders, prefix[:consumed])
    return dataclasses.replace(placement, prefix=placement.prefix + prefix[consumed:])


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
