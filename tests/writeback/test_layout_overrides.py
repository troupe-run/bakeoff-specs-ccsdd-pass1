"""Unit tests of the FileLayout in files with mapping-form includes (task 3.1):
``troupe.config.writeback.layout`` over ``$ref`` mappings and merged mappings.

Each case loads a real theatre configuration with the real loader and analyses one of its files.
At the slot of a mapping-form include root the layout uses the ``$ref`` mapping as written; the
walks for comments, indentation step, dash offset and block-scalar owner columns visit the written
parts of merged mappings and ``$ref`` mappings. A planner-level case proves that an existing-value
change next to a ``$ref`` mapping leaves its lines untouched.

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

from collections.abc import Mapping
from pathlib import Path

from troupe.config.loader import load_config
from troupe.config.model import (
    ConfigDocument,
    ConfigMapping,
    ConfigNode,
    ConfigSequence,
    SourceFile,
    Span,
)
from troupe.config.paths import ConfigPath
from troupe.config.writeback.compare import node_to_plain, plain_equal
from troupe.config.writeback.layout import FileLayout, InsertionPoint, ValueSlot
from troupe.config.writeback.planner import plan_patch
from troupe.config.writeback.target import resolve_target
from troupe.config.writeback.value import parse_value

ROOT = "troupe.yml"
CODER = "model: m\ntemperature: 0.7\nparams:\n    a: 1\n"


def _load(tmp_path: Path, config: str, files: Mapping[str, str] | None = None) -> ConfigDocument:
    for name, text in {**(files or {"c.yaml": CODER}), ROOT: config}.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode("utf-8"))
    return load_config(tmp_path / ROOT)


def _file(document: ConfigDocument, name: str) -> SourceFile:
    for source in document.files:
        if source.path.name == name:
            return source
    raise AssertionError(f"{name} was not read")


def _setup(
    tmp_path: Path, config: str, files: Mapping[str, str] | None = None
) -> tuple[FileLayout, ConfigDocument]:
    document = _load(tmp_path, config, files)
    return FileLayout.build(document.root_file, document), document


def _node(document: ConfigDocument, *segments: str | int) -> ConfigNode:
    node: ConfigNode = document.root
    for segment in segments:
        if isinstance(segment, int):
            assert isinstance(node, ConfigSequence)
            node = node.items[segment]
        else:
            assert isinstance(node, ConfigMapping)
            entry = node.get(segment)
            assert entry is not None
            node = entry.value
    return node


def _form(document: ConfigDocument, *segments: str | int) -> ConfigMapping:
    """The `$ref` mapping as written of the mapping-form include root at ``segments``."""
    include = _node(document, *segments).include
    assert include is not None and include.form is not None
    return include.form


def _entry_slot(layout: FileLayout, document: ConfigDocument, *segments: str) -> ValueSlot:
    parent = _node(document, *segments[:-1])
    assert isinstance(parent, ConfigMapping)
    entry = parent.get(segments[-1])
    assert entry is not None
    return layout.entry_slot(parent, entry, ConfigPath(tuple(segments)))


def _item_slot(layout: FileLayout, document: ConfigDocument, index: int, key: str) -> ValueSlot:
    sequence = _node(document, key)
    assert isinstance(sequence, ConfigSequence)
    return layout.item_slot(sequence, index, ConfigPath((key, str(index))))


def _comments(layout: FileLayout) -> list[int | None]:
    return [layout.comment_start(line) for line in range(layout.line_count)]


# --- end-of-line comments ----------------------------------------------------------------------


def test_comments_in_a_block_ref_mapping_skip_hashes_in_quoted_overrides(tmp_path: Path) -> None:
    """Req 9.1: in a block `$ref` mapping the `#` inside quoted override values (and keys) is no
    comment; the end-of-line comments after the `$ref` line and the override lines are found
    (task 3.1)."""
    text = (
        "coder:\n"
        '  $ref: "file:c.yaml"   # the coder\n'
        "  temperature: 'a # b'  # hot\n"
        '  "k # q": 1\n'
        "  params:\n"
        "    b: 'x # y'\n"
    )
    layout, _ = _setup(tmp_path, text)
    assert _comments(layout) == [
        None,
        text.index("# the coder"),
        text.index("# hot"),
        None,
        None,
        None,
        None,
    ]


def test_comments_in_a_flow_ref_mapping_skip_hashes_in_quoted_overrides(tmp_path: Path) -> None:
    """Req 9.1: in a flow `$ref` mapping the `#` inside a quoted override is no comment; the
    comment after the closing brace is (task 3.1)."""
    text = (
        "coder: {$ref: 'file:c.yaml', temperature: 'a # b'}  # c\n"
        "list: [{$ref: file:c.yaml, model: 'm # n'}]\n"
    )
    layout, _ = _setup(tmp_path, text)
    assert _comments(layout) == [text.index("# c"), None, None]


def test_comment_detection_in_a_bare_ref_mapping(tmp_path: Path) -> None:
    """Req 9.1: a `$ref` mapping without overrides gets its tokens too (task 3.1)."""
    text = "coder:\n  $ref: 'file:c.yaml # x'  # c\n"
    layout, _ = _setup(tmp_path, text, {"c.yaml # x": CODER})
    assert _comments(layout) == [None, text.index("# c"), None]


def test_block_scalar_override_lines_hold_no_comment(tmp_path: Path) -> None:
    """Req 9.1: the content lines of a block scalar override have no comment (task 3.1)."""
    text = "coder:\n  $ref: file:c.yaml\n  prompt: |  # c\n    line # x\n    # y\nnext: 1  # n\n"
    layout, _ = _setup(tmp_path, text)
    assert _comments(layout) == [None, None, text.index("# c"), None, None, text.index("# n"), None]


def test_comments_in_the_included_file_of_a_merged_mapping(tmp_path: Path) -> None:
    """Req 9.1: the included file's own tokens are found below a merged include root (task 3.1)."""
    coder = "model: 'm # n'  # c\nparams:\n    a: 'x # y'\n"
    text = "coder:\n  $ref: file:c.yaml\n  params:\n    b: 1\n"
    document = _load(tmp_path, text, {"c.yaml": coder})
    layout = FileLayout.build(_file(document, "c.yaml"), document)
    assert _comments(layout) == [coder.index("# c"), None, None, None]


# --- indentation step and dash offset ------------------------------------------------------------


def test_indent_step_from_a_block_ref_mapping(tmp_path: Path) -> None:
    """Req 9.1: the `$ref` mapping under its key gives the file's indentation step (task 3.1)."""
    layout, _ = _setup(tmp_path, "coder:\n    $ref: file:c.yaml\n    temperature: 1\n")
    assert layout.indent_step() == 4


def test_dash_offset_from_a_list_override(tmp_path: Path) -> None:
    """Req 9.1: a block list override under its key gives the file's dash offset (task 3.1)."""
    text = "coder: {$ref: file:c.yaml}\nother:\n  $ref: file:c.yaml\n  tools:\n      - a\n"
    layout, _ = _setup(tmp_path, text)
    assert (layout.indent_step(), layout.dash_offset()) == (2, 4)


def test_indent_step_of_the_included_file_ignores_override_keys(tmp_path: Path) -> None:
    """Req 9.1: the entries of a merged nested mapping are not all in the included file: an
    overridden first key (written in the including file) does not set the included file's step
    (task 3.1)."""
    text = "coder:\n  $ref: file:c.yaml\n  params:\n    a: 2\n"
    document = _load(tmp_path, text)
    layout = FileLayout.build(_file(document, "c.yaml"), document)
    assert layout.indent_step() == 4


# --- slots of mapping-form include roots ---------------------------------------------------------


def test_slot_of_a_block_ref_mapping_uses_the_mapping_as_written(tmp_path: Path) -> None:
    """Req 5.5, 9.1: at the slot of a merged include root the layout uses its block `$ref`
    mapping: empty inline extent, its lines as body lines, its last override as content end
    (task 3.1)."""
    text = (
        "agents:\n"
        "  coder:  # c\n"
        "    $ref: file:c.yaml  # r\n"
        "    temperature: 0.3\n"
        "    # trailing\n"
        "  lead: x\n"
    )
    layout, document = _setup(tmp_path, text)
    slot = _entry_slot(layout, document, "agents", "coder")
    end = slot.indicator_end
    assert layout.inline_extent(slot) == Span(end, end)
    assert layout.body_lines(slot) == Span(text.index("    $ref"), text.index("    # trailing"))
    assert layout.content_end(slot.node, slot) == text.index("0.3") + 3
    assert layout.content_end(_node(document, "agents"), None) == text.index("x\n") + 1


def test_slot_of_a_bare_block_ref_mapping(tmp_path: Path) -> None:
    """Req 5.5, 9.1: a `$ref` mapping without overrides is used as written, not its include
    string alone (task 3.1)."""
    text = "coder:\n  $ref: file:c.yaml  # r\nnext: 1\n"
    layout, document = _setup(tmp_path, text)
    slot = _entry_slot(layout, document, "coder")
    assert layout.inline_extent(slot) == Span(slot.indicator_end, slot.indicator_end)
    assert layout.body_lines(slot) == Span(text.index("  $ref"), text.index("next"))
    assert layout.content_end(slot.node, slot) == text.index("  # r")


def test_slot_of_a_flow_ref_mapping(tmp_path: Path) -> None:
    """Req 5.5, 9.1: a flow `$ref` mapping (with or without overrides, YAML or text include) is
    the inline extent and content end of its slot (task 3.1)."""
    text = (
        "a: {$ref: file:c.yaml, temperature: 1}  # c\n"
        "b: {$ref: file:c.yaml}\n"
        "t: {$ref: file:t.txt}\n"
    )
    layout, document = _setup(tmp_path, text, {"c.yaml": CODER, "t.txt": "hi\n"})
    for key, line in (("a", 0), ("b", 1), ("t", 2)):
        slot = _entry_slot(layout, document, key)
        start = layout.line_start(line) + 3
        span = Span(start, text.index("}", start) + 1)
        assert layout.inline_extent(slot) == span
        assert layout.body_lines(slot) is None
        assert layout.content_end(slot.node, slot) == span.end


def test_list_items_that_are_ref_mappings(tmp_path: Path) -> None:
    """Req 5.5, 9.1: the dashes after block and flow `$ref` mapping items are found from the
    mappings' content ends; a compact item's inline extent stops before its comment (task 3.1)."""
    text = (
        "crew:\n"
        "  - $ref: file:c.yaml  # first\n"
        "    temperature: 0.3\n"
        "  - {$ref: file:c.yaml, model: n}\n"
        "  - $ref: file:c.yaml\n"
        "  - lead  # l\n"
    )
    layout, document = _setup(tmp_path, text)
    slots = [_item_slot(layout, document, n, "crew") for n in range(4)]
    dashes = [text.index("- $ref"), text.index("- {"), text.rindex("- $ref"), text.index("- lead")]
    assert [s.indicator_end for s in slots] == [d + 1 for d in dashes]
    assert layout.inline_extent(slots[0]) == Span(text.index("$ref"), text.index("  # first"))
    assert layout.body_lines(slots[0]) == Span(text.index("    temp"), text.index("  - {"))
    assert layout.content_end(slots[0].node, slots[0]) == text.index("0.3") + 3
    assert layout.inline_extent(slots[1]) == Span(dashes[1] + 2, text.index("}") + 1)
    third = Span(dashes[2] + 2, text.index("\n", dashes[2]))
    assert layout.inline_extent(slots[2]) == third
    assert layout.body_lines(slots[2]) is None
    assert layout.content_end(_node(document, "crew"), None) == text.index("lead") + 4


def test_scalar_form_include_slot_is_unchanged(tmp_path: Path) -> None:
    """Req 9.1: a scalar-form include root's slot is still its `file:` directive (task 3.1)."""
    text = "coder: file:c.yaml  # c\n"
    layout, document = _setup(tmp_path, text)
    slot = _entry_slot(layout, document, "coder")
    directive = Span(text.index("file:"), text.index("  # c"))
    assert layout.inline_extent(slot) == directive
    assert layout.content_end(slot.node, slot) == directive.end
    assert _comments(layout) == [text.index("# c"), None]


# --- block scalar owner columns ------------------------------------------------------------------


def test_block_scalar_override_with_indentation_indicator(tmp_path: Path) -> None:
    """Req 9.1: a block scalar override's indentation indicator counts from its key's column, so
    a whitespace line beyond that indentation is content (task 3.1)."""
    text = "coder:\n  $ref: file:c.yaml\n  prompt: |2\n      text\n     \nnext: 1\n"
    layout, document = _setup(tmp_path, text)
    form = _form(document, "coder")
    entry = form.get("prompt")
    assert entry is not None
    assert layout.content_end(entry.value, None) == text.index("     \nnext") + 5


# --- insertion points of `$ref` mappings ---------------------------------------------------------


def test_insertion_point_of_a_ref_mapping(tmp_path: Path) -> None:
    """Req 5.5, 9.1: a new entry of a block `$ref` mapping goes after its last entry and its
    trailing comment lines (task 3.1)."""
    text = (
        "agents:\n"
        "  coder:\n"
        "    $ref: file:c.yaml\n"
        "    temperature: 0.3  # t\n"
        "    # about temperature\n"
        "\n"
        "  lead:\n"
        "    $ref: file:c.yaml  # bare\n"
        "next: 1\n"
    )
    layout, document = _setup(tmp_path, text)
    coder = _form(document, "agents", "coder")
    assert layout.entries_column(coder) == 4
    assert layout.insertion_point(coder) == InsertionPoint(text.index("\n\n  lead") + 1, False)
    lead = _form(document, "agents", "lead")
    assert layout.insertion_point(lead) == InsertionPoint(text.index("next"), False)


def test_insertion_point_of_a_parent_ending_with_a_ref_mapping(tmp_path: Path) -> None:
    """Req 9.1: a new entry after a `$ref` mapping goes below all of its lines (task 3.1)."""
    text = "agents:\n  coder:\n    $ref: file:c.yaml\n    temperature: 0.3\nnext: 1\n"
    layout, document = _setup(tmp_path, text)
    point = layout.insertion_point(_mapping_at(document, "agents"))
    assert point == InsertionPoint(text.index("next"), False)


def _mapping_at(document: ConfigDocument, *segments: str) -> ConfigMapping:
    node = _node(document, *segments)
    assert isinstance(node, ConfigMapping)
    return node


# --- planner: an existing value next to a `$ref` mapping -----------------------------------------


def _patched_root(tmp_path: Path, text: str, path: str, value: str) -> str:
    document = _load(tmp_path, text)
    config_path = ConfigPath.parse(path)
    request = resolve_target(document, config_path, parse_value(value, config_path))
    patches = plan_patch(document, request)
    assert [patch.file.path.name for patch in patches] == [ROOT]
    new_text = patches[0].new_text
    reloaded = load_config(tmp_path / ROOT, {document.root_file.real_path: new_text})
    assert plain_equal(node_to_plain(reloaded.root), request.intended, ordered=True)
    return new_text


def test_list_item_after_a_ref_mapping_item_changes_alone(tmp_path: Path) -> None:
    """Req 9.1: changing the list item after a block `$ref` mapping item leaves the `$ref`
    mapping's lines untouched (task 3.1)."""
    text = "crew:\n  - $ref: file:c.yaml  # c\n    temperature: 0.3\n  - lead  # l\n"
    expected = "crew:\n  - $ref: file:c.yaml  # c\n    temperature: 0.3\n  - boss  # l\n"
    assert _patched_root(tmp_path, text, "crew.1", "boss") == expected


def test_sibling_in_a_flow_mapping_next_to_a_ref_mapping_changes_alone(tmp_path: Path) -> None:
    """Req 9.1: changing a flow sibling of a flow `$ref` mapping holding a quoted `#` keeps the
    `$ref` mapping and the real comment's column (task 3.1)."""
    text = "agents: {coder: {$ref: file:c.yaml, model: 'a # b'}, lead: xyz}  # c\n"
    expected = "agents: {coder: {$ref: file:c.yaml, model: 'a # b'}, lead: x}    # c\n"
    assert _patched_root(tmp_path, text, "agents.lead", "x") == expected
