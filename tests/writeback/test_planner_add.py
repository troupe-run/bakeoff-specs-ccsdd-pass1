"""Unit tests of the PatchPlanner's new keys, intermediate mappings and list appends (task 3.3):
``troupe.config.writeback.planner.plan_patch`` for add-key and append targets.

Each case loads a real theatre configuration, resolves the set with the real TargetResolver, plans
the patch, compares every patched file byte-exactly and re-loads the patched texts with the real
loader (``load_config`` with overrides) to prove they read back as the intended configuration.

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

from collections.abc import Mapping
from pathlib import Path

import pytest

from troupe.config.errors import ExitCode, WriteRefusedError
from troupe.config.loader import load_config
from troupe.config.model import ConfigDocument
from troupe.config.paths import ConfigPath
from troupe.config.writeback.compare import node_to_plain, plain_equal
from troupe.config.writeback.planner import FilePatch, plan_patch
from troupe.config.writeback.target import (
    AddKeyTarget,
    AppendTarget,
    SetRequest,
    resolve_target,
)
from troupe.config.writeback.value import parse_value

ROOT = "troupe.yml"


def _load(
    tmp_path: Path, config: str | bytes, files: Mapping[str, str | bytes] | None = None
) -> ConfigDocument:
    for name, text in {**(files or {}), ROOT: config}.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text if isinstance(text, bytes) else text.encode("utf-8"))
    return load_config(tmp_path / ROOT)


def _request(document: ConfigDocument, path: str, value: str) -> SetRequest:
    config_path = ConfigPath.parse(path)
    return resolve_target(document, config_path, parse_value(value, config_path))


def _plan(
    tmp_path: Path,
    config: str | bytes,
    path: str,
    value: str,
    files: Mapping[str, str | bytes] | None = None,
) -> dict[str, bytes]:
    """Plan an add-key or append set; return each patched file's name and new bytes.

    Also proves that the target is a new key or list item, that the patched texts, re-loaded
    together, are the intended configuration and that no patch leaves its file unchanged.
    """
    document = _load(tmp_path, config, files)
    request = _request(document, path, value)
    assert isinstance(request.target, AddKeyTarget | AppendTarget)
    patches = plan_patch(document, request)
    assert patches, "a plan for a changing set is never empty"
    assert all(isinstance(patch, FilePatch) for patch in patches)
    assert all(patch.new_text != patch.file.text for patch in patches)
    overrides = {file.real_path: file.text for file in document.files}
    overrides.update({patch.file.real_path: patch.new_text for patch in patches})
    reloaded = load_config(tmp_path / ROOT, overrides)
    assert plain_equal(node_to_plain(reloaded.root), request.intended, ordered=True)
    result = {
        patch.file.path.relative_to(tmp_path).as_posix(): patch.new_text.encode("utf-8")
        for patch in patches
    }
    assert len(result) == len(patches), "never two patches for the same file"
    return result


def _root(tmp_path: Path, config: str, path: str, value: str) -> str:
    """Plan a set that must change only the root file; return its new text."""
    patched = _plan(tmp_path, config, path, value)
    assert list(patched) == [ROOT]
    return patched[ROOT].decode("utf-8")


# --- placement of new block entries --------------------------------------------------------------


def test_new_key_goes_below_trailing_comments_of_its_mapping(tmp_path: Path) -> None:
    """Req 3.1, 11.1, 11.2: the key goes after the last entry and its indented comments; a
    less-indented comment ends the mapping and blank lines without a further comment stay below."""
    text = "db:\n  host: a\n  # db comment\n\n# root comment\nother: 1\n"
    expected = "db:\n  host: a\n  # db comment\n  port: 5432\n\n# root comment\nother: 1\n"
    assert _root(tmp_path, text, "db.port", "5432") == expected


def test_new_root_key_goes_below_every_trailing_comment(tmp_path: Path) -> None:
    """Req 3.1, 11.1, 11.2: at the root (column 0) every trailing comment line qualifies, also
    across blank lines followed by a further comment."""
    text = "a: 1\nb: 2\n# end of b\n\n# separate\n"
    expected = "a: 1\nb: 2\n# end of b\n\n# separate\nc: 3\n"
    assert _root(tmp_path, text, "c", "3") == expected


def test_new_key_after_a_trailing_blank_line_keeps_it_below(tmp_path: Path) -> None:
    """Req 11.2: blank lines that no qualifying comment follows stay below the new entry."""
    assert _root(tmp_path, "a: 1\n\n", "b", "2") == "a: 1\nb: 2\n\n"


def test_new_key_after_a_block_scalar_and_its_comment(tmp_path: Path) -> None:
    """Req 3.1, 11.1: a last value written as a literal block ends at its content; the comment
    indented like the entries still belongs to the mapping."""
    text = "m:\n  s: |\n    t\n  # c\nn: 1\n"
    expected = "m:\n  s: |\n    t\n  # c\n  k: v\nn: 1\n"
    assert _root(tmp_path, text, "m.k", "v") == expected


def test_new_key_in_a_compact_list_item_mapping(tmp_path: Path) -> None:
    """Req 3.1, 11.1: a mapping started on a `-` line gets its new key at its entries' column."""
    text = "l:\n  - a: 1\n    b: 2\nn: 1\n"
    expected = "l:\n  - a: 1\n    b: 2\n    c: 3\nn: 1\n"
    assert _root(tmp_path, text, "l.0.c", "3") == expected


# --- intermediate mappings -----------------------------------------------------------------------


def test_missing_intermediate_mappings_are_created_under_the_first_missing_key(
    tmp_path: Path,
) -> None:
    """Req 3.1, 3.2: `a.p.q` below the existing `a` creates `p` with `q` nested one step in."""
    text = "a:\n  x: 1\nb: 2\n"
    expected = "a:\n  x: 1\n  p:\n    q: 1\nb: 2\n"
    assert _root(tmp_path, text, "a.p.q", "1") == expected


def test_intermediate_mappings_use_the_file_step(tmp_path: Path) -> None:
    """Req 3.2, 8.1, 8.2: a file indented by 4 nests the created mappings by 4."""
    text = "a:\n    x: 1\n"
    expected = "a:\n    x: 1\n    p:\n        q: 1\n"
    assert _root(tmp_path, text, "a.p.q", "1") == expected


def test_intermediate_mappings_keep_new_keys_minimal_and_value_in_block_style(
    tmp_path: Path,
) -> None:
    """Req 3.2, 7.1, 7.4, 8.1, 8.3: the first missing key copies the sibling key style, keys
    below it are minimal; a new list under a key uses the file's dash offset."""
    text = "l:\n- y\n'a': 'x'\n"
    expected = "l:\n- y\n'a': 'x'\n'p':\n  q:\n  - r\n  - s\n"
    assert _root(tmp_path, text, "p.q", "[r, s]") == expected


def test_intermediate_mappings_in_a_flow_mapping_are_flow(tmp_path: Path) -> None:
    """Req 3.2, 8.5, 11.3: below a flow mapping the created mappings are written in flow style."""
    assert _root(tmp_path, "m: {a: 1}\n", "m.p.q", "1") == "m: {a: 1, p: {q: 1}}\n"


# --- empty roots ---------------------------------------------------------------------------------


def test_key_in_an_empty_root(tmp_path: Path) -> None:
    """Req 3.1, 3.3, 7.3: an empty root file gets the key at column 0 in the minimal style."""
    assert _root(tmp_path, "", "a", "x") == "a: x\n"


def test_key_in_a_comment_only_root(tmp_path: Path) -> None:
    """Req 3.3, 11.1: a root holding only comments gets the key after them."""
    assert _root(tmp_path, "# only\n\n# notes\n", "a", "1") == "# only\n\n# notes\na: 1\n"


def test_nested_key_in_an_empty_root(tmp_path: Path) -> None:
    """Req 3.2, 3.3, 8.2: intermediates in an empty root use the default step 2."""
    assert _root(tmp_path, "", "a.b", "1") == "a:\n  b: 1\n"


def test_key_in_a_comment_only_root_without_final_break(tmp_path: Path) -> None:
    """Req 3.3, 11.6: the key goes on a new line and the file still ends without a break."""
    assert _root(tmp_path, "# only", "a", "1") == "# only\na: 1"


# --- appends -------------------------------------------------------------------------------------


def test_block_append_goes_after_the_last_item(tmp_path: Path) -> None:
    """Req 4.1, 11.1, 7.2: index == length appends at the items' `-` column; the comment on the
    last item stays where it is."""
    text = "l:\n  - a\n  - b  # last\nn: 1\n"
    expected = "l:\n  - a\n  - b  # last\n  - c\nn: 1\n"
    assert _root(tmp_path, text, "l.2", "c") == expected


def test_block_append_copies_the_last_items_quote_style(tmp_path: Path) -> None:
    """Req 4.1, 7.2: a new string item is written like the preceding item (flush list)."""
    assert _root(tmp_path, "l:\n- 'a'\n", "l.1", "b") == "l:\n- 'a'\n- 'b'\n"


def test_block_append_of_a_mapping_is_compact(tmp_path: Path) -> None:
    """Req 4.1, 8.4, 7.4: a mapping item starts on its `-` line; further keys two columns in."""
    text = "l:\n  - a: 1\n"
    expected = "l:\n  - a: 1\n  - b: 2\n    c: 3\n"
    assert _root(tmp_path, text, "l.1", "{b: 2, c: 3}") == expected


def test_block_append_of_a_multi_line_string_is_a_literal_block(tmp_path: Path) -> None:
    """Req 4.1, 9.1, 9.2: the literal content is one step right of the `-`."""
    text = "l:\n  - a\n"
    expected = "l:\n  - a\n  - |\n    x\n    y\n"
    assert _root(tmp_path, text, "l.1", '"x\\ny\\n"') == expected


def test_flow_append_goes_on_the_last_items_line(tmp_path: Path) -> None:
    """Req 4.1, 11.3, 12.1, 12.2: `, c` follows the last item; the comment keeps its column or
    gets one space when reached."""
    assert _root(tmp_path, "l: [a, b]        # c\n", "l.2", "c") == "l: [a, b, c]     # c\n"
    assert _root(tmp_path, "l: [a, b]  # c\n", "l.2", "c") == "l: [a, b, c] # c\n"


def test_flow_append_keeps_a_trailing_comma(tmp_path: Path) -> None:
    """Req 11.3: a trailing comma and the closing bracket stay after the new item."""
    assert _root(tmp_path, "l: [a, b,]\n", "l.2", "c") == "l: [a, b, c,]\n"


def test_flow_append_in_a_multi_line_flow_list(tmp_path: Path) -> None:
    """Req 11.3: the new item goes on the last item's line, before its trailing comma."""
    text = "l: [\n  a,\n  b,\n]\n"
    assert _root(tmp_path, text, "l.2", "c") == "l: [\n  a,\n  b, c,\n]\n"


def test_flow_append_copies_the_last_items_quote_style(tmp_path: Path) -> None:
    """Req 7.2, 11.3: a new string item in a flow list is written like the preceding item."""
    assert _root(tmp_path, 'l: ["a"]\n', "l.1", "b") == 'l: ["a", "b"]\n'


def test_append_to_an_empty_flow_list(tmp_path: Path) -> None:
    """Req 4.1, 7.3, 11.3: an empty flow list gets its first item right after `[`."""
    assert _root(tmp_path, "m: {}\nl: []\n", "l.0", "x") == "m: {}\nl: [x]\n"


# --- new keys in flow mappings -------------------------------------------------------------------


def test_new_key_in_a_flow_mapping(tmp_path: Path) -> None:
    """Req 3.1, 11.3: `, b: 2` follows the last entry inside the braces."""
    assert _root(tmp_path, "m: {a: 1}\n", "m.b", "2") == "m: {a: 1, b: 2}\n"


def test_new_key_after_an_empty_flow_value_keeps_it_empty(tmp_path: Path) -> None:
    """Req 3.1, 11.3: after an empty last value a space keeps `b:` from absorbing the comma."""
    assert _root(tmp_path, "m: {a: 1, b: }\n", "m.c", "2") == "m: {a: 1, b: , c: 2 }\n"


def test_new_key_in_a_flow_mapping_copies_sibling_styles(tmp_path: Path) -> None:
    """Req 7.1, 7.2: key and string value are quoted like the last entry's key and value."""
    assert _root(tmp_path, "m: {'a': 'x'}\n", "m.b", "y") == "m: {'a': 'x', 'b': 'y'}\n"


def test_new_key_in_an_empty_flow_mapping(tmp_path: Path) -> None:
    """Req 3.1, 7.3, 11.3: an empty flow mapping gets its first entry right after `{`."""
    assert _root(tmp_path, "m: {}\nl: []\n", "m.k", "v") == "m: {k: v}\nl: []\n"


def test_multi_line_string_for_a_new_flow_key_is_double_quoted(tmp_path: Path) -> None:
    """Req 9.3: below a flow mapping a multi-line string is a double-quoted flow scalar."""
    expected = 'm: {a: 1, b: "x\\ny\\n"}\n'
    assert _root(tmp_path, "m: {a: 1}\n", "m.b", '"x\\ny\\n"') == expected


def test_new_collection_in_a_flow_mapping_is_flow(tmp_path: Path) -> None:
    """Req 8.5, 7.4: a new collection below a flow mapping is written in flow style."""
    expected = "m: {a: 1, b: [x, {y: 1}]}\n"
    assert _root(tmp_path, "m: {a: 1}\n", "m.b", "[x, {y: 1}]") == expected


# --- styles of new keys and values ---------------------------------------------------------------


def test_new_key_copies_the_last_keys_and_values_quote_style(tmp_path: Path) -> None:
    """Req 7.1, 7.2: the double-quoted sibling key and value are copied."""
    assert _root(tmp_path, '"a": "x"\n', "b", "y") == '"a": "x"\n"b": "y"\n'


def test_new_string_falls_back_to_minimal_when_sibling_style_cannot_hold_it(
    tmp_path: Path,
) -> None:
    """Req 7.2: plain cannot hold the string `0.2`, so the minimal style (single quotes) is used."""
    assert _root(tmp_path, "a: x\n", "b", '"0.2"') == "a: x\nb: '0.2'\n"


def test_new_string_after_a_collection_sibling_is_minimal(tmp_path: Path) -> None:
    """Req 7.3: a sibling whose value is a collection gives no preferred value style."""
    assert _root(tmp_path, "'a':\n  k: 1\n", "b", "y") == "'a':\n  k: 1\n'b': y\n"


def test_new_key_with_a_multi_line_string_is_a_literal_block(tmp_path: Path) -> None:
    """Req 9.1, 9.2: a new key's multi-line value in a block mapping is a literal block."""
    assert _root(tmp_path, "a: 1\n", "b", '"x\\ny\\n"') == "a: 1\nb: |\n  x\n  y\n"


def test_new_list_value_uses_the_dash_offset_of_the_file(tmp_path: Path) -> None:
    """Req 8.1, 8.3: the new list is written with dashes at the file's dash offset (4 here)."""
    text = "a:\n    x: 1\n"
    expected = "a:\n    x: 1\n    l:\n        - p\n        - q: 1\n"
    assert _root(tmp_path, text, "a.l", "[p, {q: 1}]") == expected


# --- line breaks ---------------------------------------------------------------------------------


def test_new_entry_in_a_file_without_final_break(tmp_path: Path) -> None:
    """Req 11.6: a break goes before the new entry and the file still ends without one."""
    assert _root(tmp_path, "a: 1", "b", "2") == "a: 1\nb: 2"


def test_new_collection_in_a_file_without_final_break(tmp_path: Path) -> None:
    """Req 3.2, 11.6: inner breaks are written; only the final break is left out."""
    assert _root(tmp_path, "a: 1", "b.c", "2") == "a: 1\nb:\n  c: 2"


def test_new_literal_at_a_break_less_end_keeps_breaks_its_content_needs(tmp_path: Path) -> None:
    """Req 9.1, 11.6: strip content stays break-less; clip content needs its final break."""
    assert _root(tmp_path, "a: 1", "b", '"x\\ny"') == "a: 1\nb: |-\n  x\n  y"
    assert _root(tmp_path, "a: 1", "b", '"x\\ny\\n"') == "a: 1\nb: |\n  x\n  y\n"


def test_append_in_a_file_without_final_break(tmp_path: Path) -> None:
    """Req 4.1, 11.6: an appended item at a break-less end keeps the end break-less."""
    assert _root(tmp_path, "l:\n  - a", "l.1", "b") == "l:\n  - a\n  - b"


def test_new_entries_use_crlf_in_a_crlf_file(tmp_path: Path) -> None:
    """Req 3.2, 13.2: every inserted line break of a CRLF file is CRLF."""
    patched = _plan(tmp_path, b"a: 1\r\n", "b.c", "2")
    assert patched == {ROOT: b"a: 1\r\nb:\r\n  c: 2\r\n"}


# --- included files ------------------------------------------------------------------------------


def test_new_key_below_an_include_is_written_as_an_override(tmp_path: Path) -> None:
    """Req 3.2 (config-include-overrides 4.1, 5.1, 5.5, superseding the old rule 2 / Req 3.1
    here): the deepest existing mapping is the include's content, so the scalar-form include is
    converted to mapping form holding the new key; the included file does not change."""
    config = "agents: file:agents.yaml\n"
    patched = _plan(tmp_path, config, "agents.lead.model", "x", {"agents.yaml": "coder: y\n"})
    assert patched == {"troupe.yml": b"agents:\n  $ref: file:agents.yaml\n  lead:\n    model: x\n"}


def test_append_goes_into_the_included_file_holding_the_list(tmp_path: Path) -> None:
    """Req 4.1: the list written in the included file is extended there."""
    config = "tools: file:tools.yaml\n"
    patched = _plan(tmp_path, config, "tools.1", "b", {"tools.yaml": "- a\n"})
    assert patched == {"tools.yaml": b"- a\n- b\n"}


# --- refusals ------------------------------------------------------------------------------------


def test_lone_surrogate_in_a_new_key_value_is_refused_with_path_and_file(tmp_path: Path) -> None:
    """Req 14.9: a value that cannot be written as UTF-8 is refused naming PATH and the file."""
    document = _load(tmp_path, "a: 1\n")
    request = _request(document, "b", '"\\ud800"')
    with pytest.raises(WriteRefusedError) as caught:
        plan_patch(document, request)
    assert caught.value.exit_code is ExitCode.WRITE_REFUSED
    assert caught.value.path == "b"
    assert caught.value.file == tmp_path / ROOT
