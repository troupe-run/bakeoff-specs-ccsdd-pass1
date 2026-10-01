"""Unit tests of the PatchPlanner's replacements that change the kind or block form of a value
(task 3.4): ``troupe.config.writeback.planner.plan_patch`` for replace targets whose new value
cannot be written in the old value's place as it is.

Each case loads a real theatre configuration, resolves the set with the real TargetResolver, plans
the patch, compares the patched root file byte-exactly and re-loads the patched text with the real
loader (``load_config`` with overrides) to prove it reads back as the intended configuration.

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

from pathlib import Path

from troupe.config.loader import load_config
from troupe.config.paths import ConfigPath
from troupe.config.writeback.compare import node_to_plain, plain_equal
from troupe.config.writeback.planner import plan_patch
from troupe.config.writeback.target import ReplaceTarget, resolve_target
from troupe.config.writeback.value import parse_value

ROOT = "troupe.yml"


def _replace(tmp_path: Path, config: str | bytes, path: str, value: str) -> bytes:
    """Plan a replacement that must change only the root file; return its new bytes.

    Also proves that the target is an existing value and that the patched text, re-loaded, is the
    intended configuration (including key order).
    """
    (tmp_path / ROOT).write_bytes(config if isinstance(config, bytes) else config.encode("utf-8"))
    document = load_config(tmp_path / ROOT)
    config_path = ConfigPath.parse(path)
    request = resolve_target(document, config_path, parse_value(value, config_path))
    assert isinstance(request.target, ReplaceTarget)
    patches = plan_patch(document, request)
    assert [patch.file.real_path for patch in patches] == [document.root_file.real_path]
    new_text = patches[0].new_text
    assert new_text != document.root_file.text
    reloaded = load_config(tmp_path / ROOT, {document.root_file.real_path: new_text})
    assert plain_equal(node_to_plain(reloaded.root), request.intended, ordered=True)
    return new_text.encode("utf-8")


def _root(tmp_path: Path, config: str, path: str, value: str) -> str:
    return _replace(tmp_path, config, path, value).decode("utf-8")


# --- a scalar becomes a block collection (11.4, 11.5) --------------------------------------------


def test_scalar_to_mapping_keeps_the_key_line_through_its_comment(tmp_path: Path) -> None:
    """Req 11.4, 11.5, 8.1: the scalar is removed, the comment stays at its column and the new
    mapping starts on the following line, one indentation step right of the key."""
    text = "a: 5  # note\nb: 1\n"
    assert _root(tmp_path, text, "a", "{x: 1}") == "a:    # note\n  x: 1\nb: 1\n"


def test_scalar_to_mapping_without_comment_drops_the_spaces_after_the_colon(
    tmp_path: Path,
) -> None:
    """Req 11.4, 11.5: with no comment after the scalar the spaces after `:` go too."""
    assert _root(tmp_path, "a: 5\nb: 1\n", "a", "{x: 1}") == "a:\n  x: 1\nb: 1\n"


def test_quoted_scalar_with_trailing_spaces_to_list(tmp_path: Path) -> None:
    """Req 11.4, 11.5, 8.3: trailing spaces after a removed scalar go with it; the new list's
    dashes take the file's dash offset (0 here, from the existing list)."""
    text = "a: 'x'   \nl:\n- p\n"
    assert _root(tmp_path, text, "a", "[1, 2]") == "a:\n- 1\n- 2\nl:\n- p\n"


def test_scalar_to_nested_mapping_uses_the_file_step(tmp_path: Path) -> None:
    """Req 8.1, 8.2, 11.4: nested mappings are indented by the file's step (4 here)."""
    text = "x:\n    y: 1\na: 5\n"
    expected = "x:\n    y: 1\na:\n    p:\n        q: 1\n"
    assert _root(tmp_path, text, "a", "{p: {q: 1}}") == expected


def test_empty_value_to_mapping(tmp_path: Path) -> None:
    """Req 11.4: an empty value's key line stays as it is; the mapping follows it."""
    assert _root(tmp_path, "a:\nb: 1\n", "a", "{x: 1}") == "a:\n  x: 1\nb: 1\n"


def test_empty_value_with_comment_to_mapping(tmp_path: Path) -> None:
    """Req 11.4, 11.5: an empty value's comment stays untouched."""
    text = "a:   # c\nb: 1\n"
    assert _root(tmp_path, text, "a", "{x: 1}") == "a:   # c\n  x: 1\nb: 1\n"


def test_scalar_to_mapping_with_crlf(tmp_path: Path) -> None:
    """Req 11.4, 13.2: new lines use the file's CRLF line breaks."""
    patched = _replace(tmp_path, b"a: 5\r\nb: 1\r\n", "a", "{x: 1}")
    assert patched == b"a:\r\n  x: 1\r\nb: 1\r\n"


def test_scalar_to_mapping_at_end_of_file_without_final_break(tmp_path: Path) -> None:
    """Req 11.4, 11.6: at a file end without a line break the new lines start with one and the
    file still ends without a final break."""
    assert _root(tmp_path, "b: 1\na: 5", "a", "{x: 1, y: 2}") == "b: 1\na:\n  x: 1\n  y: 2"


def test_list_item_scalar_to_mapping_is_compact(tmp_path: Path) -> None:
    """Req 8.4, 11.4: a list item's new mapping starts after `- `, further keys two columns right
    of the `-`."""
    text = "l:\n  - x\n  - y\n"
    expected = "l:\n  - a: 1\n    b: 2\n  - y\n"
    assert _root(tmp_path, text, "l.0", "{a: 1, b: 2}") == expected


def test_list_item_scalar_with_comment_to_mapping_starts_below(tmp_path: Path) -> None:
    """Req 11.4, 11.5, 8.4: an item line with a comment keeps it at its column; the mapping
    starts on the next line, two columns right of the `-`."""
    text = "l:\n  - x  # c\n  - y\n"
    expected = "l:\n  -    # c\n    a: 1\n    b: 2\n  - y\n"
    assert _root(tmp_path, text, "l.0", "{a: 1, b: 2}") == expected


def test_list_item_scalar_to_list_is_compact(tmp_path: Path) -> None:
    """Req 8.4: a list item that becomes a list starts after `- `."""
    assert _root(tmp_path, "l:\n- x\n- y\n", "l.1", "[p, q]") == "l:\n- x\n- - p\n  - q\n"


def test_multi_line_string_over_plain_scalar_keeps_the_comment(tmp_path: Path) -> None:
    """Req 9.1, 9.2, 11.4: a literal block over a plain scalar keeps the key line's comment."""
    text = "a: x  # c\n"
    assert _root(tmp_path, text, "a", '"one\\ntwo\\n"') == "a: |  # c\n  one\n  two\n"


# --- a block collection is replaced (5.5, 5.6) ---------------------------------------------------


def test_block_mapping_replaced_by_scalar(tmp_path: Path) -> None:
    """Req 5.5: the scalar goes on the key's line; the mapping's lines, comment lines inside it
    included, go through its last content line; comment lines after it stay."""
    text = "a:\n  x: 1\n  # inside\n  y: 2\n  # after y\n# after a\nb: 1\n"
    expected = "a: 5\n  # after y\n# after a\nb: 1\n"
    assert _root(tmp_path, text, "a", "5") == expected


def test_block_mapping_replaced_by_empty_mapping(tmp_path: Path) -> None:
    """Req 5.5, 5.6: an empty mapping is written as `{}` on the key's line."""
    assert _root(tmp_path, "a:\n  x: 1\nb: 1\n", "a", "{}") == "a: {}\nb: 1\n"


def test_block_list_replaced_by_empty_list(tmp_path: Path) -> None:
    """Req 5.5, 5.6: an empty list is written as `[]` on the key's line."""
    assert _root(tmp_path, "l:\n- a\n- b\nn: 1\n", "l", "[]") == "l: []\nn: 1\n"


def test_block_mapping_with_key_comment_replaced_by_scalar(tmp_path: Path) -> None:
    """Req 5.5, 12.1: the key line's comment stays at its column."""
    text = "a:    # c\n  x: 1\nb: 1\n"
    assert _root(tmp_path, text, "a", "long-value") == "a: long-value # c\nb: 1\n"


def test_block_list_replaced_by_mapping(tmp_path: Path) -> None:
    """Req 5.5, 8.1, 11.4: a list becomes a block mapping at the file's step."""
    text = "l:\n  - a\n  - b\nn: 1\n"
    assert _root(tmp_path, text, "l", "{x: 1, y: [p]}") == "l:\n  x: 1\n  y:\n    - p\nn: 1\n"


def test_block_mapping_replaced_by_list_uses_the_dash_offset(tmp_path: Path) -> None:
    """Req 5.5, 8.3: a mapping becomes a block list with the file's dash offset."""
    text = "m:\n  k: 1\nl:\n  - a\n"
    assert _root(tmp_path, text, "m", "[p, q]") == "m:\n  - p\n  - q\nl:\n  - a\n"


def test_block_mapping_replaced_by_multi_line_string(tmp_path: Path) -> None:
    """Req 5.5, 9.1, 9.2: a multi-line string over a block collection is a literal block."""
    text = "a:\n  x: 1\nb: 1\n"
    assert _root(tmp_path, text, "a", '"one\\ntwo"') == "a: |-\n  one\n  two\nb: 1\n"


def test_last_block_mapping_replaced_at_end_of_file_without_final_break(tmp_path: Path) -> None:
    """Req 5.5, 13.2: the file still ends without a final line break."""
    assert _root(tmp_path, "b: 1\na:\n  x: 1", "a", "5") == "b: 1\na: 5"


def test_last_block_mapping_replaced_by_list_at_end_of_file_without_final_break(
    tmp_path: Path,
) -> None:
    """Req 5.5, 11.6: new block lines in place of a last body without a final break also end
    without one."""
    assert _root(tmp_path, "b: 1\na:\n  x: 1", "a", "[p, q]") == "b: 1\na:\n  - p\n  - q"


def test_last_block_mapping_replaced_by_literal_block_at_end_of_file(tmp_path: Path) -> None:
    """Req 9.1, 11.6: a literal block's final line break is part of the value, so it is kept."""
    assert _root(tmp_path, "b: 1\na:\n  x: 1", "a", '"one\\n"') == "b: 1\na: |\n  one\n"


def test_compact_list_item_mapping_replaced_by_scalar(tmp_path: Path) -> None:
    """Req 5.5: a mapping started on a `-` line is replaced on that line, its further lines go."""
    text = "l:\n  - a: 1\n    b: 2\n  - y\n"
    assert _root(tmp_path, text, "l.0", "z") == "l:\n  - z\n  - y\n"


def test_flow_mapping_in_block_parent_replaced_by_list(tmp_path: Path) -> None:
    """Req 8.1, 11.4: a new list over a flow mapping whose parent is block is written in block
    style."""
    text = "m: {a: 1}  # c\nn: 1\n"
    assert _root(tmp_path, text, "m", "[p]") == "m:" + " " * 9 + "# c\n  - p\nn: 1\n"


def test_flow_list_in_block_parent_replaced_by_scalar(tmp_path: Path) -> None:
    """Req 5.5, 12.1: a scalar over a flow list in a block parent goes in its place."""
    text = "m: [1, 2]  # c\n"
    assert _root(tmp_path, text, "m", "x") == "m: x" + " " * 7 + "# c\n"


# --- block scalars replaced by other values (9.6, 9.7) -------------------------------------------


def test_literal_block_replaced_by_non_string(tmp_path: Path) -> None:
    """Req 9.6: a non-string is written plain in place of the indicator; content lines go."""
    assert _root(tmp_path, "a: |\n  t\nb: 1\n", "a", "5") == "a: 5\nb: 1\n"


def test_folded_block_replaced_by_boolean(tmp_path: Path) -> None:
    """Req 9.6: also for folded blocks, keeping the header's comment column."""
    text = "a: >-  # c\n  some\n  text\nb: 1\n"
    assert _root(tmp_path, text, "a", "true") == "a: true # c\nb: 1\n"


def test_keep_block_replaced_by_non_string_removes_its_trailing_blank_lines(
    tmp_path: Path,
) -> None:
    """Req 9.6: a keep block's trailing blank lines are its content and go with it."""
    assert _root(tmp_path, "a: |+\n  t\n\nb: 1\n", "a", "null") == "a: null\nb: 1\n"


def test_literal_block_replaced_by_string_it_cannot_hold(tmp_path: Path) -> None:
    """Req 9.7: a first line starting with a space cannot be a literal block: double-quoted."""
    text = "a: |\n  t\nb: 1\n"
    assert _root(tmp_path, text, "a", '"  x\\ny\\n"') == 'a: "  x\\ny\\n"\nb: 1\n'


def test_literal_block_replaced_by_one_line_string_it_cannot_hold(tmp_path: Path) -> None:
    """Req 9.7: a one-line string a literal block cannot hold is double-quoted, too."""
    assert _root(tmp_path, "a: |\n  t\n", "a", "' x'") == 'a: " x"\n'


def test_folded_block_replaced_by_string_with_control_character(tmp_path: Path) -> None:
    """Req 9.7: a control character cannot be in a block scalar: double-quoted."""
    text = "a: >\n  t\nb: 1\n"
    assert _root(tmp_path, text, "a", '"x\\by\\n"') == 'a: "x\\by\\n"\nb: 1\n'


def test_literal_block_in_list_item_replaced_by_mapping(tmp_path: Path) -> None:
    """Req 9.6, 8.4, 11.4: a list item's block scalar becomes a compact mapping."""
    text = "l:\n  - |\n    t\n  - y\n"
    assert _root(tmp_path, text, "l.0", "{a: 1}") == "l:\n  - a: 1\n  - y\n"


def test_literal_block_with_header_comment_replaced_by_mapping(tmp_path: Path) -> None:
    """Req 11.4, 11.5: the header's comment stays at its column; the mapping follows."""
    text = "a: |  # c\n  t\nb: 1\n"
    assert _root(tmp_path, text, "a", "{x: 1}") == "a:    # c\n  x: 1\nb: 1\n"


# --- flow parents (8.5) --------------------------------------------------------------------------


def test_flow_list_in_flow_mapping_replaced_by_mapping(tmp_path: Path) -> None:
    """Req 8.5: inside a flow parent the new value is a flow rendering in the old span."""
    text = "m: {a: [1, 2], b: 3}\n"
    assert _root(tmp_path, text, "m.a", "{x: 1}") == "m: {a: {x: 1}, b: 3}\n"


def test_flow_list_item_scalar_replaced_by_list(tmp_path: Path) -> None:
    """Req 8.5: a flow list item becomes a flow list."""
    assert _root(tmp_path, "m: [1, 2]\n", "m.0", "[a, b]") == "m: [[a, b], 2]\n"


def test_flow_mapping_in_flow_list_replaced_by_multi_line_string(tmp_path: Path) -> None:
    """Req 8.5, 9.3: a multi-line string in a flow parent is double-quoted."""
    text = "m: [{a: 1}, 2]\n"
    assert _root(tmp_path, text, "m.0", '"x\\ny"') == 'm: ["x\\ny", 2]\n'


def test_empty_value_in_flow_mapping_replaced_by_list(tmp_path: Path) -> None:
    """Req 8.5: an empty value in a flow mapping gets the flow rendering after its `:`."""
    assert _root(tmp_path, "m: {a: , b: 1}\n", "m.a", "[x]") == "m: {a: [x] , b: 1}\n"
