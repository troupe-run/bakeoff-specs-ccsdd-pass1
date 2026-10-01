"""Unit tests of the PatchPlanner's entry-wise merges and deletions (task 3.5):
``troupe.config.writeback.planner.plan_patch`` for a non-empty mapping set over a mapping and a
non-empty list set over a list.

Each case loads a real theatre configuration, resolves the set with the real TargetResolver, plans
the patch, compares the patched root file byte-exactly and re-loads the patched text with the real
loader (``load_config`` with overrides) to prove it reads back as the intended configuration
(including key order).

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

from pathlib import Path

import pytest

from troupe.config.errors import ExitCode, WriteRefusedError
from troupe.config.loader import load_config
from troupe.config.paths import ConfigPath
from troupe.config.writeback.compare import node_to_plain, plain_equal
from troupe.config.writeback.planner import plan_patch
from troupe.config.writeback.target import ReplaceTarget, resolve_target
from troupe.config.writeback.value import parse_value

ROOT = "troupe.yml"


def _root(tmp_path: Path, config: str, path: str, value: str) -> str:
    """Plan a merge that must change only the root file; return its new text.

    Also proves that the target is an existing value and that the patched text, re-loaded, is the
    intended configuration (including key order).
    """
    (tmp_path / ROOT).write_bytes(config.encode("utf-8"))
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
    return new_text


def _refused(tmp_path: Path, config: str, path: str, value: str) -> WriteRefusedError:
    (tmp_path / ROOT).write_bytes(config.encode("utf-8"))
    document = load_config(tmp_path / ROOT)
    config_path = ConfigPath.parse(path)
    request = resolve_target(document, config_path, parse_value(value, config_path))
    with pytest.raises(WriteRefusedError) as caught:
        plan_patch(document, request)
    assert caught.value.exit_code is ExitCode.WRITE_REFUSED
    return caught.value


# --- fixtures of the task (reused verbatim by 5.7) -----------------------------------------------


def test_block_mapping_merge_changes_deletes_and_appends(tmp_path: Path) -> None:
    """Req 5.1, 5.3, 12.1: `host` changes in place (comment column kept), `port` and the comment
    line above it stay byte-identical, `user` is deleted, `pool` is appended."""
    text = "db:\n  host: a   # primary\n  # port comment\n  port: 5432\n  user: x\n"
    expected = "db:\n  host: b   # primary\n  # port comment\n  port: 5432\n  pool: 3\n"
    assert _root(tmp_path, text, "db", "{host: b, port: 5432, pool: 3}") == expected


def test_block_list_merge_by_index(tmp_path: Path) -> None:
    """Req 5.2, 5.3: item 0 is unchanged with its comment, item 1 changes, item 2 is deleted."""
    text = "l:\n  - a  # first\n  - b\n  - c\n"
    assert _root(tmp_path, text, "l", "[a, z]") == "l:\n  - a  # first\n  - z\n"


def test_flow_mapping_deletes_a_middle_entry_with_its_preceding_comma(tmp_path: Path) -> None:
    """Req 5.1, 5.4: `b` goes with the `, ` before it; `c` changes in place."""
    assert _root(tmp_path, "m: {a: 1, b: 2, c: 3}\n", "m", "{a: 1, c: 4}") == "m: {a: 1, c: 4}\n"


def test_flow_mapping_deletes_the_first_entry_with_the_following_comma(tmp_path: Path) -> None:
    """Req 5.4: a deleted first entry takes the `, ` after it."""
    assert _root(tmp_path, "m: {a: 1, b: 2}\n", "m", "{b: 2}") == "m: {b: 2}\n"


def test_flow_mapping_with_all_entries_replaced_is_rendered_anew(tmp_path: Path) -> None:
    """Req 5.1, 8.5: no existing entry stays, so the flow mapping is rendered as a whole."""
    assert _root(tmp_path, "m: {a: 1, b: 2}\n", "m", "{x: 1}") == "m: {x: 1}\n"


def test_deleting_a_compact_first_key_is_refused(tmp_path: Path) -> None:
    """Req 5.7, 14.9: the first key shares the `-` line with the item, so deleting it would cut a
    line in two; exit 3 naming PATH and the file."""
    error = _refused(tmp_path, "l:\n  - a: 1\n    b: 2\n", "l.0", "{b: 2}")
    assert error.path == "l.0"
    assert error.file == tmp_path / ROOT
    assert "shares its first line" in error.problem


# --- block deletions -----------------------------------------------------------------------------


def test_block_deletion_takes_interior_comments_and_keeps_following_ones(tmp_path: Path) -> None:
    """Req 5.3: lines from the entry's first line through its last content line go, including a
    comment line inside it; the comment and blank line after its last content line stay."""
    text = "m:\n  a: 1\n  b:\n    # inner\n    c: 2  # eol\n\n  # after\n  d: 3\n"
    expected = "m:\n  a: 1\n\n  # after\n  d: 3\n"
    assert _root(tmp_path, text, "m", "{a: 1, d: 3}") == expected


def test_block_deletion_of_a_block_scalar_entry(tmp_path: Path) -> None:
    """Req 5.3: a literal block entry goes through its last content line."""
    text = "m:\n  a: |\n    one\n    two\n  b: 1\n"
    assert _root(tmp_path, text, "m", "{b: 1}") == "m:\n  b: 1\n"


def test_all_block_entries_replaced(tmp_path: Path) -> None:
    """Req 5.1, 5.3, 11.1: every old entry is deleted and the new one takes their place, before
    the next root key."""
    text = "db:\n  a: 1\n  b: 2\nz: 0\n"
    assert _root(tmp_path, text, "db", "{x: 1}") == "db:\n  x: 1\nz: 0\n"


def test_block_deletion_at_a_file_end_without_line_break(tmp_path: Path) -> None:
    """Req 5.3, 13.2: the file keeps ending without a final line break."""
    text = "db:\n  host: a\n  user: x"
    assert _root(tmp_path, text, "db", "{host: a}") == "db:\n  host: a"


def test_block_deletion_and_append_at_a_file_end_without_line_break(tmp_path: Path) -> None:
    """Req 5.1, 5.3, 11.6: the deleted last line and the new entry give no blank line."""
    text = "db:\n  host: a\n  user: x"
    assert _root(tmp_path, text, "db", "{host: a, pool: 3}") == "db:\n  host: a\n  pool: 3"


def test_several_list_items_deleted_at_a_file_end_without_line_break(tmp_path: Path) -> None:
    """Req 5.2, 5.3: adjacent deletions of surplus items at a break-less file end."""
    assert _root(tmp_path, "l:\n  - a\n  - b\n  - c", "l", "[a]") == "l:\n  - a"


def test_list_item_mapping_deleted_whole(tmp_path: Path) -> None:
    """Req 5.2, 5.3: a compact list-item mapping is deleted through its last content line."""
    text = "l:\n- a: 1\n  b: 2\n- x\n- c: 3\n  d: 4\nk: 1\n"
    assert _root(tmp_path, text, "l", "[{a: 1, b: 2}]") == "l:\n- a: 1\n  b: 2\nk: 1\n"


def test_deleting_a_key_of_a_compact_mapping_after_its_first_line(tmp_path: Path) -> None:
    """Req 5.1, 5.3: a key that starts its own line can be deleted from a list-item mapping."""
    text = "l:\n  - a: 1\n    b: 2\n"
    assert _root(tmp_path, text, "l.0", "{a: 1}") == "l:\n  - a: 1\n"


# --- merges of nested values, order and appends --------------------------------------------------


def test_nested_merge_changes_only_differing_leaves(tmp_path: Path) -> None:
    """Req 5.1, 12.4: the merge recurses; unchanged entries and comments stay byte-identical."""
    text = "a:\n  b:\n    x: 1  # keep\n    y: 2  # also\n  c: 3\n"
    expected = "a:\n  b:\n    x: 1  # keep\n    y: 50 # also\n  c: 3\n"
    assert _root(tmp_path, text, "a", "{b: {x: 1, y: 50}, c: 3}") == expected


def test_existing_key_order_is_kept(tmp_path: Path) -> None:
    """Req 5.1: VALUE listing existing keys in another order keeps the file's order."""
    text = "m:\n  a: 1\n  b: 2\n"
    assert _root(tmp_path, text, "m", "{c: 3, b: 5, a: 1}") == "m:\n  a: 1\n  b: 5\n  c: 3\n"


def test_surplus_list_items_are_appended(tmp_path: Path) -> None:
    """Req 5.2, 4.1: new items after the old ones go in one insertion."""
    expected = "l:\n  - a\n  - b\n  - c\nk: 1\n"
    assert _root(tmp_path, "l:\n  - a\nk: 1\n", "l", "[a, b, c]") == expected


def test_kind_change_inside_a_merge(tmp_path: Path) -> None:
    """Req 5.1, 5.5: a kept entry whose kind changes is replaced through the slot change."""
    text = "m:\n  a:\n    x: 1\n  b: 2\n"
    assert _root(tmp_path, text, "m", "{a: 5, b: 2}") == "m:\n  a: 5\n  b: 2\n"


# --- flow deletions ------------------------------------------------------------------------------


def test_flow_list_surplus_items_deleted_keeping_the_comment_column(tmp_path: Path) -> None:
    """Req 5.2, 5.4, 12.1: each deleted item takes the comma before it; the comment keeps its
    column."""
    text = "l: [a, b, c]  # c\n"
    assert _root(tmp_path, text, "l", "[a]") == "l: [a]        # c\n"


def test_flow_mapping_leading_entries_deleted(tmp_path: Path) -> None:
    """Req 5.4: deleted entries before the first kept one each take the comma after them."""
    assert _root(tmp_path, "m: {a: 1, b: 2, c: 3}\n", "m", "{c: 3}") == "m: {c: 3}\n"


def test_flow_mapping_last_entry_deleted_and_new_one_added(tmp_path: Path) -> None:
    """Req 5.1, 5.4, 11.3: the deleted last entry goes and the new one follows the kept entry."""
    assert _root(tmp_path, "m: {a: 1, b: 2}\n", "m", "{a: 1, c: 3}") == "m: {a: 1, c: 3}\n"


def test_flow_list_trailing_comma_is_kept(tmp_path: Path) -> None:
    """Req 5.4: the trailing comma after the last item stays."""
    assert _root(tmp_path, "l: [a, b, c,]\n", "l", "[a, b]") == "l: [a, b,]\n"


def test_flow_list_merge_inside_a_block_list(tmp_path: Path) -> None:
    """Req 5.2, 5.4: a flow collection nested in a block list merges in place."""
    text = "l:\n  - [1, 2, 3]\n  - x\n"
    assert _root(tmp_path, text, "l", "[[1, 9], x]") == "l:\n  - [1, 9]\n  - x\n"


def test_flow_deletion_spanning_lines_is_refused(tmp_path: Path) -> None:
    """Req 5.4, 14.9: a deletion range that spans lines would need cutting across lines; exit 3."""
    error = _refused(tmp_path, "m: {a: 1,\n  b: 2}\n", "m", "{a: 1}")
    assert error.path == "m"
    assert error.file == tmp_path / ROOT
    assert "spans several lines" in error.problem


def test_multi_line_flow_collection_replaced_whole_is_refused(tmp_path: Path) -> None:
    """Req 14.9: re-rendering a multi-line flow collection could drop text between its lines."""
    error = _refused(tmp_path, "m: {a: 1,\n  b: 2}\n", "m", "{x: 1}")
    assert error.path == "m"
    assert "spans several lines" in error.problem


# --- sibling styles after deletions (7.1-7.3) ---------------------------------------------------


@pytest.mark.parametrize(
    ("config", "value", "expected"),
    [
        pytest.param(
            "db:\n  host: h\n  'user': 'u'\n",
            "{host: h, pw: s}",
            "db:\n  host: h\n  pw: s\n",
            id="deleted-single-quoted-last-entry",
        ),
        pytest.param(
            'db:\n  host: h\n  "user": "u"\n',
            "{host: h, pw: s}",
            "db:\n  host: h\n  pw: s\n",
            id="deleted-double-quoted-last-entry",
        ),
        pytest.param(
            "db:\n  'host': 'h'\n  user: u\n",
            "{host: h, pw: s}",
            "db:\n  'host': 'h'\n  'pw': 's'\n",
            id="deleted-plain-last-entry",
        ),
        pytest.param(
            "db:\n  'a': 'x'\n",
            "{b: y}",
            "db:\n  b: y\n",
            id="no-surviving-entry",
        ),
        pytest.param(
            "o: {a: x, 'b': 'y'}\n",
            "{a: x, c: z}",
            "o: {a: x, c: z}\n",
            id="flow-deleted-quoted-last-entry",
        ),
    ],
)
def test_new_keys_copy_the_last_surviving_entry(
    tmp_path: Path, config: str, value: str, expected: str
) -> None:
    """Req 7.1, 7.2, 7.3, 5.1: new keys and values copy the quoting style of the last entry that
    stays in the merged mapping, never that of an entry the same set deletes; with no remaining
    entry they are written in the minimal style."""
    assert _root(tmp_path, config, config.split(":", 1)[0], value) == expected


def test_appended_list_items_copy_the_last_kept_item(tmp_path: Path) -> None:
    """Req 7.2, 5.2, 4.1: a list merge only appends when no item is deleted, so the surplus items
    copy the style of the old last item, which stays."""
    assert (
        _root(tmp_path, "l:\n  - a\n  - 'b'\n", "l", "[a, b, c]") == "l:\n  - a\n  - 'b'\n  - 'c'\n"
    )
