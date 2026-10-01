"""Unit tests of the PatchPlanner's in-place changes of existing values (task 3.2):
``troupe.config.writeback.planner.plan_patch`` for replace targets.

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
from troupe.config.writeback.target import SetRequest, resolve_target
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
    """Plan the set; return each patched file's name (relative to tmp_path) and new bytes.

    Also proves that the patched texts, re-loaded together, are the intended configuration and
    that no patch leaves its file unchanged.
    """
    document = _load(tmp_path, config, files)
    request = _request(document, path, value)
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


def _root(tmp_path: Path, config: str | bytes, path: str, value: str) -> str:
    """Plan a set that must change only the root file; return its new text."""
    patched = _plan(tmp_path, config, path, value)
    assert list(patched) == [ROOT]
    return patched[ROOT].decode("utf-8")


def _refused(
    tmp_path: Path,
    config: str,
    path: str,
    value: str,
    files: Mapping[str, str | bytes] | None = None,
) -> WriteRefusedError:
    document = _load(tmp_path, config, files)
    request = _request(document, path, value)
    with pytest.raises(WriteRefusedError) as caught:
        plan_patch(document, request)
    assert caught.value.exit_code is ExitCode.WRITE_REFUSED
    return caught.value


# --- scalars in place: quoting kept or fallen back -----------------------------------------------


def test_single_quoted_scalar_keeps_style_and_comment_column(tmp_path: Path) -> None:
    """Req 2.1, 6.1, 12.1, 12.2: `'x'` becomes `'yy'`; the comment moves back to one space."""
    assert _root(tmp_path, "a: 'x'  # c\n", "a", "yy") == "a: 'yy' # c\n"


def test_double_quoted_scalar_keeps_style_and_escapes_minimally(tmp_path: Path) -> None:
    """Req 6.1, 6.6: a double-quoted value stays double-quoted with only `"` escaped."""
    assert _root(tmp_path, 'a: "x"\n', "a", 'é"q') == 'a: "é\\"q"\n'


def test_string_that_reads_as_a_number_falls_back_to_single_quotes(tmp_path: Path) -> None:
    """Req 6.2, 6.4: plain `0.2` would be a float, so the string is written `'0.2'`."""
    assert _root(tmp_path, "a: x\n", "a", '"0.2"') == "a: '0.2'\n"


def test_non_string_replaces_quoted_scalar_plain_in_its_spelling(tmp_path: Path) -> None:
    """Req 6.3: integers are written plain in VALUE's spelling, whatever the old quoting."""
    assert _root(tmp_path, "a: 'x'\n", "a", "5") == "a: 5\n"
    assert _root(tmp_path, "a: x\n", "a", "0x1F") == "a: 0x1F\n"


def test_string_in_flow_list_needing_quotes_is_single_quoted(tmp_path: Path) -> None:
    """Req 6.2, 6.4: `x,y` cannot be plain inside a flow list; the rest of the line is kept."""
    assert _root(tmp_path, "l: [a, b]\n", "l.0", "x,y") == "l: ['x,y', b]\n"


def test_block_list_item_changes_in_place(tmp_path: Path) -> None:
    """Req 2.1, 6.1, 12.1: an item of a block list is changed on its `-` line only."""
    text = "l:\n  - a\n  - b    # c\n"
    assert _root(tmp_path, text, "l.1", "zzz") == "l:\n  - a\n  - zzz  # c\n"


def test_empty_value_gets_the_new_scalar_after_the_colon(tmp_path: Path) -> None:
    """Req 2.1, 6.3: an empty null `a:` is filled in after its `:`."""
    assert _root(tmp_path, "a:\n", "a", "1") == "a: 1\n"


def test_empty_value_with_comment_keeps_the_comment_column(tmp_path: Path) -> None:
    """Req 2.1, 12.1, 12.2: inserting after `a:` keeps the comment at its column."""
    assert _root(tmp_path, "a:    # c\nb: 2\n", "a", "1") == "a: 1  # c\nb: 2\n"


def test_file_string_is_written_plain_and_reads_back_as_include(tmp_path: Path) -> None:
    """Req 2.7: `file:inc.yaml` is written as a plain string; it reads back as the include."""
    patched = _plan(tmp_path, "a: x\n", "a", "file:inc.yaml", {"inc.yaml": "k: 1\n"})
    assert patched == {ROOT: b"a: file:inc.yaml\n"}


def test_scalar_inside_included_file_changes_only_that_file(tmp_path: Path) -> None:
    """Req 2.1, 2.6, 13.1: a value written in a YAML include is changed in the included file."""
    files = {"agents/coder.yaml": "model: small\ntemperature: 0.2\n"}
    config = "model: gpt\nagents:\n  coder: file:agents/coder.yaml\n"
    patched = _plan(tmp_path, config, "agents.coder.model", "large", files)
    assert patched == {"agents/coder.yaml": b"model: large\ntemperature: 0.2\n"}


# --- end-of-line comment columns -----------------------------------------------------------------


def test_longer_value_moves_comment_back_to_its_column(tmp_path: Path) -> None:
    """Req 12.1, 12.4: the comment keeps its column; the next line is untouched."""
    text = "a: 1    # c\nb: 2   # d\n"
    assert _root(tmp_path, text, "a", "100") == "a: 100  # c\nb: 2   # d\n"


def test_value_reaching_comment_gets_one_space(tmp_path: Path) -> None:
    """Req 12.2: a value that reaches the comment's column is separated by exactly one space."""
    assert _root(tmp_path, "a: 1    # c\n", "a", "1234567") == "a: 1234567 # c\n"


def test_comment_columns_count_code_points(tmp_path: Path) -> None:
    """Req 12.3: columns are counted in code points, not bytes."""
    assert _root(tmp_path, "name: é   # c\n", "name", "ééé") == "name: ééé # c\n"


def test_quoted_value_with_hash_keeps_the_real_comment_column(tmp_path: Path) -> None:
    """Req 6.1, 12.1: a `#` inside the quotes is no comment; the real one keeps its column."""
    assert _root(tmp_path, 'a: "x # y"  # c\n', "a", "z") == 'a: "z"      # c\n'


# --- multi-line strings over plain and quoted scalars --------------------------------------------


def test_multi_line_string_over_plain_scalar_is_a_literal_block(tmp_path: Path) -> None:
    """Req 9.1, 9.2, 11.4, 12.1: `|` replaces the scalar; the comment keeps its column."""
    text = "a: x  # c\nb: 1\n"
    expected = "a: |  # c\n  one\n  two\nb: 1\n"
    assert _root(tmp_path, text, "a", '"one\\ntwo\\n"') == expected


def test_literal_block_chomping_follows_trailing_breaks(tmp_path: Path) -> None:
    """Req 9.1: `|-` without a final break, `|+` with more than one."""
    assert _root(tmp_path, "a: x\n", "a", '"one\\ntwo"') == "a: |-\n  one\n  two\n"
    assert _root(tmp_path, "a: x\n", "a", '"one\\n\\n"') == "a: |+\n  one\n\n"


def test_multi_line_string_over_quoted_list_item(tmp_path: Path) -> None:
    """Req 9.1, 9.2: a list item's literal content is one step right of its `-`."""
    text = "l:\n  - 'a'\n  - b\n"
    expected = "l:\n  - |\n    x\n    y\n  - b\n"
    assert _root(tmp_path, text, "l.0", '"x\\ny\\n"') == expected


def test_multi_line_string_over_empty_value(tmp_path: Path) -> None:
    """Req 9.1, 9.2: an empty value gets ` |` after its `:` and the content lines below."""
    assert _root(tmp_path, "a:\nb: 1\n", "a", '"l1\\nl2\\n"') == "a: |\n  l1\n  l2\nb: 1\n"


def test_literal_block_uses_file_step_and_crlf(tmp_path: Path) -> None:
    """Req 9.2, 13.2: content is indented by the file's step and lines end with CRLF."""
    text = b"a:\r\n    x: 1\r\nb: 2\r\n"
    patched = _plan(tmp_path, text, "b", '"p\\nq\\n"')
    assert patched == {ROOT: b"a:\r\n    x: 1\r\nb: |\r\n    p\r\n    q\r\n"}


def test_literal_block_at_end_of_file_without_final_break(tmp_path: Path) -> None:
    """Req 9.1, 13.1: strip content at a break-less end stays break-less; clip needs its break."""
    assert _root(tmp_path, "a: x", "a", '"one\\ntwo"') == "a: |-\n  one\n  two"
    assert _root(tmp_path, "a: x", "a", '"one\\ntwo\\n"') == "a: |\n  one\n  two\n"


def test_multi_line_string_a_literal_block_cannot_hold_is_double_quoted(tmp_path: Path) -> None:
    """Req 9.7: a first line starting with a space is written double-quoted instead."""
    expected = 'a: "  indented\\nx\\n"\n'
    assert _root(tmp_path, "a: x\n", "a", '"  indented\\nx\\n"') == expected


def test_multi_line_string_in_flow_list_is_double_quoted(tmp_path: Path) -> None:
    """Req 9.3: inside a flow collection a multi-line string is a double-quoted flow scalar."""
    assert _root(tmp_path, "l: [a, b]\n", "l.0", '"x\\ny"') == 'l: ["x\\ny", b]\n'


def test_multi_line_string_in_flow_mapping_is_double_quoted(tmp_path: Path) -> None:
    """Req 9.3, 12.1: the flow mapping keeps its other entries and the comment its column."""
    text = "m: {a: 1, b: x}      # c\n"
    expected = 'm: {a: 1, b: "l1\\nl2\\n"} # c\n'
    assert _root(tmp_path, text, "m.b", '"l1\\nl2\\n"') == expected


# --- existing block scalars ----------------------------------------------------------------------


def test_literal_block_rewrite_keeps_header_comment(tmp_path: Path) -> None:
    """Req 9.4, 9.5: indicator, chomping and header comment kept; content replaced."""
    text = "a: |  # hdr\n  old\nb: 1\n"
    expected = "a: |  # hdr\n  new\n  lines\nb: 1\n"
    assert _root(tmp_path, text, "a", '"new\\nlines\\n"') == expected


def test_literal_block_rewrite_changes_chomping_when_required(tmp_path: Path) -> None:
    """Req 9.4: a one-line string without a final break turns `|` into `|-`."""
    assert _root(tmp_path, "a: |\n  old\n", "a", '"new"') == "a: |-\n  new\n"


def test_literal_block_rewrite_keeps_content_indentation(tmp_path: Path) -> None:
    """Req 9.5: new lines are written at the block's existing content indentation."""
    assert _root(tmp_path, "a: |-\n    deep\n", "a", '"x\\ny"') == "a: |-\n    x\n    y\n"


def test_folded_block_rewraps_only_changed_paragraph(tmp_path: Path) -> None:
    """Req 10.1, 10.2: the unchanged paragraph keeps its bytes, the changed one is re-wrapped."""
    text = "text: >\n  The quick brown fox\n  jumps over.\n\n  Second para here.\nnext: 1\n"
    value = '"The quick brown fox jumps over.\\nSecond para changed now and longer.\\n"'
    expected = (
        "text: >\n  The quick brown fox\n  jumps over.\n\n"
        "  Second para\n  changed now and\n  longer.\nnext: 1\n"
    )
    assert _root(tmp_path, text, "text", value) == expected


def test_folded_block_more_indented_line_is_refused(tmp_path: Path) -> None:
    """Req 10.6, 14.9: a change that would need a more-indented folded line exits 3."""
    text = "text: >\n  a b\nnext: 1\n"
    error = _refused(tmp_path, text, "text", '"a b\\n  indented\\n"')
    assert error.path == "text"
    assert error.file == tmp_path / ROOT


# --- text includes -------------------------------------------------------------------------------


def test_text_include_of_crlf_target_is_rewritten_with_crlf(tmp_path: Path) -> None:
    """Req 2.2, 2.3: only prompt.txt changes; its content is VALUE with CRLF breaks."""
    files = {"prompt.txt": b"old\r\n"}
    patched = _plan(tmp_path, "prompt: file:prompt.txt\n", "prompt", '"new\\ntext\\n"', files)
    assert patched == {"prompt.txt": b"new\r\ntext\r\n"}


def test_text_include_of_lf_target_gets_value_exactly(tmp_path: Path) -> None:
    """Req 2.2: an LF target's content becomes VALUE exactly."""
    files = {"prompt.txt": b"old\n"}
    patched = _plan(tmp_path, "prompt: file:prompt.txt\n", "prompt", '"new\\ntext"', files)
    assert patched == {"prompt.txt": b"new\ntext"}


def test_text_include_in_list_item_is_rewritten(tmp_path: Path) -> None:
    """Req 2.2: a text include written as a list item is rewritten in its target file."""
    files = {"a.txt": "one\n"}
    patched = _plan(tmp_path, "l:\n  - file:a.txt\n  - x\n", "l.0", "two", files)
    assert patched == {"a.txt": b"two"}


def test_non_string_over_text_include_is_refused(tmp_path: Path) -> None:
    """Req 2.4: a non-string VALUE for a text include exits 3 naming PATH and the target."""
    files = {"prompt.txt": "old\n"}
    error = _refused(tmp_path, "prompt: file:prompt.txt\n", "prompt", "5", files)
    assert error.path == "prompt"
    assert error.file == tmp_path / "prompt.txt"
    assert "string" in error.problem


def test_other_include_string_over_text_include_is_refused(tmp_path: Path) -> None:
    """Req 2.4, 14.9: replacing a text include by a different `file:` string exits 3."""
    files = {"prompt.txt": "old\n", "other.txt": "x\n"}
    error = _refused(tmp_path, "prompt: file:prompt.txt\n", "prompt", "file:other.txt", files)
    assert error.path == "prompt"
    assert error.file == tmp_path / "prompt.txt"


# --- YAML include roots --------------------------------------------------------------------------

INCLUDE_ROOT = "model: gpt\nagents:\n  coder: file:agents/coder.yaml\n"
CODER = {"agents/coder.yaml": "model: small\n", "agents/other.yaml": "model: big\n"}


def test_yaml_include_root_replaced_by_a_mapping_is_refused(tmp_path: Path) -> None:
    """Req 2.5: a value that wholly comes from a YAML include exits 3 naming PATH and the file."""
    error = _refused(tmp_path, INCLUDE_ROOT, "agents.coder", "{model: x}", CODER)
    assert error.path == "agents.coder"
    assert error.file == tmp_path / "agents/coder.yaml"


def test_yaml_include_root_replaced_by_another_include_is_refused(tmp_path: Path) -> None:
    """Req 2.5: even a different `file:` string over a YAML include root exits 3."""
    error = _refused(tmp_path, INCLUDE_ROOT, "agents.coder", "file:agents/other.yaml", CODER)
    assert error.path == "agents.coder"
    assert error.file == tmp_path / "agents/coder.yaml"


def test_yaml_include_root_replaced_by_a_string_is_refused(tmp_path: Path) -> None:
    """Req 2.5: a string over a YAML include root is not written into the included file."""
    error = _refused(tmp_path, INCLUDE_ROOT, "agents.coder", "plain text", CODER)
    assert error.path == "agents.coder"
    assert error.file == tmp_path / "agents/coder.yaml"
    assert "file:agents/coder.yaml" in error.problem


def test_yaml_include_root_replaced_by_a_scalar_is_refused(tmp_path: Path) -> None:
    """Req 2.5: a scalar over a YAML include root exits 3."""
    error = _refused(tmp_path, INCLUDE_ROOT, "agents.coder", "5", CODER)
    assert error.file == tmp_path / "agents/coder.yaml"


# --- layouts the planner cannot write ------------------------------------------------------------


def test_explicit_key_layout_is_refused(tmp_path: Path) -> None:
    """Req 14.9: a value under an explicit `? key` cannot be located; the set exits 3."""
    error = _refused(tmp_path, "? a\n: x\n", "a", "y")
    assert error.path == "a"
    assert error.file == tmp_path / ROOT
