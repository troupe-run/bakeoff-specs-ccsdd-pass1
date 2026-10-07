"""ValueParser: the typed, style-free value tree of a VALUE given to ``troupe config set``.

The parsed-value types live here so that the parser and the plain-tree module share them.

VALUE is composed with ruamel.yaml's round-trip composer (never loaded), using the same
techniques as the foundation loader: tags and anchors are detected from the node's own source
text, duplicate keys are checked here (the composer does not reject them), and plain scalars are
typed with the shared :func:`~troupe.config.scalars.resolve_plain_scalar` so VALUE and the loaded
configuration agree.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Final, TypeAlias

from ruamel.yaml import YAML
from ruamel.yaml.error import MarkedYAMLError, YAMLError
from ruamel.yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

from troupe.config.constants import INCLUDE_REF_KEY
from troupe.config.errors import UsageError, WriteRefusedError
from troupe.config.includes import is_include_string
from troupe.config.model import SchemaSource
from troupe.config.paths import ConfigPath
from troupe.config.scalars import ScalarValue, resolve_plain_scalar
from troupe.config.schema import uri_format_at


class ScalarMark(Enum):
    """How a VALUE scalar is written and compared, decided once by :func:`mark_include_strings`."""

    NONE = "none"
    """An ordinary value (also an include string the schema makes a literal)."""
    INCLUDE = "include"
    """An include string written plain to be an include (``--include``)."""
    LITERAL = "literal"
    """An include-like string written tagged ``!literal``."""


@dataclass(frozen=True)
class NewScalar:
    """A scalar of VALUE: its typed value and, for non-strings, the plain text it was written as."""

    value: ScalarValue
    spelling: str | None
    """Source text for ``int``/``float``/``bool``/``None`` (e.g. ``0x1F``); ``None`` for strings."""
    mark: ScalarMark | None = None
    """The scalar's mark; ``None`` means "not marked yet" (``parse_value`` output that ``set`` has
    not marked), which keeps the ``file:`` prefix rule in the plain tree."""


@dataclass(frozen=True)
class NewMapping:
    """A flow mapping of VALUE; entries keep VALUE order."""

    entries: tuple[tuple[str, "NewValue"], ...]


@dataclass(frozen=True)
class NewSequence:
    """A flow list of VALUE."""

    items: tuple["NewValue", ...]


# A runtime union (not a ``type`` statement) so callers can also use it with ``isinstance``.
NewValue: TypeAlias = NewScalar | NewMapping | NewSequence  # noqa: UP040
"""One node of a parsed VALUE."""


_NOT_A_FLOW_NODE: Final = "VALUE is not a single flow node"

REF_MAPPING_NEEDS_INCLUDE: Final = "a `$ref` mapping is an include; give --include to write it"
"""Problem of a VALUE holding a ``$ref`` mapping without ``--include`` (8.6)."""


def mark_include_strings(
    value: NewValue, path: ConfigPath, schema: SchemaSource | None, *, include: bool
) -> NewValue:
    """Return ``value`` with every scalar marked by its own path (8.1, 8.2, 9.3, 9.5).

    ``path`` is PATH; a nested scalar's own path appends the mapping keys and list indices that
    lead to it. An include string is ``NONE`` when ``schema`` gives its own path ``format: uri``,
    else ``INCLUDE`` with ``include`` and ``LITERAL`` without. Every other scalar is ``NONE``, and
    keys are never marked. Raises :class:`WriteRefusedError` naming ``path`` when a mapping in
    ``value`` has a ``$ref`` key and ``include`` is false (8.6).
    """

    def mark(node: NewValue, own: ConfigPath) -> NewValue:
        if isinstance(node, NewScalar):
            return NewScalar(node.value, node.spelling, scalar_mark(node.value, own))
        if isinstance(node, NewMapping):
            if not include and any(key == INCLUDE_REF_KEY for key, _ in node.entries):
                raise WriteRefusedError(REF_MAPPING_NEEDS_INCLUDE, path=str(path))
            return NewMapping(
                tuple((key, mark(item, own.child(key))) for key, item in node.entries)
            )
        return NewSequence(
            tuple(mark(item, own.child(index)) for index, item in enumerate(node.items))
        )

    def scalar_mark(scalar: ScalarValue, own: ConfigPath) -> ScalarMark:
        if not is_include_string(scalar) or uri_format_at(schema, own):
            return ScalarMark.NONE
        return ScalarMark.INCLUDE if include else ScalarMark.LITERAL

    return mark(value, path)


def parse_value(text: str, path: ConfigPath) -> NewValue:
    """Parse ``text`` as exactly one YAML 1.2 core-schema flow node (1.2-1.4).

    Raises :class:`UsageError` naming ``path`` when VALUE is not valid UTF-8-encodable YAML, is
    empty or comment-only, holds several documents, a block node, a tag, an anchor or an alias, or
    when a mapping in it has a duplicate, empty or non-scalar key (the message names that key).
    """
    return _Parser(text, path).parse()


class _Parser:
    """Composes one VALUE and converts its nodes, refusing anything outside the contract."""

    def __init__(self, text: str, path: ConfigPath) -> None:
        self._text = text
        self._path = path

    def parse(self) -> NewValue:
        try:
            self._text.encode("utf-8")
        except UnicodeEncodeError as error:
            raise self._not_flow(
                f"character {self._text[error.start]!r} cannot be encoded as UTF-8"
            ) from error
        return self._convert(self._compose())

    # ruamel.yaml's ``compose`` and node attributes are typed ``Any`` upstream; they are narrowed
    # here with ``isinstance`` checks and explicit ``str``/``int`` conversions.
    def _compose(self) -> Node:
        try:
            node = YAML(typ="rt").compose(self._text)
        except MarkedYAMLError as error:
            raise self._not_flow(_describe_marked_error(error)) from error
        except YAMLError as error:
            first_line = str(error).splitlines()[0] if str(error) else type(error).__name__
            raise self._not_flow(f"invalid YAML: {first_line}") from error
        if node is None:
            raise self._not_flow("VALUE is empty or contains only comments")
        if not isinstance(node, Node):  # pragma: no cover - ruamel returns a Node or None
            raise self._not_flow("invalid YAML: unexpected composer result")
        return node

    def _convert(self, node: Node) -> NewValue:
        self._refuse_properties(node)
        if isinstance(node, ScalarNode):
            return self._scalar(node)
        if isinstance(node, SequenceNode):
            self._refuse_block_collection(node, "list")
            return NewSequence(tuple(self._convert(item) for item in node.value))
        if isinstance(node, MappingNode):
            self._refuse_block_collection(node, "mapping")
            return self._mapping(node)
        raise self._not_flow(  # pragma: no cover - the composer produces only these node kinds
            f"unsupported YAML node {type(node).__name__}"
        )

    def _scalar(self, node: ScalarNode) -> NewScalar:
        text = str(node.value)
        if node.style is None:
            value = resolve_plain_scalar(text)
            return NewScalar(value, None if isinstance(value, str) else text)
        if node.style in ("'", '"'):
            return NewScalar(text, None)
        raise self._not_flow(f"block scalar {self._source(node)!r} (style {node.style})")

    def _mapping(self, node: MappingNode) -> NewMapping:
        entries: list[tuple[str, NewValue]] = []
        seen_text: set[str] = set()
        seen_typed: set[tuple[type, ScalarValue]] = set()
        for key_node, value_node in node.value:
            if not isinstance(key_node, ScalarNode):
                raise self._bad_key(f"mapping key {self._source(key_node)} is not a scalar")
            self._refuse_properties(key_node)
            key = str(key_node.value)
            if key == "":
                raise self._bad_key("mapping key '' is empty")
            # As in the loader: equal key text is a duplicate, and so are plain keys with equal
            # typed values (``true``/``True``, ``~``/``null``).
            typed_value = resolve_plain_scalar(key) if key_node.style is None else key
            typed = (type(typed_value), typed_value)
            is_typed = key_node.style is None and not isinstance(typed_value, str)
            if key in seen_text or (is_typed and typed in seen_typed):
                raise self._bad_key(f"duplicate mapping key {key!r}")
            seen_text.add(key)
            if is_typed:
                seen_typed.add(typed)
            entries.append((key, self._convert(value_node)))
        return NewMapping(tuple(entries))

    def _refuse_properties(self, node: Node) -> None:
        """Refuse an anchor (and so any alias of it) or an explicit tag on ``node``."""
        if node.anchor is not None:
            raise self._not_flow(f"anchor '&{node.anchor}' (anchors and aliases are refused)")
        own_text = self._text[int(node.start_mark.index) :]
        if node.ctag.handle is not None or own_text.startswith("!"):
            tag = own_text.split()[0] if own_text.startswith("!") else str(node.tag)
            raise self._not_flow(f"explicit tag '{tag}' (tags are refused)")

    def _refuse_block_collection(self, node: MappingNode | SequenceNode, kind: str) -> None:
        if node.flow_style is not True:
            raise self._not_flow(f"block {kind} (write it in flow style, e.g. {{a: 1}} or [a, b])")

    def _source(self, node: Node) -> str:
        return self._text[int(node.start_mark.index) : int(node.end_mark.index)]

    def _not_flow(self, reason: str) -> UsageError:
        return UsageError(f"{_NOT_A_FLOW_NODE}: {reason}", path=str(self._path))

    def _bad_key(self, reason: str) -> UsageError:
        return UsageError(f"invalid VALUE: {reason}", path=str(self._path))


def _describe_marked_error(error: MarkedYAMLError) -> str:
    """Return ``invalid YAML: <context>, <problem> (line L, column C)``, 1-based positions."""
    parts = [str(part) for part in (error.context, error.problem) if part]
    problem = ", ".join(parts) if parts else type(error).__name__
    mark = error.problem_mark or error.context_mark
    if mark is None:
        return f"invalid YAML: {problem}"
    return f"invalid YAML: {problem} (line {int(mark.line) + 1}, column {int(mark.column) + 1})"
