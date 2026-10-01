"""Unit tests of the FileLayout (``troupe.config.writeback.layout``): lines, columns, line breaks,
indentation step and list dash offset.

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

from pathlib import Path

import pytest

from troupe.config.loader import load_config
from troupe.config.model import ConfigDocument, SourceFile
from troupe.config.writeback.layout import FileLayout


def _write(tmp_path: Path, name: str, content: str | bytes) -> Path:
    target = tmp_path / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content.encode("utf-8") if isinstance(content, str) else content)
    return target


def _load(
    tmp_path: Path, config: str | bytes, files: dict[str, str | bytes] | None = None
) -> ConfigDocument:
    for name, text in (files or {}).items():
        _write(tmp_path, name, text)
    return load_config(_write(tmp_path, "troupe.yml", config))


def _file(document: ConfigDocument, name: str) -> SourceFile:
    for source in document.files:
        if source.path.name == name:
            return source
    raise AssertionError(f"{name} was not read")


def _layout(tmp_path: Path, config: str | bytes) -> FileLayout:
    document = _load(tmp_path, config)
    return FileLayout.build(document.root_file, document)


# --- lines and columns -----------------------------------------------------------------------


def test_lines_are_indexed_with_content_spans_without_line_breaks(tmp_path: Path) -> None:
    """Req 13.2: line starts, the line of an index and line content ends exclude the break."""
    text = "a: 1\r\nb:\n  c: 2\n"
    layout = _layout(tmp_path, text.encode("utf-8"))
    assert layout.line_count == 4  # the empty line after the final break counts
    assert [layout.line_start(n) for n in range(4)] == [0, 6, 9, 16]
    assert layout.line_of(0) == 0
    assert layout.line_of(4) == 0  # the '\r' belongs to line 0
    assert layout.line_of(5) == 0
    assert layout.line_of(6) == 1
    assert layout.line_of(len(text)) == 3
    assert [(s.start, s.end) for s in map(layout.line_span, range(4))] == [
        (0, 4),
        (6, 8),
        (9, 15),
        (16, 16),
    ]
    assert [layout.line_break(n) for n in range(4)] == ["\r\n", "\n", "\n", ""]


def test_columns_count_code_points_not_bytes(tmp_path: Path) -> None:
    """Req 12.3: columns of multibyte text are counted in code points."""
    text = 'näme: "日本語"  # c\n'
    layout = _layout(tmp_path, text)
    assert layout.column(text.index("#")) == 13
    assert len(text[: text.index("#")].encode("utf-8")) != 13
    assert layout.column(text.index('"')) == 6


def test_columns_restart_on_each_line(tmp_path: Path) -> None:
    """Req 12.3: a column is the code-point offset from the start of the index's line."""
    text = "a:\n  bé: x\n"
    layout = _layout(tmp_path, text)
    assert layout.column(text.index("b")) == 2
    assert layout.column(text.index("x")) == 6


def test_a_byte_order_mark_does_not_shift_first_line_columns(tmp_path: Path) -> None:
    """Req 8.2, 12.3: the BOM is not a column; a BOM file still detects step 2."""
    text = "\ufeffa: 1  # c\nb:\n  c: 2\n"
    layout = _layout(tmp_path, text)
    assert layout.column(text.index("a")) == 0
    assert layout.column(text.index("#")) == 6
    assert layout.indent_step() == 2


def test_blank_and_comment_lines_with_their_indentation(tmp_path: Path) -> None:
    """Req 11.1, 13.2: blank lines, comment lines (first non-space '#') and indentation."""
    text = "a:\n  b: 1  # not a comment line\n\n   \n    # deep\n# top\nc: 2\n"
    layout = _layout(tmp_path, text)
    assert [layout.is_blank(n) for n in range(8)] == [
        False,
        False,
        True,
        True,
        False,
        False,
        False,
        True,
    ]
    assert [layout.is_comment_line(n) for n in range(8)] == [
        False,
        False,
        False,
        False,
        True,
        True,
        False,
        False,
    ]
    assert [layout.indentation(n) for n in range(8)] == [0, 2, 0, 3, 4, 0, 0, 0]


def test_starts_line_only_when_spaces_precede_the_index(tmp_path: Path) -> None:
    """Req 13.2: an index starts its line when only spaces precede it on that line."""
    text = "a:\n  - x\n"
    layout = _layout(tmp_path, text)
    assert layout.starts_line(text.index("-"))
    assert layout.starts_line(0)
    assert not layout.starts_line(text.index("x"))


# --- line breaks for insertions --------------------------------------------------------------


def test_crlf_file_inserts_crlf(tmp_path: Path) -> None:
    """Req 13.2: in a CRLF file inserted lines get CRLF, wherever they go."""
    layout = _layout(tmp_path, b"a: 1\r\nb: 2\r\n")
    assert layout.line_break_at(0) == "\r\n"
    assert layout.line_break_at(6) == "\r\n"
    assert layout.line_break_at(12) == "\r\n"


def test_lf_file_inserts_lf(tmp_path: Path) -> None:
    """Req 13.2: in an LF file inserted lines get LF."""
    layout = _layout(tmp_path, b"a: 1\nb: 2\n")
    assert layout.line_break_at(5) == "\n"
    assert layout.line_break_at(10) == "\n"


def test_file_without_line_break_inserts_lf(tmp_path: Path) -> None:
    """Req 13.2, 11.6: a file without any line break gets LF."""
    layout = _layout(tmp_path, b"a: 1")
    assert layout.line_break_at(4) == "\n"


def test_empty_file_inserts_lf(tmp_path: Path) -> None:
    """Req 13.2: an empty root file gets LF."""
    layout = _layout(tmp_path, b"")
    assert layout.line_count == 1
    assert layout.line_break_at(0) == "\n"


def test_mixed_file_inserts_the_break_of_the_preceding_line(tmp_path: Path) -> None:
    """Req 13.2: in a mixed file an insertion copies the break of the line it follows."""
    text = "a: 1\r\nb: 2\nc: 3"
    layout = _layout(tmp_path, text.encode("utf-8"))
    after_a = text.index("b")
    after_b = text.index("c")
    assert layout.line_break_at(after_a) == "\r\n"
    assert layout.line_break_at(after_b) == "\n"
    # At the end of the break-less last line the line it follows has no break: LF.
    assert layout.line_break_at(len(text)) == "\n"
    # Within a line, the insertion follows that line.
    assert layout.line_break_at(text.index("1")) == "\r\n"
    # At the very start nothing precedes the insertion: LF.
    assert layout.line_break_at(0) == "\n"


def test_mixed_file_with_lone_cr_copies_crlf_of_preceding_line(tmp_path: Path) -> None:
    """Req 13.2: a lone CR makes a file mixed; a CRLF-ended preceding line still gives CRLF."""
    text = "a: 1\rb: 2\r\nc: 3\n"
    layout = _layout(tmp_path, text.encode("utf-8"))
    assert layout.line_count == 4
    assert layout.line_break_at(text.index("c")) == "\r\n"
    assert layout.line_break_at(len(text)) == "\n"


# --- indentation step ------------------------------------------------------------------------


def test_step_two_is_detected(tmp_path: Path) -> None:
    """Req 8.2: the step is the nested mapping's key column minus the parent key column."""
    assert _layout(tmp_path, "a: 1\nb:\n  c: 1\n  d:\n      e: 1\n").indent_step() == 2


def test_step_four_is_detected(tmp_path: Path) -> None:
    """Req 8.2: a file nesting by four columns has step 4."""
    assert _layout(tmp_path, "db:\n    host: x\n    pool:\n        size: 1\n").indent_step() == 4


def test_flat_file_defaults_to_step_two(tmp_path: Path) -> None:
    """Req 8.2: a file without a nested block mapping uses 2."""
    assert _layout(tmp_path, "a: 1\nb: [x, y]\nc: {d: 1}\n").indent_step() == 2


def test_empty_file_defaults_to_step_two_and_dash_offset_two(tmp_path: Path) -> None:
    """Req 8.2, 8.3: an empty root file uses the defaults."""
    layout = _layout(tmp_path, "")
    assert layout.indent_step() == 2
    assert layout.dash_offset() == 2


def test_step_uses_the_first_nested_mapping_in_document_order(tmp_path: Path) -> None:
    """Req 8.2: the first nested block mapping wins, even inside a list item."""
    text = "l:\n  - k:\n       m: 1\nz:\n  y: 1\n"
    # 'k' is at column 4, 'm' at column 7: the first nested mapping lies inside the list item.
    assert _layout(tmp_path, text).indent_step() == 3


def test_step_ignores_mappings_that_are_not_the_value_of_a_mapping_entry(tmp_path: Path) -> None:
    """Req 8.2: a mapping that is a list item is not "nested under a mapping key"."""
    text = "l:\n- a: 1\n  b: 1\nm:\n   n: 1\n"
    assert _layout(tmp_path, text).indent_step() == 3


def test_step_ignores_flow_mappings(tmp_path: Path) -> None:
    """Req 8.2: only block mappings nested under block mapping entries count."""
    text = "a: {b: 1}\nc:\n    d: 1\n"
    assert _layout(tmp_path, text).indent_step() == 4


def test_step_ignores_nesting_that_lies_in_an_included_file(tmp_path: Path) -> None:
    """Req 8.2: only nodes written in the file count; the included file has its own step."""
    document = _load(
        tmp_path,
        "agents: file:agents.yml\nname: x\n",
        {"agents.yml": "coder:\n    model: m\n    tools:\n        - a\n"},
    )
    root_layout = FileLayout.build(document.root_file, document)
    assert root_layout.indent_step() == 2
    assert root_layout.dash_offset() == 2
    agents_layout = FileLayout.build(_file(document, "agents.yml"), document)
    assert agents_layout.indent_step() == 4
    assert agents_layout.dash_offset() == 4


def test_include_root_mapping_does_not_count_as_nested_in_the_including_file(
    tmp_path: Path,
) -> None:
    """Req 8.2: a block mapping loaded by an include under a key is not nested in either file."""
    document = _load(
        tmp_path,
        "b: file:inner.yml\nc:\n      d: 1\n",
        {"inner.yml": "x: 1\n"},
    )
    assert FileLayout.build(document.root_file, document).indent_step() == 6
    assert FileLayout.build(_file(document, "inner.yml"), document).indent_step() == 2


# --- list dash offset ------------------------------------------------------------------------


def test_dash_offset_zero_for_indentless_lists(tmp_path: Path) -> None:
    """Req 8.3: a file writing lists flush with their key has dash offset 0."""
    layout = _layout(tmp_path, "a:\n  b: 1\ntools:\n- x\n- y\n")
    assert layout.dash_offset() == 0
    assert layout.indent_step() == 2


def test_dash_offset_two_for_indented_lists(tmp_path: Path) -> None:
    """Req 8.3: a file indenting its list dashes by two has dash offset 2."""
    layout = _layout(tmp_path, "db:\n    host: x\ntools:\n  - x\n")
    assert layout.dash_offset() == 2
    assert layout.indent_step() == 4


def test_dash_offset_defaults_to_the_step(tmp_path: Path) -> None:
    """Req 8.3: without a block list under a mapping key the dash offset is the step."""
    assert _layout(tmp_path, "db:\n    host: x\nl: [a]\n").dash_offset() == 4
    assert _layout(tmp_path / "flat", "a: 1\n").dash_offset() == 2


def test_dash_offset_uses_the_first_list_in_document_order(tmp_path: Path) -> None:
    """Req 8.3: the first block list under a mapping key wins, also when nested deeper."""
    text = "a:\n  b:\n  - x\nc:\n    - y\n"
    assert _layout(tmp_path, text).dash_offset() == 0


def test_dash_offset_ignores_lists_that_are_list_items(tmp_path: Path) -> None:
    """Req 8.3: a block list that is an item of a list is not under a mapping key."""
    text = "a:\n   - - x\n"
    # The outer list is under 'a' with its dash at column 3.
    assert _layout(tmp_path, text).dash_offset() == 3


@pytest.mark.parametrize("ending", [b"\n", b"\r\n"])
def test_detection_is_independent_of_line_endings(tmp_path: Path, ending: bytes) -> None:
    """Req 8.2, 8.3, 13.2: CRLF does not change columns, the step or the dash offset."""
    text = ending.join([b"a:", b"    b: 1", b"l:", b"- x", b""])
    layout = _layout(tmp_path, text)
    assert layout.indent_step() == 4
    assert layout.dash_offset() == 0
