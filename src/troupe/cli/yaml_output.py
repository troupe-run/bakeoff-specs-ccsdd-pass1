"""YamlOutput: render a configuration node as block-style YAML (config-get-show 3.1-3.7, 4.2-4.8).

``show`` prints a mapping or a list (or the whole configuration) with :func:`render_yaml`. Only the
values and their order are printed: the source's comments, quoting, flow style, blank lines and
number spellings are not carried over (3.6). The output reads back with the project's own loader
as the printed node (4.1).

The renderer first builds a list of :class:`OutputLine`, each holding the text of one output line
and the file of the scalar the line holds (``None`` for ``key:`` lines, ``{}``/``[]`` lines and
dash lines that open a nested collection). It then joins them with ``\\n`` plus a final ``\\n``;
with a labeler, a line holding a scalar gets `` # from <label>`` (design Decision 2), so the output
with labels equals the output without them once the suffixes are removed.

Layout (3.1-3.5, 3.7): two spaces per level; ``key: value`` in document order; a non-empty
collection value is a ``key:`` line followed by its content two spaces deeper; list items are
``- `` two spaces deeper than their key (column 0 for a printed list); a collection inside a list
item starts on the dash line (compact form); empty collections are ``{}``/``[]``, an empty
configuration ``{}``; no document markers and no trailing spaces.

Scalars and keys (4.2-4.8) use the shared presentation rules of ``troupe.config.yaml_style``.
"""

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from troupe.config.model import (
    ConfigMapping,
    ConfigNode,
    ConfigScalar,
    ConfigSequence,
    ScalarStyle,
    SourceFile,
)
from troupe.config.scalars import resolve_plain_scalar
from troupe.config.yaml_style import Context, QuoteStyle, can_represent, choose_style, quote

type SourceLabeler = Callable[[SourceFile], str]
"""Names a loaded file in a source comment (e.g. ``source_label`` bound to a theatre)."""

_INDENT: Final = 2
_DASH: Final = "- "
_EMPTY_MAPPING: Final = "{}"
_EMPTY_SEQUENCE: Final = "[]"
_SOURCE_PREFIX: Final = " # from "
_PLAIN: Final[QuoteStyle] = ScalarStyle.PLAIN
_SINGLE_QUOTED: Final[QuoteStyle] = ScalarStyle.SINGLE_QUOTED
_DOUBLE_QUOTED: Final[QuoteStyle] = ScalarStyle.DOUBLE_QUOTED


@dataclass(frozen=True)
class OutputLine:
    """One output line and the file of the scalar it holds (``None`` when it holds none)."""

    text: str
    source: SourceFile | None


def render_yaml(node: ConfigNode, *, label: SourceLabeler | None) -> bytes:
    """Block YAML for ``node`` (a mapping or a sequence), UTF-8 encoded.

    With ``label``, every line holding a scalar ends with `` # from `` + ``label(file)``.
    """
    if isinstance(node, ConfigScalar):
        raise NotImplementedError("rendering a scalar alone as a document is not supported")
    lines = _collection_lines(node, 0)
    return "".join(_joined(line, label) + "\n" for line in lines).encode("utf-8")


def _joined(line: OutputLine, label: SourceLabeler | None) -> str:
    if label is None or line.source is None:
        return line.text
    return line.text + _SOURCE_PREFIX + label(line.source)


# --- layout -------------------------------------------------------------------------------------


def _collection_lines(node: ConfigMapping | ConfigSequence, indent: int) -> list[OutputLine]:
    """The lines of a collection whose entries or items start at column ``indent`` (3.1-3.5)."""
    if isinstance(node, ConfigMapping):
        if not node.entries:
            return [OutputLine(" " * indent + _EMPTY_MAPPING, None)]
        return [
            line for entry in node.entries for line in _entry_lines(entry.key, entry.value, indent)
        ]
    if not node.items:
        return [OutputLine(" " * indent + _EMPTY_SEQUENCE, None)]
    return [line for item in node.items for line in _item_lines(item, indent)]


def _entry_lines(key: str, value: ConfigNode, indent: int) -> list[OutputLine]:
    """``key: scalar``, ``key: {}``/``key: []``, or ``key:`` followed by the content (3.2, 3.3)."""
    head = " " * indent + _key_text(key) + ":"
    if isinstance(value, ConfigScalar):
        return _scalar_lines(head + " ", value)
    if _is_empty(value):
        return [OutputLine(head + " " + _empty_text(value), None)]
    return [OutputLine(head, None), *_collection_lines(value, indent + _INDENT)]


def _item_lines(item: ConfigNode, indent: int) -> list[OutputLine]:
    """``- scalar``, ``- {}``/``- []``, or a collection in compact form on the dash line (3.3)."""
    dash = " " * indent + _DASH
    if isinstance(item, ConfigScalar):
        return _scalar_lines(dash, item)
    if _is_empty(item):
        return [OutputLine(dash + _empty_text(item), None)]
    first, *rest = _collection_lines(item, indent + _INDENT)
    return [OutputLine(dash + first.text[indent + _INDENT :], first.source), *rest]


def _is_empty(node: ConfigMapping | ConfigSequence) -> bool:
    return not (node.entries if isinstance(node, ConfigMapping) else node.items)


def _empty_text(node: ConfigMapping | ConfigSequence) -> str:
    return _EMPTY_MAPPING if isinstance(node, ConfigMapping) else _EMPTY_SEQUENCE


# --- scalars and keys ---------------------------------------------------------------------------


def _scalar_lines(prefix: str, node: ConfigScalar) -> list[OutputLine]:
    """The line(s) of a scalar written after ``prefix`` (``key: `` or ``- ``), sourced (4.2-4.7).

    This is the single place deciding a scalar's form, so that all its lines are produced here.
    """
    return [OutputLine(prefix + _one_line_scalar(node), node.origin.file)]


def _one_line_scalar(node: ConfigScalar) -> str:
    """A scalar on one line: canonical non-string forms (4.2) or a quoted string (4.3, 4.6, 4.7).

    A string containing a line feed is written double-quoted with escapes, which reads back as
    the same string.
    """
    value = node.value
    if isinstance(value, str):
        if "\n" in value:
            return quote(value, _DOUBLE_QUOTED)
        return quote(value, choose_style(value, Context.BLOCK_VALUE, None))
    if isinstance(value, bool):  # before int: bool is a subclass of int
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, int):
        return str(value)
    if math.isnan(value):
        return ".nan"
    if math.isinf(value):
        return ".inf" if value > 0 else "-.inf"
    return repr(value)


def _key_text(key: str) -> str:
    """A key written plain only when it reads back as the same string key (4.8).

    ``choose_style`` compares keys as text, so a plain key the core schema would read as a
    non-string (``true``, ``null``, ``1.5``) restarts the chain at single-quoted.
    """
    style = choose_style(key, Context.BLOCK_KEY, None)
    if style is _PLAIN and not isinstance(resolve_plain_scalar(key), str):
        style = (
            _SINGLE_QUOTED
            if can_represent(_SINGLE_QUOTED, key, Context.BLOCK_KEY)
            else _DOUBLE_QUOTED
        )
    return quote(key, style)
