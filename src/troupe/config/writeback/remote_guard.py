"""RemoteGuard: the refusal of every write into remote content (config-remote-schema 6.1, 6.2).

Remote content is read-only from troupe's point of view. The planner and the alias expander ask
:func:`remote_refusal` for the error to raise whenever an edit would go into a remote source
(research.md Decision 5). The error tells the user which include line to change instead: the
local file holding the outermost include directive that leads to the remote source, PATH, the
directive's text exactly as written there (quotes included) and its 1-based line.

The directive is looked up among the include references of the loaded document. The ones met on
PATH's walk come first, then those at or below PATH's deepest node, then every other one in
document order, so that a file included at several places is reported at PATH's own place. While
the directive found is written in a remote source (remote in remote, 6.2), the lookup continues
with the include whose target is that source, until the directive lies in a local file.
"""

from collections.abc import Iterator
from typing import Final

from troupe.config.errors import WriteRefusedError
from troupe.config.model import (
    ConfigDocument,
    ConfigMapping,
    ConfigNode,
    ConfigSequence,
    IncludeRef,
    SourceFile,
)
from troupe.config.paths import ConfigPath
from troupe.config.resolve import walk_path

REMOTE_READ_ONLY: Final = (
    "the value comes from the remote include {string} (line {line}); troupe does not change "
    "remote content: change that include line or the shared configuration"
)
"""The problem of a write into remote content; ``{string}`` is the include directive as written
in the local file and ``{line}`` its 1-based line (6.1)."""


def remote_refusal(
    document: ConfigDocument, source: SourceFile, path: ConfigPath
) -> WriteRefusedError:
    """The refusal (exit 3) of a write into the remote ``source`` at ``path``.

    It names the local file holding the outermost include directive that leads to ``source``,
    preferring the one on PATH's walk (6.2), PATH, the directive's text exactly as written and
    its line (6.1).
    """
    refs = _ordered_refs(document, path)
    key = source.key
    seen: set[object] = set()
    found: IncludeRef | None = None
    while key not in seen:
        seen.add(key)
        found = next((ref for ref in refs if ref.target.key == key), None)
        if found is None or found.directive.file.url is None:
            break
        key = found.directive.file.key
    if found is None or found.directive.file.url is not None:  # pragma: no cover - backstop
        # Every remote source is reached through a directive written in a local file.
        raise ValueError(f"no local include directive leads to {source.shown}")
    directive = found.directive
    text = directive.file.text
    written = text[directive.span.start : directive.span.end]
    line = text.count("\n", 0, directive.span.start) + 1
    return WriteRefusedError(
        REMOTE_READ_ONLY.format(string=written, line=line),
        file=directive.file.shown,
        path=str(path),
    )


def _ordered_refs(document: ConfigDocument, path: ConfigPath) -> list[IncludeRef]:
    """Every include reference of ``document``, without repeats: those of the nodes on PATH's
    walk (and their merge layers) first, then those at or below its deepest node, then the
    rest in document order."""
    walk = walk_path(document.root, path)
    ordered: dict[int, IncludeRef] = {}
    for node in walk.nodes:
        for ref in _layer_refs(node):
            ordered.setdefault(id(ref), ref)
    for ref in _subtree_refs(walk.deepest):
        ordered.setdefault(id(ref), ref)
    for ref in _subtree_refs(document.root):
        ordered.setdefault(id(ref), ref)
    return list(ordered.values())


def _layer_refs(node: ConfigNode) -> Iterator[IncludeRef]:
    """The include references ``node`` stands for: its include chain and those of the layers of
    a merged mapping."""
    ref = node.include
    while ref is not None:
        yield ref
        ref = ref.inner
    if isinstance(node, ConfigMapping) and node.layers is not None:
        yield from _layer_refs(node.layers.base)
        yield from _layer_refs(node.layers.over)


def _subtree_refs(node: ConfigNode) -> Iterator[IncludeRef]:
    """The include references of ``node`` and every node below it, in document order."""
    yield from _layer_refs(node)
    children: tuple[ConfigNode, ...] = ()
    if isinstance(node, ConfigMapping):
        children = tuple(entry.value for entry in node.entries)
        if node.layers is not None:
            children += (node.layers.base, node.layers.over)
    elif isinstance(node, ConfigSequence):
        children = node.items
    for child in children:
        yield from _subtree_refs(child)
