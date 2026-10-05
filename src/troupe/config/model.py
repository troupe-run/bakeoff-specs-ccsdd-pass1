"""Source-mapped configuration model (Requirements 4.2, 4.3, 6.6).

An immutable tree of configuration nodes with exact source provenance. This module is pure data:
it performs no I/O. All dataclasses are frozen and all collections are tuples. Node values are the
true YAML values (plain built-ins only; folded ``>`` scalars are folded with no U+0007 markers),
while ``SourceFile.text`` and every span are left exactly as the file was read.

Span semantics
--------------
* ``Span.start`` (inclusive) and ``Span.end`` (exclusive) are **character (code point) indices**
  into ``SourceFile.text``, the exact decoded file text (UTF-8, no newline translation, BOM kept).
  A byte offset, when needed, is ``len(text[:i].encode("utf-8"))``.
* Plain and quoted scalar spans are exact; quoted spans cover the quotes.
* A block scalar's span starts at its indicator (``|`` or ``>``, with any chomping/indentation
  indicators) and covers the header line and all content lines including the final line break, but
  it may also extend over trailing blank lines (for ``'|\\n  a\\n  b\\n\\n\\n'`` the value is
  ``'a\\nb\\n'`` and the span covers all the trailing blank lines). A block scalar's ``end`` is
  therefore not tight.
* The span of an empty (zero-length) null value (e.g. ``key:`` with nothing after it) is not a
  reliable position; consumers must not patch at it.
* Mapping and sequence spans are as reported by the composer. Flow collection spans are exact
  (``[`` ... ``]``, ``{`` ... ``}``). Block collection spans start at the first entry and may extend
  over trailing blank lines and comments, so consumers must not treat a block collection's ``end``
  as tight.
* An empty or comment-only root file yields an empty root mapping with
  ``style=CollectionStyle.BLOCK`` and ``origin=Origin(root_file, Span(0, 0))``.

Include provenance
------------------
A node produced by an include carries ``include: IncludeRef``; its own ``origin`` lies in the target
file, and the ``file:`` string it replaced is ``include.directive`` (in the including file). Nodes
nested inside an included YAML file that are not themselves include roots have ``include=None`` and
an ``origin`` in the target file. A text include is a ``ConfigScalar`` whose ``value`` is the
target's exact text, with ``style=None``, ``origin=Origin(target, Span(0, len(target.text)))`` and
``include.kind == IncludeKind.TEXT``. When an included file's root is itself an include (a
chained include such as ``c: file:a.yaml`` with a.yaml holding ``$ref: file:b.yaml``), the node
carries the outer include, whose ``inner`` is the include written as a.yaml's root.

Mapping-form includes and merged mappings
-----------------------------------------
A mapping-form include ``{$ref: "file:...", <overrides>}`` keeps the ``$ref`` mapping as written in
``include.form``. With overrides, its value is the deep merge of the included content with the
overrides (``troupe.config.overrides.deep_merge``): a **merged mapping**, whose ``layers`` hold the
included side (``base``) and the override side (``over``) as loaded. Every nested mapping present
on both sides is merged too. A merged mapping has the ``style`` and ``origin`` of its included side;
its scalars and lists are the nodes of the layer they come from, so their origins name their own
file.
A merged mapping never contains a ``$ref`` entry. Scalar-form includes and unmerged nodes have
``include.form is None`` and ``layers is None``.

Invariants: keys within one mapping are unique; no node has an explicit tag or anchor; values are
built-ins only.
"""

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TypeAlias

from troupe.config.scalars import ScalarValue


class LineEnding(Enum):
    """A file's newline convention, so writers can keep CRLF."""

    NONE = "none"  # no line break in the file
    LF = "lf"
    CRLF = "crlf"
    MIXED = "mixed"


@dataclass(frozen=True)
class SourceFile:
    """One file that was read while loading the configuration."""

    path: Path  # path as opened (display form, used in messages; writers target ``real_path``)
    real_path: Path  # Path.resolve() of path; identity for cycles and overrides
    text: str  # exact decoded content
    line_ending: LineEnding


@dataclass(frozen=True)
class Span:
    """A character range in ``SourceFile.text`` (see the module docstring for semantics)."""

    start: int  # character index, inclusive
    end: int  # character index, exclusive


@dataclass(frozen=True)
class Origin:
    """Where a node or key was written: a file and a span within it."""

    file: SourceFile
    span: Span


class IncludeKind(Enum):
    """The kind of include that produced a node."""

    NONE = "none"  # never stored on IncludeRef; used by callers as "not included"
    YAML = "yaml-include"
    TEXT = "text-include"


@dataclass(frozen=True)
class IncludeRef:
    """Provenance of a node that replaced a ``file:`` include directive."""

    kind: IncludeKind  # YAML or TEXT
    spec: str  # text after "file:" exactly as written
    target: SourceFile  # the included file
    directive: Origin  # span of the "file:..." scalar in the including file (or the `$ref` value)
    form: "ConfigMapping | None" = None
    """Mapping form: the ``$ref`` mapping as written in the including file (its entries: ``$ref``
    with the raw include string scalar, then the overrides as loaded). ``None`` for the scalar
    form."""
    inner: "IncludeRef | None" = None
    """When the target file's root is itself an include and this include has no overrides (so the
    node is the target root's content, not a merge), that inner include, whose directive is the
    target file's root; ``None`` otherwise. With overrides the inner include is kept on the merged
    mapping's included side (``layers.base.include``) instead."""


class ScalarStyle(Enum):
    """The style a scalar (value or key) was written in."""

    PLAIN = "plain"
    SINGLE_QUOTED = "single"
    DOUBLE_QUOTED = "double"
    LITERAL = "literal"  # |
    FOLDED = "folded"  # >


class CollectionStyle(Enum):
    """The style a mapping or sequence was written in."""

    BLOCK = "block"
    FLOW = "flow"


@dataclass(frozen=True)
class ConfigScalar:
    """A scalar value."""

    value: ScalarValue
    style: ScalarStyle | None  # None only for text includes
    origin: Origin
    include: IncludeRef | None = None


@dataclass(frozen=True)
class MappingEntry:
    """One key/value pair of a mapping."""

    key: str  # key scalar text as written (unquoted content)
    key_origin: Origin  # span of the key scalar (incl. quotes)
    key_style: ScalarStyle  # derived from the key node's style
    value: "ConfigNode"


@dataclass(frozen=True)
class MergeLayers:
    """The two written sides of a merged mapping (a mapping-form include with overrides)."""

    base: "ConfigMapping"
    """The included side as loaded. For an include root its ``include`` is the target root's own
    include (``None`` unless the target file's root is itself a ``$ref`` mapping), never the
    include whose overrides are merged."""
    over: "ConfigMapping"  # the override side as loaded; for an include root, its ``$ref`` mapping


@dataclass(frozen=True)
class ConfigMapping:
    """A mapping; ``entries`` are in document order (merged order for a merged mapping)."""

    entries: tuple[MappingEntry, ...]
    style: CollectionStyle
    origin: Origin
    include: IncludeRef | None = None
    layers: MergeLayers | None = None  # set on every merged mapping, None otherwise

    def get(self, key: str) -> MappingEntry | None:
        """Return the entry whose key equals ``key``, or ``None`` when there is none."""
        for entry in self.entries:
            if entry.key == key:
                return entry
        return None


@dataclass(frozen=True)
class ConfigSequence:
    """A sequence (list)."""

    items: tuple["ConfigNode", ...]
    style: CollectionStyle
    origin: Origin
    include: IncludeRef | None = None


# A runtime union (not a ``type`` statement) so callers can also use it with ``isinstance``.
ConfigNode: TypeAlias = ConfigScalar | ConfigMapping | ConfigSequence  # noqa: UP040


@dataclass(frozen=True)
class ConfigDocument:
    """A fully expanded configuration: the root file, its root mapping and every file read."""

    root_file: SourceFile
    root: ConfigMapping  # empty root file: empty BLOCK mapping at Origin(root_file, Span(0, 0))
    files: tuple[SourceFile, ...]  # every file read (root, YAML and text include targets), in order

    def include_kind(self, node: ConfigNode) -> IncludeKind:
        """Return the kind of include that produced ``node``; ``NONE`` if it is not an include."""
        if node.include is None:
            return IncludeKind.NONE
        return node.include.kind
