"""Load the configuration into the source-mapped model (Requirements 3.1-3.9).

The loader is the only reader of the user's configuration files. It reads bytes read-only,
decodes them strictly as UTF-8 without newline translation (so a leading BOM and every CRLF are
kept), composes exactly one YAML document with ruamel.yaml's round-trip composer and builds the
immutable model of :mod:`troupe.config.model` from the composed nodes.

ruamel.yaml is used only for structure, positions (``start_mark.index``/``end_mark.index`` are
character indices into the composed text) and styles. Plain scalars are typed with
:func:`troupe.config.scalars.resolve_plain_scalar` (YAML 1.2 core schema); quoted and block
scalars are strings. Folded (``>``) values have the U+0007 fold markers that the round-trip
composer inserts removed (3.8).

Unsupported YAML is refused with :class:`ConfigLoadError` naming the file and, where known, the key
path (product-owner decision I; decision H as narrowed by config-aliases 1.3, 1.4 and 2.3):
non-UTF-8 bytes, syntax errors, several documents, duplicate keys, explicit tags other than
``!literal`` on a scalar value, anchors on mapping keys, undefined aliases (a composer error),
aliases used as keys or inside their own anchored node, merge keys whose value cannot be merged,
includes as merge sources and merge keys among ``$ref`` overrides, non-scalar keys and a top-level
node that is not a mapping. Anchors, aliases and merge keys themselves load (step 6). An empty or
comment-only file is an empty block mapping at ``Span(0, 0)``.

Anchors (config-aliases 1.1, 1.5): an anchor ``&name`` on any value node is recorded as
:class:`Anchor` (the name and the span of the property), and the node's own span is its value
without the property (config-aliases research.md, Decision 3): the first key of a block mapping,
the first ``-`` of a block list, the next token for scalars and flow collections, and empty at the
property's end for an empty null. The anchor of an include string or ``$ref`` mapping is recorded
on the included node.

Aliases (config-aliases 1.1-1.5, research.md Decision 2): at each alias the composer recorded, the
anchored node is built again for the alias's path, an **alias place**: it and its descendants carry
the anchored node's origins, and its root records :class:`AliasRef` (the name, the ``*name``
token and the node built at the anchor). Aliases inside an anchored node are found again when it is
rebuilt, includes inside it are included again, and a reused name refers to the most recent
definition before the alias (ruamel resolves each alias to that node). Aliases are file-local: an
alias to an anchor defined only in another file is an undefined alias. An alias used as a mapping
key, and an alias inside its own anchored node, are refused naming the key path.

Merge keys (config-aliases 2.1-2.4, research.md Decision 4): a mapping with a plain
:data:`MERGE_KEY` key has its ``<<`` value built as usual; it must be a mapping, an alias place of a
mapping or a list of those (the merge sources), and no source may be an include (an include string,
a ``$ref`` mapping or an alias of either; nested values of a source may be includes). The loaded
mapping is the merged view of :func:`troupe.config.merge_keys.merge_mapping`, with the mapping as
written kept in ``merge``. A merge key among the overrides of a mapping-form include is refused. A
quoted ``<<`` key is an ordinary key.

Literals (config-remote-schema 4.1-4.3, research.md choices 2 and 21): a scalar value tagged exactly
:data:`LITERAL_TAG` loads as its text, in any style and without plain-scalar typing (an empty one is
``""``; folded values lose their fold markers), and is never an include. The tag's span is recorded
in ``ConfigScalar.literal`` and, like an anchor, is not part of the node's own span (``&a !literal
x`` loads). ``!literal`` on a mapping, a list or a mapping key, ``!literal`` written before an
anchor, and a ``$ref`` value tagged ``!literal`` are refused, as is every other tag.

Includes (4.1-4.7): any string scalar value (any style, never a key, never ``!literal``) starting
with :data:`INCLUDE_PREFIX` is replaced by its target, ``including_file.path.parent / spec``. A
target whose name ends in one of :data:`YAML_INCLUDE_SUFFIXES` is loaded recursively and its root
embedded as if written in place (an empty target is null); any other target is its exact decoded
text as a string and is never parsed. A YAML target already on the include chain from the root is a
cycle. Every produced node carries an :class:`IncludeRef`. Each file is read at most once; a file
included several times (a diamond) is shared and listed once in ``ConfigDocument.files``.

Mapping-form includes (config-include-overrides Requirement 1): a mapping with a key whose text is
:data:`INCLUDE_REF_KEY` (any key style) is an include of that key's value, which is kept as written
(never expanded) and must be an include string. The mapping as written is ``IncludeRef.form``; its
other entries, the overrides, are built as usual (so they may hold includes themselves). A bare
``$ref`` mapping loads exactly as the scalar form of the same string, YAML or text. With overrides,
the value is :func:`troupe.config.overrides.deep_merge` of the included mapping with the form, so
no ``$ref`` key is part of the loaded value. Overrides on a non-YAML target, on a YAML target
whose content is not a mapping, and a ``$ref`` value that is not an include string are refused.
This works wherever a value may be written, including the document root (which must still load
as a mapping).

Nothing is ever created, modified, renamed or deleted (3.6).
"""

import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from ruamel.yaml import YAML
from ruamel.yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode
from ruamel.yaml.tokens import AnchorToken, TagToken

from troupe.config.compose import AliasOccurrence, AliasSlot, ComposedFile, compose_file
from troupe.config.constants import (
    INCLUDE_PREFIX,
    INCLUDE_REF_KEY,
    LITERAL_TAG,
    MERGE_KEY,
    YAML_INCLUDE_SUFFIXES,
)
from troupe.config.errors import ConfigLoadError
from troupe.config.merge_keys import merge_mapping
from troupe.config.model import (
    AliasRef,
    Anchor,
    CollectionStyle,
    ConfigDocument,
    ConfigMapping,
    ConfigNode,
    ConfigScalar,
    ConfigSequence,
    IncludeKind,
    IncludeRef,
    LineEnding,
    MappingEntry,
    Origin,
    ScalarStyle,
    SourceFile,
    Span,
)
from troupe.config.overrides import deep_merge, is_ref_key, override_entries
from troupe.config.paths import ROOT, ConfigPath
from troupe.config.scalars import ScalarValue, resolve_plain_scalar

_SCALAR_STYLES: Final[dict[str | None, ScalarStyle]] = {
    None: ScalarStyle.PLAIN,
    "'": ScalarStyle.SINGLE_QUOTED,
    '"': ScalarStyle.DOUBLE_QUOTED,
    "|": ScalarStyle.LITERAL,
    ">": ScalarStyle.FOLDED,
}
"""ruamel's ``ScalarNode.style`` characters mapped to the model's scalar styles."""

_FOLD_MARKER: Final = "\x07"
"""Inserted by ruamel's round-trip composer into folded values; never part of a folded value."""

_MERGE_VALUE_NEEDED: Final = (
    f"merge key '{MERGE_KEY}' needs a mapping, an alias of a mapping or a list of those"
)
"""Start of the refusal of a merge key whose value cannot be merged (config-aliases 2.3)."""


@dataclass(frozen=True)
class _Tag:
    """An explicit tag written on a node: its text, where it is written (``None`` when only the
    composer knows it) and whether it follows the node's anchor."""

    text: str
    span: Span | None
    after_anchor: bool


def load_config(
    config_file: Path,
    overrides: Mapping[Path, str] | None = None,
) -> ConfigDocument:
    """Load ``config_file`` into a :class:`ConfigDocument`, or raise :class:`ConfigLoadError`.

    ``overrides`` maps a file's real path (``Path.resolve()``) to text that is used instead of the
    file's content on disk; an overridden file is never read from disk.
    """
    loader = _Loader({} if overrides is None else dict(overrides))
    root_file = loader.read_source(config_file)
    composed = _compose(root_file)
    if composed.root is None:
        root = ConfigMapping(
            entries=(), style=CollectionStyle.BLOCK, origin=Origin(root_file, Span(0, 0))
        )
    else:
        builder = _Builder(root_file, loader, (root_file,), composed.aliases)
        node = builder.build(composed.root, ROOT)
        if not isinstance(node, ConfigMapping):
            kind = "a list" if isinstance(node, ConfigSequence) else "a scalar"
            raise ConfigLoadError(
                f"the top-level node is {kind}; expected a mapping", file=root_file.path
            )
        root = node
    return ConfigDocument(root_file=root_file, root=root, files=tuple(loader.files))


def load_include(including: SourceFile, spec: str, overrides: Mapping[Path, str]) -> ConfigNode:
    """Load the include string ``file:<spec>`` as if written in ``including``, or raise
    :class:`ConfigLoadError`.

    The target is resolved relative to ``including``'s directory and loaded with the same rules as
    in a document (nested includes, mapping forms, cycle detection along the chain starting at
    ``including``); ``overrides`` gives files' texts by real path, as for :func:`load_config`. The
    result is the include root node (its ``include`` set). Used by writers that need the content
    of an include a set is about to write (config-include-overrides 11.2).
    """
    loader = _Loader(dict(overrides))
    directive = ConfigScalar(
        value=INCLUDE_PREFIX + spec, style=ScalarStyle.PLAIN, origin=Origin(including, Span(0, 0))
    )
    return _Builder(including, loader, (including,), {}).include(directive, spec, ROOT)


class _Loader:
    """Reads source files (from disk or overrides) and remembers every file read, in order.

    Each real path is read at most once; later requests share the first :class:`SourceFile`.
    """

    def __init__(self, overrides: Mapping[Path, str]) -> None:
        self._overrides = overrides
        self._sources: dict[Path, SourceFile] = {}
        self.files: list[SourceFile] = []

    def read_source(self, path: Path) -> SourceFile:
        """Read the root file; an unreadable file is reported as the file itself."""
        real_path = path.resolve()
        cached = self._sources.get(real_path)
        if cached is not None:  # pragma: no cover - the root file is the first file read
            return cached
        override = self._overrides.get(real_path)
        if override is not None:
            return self._remember(path, real_path, override)
        try:
            data = path.read_bytes()
        except OSError as error:
            raise ConfigLoadError(f"cannot read file: {_reason(error)}", file=path) from error
        return self._remember(path, real_path, _decode(data, path))

    def read_include(self, target: Path, including: SourceFile, key_path: ConfigPath) -> SourceFile:
        """Read an include target; a missing or unreadable one names it and the including file."""
        real_path = target.resolve()
        cached = self._sources.get(real_path)
        if cached is not None:
            return cached
        override = self._overrides.get(real_path)
        if override is not None:
            return self._remember(target, real_path, override)
        if not target.is_file():
            raise ConfigLoadError(
                f"include target {target} does not exist or is not a regular file",
                file=including.path,
                path=_text(key_path),
            )
        try:
            data = target.read_bytes()
        except OSError as error:
            raise ConfigLoadError(
                f"cannot read include target {target}: {_reason(error)}",
                file=including.path,
                path=_text(key_path),
            ) from error
        return self._remember(target, real_path, _decode(data, target))

    def _remember(self, path: Path, real_path: Path, text: str) -> SourceFile:
        source = SourceFile(
            path=path, real_path=real_path, text=text, line_ending=_detect_line_ending(text)
        )
        self._sources[real_path] = source
        self.files.append(source)
        return source


def _reason(error: OSError) -> str:
    return error.strerror or type(error).__name__


def _decode(data: bytes, path: Path) -> str:
    """Decode ``data`` strictly as UTF-8 without newline translation, keeping a BOM."""
    try:
        return data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ConfigLoadError(
            f"file is not valid UTF-8 (invalid byte at offset {error.start})", file=path
        ) from error


def _detect_line_ending(text: str) -> LineEnding:
    """Return the newline convention of ``text``; a lone CR counts as a different convention."""
    crlf = text.count("\r\n")
    lone_lf = text.count("\n") - crlf
    lone_cr = text.count("\r") - crlf
    if lone_cr:
        return LineEnding.MIXED
    if crlf and lone_lf:
        return LineEnding.MIXED
    if crlf:
        return LineEnding.CRLF
    if lone_lf:
        return LineEnding.LF
    return LineEnding.NONE


# ruamel.yaml's node attributes are typed ``Any`` upstream; the loader narrows them with
# ``isinstance`` checks and explicit ``str``/``int`` conversions at this boundary.
def _compose(source: SourceFile) -> ComposedFile:
    """Compose the single document of ``source`` (root ``None`` for an empty or comment-only
    file), with its alias occurrences."""
    return compose_file(source)


class _Builder:
    """Builds model nodes for one composed file, refusing unsupported YAML on the way.

    ``chain`` holds the YAML files from the root down to this builder's file (inclusive); a YAML
    include of any of them is a cycle. ``aliases`` are the alias occurrences recorded when this
    builder's file was composed.

    ``_stack`` holds the ids of the ruamel nodes being built (an alias of one of them lies inside
    its own anchored node), and ``_anchored`` maps the id of each anchored ruamel node to the node
    built at its anchor (the first build, since an alias always follows its anchor).
    """

    def __init__(
        self,
        source: SourceFile,
        loader: _Loader,
        chain: tuple[SourceFile, ...],
        aliases: Mapping[AliasSlot, AliasOccurrence],
    ) -> None:
        self._source = source
        self._loader = loader
        self._chain = chain
        self._aliases = aliases
        self._tags_by_anchor: dict[int, _Tag] | None = None
        self._stack: list[int] = []
        self._anchored: dict[int, ConfigNode] = {}

    def build(self, node: Node, path: ConfigPath) -> ConfigNode:
        """Build ``node`` for ``path``; an anchored node built for the first time is remembered as
        the node built at its anchor."""
        self._stack.append(id(node))
        try:
            built = self._build(node, path)
        finally:
            self._stack.pop()
        if node.anchor is not None:
            self._anchored.setdefault(id(node), built)
        return built

    def _child(self, slot: AliasSlot, node: Node, path: ConfigPath) -> ConfigNode:
        """Build the child ``node`` in ``slot``: an alias place when an alias fills the slot."""
        occurrence = self._alias_in(slot, node, path)
        built = self.build(node, path)
        return built if occurrence is None else self._alias_place(built, occurrence, node)

    def _alias_in(self, slot: AliasSlot, node: Node, path: ConfigPath) -> AliasOccurrence | None:
        """Return the alias filling ``slot``, or ``None``; refuse an alias inside its own anchored
        node (config-aliases 1.4)."""
        occurrence = self._aliases.get(slot)
        if occurrence is not None and id(node) in self._stack:
            raise ConfigLoadError(
                f"alias '*{occurrence.name}' refers to its own anchored node",
                file=self._source.path,
                path=_text(path),
            )
        return occurrence

    def _alias_place(
        self, built: ConfigNode, occurrence: AliasOccurrence, node: Node
    ) -> ConfigNode:
        """Return ``built`` (``node`` built again at an alias) recording the alias it stands for."""
        anchored = self._anchored.get(id(node))
        if anchored is None:  # pragma: no cover - ruamel resolves aliases to earlier anchors only
            raise ConfigLoadError(
                f"internal error: alias '*{occurrence.name}' before its anchored node was built",
                file=self._source.path,
            )
        token = Origin(self._source, occurrence.span)
        return dataclasses.replace(built, alias=AliasRef(occurrence.name, token, anchored))

    def _build(self, node: Node, path: ConfigPath) -> ConfigNode:
        literal = self._refuse_properties(node, path)
        if isinstance(node, ScalarNode):
            scalar = self._scalar(node, path, literal)
            if (
                scalar.literal is None
                and isinstance(scalar.value, str)
                and scalar.value.startswith(INCLUDE_PREFIX)
            ):
                return self.include(scalar, scalar.value[len(INCLUDE_PREFIX) :], path)
            return scalar
        if isinstance(node, SequenceNode):
            items: list[ConfigNode] = []
            for index, item in enumerate(node.value):
                items.append(self._child((id(node), index), item, path.child(index)))
            return ConfigSequence(
                items=tuple(items),
                style=_collection_style(node),
                origin=self._origin(node),
                anchor=self._anchor(node),
            )
        if isinstance(node, MappingNode):
            return self._build_mapping(node, path)
        raise ConfigLoadError(  # pragma: no cover - the composer produces only these node kinds
            f"unsupported YAML node {type(node).__name__}", file=self._source.path, path=_text(path)
        )

    def _scalar(self, node: ScalarNode, path: ConfigPath, literal: Span | None) -> ConfigScalar:
        """Build a scalar as written, without include expansion; ``literal`` is the span of its
        ``!literal`` tag, if any (config-remote-schema 4.1)."""
        style = self._scalar_style(node, path)
        return ConfigScalar(
            value=_scalar_value(node, style, literal=literal is not None),
            style=style,
            origin=self._origin(node, literal),
            anchor=self._anchor(node),
            literal=literal,
        )

    def _build_mapping(self, node: MappingNode, path: ConfigPath) -> ConfigNode:
        """Build a mapping; one with a ``$ref`` key is a mapping-form include (Req 1.1), and one
        with a plain ``<<`` key is the merged view of its merge sources (config-aliases 2.1)."""
        entries: list[MappingEntry] = []
        directive: ConfigScalar | None = None
        merge_at: int | None = None
        seen_text: set[str] = set()
        seen_values: set[tuple[type, ScalarValue]] = set()
        key_alias = self._aliases.get((id(node), None))
        for key_node, value_node in node.value:
            if key_alias is not None:
                self._refuse_alias_key(key_alias, key_node, path)
            if not isinstance(key_node, ScalarNode):
                raise ConfigLoadError(
                    "mapping key is not a scalar (complex keys are not supported)",
                    file=self._source.path,
                    path=_text(path),
                )
            key_style = self._scalar_style(key_node, path)
            key_value = _scalar_value(key_node, key_style)
            key = str(key_node.value) if key_style is not ScalarStyle.FOLDED else str(key_value)
            key_path = path.child(key)
            self._refuse_properties(key_node, key_path, key=True)
            # Paths are textual, so equal key text is a duplicate; equal typed plain values
            # (``true``/``True``, ``~``/``null``) are duplicates under YAML 1.2 as well.
            typed = (type(key_value), key_value)
            is_typed = key_style is ScalarStyle.PLAIN and not isinstance(key_value, str)
            if key in seen_text or (is_typed and typed in seen_values):
                raise ConfigLoadError(
                    "duplicate key in mapping", file=self._source.path, path=_text(key_path)
                )
            seen_text.add(key)
            if is_typed:
                seen_values.add(typed)
            slot = (id(node), id(key_node))
            if is_ref_key(key):
                # The include string stays as written in the form; it is expanded below.
                directive = self._ref_value(slot, value_node, key_path, path)
                value: ConfigNode = directive
            else:
                value = self._child(slot, value_node, key_path)
                if key_style is ScalarStyle.PLAIN and key == MERGE_KEY:
                    merge_at = len(entries)
            entries.append(
                MappingEntry(
                    key=key,
                    key_origin=self._origin(key_node),
                    key_style=key_style,
                    value=value,
                )
            )
        mapping = ConfigMapping(
            entries=tuple(entries),
            style=_collection_style(node),
            origin=self._origin(node),
            anchor=self._anchor(node),
        )
        if merge_at is not None:
            mapping = self._merged_view(mapping, merge_at, path, form=directive is not None)
        if directive is None:
            return mapping
        assert isinstance(directive.value, str)  # checked by ``_ref_value``
        return self.include(directive, directive.value[len(INCLUDE_PREFIX) :], path, form=mapping)

    def _merged_view(
        self, written: ConfigMapping, merge_at: int, path: ConfigPath, *, form: bool
    ) -> ConfigMapping:
        """Return the merged view of ``written``, whose plain ``<<`` entry is at ``merge_at``
        (config-aliases 2.1, 2.2); refuse a value that cannot be merged and a merge key among the
        overrides of a mapping-form include (``form``), naming the merge key's path (2.3)."""
        entry = written.entries[merge_at]
        key_path = path.child(MERGE_KEY)
        if form:
            raise ConfigLoadError(
                f"merge key '{MERGE_KEY}' is not supported among the overrides of a "
                "mapping-form include",
                file=self._source.path,
                path=_text(key_path),
            )
        sources = self._merge_sources(entry.value, key_path)
        return merge_mapping(written, merge_at, sources, entry.key_origin)

    def _merge_sources(self, value: ConfigNode, key_path: ConfigPath) -> tuple[ConfigMapping, ...]:
        """Return the mappings a merge key's ``value`` merges: the mapping (or alias place of a
        mapping) itself, or each item of a list of those; refuse any other value naming the merge
        key's path (config-aliases 2.3). An include (an include string, a ``$ref`` mapping, or an
        alias place of either) is written as a scalar or a ``$ref`` mapping, never as a mapping, so
        it is refused as a source, which also keeps the two merge mechanisms apart (research.md
        Decision 4); a source's nested values may be includes."""
        if value.include is not None:
            raise self._bad_merge_value("found an include", key_path)
        if isinstance(value, ConfigMapping):
            return (value,)
        if isinstance(value, ConfigScalar):
            raise self._bad_merge_value("found a scalar", key_path)
        if value.alias is not None:
            raise self._bad_merge_value("found an alias of a list", key_path)
        sources: list[ConfigMapping] = []
        for index, item in enumerate(value.items):
            if item.include is not None:
                raise self._bad_merge_value(f"item {index} is an include", key_path)
            if not isinstance(item, ConfigMapping):
                kind = "a scalar" if isinstance(item, ConfigScalar) else "a list"
                raise self._bad_merge_value(f"item {index} is {kind}", key_path)
            sources.append(item)
        return tuple(sources)

    def _bad_merge_value(self, found: str, key_path: ConfigPath) -> ConfigLoadError:
        return ConfigLoadError(
            f"{_MERGE_VALUE_NEEDED}; {found}", file=self._source.path, path=_text(key_path)
        )

    def _ref_value(
        self, slot: AliasSlot, node: Node, key_path: ConfigPath, path: ConfigPath
    ) -> ConfigScalar:
        """Return the ``$ref`` value as written; it must be an include string (Req 2.2), never a
        ``!literal`` string (config-remote-schema 4.3). An alias of an include string is an alias
        place of that string (config-aliases 1.5)."""
        occurrence = self._alias_in(slot, node, key_path)
        literal = self._refuse_properties(node, key_path)
        if literal is not None:
            raise ConfigLoadError(
                f"`{INCLUDE_REF_KEY}` must be an include string ({INCLUDE_PREFIX}...), "
                f"not a '{LITERAL_TAG}' string",
                file=self._source.path,
                path=_text(path),
            )
        if isinstance(node, ScalarNode):
            scalar = self._scalar(node, key_path, None)
            if isinstance(scalar.value, str) and scalar.value.startswith(INCLUDE_PREFIX):
                if node.anchor is not None:
                    self._anchored.setdefault(id(node), scalar)
                if occurrence is None:
                    return scalar
                place = self._alias_place(scalar, occurrence, node)
                assert isinstance(place, ConfigScalar)  # ``dataclasses.replace`` keeps the type
                return place
        raise ConfigLoadError(
            f"`{INCLUDE_REF_KEY}` must be an include string ({INCLUDE_PREFIX}...)",
            file=self._source.path,
            path=_text(path),
        )

    def include(
        self,
        directive: ConfigScalar,
        spec: str,
        path: ConfigPath,
        *,
        form: ConfigMapping | None = None,
    ) -> ConfigNode:
        """Replace the include string ``directive`` by its target (4.1-4.7).

        ``form`` is the ``$ref`` mapping of a mapping-form include (Req 1.1). Bare, the result is
        exactly the scalar form's, with ``include.form`` set (1.4); with overrides, it is the deep
        merge of the included mapping with ``form`` (1.2, 1.3).

        An anchor written on the include string, or on the ``$ref`` mapping, is recorded on the
        result (config-aliases 1.5). It replaces an anchor on the target file's root, which no
        alias can use (aliases are file-local, and one inside the root's own node is recursive).
        """
        anchor = directive.anchor if form is None else form.anchor
        node = self._include(directive, spec, path, form=form)
        return node if anchor is None else dataclasses.replace(node, anchor=anchor)

    def _include(
        self,
        directive: ConfigScalar,
        spec: str,
        path: ConfigPath,
        *,
        form: ConfigMapping | None,
    ) -> ConfigNode:
        """Return :meth:`include`'s result before the include's own anchor is recorded."""
        has_overrides = form is not None and bool(override_entries(form))
        target_path = self._source.path.parent / spec
        if not target_path.name.endswith(YAML_INCLUDE_SUFFIXES):
            if has_overrides:
                raise ConfigLoadError(
                    f"overrides apply only to YAML includes; {target_path} is not a "
                    f"{'/'.join(YAML_INCLUDE_SUFFIXES)} file",
                    file=self._source.path,
                    path=_text(path),
                )
            target = self._loader.read_include(target_path, self._source, path)
            ref = IncludeRef(IncludeKind.TEXT, spec, target, directive.origin, form)
            return ConfigScalar(
                value=target.text,
                style=None,
                origin=Origin(target, Span(0, len(target.text))),
                include=ref,
            )
        self._refuse_cycle(target_path)
        target = self._loader.read_include(target_path, self._source, path)
        ref = IncludeRef(IncludeKind.YAML, spec, target, directive.origin, form)
        composed = _compose(target)
        embedded: ConfigNode | None = None
        if composed.root is not None:
            builder = _Builder(target, self._loader, (*self._chain, target), composed.aliases)
            embedded = builder.build(composed.root, path)
        if form is not None and has_overrides:
            if not isinstance(embedded, ConfigMapping):
                if embedded is None:
                    kind = "nothing (the file is empty)"
                else:
                    kind = "a list" if isinstance(embedded, ConfigSequence) else "a scalar"
                raise ConfigLoadError(
                    f"overrides need a mapping; the included file {target_path} holds {kind}",
                    file=self._source.path,
                    path=_text(path),
                )
            # ``embedded`` carries an include only when the target's root is itself a mapping-form
            # include; keeping it keeps that file's ``$ref`` mapping reachable for writers.
            return deep_merge(embedded, form, include=ref)
        if embedded is None:
            return ConfigScalar(
                value=None,
                style=ScalarStyle.PLAIN,
                origin=Origin(target, Span(0, 0)),
                include=ref,
            )
        # A target whose root is itself an include keeps that include as ``inner``, so writers can
        # reach the file holding its reference (config-include-overrides choice 5).
        return dataclasses.replace(
            embedded, include=dataclasses.replace(ref, inner=embedded.include)
        )

    def _refuse_cycle(self, target_path: Path) -> None:
        """Refuse a YAML include of a file already on the chain, listing the cycle (4.6)."""
        real_path = target_path.resolve()
        real_paths = [source.real_path for source in self._chain]
        if real_path not in real_paths:
            return
        cycle = [str(source.path) for source in self._chain[real_paths.index(real_path) :]]
        cycle.append(str(target_path))
        raise ConfigLoadError(f"include cycle: {' -> '.join(cycle)}", file=target_path)

    def _refuse_alias_key(
        self, occurrence: AliasOccurrence, key_node: Node, path: ConfigPath
    ) -> None:
        """Refuse ``key_node`` when it is an alias used as a key of the mapping at ``path``
        (config-aliases 1.4), naming the key path (the mapping's path for a non-scalar key).

        ``occurrence`` is the first alias recorded as a key of that mapping. Keys never carry
        anchors (refused), so a key node that is an anchored node is one reached through an alias.
        """
        if id(key_node) not in self._anchored and id(key_node) not in self._stack:
            return
        key_path = path.child(str(key_node.value)) if isinstance(key_node, ScalarNode) else path
        raise ConfigLoadError(
            f"alias '*{occurrence.name}' used as a mapping key is not supported",
            file=self._source.path,
            path=_text(key_path),
        )

    def _refuse_properties(self, node: Node, path: ConfigPath, *, key: bool = False) -> Span | None:
        """Refuse an explicit tag written on ``node`` itself (3.7), and an anchor on a mapping key
        (config-aliases 1.4). Anchors on values are accepted (config-aliases 1.1).

        A tag exactly :data:`LITERAL_TAG` on a scalar value, written after the node's anchor if it
        has one, is accepted and its span returned (config-remote-schema 4.1); written on a mapping
        key, a mapping or a list, or before an anchor, it is refused naming the tag (4.2).
        """
        tag = self._explicit_tag(node)
        literal: Span | None = None
        if tag is not None:
            literal = self._literal_tag(node, tag, path, key=key)
            if literal is None:
                raise ConfigLoadError(
                    f"explicit tag '{tag.text}' is not supported",
                    file=self._source.path,
                    path=_text(path),
                )
        if key and node.anchor is not None:
            raise ConfigLoadError(
                "anchor on a mapping key is not supported",
                file=self._source.path,
                path=_text(path),
            )
        return literal

    def _literal_tag(self, node: Node, tag: _Tag, path: ConfigPath, *, key: bool) -> Span | None:
        """Return the span of ``tag`` when it is an accepted ``!literal`` property of ``node``,
        ``None`` when it is another tag; refuse ``!literal`` where it cannot be written."""
        if tag.text != LITERAL_TAG or tag.span is None:
            return None
        where: str | None = None
        if key:
            where = "is not supported on a mapping key"
        elif isinstance(node, MappingNode):
            where = "is supported only on scalars, not on a mapping"
        elif isinstance(node, SequenceNode):
            where = "is supported only on scalars, not on a list"
        elif node.anchor is not None and not tag.after_anchor:
            where = f"must follow the anchor (write '&{node.anchor} {LITERAL_TAG}')"
        if where is not None:
            raise ConfigLoadError(
                f"explicit tag '{LITERAL_TAG}' {where}", file=self._source.path, path=_text(path)
            )
        return tag.span

    def _explicit_tag(self, node: Node) -> _Tag | None:
        """Return the explicit tag written on ``node`` itself, or ``None`` when it has none."""
        if node.anchor is not None:
            # ruamel starts an anchored node at its anchor even when a tag comes first, and keeps
            # no trace of a non-specific ``!`` or a verbatim tag; the tokens next to the anchor do.
            tag = self._tags_next_to_anchors().get(int(node.start_mark.index))
            if tag is not None:
                return tag
            if node.ctag.handle is not None:  # pragma: no cover - the token is found above
                return _Tag(str(node.tag), None, after_anchor=False)
            return None
        prefix = self._own_property_text(node)
        if prefix.startswith("!"):
            text = prefix.split()[0]
            start = int(node.start_mark.index)
            return _Tag(text, Span(start, start + len(text)), after_anchor=False)
        if node.ctag.handle is not None:
            return _Tag(str(node.tag), None, after_anchor=False)
        return None

    def _tags_next_to_anchors(self) -> dict[int, _Tag]:
        """Map the start of each anchor token in this file to the tag written next to it.

        The file is scanned once, on the first anchored node; it composed, so it scans.
        """
        if self._tags_by_anchor is None:
            tags: dict[int, _Tag] = {}
            tokens = list(YAML(typ="rt").scan(self._source.text))
            for before, token, after in zip(
                [None, *tokens[:-1]], tokens, [*tokens[1:], None], strict=True
            ):
                if not isinstance(token, AnchorToken):
                    continue
                for neighbour, follows in ((before, False), (after, True)):
                    if isinstance(neighbour, TagToken):
                        start = int(neighbour.start_mark.index)
                        end = int(neighbour.end_mark.index)
                        tags[int(token.start_mark.index)] = _Tag(
                            self._source.text[start:end], Span(start, end), after_anchor=follows
                        )
            self._tags_by_anchor = tags
        return self._tags_by_anchor

    def _anchor(self, node: Node) -> Anchor | None:
        """Return the anchor written on value ``node``, or ``None`` (config-aliases 1.1).

        An anchored node starts at its ``&name`` property (a tag written first is refused before
        this; ``!literal`` may only follow the anchor).
        """
        if node.anchor is None:
            return None
        name = str(node.anchor)
        start = int(node.start_mark.index)
        span = Span(start, start + 1 + len(name))
        written = self._source.text[span.start : span.end]
        if written != f"&{name}":  # pragma: no cover - ruamel starts anchored nodes at the anchor
            raise ConfigLoadError(
                f"internal error: anchor '&{name}' expected at characters "
                f"{span.start}-{span.end}, where the file reads {written!r}",
                file=self._source.path,
            )
        return Anchor(name, span)

    def _own_property_text(self, node: Node) -> str:
        """Return the source text from ``node``'s start that may hold its own properties.

        A node's span starts at its properties (``!tag``, ``&anchor``) when it has any. A block
        mapping without properties starts at its first key, so a ``!``/``&`` there belongs to the
        key and is checked on the key instead; plain scalars, flow collections and block
        sequences cannot otherwise start with ``!`` or ``&``.
        """
        start = int(node.start_mark.index)
        end = int(node.end_mark.index)
        if isinstance(node, MappingNode) and node.value:
            # Empty when the mapping starts at its first key (no own properties).
            end = int(node.value[0][0].start_mark.index)
        return self._source.text[start:end]

    def _scalar_style(self, node: ScalarNode, path: ConfigPath) -> ScalarStyle:
        style = _SCALAR_STYLES.get(node.style)
        if style is None:  # pragma: no cover - ruamel reports only the five YAML styles
            raise ConfigLoadError(
                f"unsupported scalar style {node.style!r}", file=self._source.path, path=_text(path)
            )
        return style

    def _origin(self, node: Node, literal: Span | None = None) -> Origin:
        """Return where ``node`` is written; the span excludes its properties, an anchor
        (config-aliases research.md, Decision 3) and a ``!literal`` tag (``literal``, which always
        follows the anchor), and is empty at the last property's end for an empty scalar."""
        start = int(node.start_mark.index)
        end = int(node.end_mark.index)
        anchor = self._anchor(node)
        if literal is not None:
            after = literal.end
        elif anchor is not None:
            after = anchor.span.end
        else:
            return Origin(self._source, Span(start, end))
        if isinstance(node, ScalarNode) and end <= after:
            # An empty null: nothing is written after the property.
            return Origin(self._source, Span(after, after))
        return Origin(self._source, Span(_next_token(self._source.text, after, end), end))


def _scalar_value(node: ScalarNode, style: ScalarStyle, *, literal: bool = False) -> ScalarValue:
    """Type a scalar: plain by the core schema unless ``literal`` (a ``!literal`` scalar is its
    text), others as strings; folded without markers."""
    text = str(node.value)
    if style is ScalarStyle.PLAIN and not literal:
        return resolve_plain_scalar(text)
    if style is ScalarStyle.FOLDED:
        return text.replace(_FOLD_MARKER, "")
    return text


def _next_token(text: str, start: int, end: int) -> int:
    """Return the index of the first character at or after ``start`` (before ``end``) that is
    neither white space nor part of a comment: the start of the next token."""
    index = start
    while index < end:
        char = text[index]
        if char in " \t\r\n":
            index += 1
        elif char == "#":
            line_end = text.find("\n", index, end)
            index = end if line_end < 0 else line_end + 1
        else:
            break
    return index


def _collection_style(node: MappingNode | SequenceNode) -> CollectionStyle:
    return CollectionStyle.FLOW if node.flow_style is True else CollectionStyle.BLOCK


def _text(path: ConfigPath) -> str | None:
    """Return the dotted form of ``path`` for an error, or ``None`` for the root."""
    return str(path) if path.segments else None
