"""YamlStyle: how a string is written as a YAML scalar, and what a literal block can hold.

This is the single home of the write-neutral scalar presentation rules, shared by write-back
(``troupe.config.writeback.scalar_render`` and ``.block_scalar``, which import and re-export these
names) and by ``show``'s YAML output (config-get-show design, Decision 1). Nothing here refuses a
write: refusals such as lone surrogates in a written file stay in write-back.

A string is written in a *quote style*: plain, single-quoted or double-quoted. :func:`choose_style`
keeps a preferred style when it can represent the text; otherwise the minimal style is used: plain
if possible, otherwise single-quoted, otherwise double-quoted, which can represent every string
(characters YAML cannot hold literally are escaped by :func:`quote`). No style folds or wraps.

Plain representability is decided by a cheap syntactic check followed by a parse probe: the text is
placed in a tiny document of the exact context (block value, flow value, block key, flow key),
composed with the same ruamel.yaml composer the loader uses, and must come back as a plain scalar
with exactly the same text, and, for values, typed as that same string by the shared
:func:`~troupe.config.scalars.resolve_plain_scalar` (so ``0.2`` or ``true`` are never written plain
as strings). Keys are compared as text only, as the loader treats keys textually.

A multi-line string in a literal block takes the chomping its trailing line breaks require
(:func:`required_chomping`); :func:`literal_characters_fit` tells whether a literal block can hold
every character of a string.
"""

import re
from collections.abc import Callable
from enum import Enum
from typing import Final, Literal, TypeAlias

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError
from ruamel.yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

from troupe.config.model import ScalarStyle
from troupe.config.scalars import resolve_plain_scalar


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
"""Control characters (config-set-writeback 6.4-6.6): C0, DEL and C1."""
_CONTROL_EXCEPT_TAB: Final = re.compile("[\x00-\x08\x0a-\x1f\x7f-\x9f]")
_NOT_PRINTABLE: Final = re.compile("[\ud800-\udfff\ufffe\uffff]")
"""Non-control characters YAML cannot hold literally (outside ``c-printable``)."""
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
    """Return whether ``text`` in ``style`` at ``context`` reads back as ``text``.

    Double-quoted represents every string (characters YAML cannot hold literally are escaped).
    """
    if style is ScalarStyle.DOUBLE_QUOTED:
        return True
    if style is ScalarStyle.SINGLE_QUOTED:
        return not (_CONTROL_EXCEPT_TAB.search(text) or _NOT_PRINTABLE.search(text))
    return _plain_possible(text, context)


def choose_style(value: str, context: Context, preferred: QuoteStyle | None) -> QuoteStyle:
    """Keep ``preferred`` if it can represent ``value``, else plain, single-, double-quoted.

    For write-back, ``preferred`` is the existing style of a changed scalar or the style of the
    preceding sibling of a new key or value; ``None`` starts the minimal chain directly.
    """
    if preferred is not None and can_represent(preferred, value, context):
        return preferred
    for style in _MINIMAL_CHAIN:
        if can_represent(style, value, context):
            return style
    raise AssertionError("unreachable: double-quoted represents every string")  # pragma: no cover


def quote(text: str, style: QuoteStyle) -> str:
    """Return ``text`` written in ``style`` on one line.

    Plain is the text as is (callers check :func:`can_represent` first); single-quoted doubles
    single quotes; double-quoted escapes ``\\``, ``"``, control characters and the characters YAML
    cannot hold literally (lone surrogates included, without refusing them).
    """
    if style is ScalarStyle.PLAIN:
        return text
    if style is ScalarStyle.SINGLE_QUOTED:
        return "'" + text.replace("'", "''") + "'"
    return '"' + _DOUBLE_QUOTED_ESCAPED.sub(_escape, text) + '"'


def _escape(match: re.Match[str]) -> str:
    """Escape one character for a double quote (named escape, else ``\\xHH``/``\\uHHHH``)."""
    char = match.group()
    named = _NAMED_ESCAPES.get(char)
    if named is not None:
        return named
    code = ord(char)
    return f"\\x{code:02X}" if code <= 0xFF else f"\\u{code:04X}"


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


# --- literal blocks ------------------------------------------------------------------------------


class Chomping(Enum):
    """Block chomping indicator: strip (``-``), clip (nothing) or keep (``+``)."""

    STRIP = "-"
    CLIP = ""
    KEEP = "+"


_UNFIT: Final = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f\u2028\u2029\ud800-\udfff\ufffe\uffff]")
"""Characters a literal block cannot hold: control characters other than TAB and LF (C0, DEL, C1
including U+0085), the Unicode line/paragraph separators, lone surrogates and the non-characters
U+FFFE/U+FFFF, none of which YAML accepts literally."""


def _trailing_breaks(value: str) -> int:
    return len(value) - len(value.rstrip("\n"))


def required_chomping(value: str) -> Chomping:
    """The chomping a value's trailing line breaks require.

    Strip for none, clip for exactly one, keep for more. A value made only of line breaks needs
    keep even for a single one (``"\\n"``): clip without a content line reads back as ``""``.
    """
    trailing = _trailing_breaks(value)
    if trailing == 0:
        return Chomping.STRIP
    return Chomping.CLIP if trailing == 1 and trailing < len(value) else Chomping.KEEP


def literal_characters_fit(value: str) -> bool:
    """Whether a literal block can hold every character of ``value`` (see ``_UNFIT``).

    Only characters are checked; a first line starting with a space needs an indentation
    indicator, which is the caller's concern.
    """
    return _UNFIT.search(value) is None
