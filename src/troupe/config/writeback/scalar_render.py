"""ScalarRender: representability, style choice, plain/single/double scalar and key rendering.

A string is written in a *quote style*: plain, single-quoted or double-quoted. The style of an
existing scalar (or of the preceding sibling of a new one) is kept when it can represent the new
text; otherwise the minimal style is used: plain if possible, otherwise single-quoted, otherwise
double-quoted, which can represent every string.

Plain representability is decided by a cheap syntactic check followed by a parse probe: the text is
placed in a tiny document of the exact context (block value, flow value, block key, flow key),
composed with the same ruamel.yaml composer the loader uses, and must come back as a plain scalar
with exactly the same text, and, for values, typed as that same string by the shared
:func:`~troupe.config.scalars.resolve_plain_scalar` (so ``0.2`` or ``true`` are never written plain
as strings). Keys are compared as text only, as the loader treats keys textually.

Existing styles are taken from the model (``MappingEntry.key_style``, ``ConfigScalar.style``) and
narrowed with :func:`quote_style_of`, never from span text. Strings starting with ``file:`` get no
special treatment here (2.7).
"""

import re
from collections.abc import Callable
from enum import Enum
from typing import Final, Literal, TypeAlias

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError
from ruamel.yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

from troupe.config.errors import WriteRefusedError
from troupe.config.model import ScalarStyle
from troupe.config.paths import ConfigPath
from troupe.config.scalars import resolve_plain_scalar
from troupe.config.writeback.value import NewScalar


class Context(Enum):
    """Where a scalar is written: as a value or a key, in a block or a flow collection."""

    BLOCK_VALUE = "block-value"
    FLOW_VALUE = "flow-value"
    BLOCK_KEY = "block-key"
    FLOW_KEY = "flow-key"


# A runtime alias (not a ``type`` statement) so it can be used in ``Literal`` narrowing at runtime.
QuoteStyle: TypeAlias = Literal[  # noqa: UP040
    ScalarStyle.PLAIN, ScalarStyle.SINGLE_QUOTED, ScalarStyle.DOUBLE_QUOTED
]
"""The styles a one-line scalar or a key can be written in."""

_MINIMAL_CHAIN: Final[tuple[QuoteStyle, ...]] = (
    ScalarStyle.PLAIN,
    ScalarStyle.SINGLE_QUOTED,
    ScalarStyle.DOUBLE_QUOTED,
)
_FLOW_CONTEXTS: Final = frozenset({Context.FLOW_VALUE, Context.FLOW_KEY})
_KEY_CONTEXTS: Final = frozenset({Context.BLOCK_KEY, Context.FLOW_KEY})

_CONTROL: Final = re.compile("[\x00-\x1f\x7f-\x9f]")
"""Control characters (6.4-6.6): C0, DEL and C1."""
_CONTROL_EXCEPT_TAB: Final = re.compile("[\x00-\x08\x0a-\x1f\x7f-\x9f]")
_NOT_PRINTABLE: Final = re.compile("[\ud800-\udfff\ufffe\uffff]")
"""Non-control characters YAML cannot hold literally (outside ``c-printable``)."""
_SURROGATE: Final = re.compile("[\ud800-\udfff]")
_FLOW_INDICATORS: Final = frozenset(",[]{}")
_MERGE_KEY: Final = "<<"
"""A plain key with this text is a YAML merge key, which the loader refuses (see the merge-key
rule in ``troupe.config.loader``, whose constant is private); such a key is quoted instead."""

_NAMED_ESCAPES: Final = {
    "\x00": "\\0",
    "\x07": "\\a",
    "\x08": "\\b",
    "\x09": "\\t",
    "\x0a": "\\n",
    "\x0b": "\\v",
    "\x0c": "\\f",
    "\x0d": "\\r",
    "\x1b": "\\e",
    "\x85": "\\N",
    "\\": "\\\\",
    '"': '\\"',
}
_DOUBLE_QUOTED_ESCAPED: Final = re.compile('[\\\\"\x00-\x1f\x7f-\x9f\ud800-\udfff\ufffe\uffff]')

_PROBE_VALUE: Final = "v"
"""The value written after a probed key."""


def quote_style_of(style: ScalarStyle | None) -> QuoteStyle | None:
    """Narrow a model style to a quote style; literal, folded and ``None`` give ``None``.

    ``None`` comes in for a sibling whose value has no scalar style (a text-include directive).
    """
    if style is ScalarStyle.PLAIN:
        return ScalarStyle.PLAIN
    if style is ScalarStyle.SINGLE_QUOTED:
        return ScalarStyle.SINGLE_QUOTED
    if style is ScalarStyle.DOUBLE_QUOTED:
        return ScalarStyle.DOUBLE_QUOTED
    return None


def can_represent(style: QuoteStyle, text: str, context: Context) -> bool:
    """Return whether ``text`` in ``style`` at ``context`` reads back as ``text`` (6.4, 6.5).

    Double-quoted represents every string (characters YAML cannot hold literally are escaped).
    """
    if style is ScalarStyle.DOUBLE_QUOTED:
        return True
    if style is ScalarStyle.SINGLE_QUOTED:
        return not (_CONTROL_EXCEPT_TAB.search(text) or _NOT_PRINTABLE.search(text))
    return _plain_possible(text, context)


def choose_style(value: str, context: Context, preferred: QuoteStyle | None) -> QuoteStyle:
    """Keep ``preferred`` if it can represent ``value``, else plain, single-, double-quoted.

    ``preferred`` is the existing style of a changed scalar (6.1, 6.2) or the style of the
    preceding sibling of a new key or value (7.1-7.3); ``None`` starts the minimal chain directly.
    """
    if preferred is not None and can_represent(preferred, value, context):
        return preferred
    for style in _MINIMAL_CHAIN:
        if can_represent(style, value, context):
            return style
    raise AssertionError("unreachable: double-quoted represents every string")  # pragma: no cover


def render_scalar(
    value: NewScalar, context: Context, preferred: QuoteStyle | None, path: ConfigPath
) -> str:
    """Return the text of ``value`` written at ``context``.

    Non-strings are written plain in the spelling given in VALUE (6.3; an empty spelling of null
    gives an empty text). Strings are written in :func:`choose_style`'s style; a string with a line
    break is double-quoted here (callers in block context use a literal block instead).

    Raises :class:`WriteRefusedError` naming ``path`` when the string holds a lone surrogate (from
    a ``\\ud800`` escape in VALUE), which cannot be written as UTF-8.
    """
    if not isinstance(value.value, str):
        if value.spelling is None:
            raise ValueError(f"non-string scalar {value.value!r} has no VALUE spelling")
        return value.spelling
    _refuse_surrogates(value.value, path)
    return _render(value.value, choose_style(value.value, context, preferred))


def render_key(
    key: str, context: Context, preferred: QuoteStyle | None, *, path: ConfigPath | None = None
) -> str:
    """Return the text of mapping key ``key`` written at ``context`` (a block or flow key, 7.1).

    Raises :class:`WriteRefusedError` (naming ``path`` when given) for a lone surrogate.
    """
    if context not in _KEY_CONTEXTS:
        raise ValueError(f"{context} is not a key context")
    _refuse_surrogates(key, path)
    return _render(key, choose_style(key, context, preferred))


def _render(text: str, style: QuoteStyle) -> str:
    if style is ScalarStyle.PLAIN:
        return text
    if style is ScalarStyle.SINGLE_QUOTED:
        return "'" + text.replace("'", "''") + "'"
    return '"' + _DOUBLE_QUOTED_ESCAPED.sub(_escape, text) + '"'


def _escape(match: re.Match[str]) -> str:
    """Escape one character for a double-quoted scalar (6.6)."""
    char = match.group()
    named = _NAMED_ESCAPES.get(char)
    if named is not None:
        return named
    code = ord(char)
    return f"\\x{code:02X}" if code <= 0xFF else f"\\u{code:04X}"


def _refuse_surrogates(text: str, path: ConfigPath | None) -> None:
    match = _SURROGATE.search(text)
    if match is not None:
        raise WriteRefusedError(
            f"the string {text!r} contains the lone surrogate U+{ord(match.group()):04X}, "
            "which cannot be written as UTF-8",
            path=None if path is None else str(path),
        )


# --- plain representability ----------------------------------------------------------------------


def _plain_possible(text: str, context: Context) -> bool:
    if text == "" or text != text.strip(" \t"):
        return False
    if _CONTROL.search(text) or _NOT_PRINTABLE.search(text):
        return False
    if context in _FLOW_CONTEXTS and not _FLOW_INDICATORS.isdisjoint(text):
        return False
    if context not in _KEY_CONTEXTS and not isinstance(resolve_plain_scalar(text), str):
        return False
    if context in _KEY_CONTEXTS and text == _MERGE_KEY:
        return False
    return all(_probe(document, pick, text) for document, pick in _probes(text, context))


_Pick: TypeAlias = Callable[[Node], Node | None]  # noqa: UP040
"""Picks the probed node out of a composed probe document."""


def _probes(text: str, context: Context) -> tuple[tuple[str, _Pick], ...]:
    """The probe documents placing ``text`` at ``context`` (a value: as mapping value and item)."""
    if context is Context.BLOCK_VALUE:
        return ((f"k: {text}\n", _mapping_value), (f"- {text}\n", _sequence_item))
    if context is Context.FLOW_VALUE:
        return ((f"[{text}]\n", _sequence_item), (f"{{k: {text}}}\n", _mapping_value))
    if context is Context.BLOCK_KEY:
        return ((f"{text}: {_PROBE_VALUE}\n", _mapping_key),)
    return ((f"{{{text}: {_PROBE_VALUE}}}\n", _mapping_key),)


def _probe(document: str, pick: _Pick, text: str) -> bool:
    """Compose ``document`` and check the picked node is an untagged plain scalar ``text``."""
    try:
        root = YAML(typ="rt").compose(document)
    except YAMLError:
        return False
    if not isinstance(root, Node):
        return False
    node = pick(root)
    return (
        isinstance(node, ScalarNode)
        and node.style is None
        and node.anchor is None
        and node.ctag.handle is None
        and str(node.value) == text
    )


# ruamel.yaml node attributes are typed ``Any`` upstream; they are narrowed with ``isinstance``.
def _mapping_value(root: Node) -> Node | None:
    if isinstance(root, MappingNode) and len(root.value) == 1:
        key, value = root.value[0]
        if isinstance(key, ScalarNode) and key.value == "k" and isinstance(value, Node):
            return value
    return None


def _mapping_key(root: Node) -> Node | None:
    if isinstance(root, MappingNode) and len(root.value) == 1:
        key, value = root.value[0]
        if isinstance(value, ScalarNode) and value.value == _PROBE_VALUE and isinstance(key, Node):
            return key
    return None


def _sequence_item(root: Node) -> Node | None:
    if isinstance(root, SequenceNode) and len(root.value) == 1 and isinstance(root.value[0], Node):
        return root.value[0]
    return None
