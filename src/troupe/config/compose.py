"""Compose one YAML file and record every alias occurrence (config-aliases Requirement 1).

ruamel.yaml's round-trip composer returns the anchored node itself at each alias, so the composed
tree cannot tell an alias place from its anchor and keeps no position of the ``*name`` token. The
recording composer hooks ``Composer.compose_node(parent, index)`` and records each alias before
ruamel resolves it, keyed by the parent node and the slot the alias fills (research.md, Decision 1):

- the key node's ``id`` for a mapping value,
- the item index for a list item,
- ``None`` for an alias used as a mapping key.

Keys use ``id()`` of composed nodes, which live as long as :attr:`ComposedFile.root`. An anchored
node is composed once, so the aliases inside it are recorded once, under that node, and are found
again wherever the node is built through an alias. Each record is checked against the source text,
which must read ``*<name>`` at the recorded span.

A reused anchor name is valid YAML (each alias refers to the most recent definition, 1.2), so
ruamel's ``ReusedAnchorWarning`` is silenced, around the compose call only. Syntax errors, several
documents and undefined aliases (1.3) are :class:`ConfigLoadError` naming the file.

Nothing is ever created, modified, renamed or deleted.
"""

import warnings
from collections.abc import Mapping
from dataclasses import dataclass

from ruamel.yaml import YAML
from ruamel.yaml.composer import Composer
from ruamel.yaml.error import MarkedYAMLError, ReusedAnchorWarning, YAMLError
from ruamel.yaml.events import AliasEvent
from ruamel.yaml.nodes import Node

from troupe.config.errors import ConfigLoadError
from troupe.config.model import SourceFile, Span


@dataclass(frozen=True)
class AliasOccurrence:
    """One alias as written: its name and the span of its ``*name`` token."""

    name: str
    span: Span


type AliasSlot = tuple[int, int | str | None]
"""``(id(parent node), slot)``: the slot is ``id(key node)`` for a mapping value, the item index
for a list item, and ``None`` for an alias used as a mapping key."""


@dataclass(frozen=True)
class ComposedFile:
    """The composed document of one file (``None`` if empty) and its alias occurrences."""

    root: Node | None
    aliases: Mapping[AliasSlot, AliasOccurrence]


class _RecordingComposer(Composer):
    """ruamel's composer, recording each alias occurrence before ruamel resolves it."""

    def __init__(self, loader: YAML | None = None) -> None:
        super().__init__(loader=loader)
        self.recorded: dict[AliasSlot, AliasOccurrence] = {}

    # ruamel calls ``compose_node(parent, index)`` with ``index`` the key node for a mapping value,
    # the item index for a list item and ``None`` for a mapping key (or the document root). The
    # parser and its events are typed ``Any`` upstream; fields are narrowed with ``str``/``int``.
    def compose_node(self, parent: Node | None, index: Node | int | None) -> Node:
        if self.parser.check_event(AliasEvent):
            event = self.parser.peek_event()
            slot = index if index is None or isinstance(index, int) else id(index)
            occurrence = AliasOccurrence(
                str(event.anchor), Span(int(event.start_mark.index), int(event.end_mark.index))
            )
            # Several aliases used as keys of one mapping share its key slot; the first is kept.
            self.recorded.setdefault((id(parent), slot), occurrence)
        node: Node = super().compose_node(parent, index)
        return node


def compose_file(source: SourceFile) -> ComposedFile:
    """Compose the single document of ``source`` and record its aliases, or raise
    :class:`ConfigLoadError` (syntax error, several documents, undefined alias, or an alias
    record that does not match the source text)."""
    yaml = YAML(typ="rt")
    yaml.Composer = _RecordingComposer
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ReusedAnchorWarning)
            node = yaml.compose(source.text)
    except MarkedYAMLError as error:
        raise ConfigLoadError(_describe_marked_error(error), file=source.path) from error
    except YAMLError as error:
        first_line = str(error).splitlines()[0] if str(error) else type(error).__name__
        raise ConfigLoadError(f"invalid YAML: {first_line}", file=source.path) from error
    composer = yaml.composer
    if not isinstance(composer, _RecordingComposer):  # pragma: no cover - set just above
        raise ConfigLoadError("invalid YAML: unexpected composer", file=source.path)
    aliases = dict(composer.recorded)
    for occurrence in aliases.values():
        written = source.text[occurrence.span.start : occurrence.span.end]
        if written != f"*{occurrence.name}":
            raise ConfigLoadError(
                f"internal error: alias '*{occurrence.name}' recorded at characters "
                f"{occurrence.span.start}-{occurrence.span.end}, where the file reads {written!r}",
                file=source.path,
            )
    if node is None:
        return ComposedFile(root=None, aliases=aliases)
    if not isinstance(node, Node):  # pragma: no cover - ruamel always returns a Node or None
        raise ConfigLoadError("invalid YAML: unexpected composer result", file=source.path)
    return ComposedFile(root=node, aliases=aliases)


def _describe_marked_error(error: MarkedYAMLError) -> str:
    """Return ``invalid YAML: <context>, <problem> (line L, column C)``, 1-based positions."""
    parts = [str(part) for part in (error.context, error.problem) if part]
    problem = ", ".join(parts) if parts else type(error).__name__
    mark = error.problem_mark or error.context_mark
    if mark is None:
        return f"invalid YAML: {problem}"
    return f"invalid YAML: {problem} (line {int(mark.line) + 1}, column {int(mark.column) + 1})"
