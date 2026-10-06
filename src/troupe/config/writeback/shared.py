"""SharedPlaces: the intended configuration of a set whose written changes are reached at several
places (config-include-overrides Requirement 11, requirements choices 17 and 18, research.md
Decisions 11 and 12).

The placement rules may direct a change into a file that is included at more than one place in the
configuration. The change is written once, in that file, and then appears at every place that
reaches its written location, merged with that place's own overrides (an outer override still wins
there), like any other content of the file. The strict ordered read-back
(:func:`~troupe.config.writeback.verify.verify_patch`) therefore compares against an intended
configuration that holds the change at every such place, and history records the resulting leaf
changes at each (11.2, 11.3).

Anchors work the same way (config-aliases 4.1, 4.2, 4.4, 10.2, research.md Decision 2). A change
at an anchored node is written once, at the anchor. An alias place carries the anchored node's
origins, so it is rebuilt from the same written locations and shows the change; a mapping with a
merge key is rebuilt from the mapping as written and merged again (own keys first, as the loader's
``merge_mapping``), so the change also appears in every mapping that merges the anchored node,
unless that mapping writes the key itself. A replacement of the anchored node itself is recorded at
its parent's slot, which an alias place does not share, so it is also recorded at the anchored
node's own written location (:attr:`WrittenChanges.anchored`), and every alias place of that node
(including a merge key's alias) is rebuilt as the replacement.

How it works. The planner records every change it plans as a semantic edit of a written location
(:class:`WrittenChanges`): the value of an entry or item of a written mapping or list replaced,
entries or items removed or added, a scalar-form include string converted to mapping form with
overrides, or a text-include target file given new content. Diamond includes are loaded once per
place, as separate node objects, so a written collection is identified by its file's real path
and span, an include string by its own file and span, and a text file by its real path.

:func:`with_other_places` then recomputes the plain configuration from the pre-write model twice,
once as loaded and once with every recorded edit applied wherever its location is reached
(:class:`_Rebuild`, which merges the layers of every merged mapping again by the deep-merge rules
1.2/1.3, so each place's own overrides keep precedence and merged key order). The intended
configuration is ``request.intended`` along PATH (the requested change at its own place stays
what the request asked for); at every other path where the two recomputations differ, the edited
recomputation is taken. A change in a file reached at one place only therefore leaves
``request.intended`` exactly as it was.

One VALUE may ask for the same written location at two places (11.4, 11.5): agreeing values are
recorded, and planned, once; a value differing from one already asked there is refused (exit 3,
:data:`CONFLICT`, naming PATH and the file), since picking one would silently drop the other.

Off-PATH changes can only be replicas: every recorded location must be reached at PATH's own place
(in the layers of a node from the root along PATH, or at or below PATH); a recorded location that
is not is refused (exit 3) naming PATH and its file. A new YAML include from VALUE carries no
content; where another place merges it with a mapping override, its target is loaded from the
pre-write files (:func:`~troupe.config.loader.load_include`, relative to the file it is written
in) and merged as ``deep_merge`` does (1.2, 1.7); a target that cannot be loaded is refused as
the read-back would refuse it.

Nothing here reads the patched texts: the intended configuration comes only from the pre-write
document and the plan's edits, so the read-back still proves the written texts.
"""

import dataclasses
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, NoReturn

from troupe.config.constants import INCLUDE_PREFIX, MERGE_KEY, YAML_INCLUDE_SUFFIXES
from troupe.config.errors import ConfigLoadError, WriteRefusedError
from troupe.config.loader import load_include
from troupe.config.merge_keys import merged_pairs
from troupe.config.model import (
    ConfigDocument,
    ConfigMapping,
    ConfigNode,
    ConfigScalar,
    ConfigSequence,
    IncludeKind,
    IncludeRef,
    Origin,
    SourceFile,
)
from troupe.config.overrides import is_ref_key
from troupe.config.paths import ConfigPath
from troupe.config.writeback.compare import (
    IncludeDirective,
    PlainMapping,
    PlainValue,
    new_to_plain,
    node_to_plain,
    plain_equal,
)
from troupe.config.writeback.value import NewValue

type WrittenLocation = tuple[Path, int, int]
"""A written location: the real path of its file and the start and end of its span (the identity
of a written collection or include string, whatever the place it is loaded at)."""

type _Slot = tuple[WrittenLocation, str | int]
"""An entry (key) of a written mapping or an item (index) of a written list."""

type WrittenCollection = ConfigMapping | ConfigSequence
"""A written mapping or list (never a merged mapping)."""


type _Location = WrittenLocation | Path
"""A written collection or include string (:data:`WrittenLocation`), or a text file (its real
path)."""


def written_location(origin: Origin) -> WrittenLocation:
    """The written location of a node, mapping or include string from its origin."""
    return (origin.file.real_path, origin.span.start, origin.span.end)


@dataclass(frozen=True)
class _NewInclude(IncludeDirective):
    """A contentless include directive from VALUE, with the file it is written in, so that its
    target can be loaded (from the pre-write files) when another place merges it (1.2, 1.7)."""

    including: SourceFile | None = field(default=None, compare=False)


def _mark(plain: PlainValue, including: SourceFile) -> PlainValue:
    """``plain`` (from VALUE) with every contentless directive marked as written in
    ``including``."""
    if isinstance(plain, IncludeDirective):
        if plain.content is not None:  # pragma: no cover - VALUE directives have no content
            return plain
        overrides = plain.overrides
        if overrides is not None:
            overrides = {key: _mark(value, including) for key, value in overrides.items()}
        return _NewInclude(plain.text, None, plain.kind, overrides, including=including)
    if isinstance(plain, dict):
        return {key: _mark(value, including) for key, value in plain.items()}
    if isinstance(plain, list):
        return [_mark(item, including) for item in plain]
    return plain


CONFLICT: Final = (
    "this change cannot be written: two places that include this file would need different "
    "values at the same written location"
)
"""The refusal of one set asking for two values at one written location (11.5, Decision 12)."""


@dataclass
class WrittenChanges:
    """The planned changes of one set, each keyed by the written location it changes.

    A file included at several places is loaded once per place, so one VALUE may ask for the same
    written location at two places (11.4, 11.5): agreeing values are recorded once, and a value
    differing from one already recorded there is refused (:data:`CONFLICT`, exit 3, naming
    ``path`` and the file).
    """

    replaced: dict[_Slot, PlainValue] = field(default_factory=dict)
    """New values of entries of written mappings and items of written lists."""
    anchored: dict[WrittenLocation, PlainValue] = field(default_factory=dict)
    """The new values of replaced anchored nodes, by the anchored node's own written location,
    for its alias places (config-aliases 4.1, 4.5, 10.2)."""
    removed: dict[WrittenLocation, set[str | int]] = field(default_factory=dict)
    """Keys (mappings) or indexes (lists) deleted from written collections."""
    added: dict[WrittenLocation, list[tuple[str | None, PlainValue]]] = field(default_factory=dict)
    """New entries (``None`` key: list items) after the last entry of written collections."""
    converted: dict[WrittenLocation, PlainMapping] = field(default_factory=dict)
    """Overrides of scalar-form include strings converted to mapping form."""
    texts: dict[Path, str] = field(default_factory=dict)
    """New contents of text-include target files."""
    files: dict[_Location, Path] = field(default_factory=dict)
    """Every changed location with the path of its file as opened (for messages)."""
    claimed: dict[_Slot, PlainValue] = field(default_factory=dict)
    """The value asked for each value slot the planner changes (:meth:`claim`)."""
    path: ConfigPath | None = None
    """PATH, named by a refusal."""

    def claim(self, holder: WrittenCollection, key: str | int, value: NewValue) -> bool:
        """Claim the value slot ``key`` of the written ``holder`` for ``value`` before planning
        its change: ``True`` the first time, ``False`` when it was already claimed for an equal
        value (the change is planned once, 11.4); a different value is refused (11.5)."""
        slot = (written_location(holder.origin), key)
        plain = new_to_plain(value)
        if slot not in self.claimed:
            self.claimed[slot] = plain
            return True
        if not plain_equal(self.claimed[slot], plain, ordered=True):
            self._conflict(holder.origin.file.path)
        return False

    def replace(self, holder: WrittenCollection, key: str | int, value: NewValue) -> None:
        """The value at ``key`` of the written ``holder`` becomes ``value``."""
        where = self._at(holder.origin)
        plain = _mark(new_to_plain(value), holder.origin.file)
        replaced = self.replaced
        if (where, key) in replaced and not plain_equal(
            replaced[(where, key)], plain, ordered=True
        ):
            self._conflict(holder.origin.file.path)
        self.replaced[(where, key)] = plain
        anchored = _anchored_at(holder, key)
        if anchored is not None:
            # The new value keeps the anchor property (4.3), so every alias place reads it.
            self.anchored[written_location(anchored.origin)] = plain

    def remove(self, holder: WrittenCollection, key: str | int) -> None:
        """The entry ``key`` (or item index) of the written ``holder`` is deleted."""
        self.removed.setdefault(self._at(holder.origin), set()).add(key)

    def add(
        self, holder: WrittenCollection, entries: Sequence[tuple[str | None, NewValue]]
    ) -> None:
        """``entries`` are added after the last entry of the written ``holder``; a key added
        twice with two mappings gets their entries merged (as the planner merges them), with an
        equal value is kept once, and with another value is refused (11.4, 11.5)."""
        added = self.added.setdefault(self._at(holder.origin), [])
        for key, value in entries:
            plain = _mark(new_to_plain(value), holder.origin.file)
            if not _add_entry(added, key, plain):
                self._conflict(holder.origin.file.path)

    def convert(self, include: IncludeRef, entries: Sequence[tuple[str, NewValue]]) -> None:
        """The scalar-form ``include`` is converted to mapping form with ``entries`` as
        overrides (added to any already recorded for it, as :meth:`add` adds them)."""
        overrides = self.converted.setdefault(self._at(include.directive), {})
        for key, value in entries:
            plain = _mark(new_to_plain(value), include.directive.file)
            if key in overrides:
                agreed = _agreed(overrides[key], plain)
                if agreed is None:
                    self._conflict(include.directive.file.path)
                plain = agreed
            overrides[key] = plain

    def text(self, target: SourceFile, content: str) -> None:
        """The text-include target file ``target`` gets ``content`` (another content asked for
        it at another place is refused, 11.5)."""
        previous = self.texts.get(target.real_path)
        if previous is not None and previous != content:
            self._conflict(target.path)
        self.texts[target.real_path] = content
        self.files[target.real_path] = target.path

    def _at(self, origin: Origin) -> WrittenLocation:
        where = written_location(origin)
        self.files[where] = origin.file.path
        return where

    def _conflict(self, file: Path) -> NoReturn:
        raise WriteRefusedError(
            CONFLICT, file=file, path=None if self.path is None else str(self.path)
        )


def _anchored_at(holder: WrittenCollection, key: str | int) -> ConfigNode | None:
    """The anchored node written at ``key`` of ``holder`` (not an alias place, which also carries
    its anchored node's ``anchor``, and not an include root, which is never replaced in its slot),
    or ``None``."""
    if isinstance(holder, ConfigMapping):
        entry = holder.get(str(key))
        node = None if entry is None else entry.value
    else:
        index = int(key)
        node = holder.items[index] if index < len(holder.items) else None
    if node is None or node.alias is not None or node.anchor is None or node.include is not None:
        return None
    return node


def _add_entry(
    added: list[tuple[str | None, PlainValue]], key: str | None, value: PlainValue
) -> bool:
    """Add one new entry to ``added``; ``False`` when ``key`` is there with a conflicting value."""
    if key is not None:
        for index, (seen, previous) in enumerate(added):
            if seen == key:
                agreed = _agreed(previous, value)
                if agreed is None:
                    return False
                added[index] = (key, agreed)
                return True
    added.append((key, value))
    return True


def _agreed(previous: PlainValue, value: PlainValue) -> PlainValue | None:
    """Two new values asked for one new key: two mappings merged by key (first-seen order), an
    equal value kept once; ``None`` when they conflict (11.4, 11.5)."""
    if isinstance(previous, dict) and isinstance(value, dict):
        merged = dict(previous)
        for key, item in value.items():
            if key in merged:
                agreed = _agreed(merged[key], item)
                if agreed is None:
                    return None
                item = agreed
            merged[key] = item
        return merged
    return previous if plain_equal(previous, value, ordered=True) else None


def with_other_places(
    document: ConfigDocument, intended: PlainMapping, path: ConfigPath, changes: WrittenChanges
) -> PlainMapping:
    """Return ``intended`` with every change of ``changes`` also at every other place of the
    merged configuration that reaches its written location (11.2).

    Every changed location must be reached at PATH's own place (on the way from the root to PATH,
    or at or below PATH), so that a value changed off PATH can only be a replica of the requested
    change; any other is refused (:class:`WriteRefusedError`, exit 3, naming PATH and the file).
    Along PATH ``intended`` is kept as it is; at every path off PATH whose plain value the
    changes alter, the value recomputed with the changes (merged with that place's overrides)
    is used. A new YAML include that another place merges with its overrides is loaded from the
    pre-write files; one that cannot be loaded is refused as the read-back would refuse it.
    """
    reached = _reached(document.root, path)
    for location, file in changes.files.items():
        if location not in reached:
            raise WriteRefusedError(
                "this change cannot be written: a planned change of this file is not reached at "
                "the place of PATH",
                file=file,
                path=str(path),
            )
    root = document.root
    before = _root_content(rebuilt_plain(root, WrittenChanges()))
    texts = {file.real_path: file.text for file in document.files}
    after = _root_content(rebuilt_plain(root, changes, _Loading(texts, path)))
    grafted = _graft(intended, before, after, path.segments)
    if not isinstance(grafted, dict):  # pragma: no cover - the root stays a mapping
        raise TypeError("the root must stay a mapping")
    return grafted


def _root_content(plain: PlainValue) -> PlainValue:
    """A root that is a mapping-form include unwrapped to its merged content (as ``root_plain``)."""
    return plain.content if isinstance(plain, IncludeDirective) else plain


class _Absent:
    """No value at a path of a recomputation (distinct from a null value)."""


_ABSENT: Final = _Absent()


def _graft(
    intended: PlainValue,
    before: PlainValue | _Absent,
    after: PlainValue | _Absent,
    segments: Sequence[str],
) -> PlainValue:
    """``intended`` along ``segments``; off them, ``after`` wherever it differs from ``before``."""
    if not segments or isinstance(before, _Absent) or isinstance(after, _Absent):
        return intended
    if isinstance(intended, IncludeDirective):
        if (
            intended.content is None
            or not isinstance(before, IncludeDirective)
            or not isinstance(after, IncludeDirective)
        ):
            return intended
        content = _graft(intended.content, before.content, after.content, segments)
        return dataclasses.replace(intended, content=content)
    segment, rest = segments[0], segments[1:]
    if isinstance(intended, dict) and isinstance(before, dict) and isinstance(after, dict):
        return {
            key: (
                _graft(value, before.get(key, _ABSENT), after.get(key, _ABSENT), rest)
                if key == segment
                else _changed(value, before.get(key, _ABSENT), after.get(key, _ABSENT))
            )
            for key, value in intended.items()
        }
    if isinstance(intended, list) and isinstance(before, list) and isinstance(after, list):
        if len(before) != len(after):
            return intended
        items: list[PlainValue] = []
        for index, value in enumerate(intended):
            old = before[index] if index < len(before) else _ABSENT
            new = after[index] if index < len(after) else _ABSENT
            if str(index) == segment:
                items.append(_graft(value, old, new, rest))
            else:
                items.append(_changed(value, old, new))
        return items
    return intended


def _changed(
    intended: PlainValue, before: PlainValue | _Absent, after: PlainValue | _Absent
) -> PlainValue:
    """``after`` when the changes alter this off-PATH value (a null value included, as at an
    alias place of an anchored node replaced by null), else ``intended``."""
    if isinstance(before, _Absent) or isinstance(after, _Absent):
        return intended
    if plain_equal(before, after, ordered=True):
        return intended
    return after


def _reached(root: ConfigMapping, path: ConfigPath) -> set[_Location]:
    """The written locations reached at PATH's own place: in the layers of every node from the
    root along PATH, and of every node at or below PATH when it exists."""
    found: set[_Location] = set()
    node: ConfigNode = root
    _layer_locations(node, found)
    for segment in path.segments:
        child: ConfigNode | None = None
        if isinstance(node, ConfigMapping):
            entry = node.get(segment)
            child = None if entry is None else entry.value
        elif isinstance(node, ConfigSequence) and segment.isdigit():
            index = int(segment)
            child = node.items[index] if index < len(node.items) else None
        if child is None:
            return found
        node = child
        _layer_locations(node, found)
    _subtree_locations(node, found)
    return found


def _layer_locations(node: ConfigNode, found: set[_Location]) -> None:
    """Add the written locations ``node`` is built from: itself, the layers of a merged mapping,
    the include strings, ``$ref`` mappings and text targets of the includes it stands for."""
    found.add(written_location(node.origin))
    for ref in _chain(node.include):
        found.add(written_location(ref.directive))
        if ref.kind is IncludeKind.TEXT:
            found.add(ref.target.real_path)
        if ref.form is not None:
            found.add(written_location(ref.form.origin))
    if isinstance(node, ConfigMapping) and node.layers is not None:
        _layer_locations(node.layers.base, found)
        _layer_locations(node.layers.over, found)


def _subtree_locations(node: ConfigNode, found: set[_Location]) -> None:
    _layer_locations(node, found)
    if isinstance(node, ConfigMapping):
        for entry in node.entries:
            _subtree_locations(entry.value, found)
    elif isinstance(node, ConfigSequence):
        for item in node.items:
            _subtree_locations(item, found)


class _Loading:
    """Loads the content of new YAML includes from the pre-write files, once each."""

    def __init__(self, texts: dict[Path, str], path: ConfigPath) -> None:
        self._texts = texts
        self._path = path
        self._loaded: dict[tuple[Path, str], PlainValue] = {}

    def content(self, include: _NewInclude) -> PlainValue:
        """The content of ``include`` with its VALUE overrides merged; a load error is refused
        naming PATH and the file holding the include (exit 3)."""
        including = include.including
        if including is None:  # pragma: no cover - every new include is marked
            raise ValueError("a new include without its file")
        spec = include.text[len(INCLUDE_PREFIX) :]
        key = (including.real_path, spec)
        if key not in self._loaded:
            try:
                node = load_include(including, spec, self._texts)
            except ConfigLoadError as error:
                raise WriteRefusedError(
                    f"written value cannot be read back: {error}",
                    file=including.path,
                    path=str(self._path),
                ) from error
            self._loaded[key] = _unwrapped(node_to_plain(node))
        content = self._loaded[key]
        if include.overrides and isinstance(content, dict):
            content = _merge(content, include.overrides, self.mergeable)
        return content

    def mergeable(self, plain: PlainValue) -> PlainValue:
        """``plain`` unwrapped to its content, a new YAML include loaded."""
        if isinstance(plain, _NewInclude) and plain.content is None and _is_yaml(plain):
            return self.content(plain)
        return _unwrapped(plain)


def _is_yaml(include: IncludeDirective) -> bool:
    return include.text.endswith(YAML_INCLUDE_SUFFIXES)


def _may_be_mapping(plain: PlainValue) -> bool:
    if isinstance(plain, IncludeDirective):
        if plain.content is None:
            return isinstance(plain, _NewInclude) and _is_yaml(plain)
        return isinstance(plain.content, dict)
    return isinstance(plain, dict)


def rebuilt_plain(
    node: ConfigNode, changes: WrittenChanges, loading: "_Loading | None" = None
) -> PlainValue:
    """The plain tree of ``node`` recomputed from its written layers with ``changes`` applied at
    their written locations; with no changes it equals ``node_to_plain(node)``."""
    return _Rebuild(changes, loading).plain(node)


class _Rebuild:
    """The plain tree of a loaded node recomputed from its written layers, with the changes
    applied at their written locations (no changes: equal to ``node_to_plain``)."""

    def __init__(self, changes: WrittenChanges, loading: _Loading | None) -> None:
        self._changes = changes
        self._mergeable: Callable[[PlainValue], PlainValue] = (
            _unwrapped if loading is None else loading.mergeable
        )

    def plain(self, node: ConfigNode) -> PlainValue:
        """As ``node_to_plain``: the content, wrapped as a directive at an include root; an alias
        place of a replaced anchored node is the replacement (config-aliases 4.1, 4.5)."""
        alias = node.alias
        if alias is not None and alias.anchored.include is None:
            anchored = written_location(alias.anchored.origin)
            if anchored in self._changes.anchored:
                return self._changes.anchored[anchored]
        content = self._content(node)
        include = node.include
        if include is None:
            return content
        overrides = self._overrides(include)
        return IncludeDirective(INCLUDE_PREFIX + include.spec, content, include.kind, overrides)

    def _content(self, node: ConfigNode) -> PlainValue:
        if isinstance(node, ConfigScalar):
            for ref in _chain(node.include):
                if ref.kind is IncludeKind.TEXT and ref.target.real_path in self._changes.texts:
                    return self._changes.texts[ref.target.real_path]
            return node.value
        if isinstance(node, ConfigMapping):
            layers = node.layers
            if layers is not None:
                # Merged again from both written sides, so each side's changes take effect
                # with this place's own overrides keeping precedence (1.2, 1.3).
                base = _unwrapped(self.plain(layers.base))
                over = _unwrapped(self._content(layers.over))
                if not (isinstance(base, dict) and isinstance(over, dict)):
                    return base  # pragma: no cover - a merge always has two mappings
                return _merge(base, over, self._mergeable)
            if node.merge is not None:
                # Merged again from the mapping as written, so a change at a merged anchored
                # node (or in an inline source) takes effect here, below the own keys (2.1, 2.2).
                content: PlainValue = _merge_key(self._entries(node.merge.written))
            else:
                content = self._entries(node)
        else:
            content = self._items(node)
        # An unmerged include root has no overrides as loaded; the changes may give its ``$ref``
        # mapping (or a converted include string) some, merged innermost include first.
        for ref in reversed(list(_chain(node.include))):
            overrides = self._overrides(ref)
            if overrides and isinstance(content, dict):
                content = _merge(content, overrides, self._mergeable)
        return content

    def _overrides(self, include: IncludeRef) -> PlainMapping:
        """The overrides of ``include`` after the changes: its ``$ref`` mapping's entries, or
        those of a converted include string."""
        if include.form is not None:
            return self._entries(include.form)
        return dict(self._changes.converted.get(written_location(include.directive), {}))

    def _entries(self, mapping: ConfigMapping) -> PlainMapping:
        """The entries of the written ``mapping`` (``$ref`` left out) after the changes."""
        where = written_location(mapping.origin)
        removed = self._changes.removed.get(where, set())
        result: PlainMapping = {}
        for entry in mapping.entries:
            if is_ref_key(entry.key) or entry.key in removed:
                continue
            slot = (where, entry.key)
            replaced = self._changes.replaced
            result[entry.key] = replaced[slot] if slot in replaced else self.plain(entry.value)
        for key, value in self._changes.added.get(where, []):
            if key is not None:
                result[key] = value
        return result

    def _items(self, sequence: ConfigSequence) -> list[PlainValue]:
        """The items of the written ``sequence`` after the changes."""
        where = written_location(sequence.origin)
        removed = self._changes.removed.get(where, set())
        items: list[PlainValue] = []
        for index, item in enumerate(sequence.items):
            if index in removed:
                continue
            slot = (where, index)
            replaced = self._changes.replaced
            items.append(replaced[slot] if slot in replaced else self.plain(item))
        items.extend(value for key, value in self._changes.added.get(where, []) if key is None)
        return items


def _merge_key(written: PlainMapping) -> PlainMapping:
    """The merged view of a mapping as written, from its plain entries: the own entries in
    written order, and in place of the ``<<`` entry the pairs that
    :func:`~troupe.config.merge_keys.merged_pairs` merges in from its sources (a mapping or a
    list of mappings), as the loader's ``merge_mapping`` (config-aliases 2.1, 2.2)."""
    own = [key for key in written if key != MERGE_KEY]
    result: PlainMapping = {}
    for key, value in written.items():
        if key != MERGE_KEY:
            result[key] = value
            continue
        sources = [_source(source) for source in (value if isinstance(value, list) else [value])]
        result.update(merged_pairs(own, (source.items() for source in sources)))
    return result


def _source(plain: PlainValue) -> PlainMapping:
    """A merge source's mapping. A source that is not one (an anchored mapping replaced by
    another kind at its anchor) merges nothing here; the read-back refuses it on loading."""
    source = _unwrapped(plain)
    return source if isinstance(source, dict) else {}


def _chain(ref: IncludeRef | None) -> Iterator[IncludeRef]:
    """The includes a node stands for, outermost first."""
    while ref is not None:
        yield ref
        ref = ref.inner


def _unwrapped(plain: PlainValue) -> PlainValue:
    if isinstance(plain, IncludeDirective) and plain.content is not None:
        return plain.content
    return plain


def _merge(
    base: PlainMapping,
    over: PlainMapping,
    mergeable: Callable[[PlainValue], PlainValue] = _unwrapped,
) -> PlainMapping:
    """The deep merge of plain mappings (1.2, 1.3), as the loader's ``deep_merge``: ``base``'s
    keys in order, each overridden one replaced in place (two mappings merged recursively, keeping
    the included value's directive), then the keys only in ``over`` in their order.

    ``mergeable`` gives a value's mapping content (an include unwrapped; a new YAML include
    loaded), asked only when both values may be mappings."""
    result: PlainMapping = {}
    for key, value in base.items():
        if key not in over:
            result[key] = value
            continue
        overriding = over[key]
        if not (_may_be_mapping(value) and _may_be_mapping(overriding)):
            result[key] = overriding
            continue
        included, overrides = mergeable(value), mergeable(overriding)
        if isinstance(included, dict) and isinstance(overrides, dict):
            merged = _merge(included, overrides, mergeable)
            if isinstance(value, IncludeDirective):
                result[key] = dataclasses.replace(value, content=merged)
            else:
                result[key] = merged
        else:
            result[key] = overriding
    for key, value in over.items():
        if key not in base:
            result[key] = value
    return result
