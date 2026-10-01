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

Unsupported YAML is refused with :class:`ConfigLoadError` naming the file and, where known, the
key path (product-owner decisions H and I): non-UTF-8 bytes, syntax errors, several documents,
duplicate keys, explicit tags, anchors, aliases (a defined alias is caught by its anchor, which
comes first in the document; an undefined one is a composer error), plain ``<<`` merge keys,
non-scalar keys and a top-level node that is not a mapping. An empty or comment-only file is an
empty block mapping at ``Span(0, 0)``.

Includes (4.1-4.7): any string scalar value (any style, never a key) starting with
:data:`INCLUDE_PREFIX` is replaced by its target, ``including_file.path.parent / spec``. A target
whose name ends in one of :data:`YAML_INCLUDE_SUFFIXES` is loaded recursively and its root embedded
as if written in place (an empty target is null); any other target is its exact decoded text as a
string and is never parsed. A YAML target already on the include chain from the root is a cycle.
Every produced node carries an :class:`IncludeRef`. Each file is read at most once; a file
included several times (a diamond) is shared and listed once in ``ConfigDocument.files``.

Nothing is ever created, modified, renamed or deleted (3.6).
"""

import dataclasses
from collections.abc import Mapping
from pathlib import Path
from typing import Final

from ruamel.yaml import YAML
from ruamel.yaml.error import MarkedYAMLError, YAMLError
from ruamel.yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

from troupe.config.constants import INCLUDE_PREFIX, YAML_INCLUDE_SUFFIXES
from troupe.config.errors import ConfigLoadError
from troupe.config.model import (
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

_MERGE_KEY: Final = "<<"
"""A plain mapping key with this text is a YAML merge key."""


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
    if composed is None:
        root = ConfigMapping(
            entries=(), style=CollectionStyle.BLOCK, origin=Origin(root_file, Span(0, 0))
        )
    else:
        node = _Builder(root_file, loader, (root_file,)).build(composed, ROOT)
        if not isinstance(node, ConfigMapping):
            kind = "a list" if isinstance(node, ConfigSequence) else "a scalar"
            raise ConfigLoadError(
                f"the top-level node is {kind}; expected a mapping", file=root_file.path
            )
        root = node
    return ConfigDocument(root_file=root_file, root=root, files=tuple(loader.files))


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


# ruamel.yaml's ``compose`` and node attributes are typed ``Any`` upstream; the loader narrows
# them with ``isinstance`` checks and explicit ``str``/``int`` conversions at this boundary.
def _compose(source: SourceFile) -> Node | None:
    """Compose the single document of ``source``; ``None`` for an empty or comment-only file."""
    try:
        node = YAML(typ="rt").compose(source.text)
    except MarkedYAMLError as error:
        raise ConfigLoadError(_describe_marked_error(error), file=source.path) from error
    except YAMLError as error:
        first_line = str(error).splitlines()[0] if str(error) else type(error).__name__
        raise ConfigLoadError(f"invalid YAML: {first_line}", file=source.path) from error
    if node is None:
        return None
    if not isinstance(node, Node):  # pragma: no cover - ruamel always returns a Node or None
        raise ConfigLoadError("invalid YAML: unexpected composer result", file=source.path)
    return node


def _describe_marked_error(error: MarkedYAMLError) -> str:
    """Return ``invalid YAML: <context>, <problem> (line L, column C)``, 1-based positions."""
    parts = [str(part) for part in (error.context, error.problem) if part]
    problem = ", ".join(parts) if parts else type(error).__name__
    mark = error.problem_mark or error.context_mark
    if mark is None:
        return f"invalid YAML: {problem}"
    return f"invalid YAML: {problem} (line {int(mark.line) + 1}, column {int(mark.column) + 1})"


class _Builder:
    """Builds model nodes for one composed file, refusing unsupported YAML on the way.

    ``chain`` holds the YAML files from the root down to this builder's file (inclusive); a YAML
    include of any of them is a cycle.
    """

    def __init__(self, source: SourceFile, loader: _Loader, chain: tuple[SourceFile, ...]) -> None:
        self._source = source
        self._loader = loader
        self._chain = chain

    def build(self, node: Node, path: ConfigPath) -> ConfigNode:
        self._refuse_properties(node, path)
        if isinstance(node, ScalarNode):
            style = self._scalar_style(node, path)
            scalar = ConfigScalar(
                value=_scalar_value(node, style), style=style, origin=self._origin(node)
            )
            if isinstance(scalar.value, str) and scalar.value.startswith(INCLUDE_PREFIX):
                return self._include(scalar, scalar.value[len(INCLUDE_PREFIX) :], path)
            return scalar
        if isinstance(node, SequenceNode):
            items = tuple(
                self.build(item, path.child(index)) for index, item in enumerate(node.value)
            )
            return ConfigSequence(
                items=items, style=_collection_style(node), origin=self._origin(node)
            )
        if isinstance(node, MappingNode):
            return self._build_mapping(node, path)
        raise ConfigLoadError(  # pragma: no cover - the composer produces only these node kinds
            f"unsupported YAML node {type(node).__name__}", file=self._source.path, path=_text(path)
        )

    def _build_mapping(self, node: MappingNode, path: ConfigPath) -> ConfigMapping:
        entries: list[MappingEntry] = []
        seen_text: set[str] = set()
        seen_values: set[tuple[type, ScalarValue]] = set()
        for key_node, value_node in node.value:
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
            self._refuse_properties(key_node, key_path)
            if key_style is ScalarStyle.PLAIN and key == _MERGE_KEY:
                raise ConfigLoadError(
                    "merge key '<<' is not supported (anchors, aliases and merge keys are refused)",
                    file=self._source.path,
                    path=_text(key_path),
                )
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
            entries.append(
                MappingEntry(
                    key=key,
                    key_origin=self._origin(key_node),
                    key_style=key_style,
                    value=self.build(value_node, key_path),
                )
            )
        return ConfigMapping(
            entries=tuple(entries), style=_collection_style(node), origin=self._origin(node)
        )

    def _include(self, directive: ConfigScalar, spec: str, path: ConfigPath) -> ConfigNode:
        """Replace the ``file:`` string ``directive`` by its target (4.1-4.7)."""
        target_path = self._source.path.parent / spec
        if not target_path.name.endswith(YAML_INCLUDE_SUFFIXES):
            target = self._loader.read_include(target_path, self._source, path)
            ref = IncludeRef(IncludeKind.TEXT, spec, target, directive.origin)
            return ConfigScalar(
                value=target.text,
                style=None,
                origin=Origin(target, Span(0, len(target.text))),
                include=ref,
            )
        self._refuse_cycle(target_path)
        target = self._loader.read_include(target_path, self._source, path)
        ref = IncludeRef(IncludeKind.YAML, spec, target, directive.origin)
        composed = _compose(target)
        if composed is None:
            return ConfigScalar(
                value=None,
                style=ScalarStyle.PLAIN,
                origin=Origin(target, Span(0, 0)),
                include=ref,
            )
        embedded = _Builder(target, self._loader, (*self._chain, target)).build(composed, path)
        return dataclasses.replace(embedded, include=ref)

    def _refuse_cycle(self, target_path: Path) -> None:
        """Refuse a YAML include of a file already on the chain, listing the cycle (4.6)."""
        real_path = target_path.resolve()
        real_paths = [source.real_path for source in self._chain]
        if real_path not in real_paths:
            return
        cycle = [str(source.path) for source in self._chain[real_paths.index(real_path) :]]
        cycle.append(str(target_path))
        raise ConfigLoadError(f"include cycle: {' -> '.join(cycle)}", file=target_path)

    def _refuse_properties(self, node: Node, path: ConfigPath) -> None:
        """Refuse an anchor or explicit tag written on ``node`` itself (3.7, 3.9)."""
        if node.anchor is not None:
            raise ConfigLoadError(
                f"anchor '&{node.anchor}' is not supported "
                "(anchors, aliases and merge keys are refused)",
                file=self._source.path,
                path=_text(path),
            )
        prefix = self._own_property_text(node)
        explicit_shorthand = node.ctag.handle is not None
        if explicit_shorthand or prefix.startswith("!"):
            tag = prefix.split()[0] if prefix.startswith("!") else str(node.tag)
            raise ConfigLoadError(
                f"explicit tag '{tag}' is not supported",
                file=self._source.path,
                path=_text(path),
            )

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

    def _origin(self, node: Node) -> Origin:
        return Origin(self._source, Span(int(node.start_mark.index), int(node.end_mark.index)))


def _scalar_value(node: ScalarNode, style: ScalarStyle) -> ScalarValue:
    """Type a scalar: plain by the core schema, others as strings; folded without markers."""
    text = str(node.value)
    if style is ScalarStyle.PLAIN:
        return resolve_plain_scalar(text)
    if style is ScalarStyle.FOLDED:
        return text.replace(_FOLD_MARKER, "")
    return text


def _collection_style(node: MappingNode | SequenceNode) -> CollectionStyle:
    return CollectionStyle.FLOW if node.flow_style is True else CollectionStyle.BLOCK


def _text(path: ConfigPath) -> str | None:
    """Return the dotted form of ``path`` for an error, or ``None`` for the root."""
    return str(path) if path.segments else None
