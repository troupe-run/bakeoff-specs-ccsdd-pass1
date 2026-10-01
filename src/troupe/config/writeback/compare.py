"""PlainTree: model nodes and parsed values as ordered plain trees; typed equality; splicing.

This module is the single definition of "same configuration" used for no-op detection, merge
decisions, verification and the leaf diff:

* :func:`node_to_plain` turns a loaded node into an ordered plain tree in which every include root
  is an :class:`IncludeDirective` carrying its ``file:`` text and its expanded content;
* :func:`new_to_plain` turns a parsed VALUE into a plain tree in which every string starting with
  :data:`~troupe.config.constants.INCLUDE_PREFIX` is a directive without content;
* :func:`scalar_equal` and :func:`plain_equal` compare by type and value (floats by ``repr``);
* :func:`splice` builds the intended configuration: the value at a path replaced, with mappings and
  lists merged entry-wise and strings placed over text includes kept as the include.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from troupe.config.constants import INCLUDE_PREFIX
from troupe.config.model import ConfigMapping, ConfigNode, ConfigScalar, IncludeKind
from troupe.config.paths import ConfigPath, is_index_segment
from troupe.config.scalars import ScalarValue
from troupe.config.writeback.value import NewMapping, NewScalar, NewValue


@dataclass(frozen=True)
class IncludeDirective:
    """An include root in a plain tree: the ``file:`` string and what it loads."""

    text: str
    """The whole ``file:...`` string as written."""
    content: "PlainValue | None"
    """The expanded content; ``None`` means "whatever the include loads" (written by this set)."""
    kind: IncludeKind | None = field(default=None, compare=False)
    """``YAML`` or ``TEXT`` for a loaded include root; ``None`` for a directive from VALUE.

    Not part of equality (only ``text`` and ``content`` are). It lets :func:`splice` recognise a
    text include without re-deciding the include kind outside the loader.
    """


type PlainValue = ScalarValue | IncludeDirective | PlainMapping | list[PlainValue]
"""One node of a plain tree: a typed scalar, an include directive, a mapping or a list."""

type PlainMapping = dict[str, PlainValue]
"""A plain mapping; insertion order is the key order."""

type TextContent = Callable[[str, IncludeDirective], PlainValue]
"""Gives the content of a text include (second argument) when a string (first) is set over it."""


def node_to_plain(node: ConfigNode) -> PlainValue:
    """Return the ordered plain tree of a loaded node.

    An include root becomes ``IncludeDirective(INCLUDE_PREFIX + include.spec, <expanded content>)``
    (a text include's content is the target's exact text). A YAML include whose whole content is
    another ``file:`` string carries only the outer include, so it is one YAML directive whose
    content is the inner include's value.
    """
    plain = _node_value(node)
    if node.include is None:
        return plain
    return IncludeDirective(INCLUDE_PREFIX + node.include.spec, plain, node.include.kind)


def _node_value(node: ConfigNode) -> PlainValue:
    if isinstance(node, ConfigScalar):
        return node.value
    if isinstance(node, ConfigMapping):
        return {entry.key: node_to_plain(entry.value) for entry in node.entries}
    return [node_to_plain(item) for item in node.items]


def new_to_plain(value: NewValue) -> PlainValue:
    """Return the plain tree of a parsed VALUE; ``file:`` strings become contentless directives."""
    if isinstance(value, NewScalar):
        if isinstance(value.value, str) and value.value.startswith(INCLUDE_PREFIX):
            return IncludeDirective(value.value, None)
        return value.value
    if isinstance(value, NewMapping):
        return {key: new_to_plain(item) for key, item in value.entries}
    return [new_to_plain(item) for item in value.items]


def scalar_equal(a: ScalarValue, b: ScalarValue) -> bool:
    """Return whether two scalars have the same type and value (floats compared by ``repr``).

    So ``1``, ``1.0``, ``True`` and ``"1"`` are pairwise different, ``nan`` equals ``nan`` and
    ``0.0`` differs from ``-0.0``.
    """
    if type(a) is not type(b):
        return False
    if isinstance(a, float):
        return repr(a) == repr(b)
    return a == b


def plain_equal(a: PlainValue, b: PlainValue, *, ordered: bool) -> bool:
    """Return whether two plain trees are the same configuration.

    Directives are equal when their texts are equal and, unless either content is ``None``, their
    contents are equal; a directive never equals a non-directive value. With ``ordered=True``
    mappings must also list their keys in the same order (verification); ``ordered=False`` ignores
    mapping key order (no-op detection and merge decisions).
    """
    if isinstance(a, IncludeDirective) or isinstance(b, IncludeDirective):
        if not (isinstance(a, IncludeDirective) and isinstance(b, IncludeDirective)):
            return False
        if a.text != b.text:
            return False
        if a.content is None or b.content is None:
            return True
        return plain_equal(a.content, b.content, ordered=ordered)
    if isinstance(a, dict) or isinstance(b, dict):
        if not (isinstance(a, dict) and isinstance(b, dict)):
            return False
        if ordered and list(a) != list(b):
            return False
        if a.keys() != b.keys():
            return False
        return all(plain_equal(value, b[key], ordered=ordered) for key, value in a.items())
    if isinstance(a, list) or isinstance(b, list):
        if not (isinstance(a, list) and isinstance(b, list)):
            return False
        if len(a) != len(b):
            return False
        return all(plain_equal(x, y, ordered=ordered) for x, y in zip(a, b, strict=True))
    return scalar_equal(a, b)


def splice(
    root: PlainMapping, path: ConfigPath, value: PlainValue, text_content: TextContent
) -> PlainMapping:
    """Return the intended configuration: ``root`` with ``value`` set at ``path``.

    Missing intermediate mappings are created and a list index equal to the list's length appends.
    Wherever a mapping is placed over a mapping (at ``path`` or at any depth below it) the
    remaining existing keys keep their order, absent keys are dropped and new keys follow in
    ``value``'s order; a list placed over a list is merged by index. Wherever a string is placed
    over a text-include directive, the result keeps the directive's text and takes its content from
    ``text_content(string, directive)``. A YAML include directive is replaced, never merged.

    ``root`` is not modified (containers on the way to ``path`` are copied; untouched subtrees are
    shared). A path that target resolution refuses raises :class:`ValueError`.
    """
    splicer = _Splicer(path, text_content)
    result = splicer.at(root, path.segments, value)
    if not isinstance(result, dict):
        raise splicer.error("the root must stay a mapping")
    return result


class _Splicer:
    def __init__(self, path: ConfigPath, text_content: TextContent) -> None:
        self._path = path
        self._text_content = text_content

    def at(self, node: PlainValue, segments: Sequence[str], value: PlainValue) -> PlainValue:
        """Return ``node`` with ``value`` placed at the relative path ``segments``."""
        if not segments:
            return self.place(node, value)
        segment, rest = segments[0], segments[1:]
        if isinstance(node, IncludeDirective):
            if node.content is None or node.kind is IncludeKind.TEXT:
                raise self.error(
                    f"segment {segment!r} lies below an include without mapping content"
                )
            return IncludeDirective(node.text, self.at(node.content, segments, value), node.kind)
        if isinstance(node, dict):
            copy = dict(node)
            if segment in node:
                copy[segment] = self.at(node[segment], rest, value)
            else:
                copy[segment] = self._new_branch(segments, value)
            return copy
        if isinstance(node, list):
            if not is_index_segment(segment):
                raise self.error(f"segment {segment!r} is not a list index")
            index = int(segment)
            if index < len(node):
                items = list(node)
                items[index] = self.at(node[index], rest, value)
                return items
            if index == len(node) and not rest:
                return [*node, value]
            raise self.error(f"index {index} is out of range for a list of {len(node)}")
        raise self.error(f"segment {segment!r} lies below a scalar")

    def _new_branch(self, segments: Sequence[str], value: PlainValue) -> PlainValue:
        """Return ``value`` nested in new mappings for the missing key ``segments[0]``.

        One mapping is created per remaining segment; none may be digit-only (3.6).
        """
        if any(is_index_segment(segment) for segment in segments):
            raise self.error("a missing key with a digit-only segment cannot be created")
        for segment in reversed(segments[1:]):
            value = {segment: value}
        return value

    def place(self, existing: PlainValue, new: PlainValue) -> PlainValue:
        """Return the value that results from setting ``new`` where ``existing`` is."""
        if (
            isinstance(new, str)
            and isinstance(existing, IncludeDirective)
            and existing.kind is IncludeKind.TEXT
        ):
            content = self._text_content(new, existing)
            return IncludeDirective(existing.text, content, IncludeKind.TEXT)
        if isinstance(new, dict) and isinstance(existing, dict):
            merged: PlainMapping = {
                key: self.place(old, new[key]) for key, old in existing.items() if key in new
            }
            merged.update((key, item) for key, item in new.items() if key not in existing)
            return merged
        if isinstance(new, list) and isinstance(existing, list):
            common = [self.place(old, item) for old, item in zip(existing, new, strict=False)]
            return [*common, *new[len(existing) :]]
        return new

    def error(self, reason: str) -> ValueError:
        return ValueError(f"cannot splice at {str(self._path)!r}: {reason}")
