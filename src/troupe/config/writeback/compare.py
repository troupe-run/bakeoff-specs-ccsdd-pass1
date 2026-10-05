"""PlainTree: model nodes and parsed values as ordered plain trees; typed equality; splicing.

This module is the single definition of "same configuration" used for no-op detection, merge
decisions, verification and the leaf diff:

* :func:`node_to_plain` turns a loaded node into an ordered plain tree in which every include root
  is an :class:`IncludeDirective` carrying its ``file:`` text and its expanded content;
* :func:`root_plain` is the plain root mapping of a document, unwrapping a mapping-form root
  include;
* :func:`new_to_plain` turns a parsed VALUE into a plain tree in which every string starting with
  :data:`~troupe.config.constants.INCLUDE_PREFIX` is a directive without content and every mapping
  whose ``$ref`` is such a string is a directive without content carrying its overrides;
* :func:`plain_equal` compares by type and value (floats by ``repr``), using
  :func:`~troupe.config.scalars.scalar_equal` (re-exported here) for scalars, and a directive with
  overrides by containment in the loaded content;
* :func:`splice` builds the intended configuration: the value at a path replaced, with mappings and
  lists merged entry-wise and strings placed over text includes kept as the include.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from troupe.config.constants import INCLUDE_PREFIX, INCLUDE_REF_KEY
from troupe.config.model import (
    ConfigDocument,
    ConfigMapping,
    ConfigNode,
    ConfigScalar,
    IncludeKind,
)
from troupe.config.paths import ConfigPath, is_index_segment
from troupe.config.scalars import ScalarValue
from troupe.config.scalars import scalar_equal as scalar_equal  # re-exported for existing imports
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
    overrides: "PlainMapping | None" = field(default=None, compare=False)
    """For a VALUE mapping-form include (``{$ref: file:..., <overrides>}``): the other entries in
    VALUE order; ``None`` otherwise.

    Not part of ``==``; :func:`plain_equal` requires a loaded content to contain them.
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


def root_plain(document: ConfigDocument) -> PlainMapping:
    """Return the plain mapping of ``document``'s root.

    A root that is a mapping-form include (a root ``$ref`` mapping) is unwrapped to its merged
    content. Raises :class:`TypeError` when the root is not a mapping.
    """
    plain = node_to_plain(document.root)
    if isinstance(plain, IncludeDirective):
        plain = plain.content
    if not isinstance(plain, dict):
        raise TypeError("the root of a configuration must be a mapping")
    return plain


def _node_value(node: ConfigNode) -> PlainValue:
    if isinstance(node, ConfigScalar):
        return node.value
    if isinstance(node, ConfigMapping):
        return {entry.key: node_to_plain(entry.value) for entry in node.entries}
    return [node_to_plain(item) for item in node.items]


def new_to_plain(value: NewValue) -> PlainValue:
    """Return the plain tree of a parsed VALUE; ``file:`` strings become contentless directives.

    A mapping with a ``$ref`` entry whose value is a ``file:`` string becomes a contentless
    directive of that string whose ``overrides`` are the plain trees of the other entries, in
    VALUE order. A ``$ref`` with any other value stays an ordinary mapping entry.
    """
    if isinstance(value, NewScalar):
        text = _include_text(value)
        return value.value if text is None else IncludeDirective(text, None)
    if isinstance(value, NewMapping):
        ref = dict(value.entries).get(INCLUDE_REF_KEY)
        text = None if ref is None else _include_text(ref)
        if text is not None:
            overrides = {
                key: new_to_plain(item) for key, item in value.entries if key != INCLUDE_REF_KEY
            }
            return IncludeDirective(text, None, overrides=overrides)
        return {key: new_to_plain(item) for key, item in value.entries}
    return [new_to_plain(item) for item in value.items]


def _include_text(value: NewValue) -> str | None:
    """Return ``value``'s string when it is a ``file:`` string scalar, else ``None``."""
    if (
        isinstance(value, NewScalar)
        and isinstance(value.value, str)
        and value.value.startswith(INCLUDE_PREFIX)
    ):
        return value.value
    return None


def plain_equal(a: PlainValue, b: PlainValue, *, ordered: bool) -> bool:
    """Return whether two plain trees are the same configuration.

    Directives are equal when their texts are equal and, unless either content is ``None``, their
    contents are equal; a directive never equals a non-directive value. A contentless directive
    with ``overrides`` (a VALUE ``$ref`` mapping) and a directive with content are equal when the
    texts are equal and the content contains the overrides (see :func:`_contains`). With
    ``ordered=True`` mappings must also list their keys in the same order (verification);
    ``ordered=False`` ignores mapping key order (no-op detection and merge decisions).
    """
    if isinstance(a, IncludeDirective) or isinstance(b, IncludeDirective):
        if not (isinstance(a, IncludeDirective) and isinstance(b, IncludeDirective)):
            return False
        if a.text != b.text:
            return False
        if a.content is None and a.overrides is not None and b.content is not None:
            return _contains(b.content, a.overrides, ordered=ordered)
        if b.content is None and b.overrides is not None and a.content is not None:
            return _contains(a.content, b.overrides, ordered=ordered)
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


def _contains(content: PlainValue, overrides: PlainMapping, *, ordered: bool) -> bool:
    """Return whether the loaded ``content`` contains the mapping ``overrides``.

    ``content`` must be a mapping (or an include directive whose content is one) holding every
    override key. A mapping override is contained recursively in the content's value, an include
    override merged into a mapping as :func:`_contains_value` says; any other override must be
    :func:`plain_equal` (with ``ordered``) to it. Containment ignores the order of
    keys: the merge decides where override keys land among the included keys, and extra content
    keys come from the included file.
    """
    if isinstance(content, IncludeDirective):
        content = content.content
    if not isinstance(content, dict):
        return False
    for key, override in overrides.items():
        if key not in content:
            return False
        if not _contains_value(content[key], override, ordered=ordered):
            return False
    return True


def _contains_value(loaded: PlainValue, override: PlainValue, *, ordered: bool) -> bool:
    """Return whether the loaded value at an override's key holds the ``override``.

    An include override (a contentless directive) whose key holds a mapping in the included file
    is merged into that mapping by the loader (1.7), so the loaded value is a plain mapping: a
    scalar-form include then holds (its content is not known here) and a ``$ref`` form needs its
    own overrides contained in that mapping.
    """
    if isinstance(override, dict):
        return _contains(loaded, override, ordered=ordered)
    if (
        isinstance(override, IncludeDirective)
        and override.content is None
        and isinstance(loaded, dict)
    ):
        if override.overrides is None:
            return True
        return _contains(loaded, override.overrides, ordered=ordered)
    return plain_equal(override, loaded, ordered=ordered)


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
