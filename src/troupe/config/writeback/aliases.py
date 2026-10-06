"""Guard: refuse, expand or proceed for a set reached through an alias or a merge key
(config-aliases Requirements 5.1, 5.2, 6.3, 8.1, 8.2, 9.2; research.md Decision 5).

:func:`alias_sites` walks PATH from the root to its node, or to the deepest existing ancestor of a
PATH that does not exist yet, checking every node on the way including the node at PATH itself (a
PATH naming an alias is reached through it, requirements choice 2). For a replacement it then
searches the subtree at PATH, but only the alias places and merged entries whose plain value the
intended configuration changes or removes, in document order and never below an alias place (outer
aliases come first, 8.1, 8.2). The first one met decides:

* a merged entry (a value a mapping receives from a merge key) is refused with
  :data:`MERGE_REFUSAL` whether or not ``--expand-alias`` is given (9.2);
* an alias place is the :class:`AliasSite` to expand with ``--expand-alias``, and otherwise is
  refused with :data:`ALIAS_REFUSAL` (5.1, 5.2);
* nothing met means proceed: ``()`` (6.3).

Where an alias is seen. An alias place records ``alias`` on its root (which also carries the
anchored node's ``anchor``, so ``alias`` is checked first). The walk follows the nodes as written
(see :class:`_Guard`): a merged mapping (an include with overrides) only repeats its included side's
provenance, so it is split into its sides, and only the sides that hold the next key of PATH are
followed; where PATH ends, every side counts. An alias used as a ``$ref`` value is recorded only on
the ``$ref`` scalar inside ``include.form``; it counts for the include's included side only, so a
key written only in the include's own overrides is not reached through it (requirements, "reached
through an alias": values written in the mapping itself are not).

The guard runs after target resolution's own refusals and before any planner refusal, and before
the no-op check, so a no-op through an alias is refused too (5.3).

:func:`anchor_uses` indexes the aliases of the configuration by the written location of the
anchored node they stand for (used by the planner's check for removing a used anchor, 4.6).
"""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Final, NoReturn

from troupe.config.constants import INCLUDE_REF_KEY, MERGE_KEY
from troupe.config.errors import WriteRefusedError
from troupe.config.model import (
    AliasRef,
    ConfigDocument,
    ConfigMapping,
    ConfigNode,
    ConfigScalar,
    ConfigSequence,
    IncludeRef,
)
from troupe.config.overrides import is_ref_key
from troupe.config.paths import ROOT, ConfigPath
from troupe.config.resolve import walk_path
from troupe.config.writeback.compare import (
    IncludeDirective,
    PlainValue,
    node_to_plain,
    plain_equal,
)
from troupe.config.writeback.shared import WrittenLocation, written_location
from troupe.config.writeback.target import ReplaceTarget, SetRequest

ALIAS_REFUSAL: Final = (
    "value is shared through alias '*{name}' (anchor '&{name}'); setting it here would change "
    "every place that uses the anchor; use --expand-alias to give this place its own copy"
)
"""The problem of a set reached through an alias without ``--expand-alias`` (5.1, 5.2)."""

MERGE_REFUSAL: Final = (
    "value comes from merge key '" + MERGE_KEY + "' at {key_path}; merge keys are never written, "
    "and --expand-alias cannot expand them"
)
"""The problem of a set reaching a value received from a merge key (9.2); ``key_path`` is the
key path of the ``<<`` key."""


@dataclass(frozen=True)
class AliasSite:
    """An alias to expand: the alias place, where its ``*name`` token is written, and its path."""

    place: ConfigNode
    """The alias place (``place.alias`` is set)."""
    parent: ConfigMapping | ConfigSequence
    """The written mapping or list holding the alias token (for an alias as a ``$ref`` value, the
    include's ``$ref`` mapping)."""
    key: str | int
    """The key (``str``) or list index (``int``) of the alias in ``parent``."""
    path: ConfigPath
    """The path of the alias place (``<include root>.$ref`` for an alias as a ``$ref`` value)."""


def alias_refusal(alias: AliasRef, path: ConfigPath) -> WriteRefusedError:
    """The refusal (exit 3) of a set at ``path`` reached through ``alias`` (5.1, 5.2)."""
    return WriteRefusedError(
        ALIAS_REFUSAL.format(name=alias.name), file=alias.token.file.path, path=str(path)
    )


def merge_problem(holder_path: ConfigPath) -> str:
    """The problem text of :data:`MERGE_REFUSAL` for the merge key of the mapping at
    ``holder_path`` (9.2)."""
    return MERGE_REFUSAL.format(key_path=holder_path.child(MERGE_KEY))


def _merge_error(
    holder: ConfigMapping, holder_path: ConfigPath, path: ConfigPath
) -> WriteRefusedError:
    """The refusal (exit 3) of a set at ``path`` reaching a value ``holder`` receives from its
    merge key, naming the file of the ``<<`` key (9.2)."""
    file = holder.origin.file.path if holder.merge is None else holder.merge.key_origin.file.path
    return WriteRefusedError(merge_problem(holder_path), file=file, path=str(path))


def alias_sites(
    document: ConfigDocument, request: SetRequest, *, expand: bool
) -> tuple[AliasSite, ...]:
    """Return the alias to expand first for ``request``, ``()`` to proceed, or raise.

    Raises :class:`WriteRefusedError` (exit 3) at the first merged entry met (9.2, with or
    without ``expand``), and at the first alias place met when ``expand`` is false (5.1, 5.2).
    With ``expand`` the first alias place met is returned as the only site; the setter expands it,
    reloads and asks again, so nested aliases are expanded outermost first (8.1, 8.2).
    """
    try:
        _Guard(request.path, expand=expand).run(document, request)
    except _Found as found:
        return (found.site,)
    return ()


class _Missing:
    """A value absent from the intended configuration (removed, or not known)."""


_MISSING: Final = _Missing()

type _Intended = PlainValue | _Missing


class _Found(Exception):
    """The first alias place met, with ``--expand-alias``: ends the search."""

    def __init__(self, site: AliasSite) -> None:
        super().__init__(str(site.path))
        self.site = site


@dataclass(frozen=True)
class _Strand:
    """A node as written in one layer that holds (part of) the value at a path, with the written
    collection it is written in and its key there (``None`` for the root)."""

    node: ConfigNode
    parent: ConfigMapping | ConfigSequence | None
    key: str | int | None


class _Guard:
    """The walk of :func:`alias_sites` over the written layers that hold the values at PATH.

    The walk follows **strands**: the nodes as written that hold a value. A merged mapping (an
    include with overrides) is split into its sides, and only the side or sides that hold the
    next key are followed (a key both sides write as mappings is followed on both, as
    ``deep_merge`` merges it; a key the override writes otherwise is the override's). An include
    whose ``$ref`` value is an alias is reached through that alias only on its included side.
    Where PATH ends (at its node or its deepest existing ancestor) every side counts.
    """

    def __init__(self, path: ConfigPath, *, expand: bool) -> None:
        self._path = path
        self._expand = expand

    def run(self, document: ConfigDocument, request: SetRequest) -> None:
        walk = walk_path(document.root, self._path)
        strands = [_Strand(document.root, None, None)]
        at = ROOT
        for segment in self._path.segments[: walk.consumed]:
            strands = self._step(strands, at, segment)
            at = at.child(segment)
        for strand in strands:
            self._sides(strand, at, None)
        if isinstance(request.target, ReplaceTarget):
            self._below(strands, request.target.node, at, _at(request.intended, self._path))

    def _met(self, site: AliasSite) -> NoReturn:
        """Decide at the first alias place met: the site with the flag, else the refusal."""
        alias = site.place.alias
        if alias is None:  # pragma: no cover - a site is only made for an alias place
            raise ValueError("an alias site is an alias place")
        if not self._expand:
            raise alias_refusal(alias, self._path)
        raise _Found(site)

    def _step(self, strands: list[_Strand], at: ConfigPath, key: str | int) -> list[_Strand]:
        """The strands of child ``key`` of the value at ``at`` held by ``strands``; meets the
        aliases of the sides that hold it and refuses a merged entry (9.2)."""
        children: list[_Strand] = []
        for strand in strands:
            for holder in self._sides(strand, at, key):
                child = self._child_of(holder, key, at)
                if child is not None:
                    children.append(child)
        return children

    def _sides(
        self, strand: _Strand, at: ConfigPath, key: str | int | None
    ) -> list[ConfigMapping | ConfigSequence]:
        """The written collections of ``strand`` that hold child ``key`` (every side for
        ``None``), meeting on the way the alias place they are reached through, if any."""
        node = strand.node
        if key is not None and not _holds(node, key):
            return []
        if _own_alias(node) and strand.parent is not None and strand.key is not None:
            self._met(AliasSite(place=node, parent=strand.parent, key=strand.key, path=at))
        ref_site = _form_site(node, at)
        if isinstance(node, ConfigMapping) and node.layers is not None:
            base, over = _picked(node, key)
            holders: list[ConfigMapping | ConfigSequence] = []
            if base:
                if ref_site is not None:
                    self._met(ref_site)
                holders += self._sides(
                    _Strand(node.layers.base, strand.parent, strand.key), at, key
                )
            if over:
                holders += self._sides(
                    _Strand(node.layers.over, strand.parent, strand.key), at, key
                )
            return holders
        if ref_site is not None:
            self._met(ref_site)
        if isinstance(node, ConfigScalar):
            return []
        return [node]

    def _child_of(
        self, holder: ConfigMapping | ConfigSequence, key: str | int, at: ConfigPath
    ) -> _Strand | None:
        """The strand of child ``key`` written in ``holder``; refuses an entry ``holder`` receives
        from its merge key (9.2)."""
        if isinstance(holder, ConfigMapping):
            entry = holder.get(str(key))
            if entry is None:
                return None
            if entry.via_merge:
                raise _merge_error(holder, at, self._path)
            return _Strand(entry.value, holder, entry.key)
        index = int(key)
        if index >= len(holder.items):
            return None
        return _Strand(holder.items[index], holder, index)

    def _below(
        self, strands: list[_Strand], node: ConfigNode, at: ConfigPath, intended: _Intended
    ) -> None:
        """Search the children of ``node`` (held by ``strands``) whose plain value changes under
        ``intended``, in document order (5.2, 8.2, 9.2). A child that is removed, replaced by
        another kind or given new keys or items counts on every side; below it, only its changed
        children are searched."""
        for key, child in _children(node):
            child_intended = _child(intended, key)
            current = node_to_plain(child)
            if not isinstance(child_intended, _Missing) and plain_equal(
                current, child_intended, ordered=False
            ):
                continue
            child_strands = self._step(strands, at, key)
            child_at = at.child(key)
            deeper, whole = _descend(current, child_intended)
            if whole:
                for strand in child_strands:
                    self._sides(strand, child_at, None)
            self._below(child_strands, child, child_at, deeper)


def _own_alias(node: ConfigNode) -> bool:
    """Whether ``node`` is itself an alias place (a merged mapping only repeats its included
    side's ``alias``, which counts on that side alone)."""
    if node.alias is None:
        return False
    return not (
        isinstance(node, ConfigMapping)
        and node.layers is not None
        and node.alias is node.layers.base.alias
    )


def _holds(node: ConfigNode, key: str | int) -> bool:
    if isinstance(node, ConfigMapping):
        return node.get(str(key)) is not None
    if isinstance(node, ConfigSequence):
        return int(key) < len(node.items)
    return False


def _picked(node: ConfigMapping, key: str | int | None) -> tuple[bool, bool]:
    """Which sides of the merged mapping ``node`` (included, override) hold child ``key``, by the
    deep-merge rule: both when both write it as mappings, else the override's when it writes
    it, else the included side's. Both for ``None``."""
    layers = node.layers
    if key is None or layers is None:
        return True, True
    base = layers.base.get(str(key))
    over = None if is_ref_key(str(key)) else layers.over.get(str(key))
    if base is not None and over is not None:
        both = isinstance(base.value, ConfigMapping) and isinstance(over.value, ConfigMapping)
        return both, True
    return over is None, over is not None


def _descend(current: PlainValue, intended: _Intended) -> tuple[_Intended, bool]:
    """The intended value to search a changed child's children with, and whether the child
    itself changes as a whole (removed, another kind, another include, or new keys or items)."""
    if isinstance(intended, _Missing):
        return _MISSING, True
    if isinstance(current, IncludeDirective) or isinstance(intended, IncludeDirective):
        if (
            isinstance(current, IncludeDirective)
            and isinstance(intended, IncludeDirective)
            and current.text == intended.text
            and current.content is not None
            and intended.content is not None
        ):
            return _descend(current.content, intended.content)
        return _MISSING, True
    if isinstance(current, dict) and isinstance(intended, dict):
        return intended, any(key not in current for key in intended)
    if isinstance(current, list) and isinstance(intended, list):
        return intended, len(intended) > len(current)
    return _MISSING, True


def _form_site(node: ConfigNode, path: ConfigPath) -> AliasSite | None:
    """The ``$ref`` scalar of ``node``'s include forms (its include and chained inner includes)
    when it is an alias place, or ``None``."""
    for ref in _chain(node.include):
        if ref.form is None:
            continue
        entry = ref.form.get(INCLUDE_REF_KEY)
        if entry is not None and entry.value.alias is not None:
            return AliasSite(
                place=entry.value, parent=ref.form, key=INCLUDE_REF_KEY, path=path.child(entry.key)
            )
    return None


def _chain(ref: IncludeRef | None) -> Iterator[IncludeRef]:
    while ref is not None:
        yield ref
        ref = ref.inner


def _at(plain: PlainValue, path: ConfigPath) -> _Intended:
    """The intended value at ``path`` (which exists there after the set)."""
    value: _Intended = plain
    for segment in path.segments:
        value = _child(value, segment)
    return value


def _child(value: _Intended, key: str | int) -> _Intended:
    """The intended value of child ``key``; :data:`_MISSING` when it is absent or unknown (the
    content of an include written by VALUE)."""
    if isinstance(value, IncludeDirective):
        value = _MISSING if value.content is None else value.content
    if isinstance(value, dict):
        return value.get(str(key), _MISSING)
    if isinstance(value, list):
        index = int(key)
        return value[index] if index < len(value) else _MISSING
    return _MISSING


def _children(node: ConfigNode) -> Iterator[tuple[str | int, ConfigNode]]:
    if isinstance(node, ConfigMapping):
        for entry in node.entries:
            yield entry.key, entry.value
    elif isinstance(node, ConfigSequence):
        yield from enumerate(node.items)


def anchor_uses(document: ConfigDocument) -> Mapping[WrittenLocation, tuple[AliasRef, ...]]:
    """Map the written location of every anchored node used by an alias to its aliases.

    The location is :func:`anchored_location` of the alias's anchored node. Aliases are listed in
    document order, each written ``*name`` token once (an alias inside an anchored node is built
    again at every alias place of that node, with the same token). The walk covers the mappings as
    written behind merge keys (so ``<<: *d`` is a use), both sides of merged mappings and the
    ``$ref`` mappings of includes; it does not descend into alias places, whose tokens are written
    at their anchored node.
    """
    found: dict[WrittenLocation, list[AliasRef]] = {}
    seen: set[WrittenLocation] = set()
    for alias in _aliases(document.root):
        token = written_location(alias.token)
        if token in seen:
            continue
        seen.add(token)
        found.setdefault(anchored_location(alias.anchored), []).append(alias)
    return {location: tuple(aliases) for location, aliases in found.items()}


def anchored_location(node: ConfigNode) -> WrittenLocation:
    """The written location of an anchored node: the ``$ref`` mapping of a mapping-form include
    root anchored there, the include string of another include root, else the node itself."""
    include = node.include
    if include is None:
        return written_location(node.origin)
    if include.form is not None and include.form.anchor is not None:
        return written_location(include.form.origin)
    return written_location(include.directive)


def _aliases(node: ConfigNode) -> Iterator[AliasRef]:
    """Every alias written in or below ``node``, in document order (see :func:`anchor_uses`)."""
    if node.alias is not None:
        yield node.alias
        return
    for ref in _chain(node.include):
        if ref.form is not None:
            for entry in ref.form.entries:
                yield from _aliases(entry.value)
    if isinstance(node, ConfigMapping):
        if node.layers is not None:
            yield from _aliases(node.layers.base)
            yield from _aliases(node.layers.over)
            return
        written = node if node.merge is None else node.merge.written
        for entry in written.entries:
            yield from _aliases(entry.value)
    elif isinstance(node, ConfigSequence):
        for item in node.items:
            yield from _aliases(item)
