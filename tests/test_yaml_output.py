"""Unit tests of YamlOutput (``troupe.cli.yaml_output``): block YAML for mappings, lists and a
scalar alone, optionally annotated with each scalar's source file.

Every node comes from a real theatre written to disk and loaded with ``load_config`` (the root
mapping, or a node reached with ``resolve_path``); expected outputs are exact bytes. Read-back
tests write the output as a fresh theatre's ``troupe.yml`` and load it with the same loader.

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

import re
from collections.abc import Callable
from pathlib import Path

import pytest

from support import MakeTheatre
from troupe.cli.render import source_label
from troupe.cli.yaml_output import render_yaml
from troupe.config.loader import load_config
from troupe.config.model import ConfigDocument, SourceFile
from troupe.config.paths import ConfigPath
from troupe.config.resolve import flatten_leaves, resolve_path
from troupe.config.scalars import scalar_equal


def _load(theatre: Path) -> ConfigDocument:
    return load_config(theatre / ".troupe" / "troupe.yml")


def _show(make_theatre: MakeTheatre, config: str, path: str | None = None) -> bytes:
    """Render the root mapping of ``config`` (or the node at ``path``) without source labels."""
    document = _load(make_theatre(config))
    node = document.root if path is None else resolve_path(document, ConfigPath.parse(path))
    return render_yaml(node, label=None)


# --- layout -------------------------------------------------------------------------------------


def test_nested_mappings_are_printed_in_block_style_in_document_order(
    make_theatre: MakeTheatre,
) -> None:
    """Req 3.1, 3.2: entries keep document order; nested mappings start on the next line, two
    spaces deeper."""
    config = (
        "name: demo\nagents:\n    lead:\n        role: lead\n        model: big\n"
        "    count: 2\nz: 1\n"
    )

    out = _show(make_theatre, config)

    assert out == (
        b"name: demo\nagents:\n  lead:\n    role: lead\n    model: big\n  count: 2\nz: 1\n"
    )


def test_list_items_are_indented_two_spaces_under_their_key(make_theatre: MakeTheatre) -> None:
    """Req 3.1, 3.3: list items are ``- item`` two spaces deeper than the key holding the list."""
    config = "top:\n  tags:\n  - a\n  - b\nnext: 1\n"

    assert _show(make_theatre, config) == b"top:\n  tags:\n    - a\n    - b\nnext: 1\n"


def test_a_printed_list_starts_at_column_zero(make_theatre: MakeTheatre) -> None:
    """Req 3.3: when the printed node is a list, its items start at column 0."""
    assert _show(make_theatre, "top:\n  tags: [a, b]\n", "top.tags") == b"- a\n- b\n"


def test_a_printed_nested_mapping_starts_at_column_zero(make_theatre: MakeTheatre) -> None:
    """Req 3.2: a printed mapping below the root is laid out from column 0."""
    config = "a:\n  b:\n    c: 1\n    d:\n      e: x\n"

    assert _show(make_theatre, config, "a.b") == b"c: 1\nd:\n  e: x\n"


def test_mappings_inside_list_items_are_compact_on_the_dash_line(
    make_theatre: MakeTheatre,
) -> None:
    """Req 3.3: a mapping that is a list item starts on the dash line, later entries aligned."""
    config = "steps:\n- name: x\n  run:\n    cmd: y\n  tags: [p, q]\n- name: z\n"

    assert _show(make_theatre, config) == (
        b"steps:\n"
        b"  - name: x\n"
        b"    run:\n"
        b"      cmd: y\n"
        b"    tags:\n"
        b"      - p\n"
        b"      - q\n"
        b"  - name: z\n"
    )


def test_lists_inside_list_items_are_compact_on_the_dash_line(make_theatre: MakeTheatre) -> None:
    """Req 3.3: a list that is a list item starts on the dash line, nesting compactly."""
    config = "m: [[a, b], [c], [[d]], [{k: 1, l: 2}]]\n"

    assert _show(make_theatre, config) == (
        b"m:\n  - - a\n    - b\n  - - c\n  - - - d\n  - - k: 1\n      l: 2\n"
    )


def test_a_printed_list_of_mappings_is_compact_from_column_zero(
    make_theatre: MakeTheatre,
) -> None:
    """Req 3.3: items of a printed list of mappings put their first entry on the dash line."""
    config = "l:\n  - a: 1\n    b: 2\n  - c: 3\n"

    assert _show(make_theatre, config, "l") == b"- a: 1\n  b: 2\n- c: 3\n"


def test_empty_collections_are_printed_as_braces_and_brackets(make_theatre: MakeTheatre) -> None:
    """Req 3.5: empty mappings and lists print as ``{}`` and ``[]``, as values and as items."""
    config = "a: {}\nb: []\nc:\n- {}\n- []\n- [[], {}]\nd:\n  e: {}\n"

    assert _show(make_theatre, config) == (
        b"a: {}\nb: []\nc:\n  - {}\n  - []\n  - - []\n    - {}\nd:\n  e: {}\n"
    )


def test_printed_empty_collections_are_braces_and_brackets(make_theatre: MakeTheatre) -> None:
    """Req 3.5: a printed empty mapping is ``{}`` and a printed empty list is ``[]``."""
    config = "a: {}\nb: []\n"

    assert _show(make_theatre, config, "a") == b"{}\n"
    assert _show(make_theatre, config, "b") == b"[]\n"


@pytest.mark.parametrize("config", ["", "# only a comment\n\n"], ids=["empty", "comment-only"])
def test_an_empty_configuration_prints_braces(make_theatre: MakeTheatre, config: str) -> None:
    """Req 3.5, 3.7: a configuration without entries prints ``{}`` and one newline."""
    assert _show(make_theatre, config) == b"{}\n"


def test_flow_style_in_the_source_becomes_block_style(make_theatre: MakeTheatre) -> None:
    """Req 3.1, 3.6: non-empty flow collections in the source are printed in block style."""
    config = "a: {b: 1, c: [x, {d: y}]}\n"

    assert _show(make_theatre, config) == b"a:\n  b: 1\n  c:\n    - x\n    - d: y\n"


def test_comments_blank_lines_and_spellings_are_not_carried_over(
    make_theatre: MakeTheatre,
) -> None:
    """Req 3.6, 3.7: comments, blank lines, quoting and number spellings vanish; no markers."""
    config = (
        '%YAML 1.2\n---\n# head\na: 0o17  # trailing\n\n\nb: ~\nc: "hello"\n'
        "'d': 'x'\ne: 0x1F\n# tail\n...\n"
    )

    assert _show(make_theatre, config) == b"a: 15\nb: null\nc: hello\nd: x\ne: 31\n"


# --- scalars ------------------------------------------------------------------------------------


def test_non_string_scalars_have_their_canonical_forms(make_theatre: MakeTheatre) -> None:
    """Req 4.2: booleans, null, decimal integers, repr floats and ``.inf``/``-.inf``/``.nan``."""
    config = (
        "t: True\nf: false\nn: ~\nn2: NULL\nempty:\noct: 0o17\nneg: -3\nfl: 1.5\n"
        "big: 1e16\nz: -0.0\ninf: .Inf\nninf: -.inf\nnan: .NaN\nitems: [true, ~, 0o17]\n"
    )

    assert _show(make_theatre, config) == (
        b"t: true\nf: false\nn: null\nn2: null\nempty: null\noct: 15\nneg: -3\nfl: 1.5\n"
        b"big: 1e+16\nz: -0.0\ninf: .inf\nninf: -.inf\nnan: .nan\n"
        b"items:\n  - true\n  - null\n  - 15\n"
    )


def test_strings_that_would_not_read_back_plain_are_single_quoted(
    make_theatre: MakeTheatre,
) -> None:
    """Req 4.3: ``true``, ``5``, ``null``, ``1.5``, an empty string and ``a: b`` are quoted."""
    config = (
        "a: 'true'\nb: \"5\"\nc: 'null'\nd: '1.5'\ne: ''\nf: 'a: b'\ng: '- x'\nh: \"it's\"\n"
        "i: ' lead'\n"
    )

    assert _show(make_theatre, config) == (
        b"a: 'true'\nb: '5'\nc: 'null'\nd: '1.5'\ne: ''\nf: 'a: b'\ng: '- x'\nh: it's\ni: ' lead'\n"
    )


def test_unnecessary_quotes_are_dropped(make_theatre: MakeTheatre) -> None:
    """Req 4.3, 3.6: a quoted string that reads back plain is printed plain."""
    config = 'a: "hello world"\nb: \'x-y\'\n"c": "d"\n'

    assert _show(make_theatre, config) == b"a: hello world\nb: x-y\nc: d\n"


def test_a_tab_is_kept_inside_single_quotes(make_theatre: MakeTheatre) -> None:
    """Req 4.3, 4.6: a string with a tab cannot be plain; single quotes hold the tab as is."""
    assert _show(make_theatre, 'a: "x\\ty"\n') == b"a: 'x\ty'\n"


def test_control_characters_are_escaped_in_double_quotes(make_theatre: MakeTheatre) -> None:
    """Req 4.3, 4.6: a control character forces double quotes and is printed as an escape."""
    config = 'a: "bell\\a"\nb: "nul\\0 and \\x01"\nc: "q\\" \\\\"\n'

    assert _show(make_theatre, config) == (b'a: "bell\\a"\nb: "nul\\0 and \\x01"\nc: q" \\\n')


def test_non_ascii_printable_characters_are_not_escaped(make_theatre: MakeTheatre) -> None:
    """Req 4.6: non-ASCII printable characters are printed as UTF-8, also inside quotes."""
    config = 'a: "caf\\u00e9"\nb: "\\u65e5\\u672c: x"\nc: "caf\\u00e9\\a"\n'

    out = _show(make_theatre, config)

    assert out == "a: café\nb: '日本: x'\nc: \"café\\a\"\n".encode()


def test_long_strings_are_not_wrapped(make_theatre: MakeTheatre) -> None:
    """Req 4.7: a long string stays on one line, plain or quoted."""
    long_plain = " ".join(["word"] * 60)
    long_quoted = "a: " + long_plain
    config = f"p: {long_plain}\nq: '{long_quoted}'\n"

    out = _show(make_theatre, config)

    assert out == f"p: {long_plain}\nq: '{long_quoted}'\n".encode()
    assert out.count(b"\n") == 2


# --- literal blocks ----------------------------------------------------------------------------


def _show_include(
    make_theatre: MakeTheatre, config: str, text: str | bytes, path: str | None = None
) -> bytes:
    """Render ``config`` whose ``file:p.txt`` directives read the text include ``text``."""
    theatre = make_theatre(config, files={".troupe/p.txt": text})
    document = _load(theatre)
    node = document.root if path is None else resolve_path(document, ConfigPath.parse(path))
    return render_yaml(node, label=None)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("line one\nline two\n", b"p: |\n  line one\n  line two\n"),
        ("line one\nline two", b"p: |-\n  line one\n  line two\n"),
        ("a\n\n", b"p: |+\n  a\n\n"),
        ("a\n\n\n", b"p: |+\n  a\n\n\n"),
        ("\n", b"p: |+\n\n"),
        ("\n\n", b"p: |+\n\n\n"),
        ("a\n\nb\n", b"p: |\n  a\n\n  b\n"),
    ],
    ids=["clip", "strip", "keep-two", "keep-three", "keep-lf-only", "keep-lfs-only", "inner-empty"],
)
def test_a_text_include_is_a_literal_block_with_the_chomping_of_its_trailing_breaks(
    make_theatre: MakeTheatre, text: str, expected: bytes
) -> None:
    """Req 4.4, 3.4: a text include is a literal block; ``|-``, ``|`` or ``|+`` reproduces its
    trailing line breaks; empty lines are bare and other lines are two spaces past the key."""
    assert _show_include(make_theatre, "p: file:p.txt\n", text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("  a\nb\n", b"p: |2\n    a\n  b\n"),
        ("\n  a\nb\n", b"p: |2\n\n    a\n  b\n"),
        ("   \na\n", b"p: |2\n     \n  a\n"),
        (" x", b"p: |2-\n   x\n"),
        ("  a\n\n", b"p: |2+\n    a\n\n"),
    ],
    ids=["first-line", "after-empty-line", "spaces-only-line", "strip", "keep"],
)
def test_an_indentation_indicator_is_added_when_the_first_non_empty_line_starts_with_a_space(
    make_theatre: MakeTheatre, text: str, expected: bytes
) -> None:
    """Req 4.4: the header gets the indicator ``2`` (before the chomping) when the first line
    that is not empty starts with a space; a spaces-only line counts as not empty."""
    assert _show_include(make_theatre, "p: file:p.txt\n", text) == expected


def test_literal_content_is_two_spaces_past_a_nested_key(make_theatre: MakeTheatre) -> None:
    """Req 4.4, 3.2: the content of a block under a key at indent 2 starts at column 4."""
    config = "outer:\n  p: file:p.txt\n  q: 1\n"

    assert (
        _show_include(make_theatre, config, "a\n  b\n")
        == b"outer:\n  p: |\n    a\n      b\n  q: 1\n"
    )


def test_literal_content_is_two_spaces_past_the_dash_of_a_list_item(
    make_theatre: MakeTheatre,
) -> None:
    """Req 4.4, 3.3: a literal list item ``- |`` has its content two spaces past the dash."""
    config = "l:\n  - file:p.txt\n  - x\n"

    assert _show_include(make_theatre, config, "a\nb\n") == b"l:\n  - |\n    a\n    b\n  - x\n"
    assert _show_include(make_theatre, config, "a\nb\n", "l") == b"- |\n  a\n  b\n- x\n"


def test_literal_content_is_two_spaces_past_the_key_of_a_mapping_in_a_list_item(
    make_theatre: MakeTheatre,
) -> None:
    """Req 4.4, 3.3: for ``- k: |`` the content is two spaces past the key, not the dash."""
    config = "l:\n  - k: file:p.txt\n    m: 1\n"

    assert _show_include(make_theatre, config, "  a\nb\n") == (
        b"l:\n  - k: |2\n        a\n      b\n    m: 1\n"
    )


def test_a_single_line_include_without_final_newline_is_a_strip_literal_block(
    make_theatre: MakeTheatre,
) -> None:
    """Req 4.4: a text include with no line break is still a literal block, ``|-`` + one line,
    even when its text would be plain or quoted on one line."""
    assert _show_include(make_theatre, "p: file:p.txt\n", "hello") == b"p: |-\n  hello\n"
    assert _show_include(make_theatre, "p: file:p.txt\n", "true") == b"p: |-\n  true\n"


def test_an_empty_include_is_a_strip_literal_block_without_content(
    make_theatre: MakeTheatre,
) -> None:
    """Req 4.4: an empty text include is the header ``|-`` with no content line."""
    assert _show_include(make_theatre, "p: file:p.txt\nq: 1\n", "") == b"p: |-\nq: 1\n"


def test_a_crlf_include_stays_a_literal_block_with_its_crlf_line_breaks(
    make_theatre: MakeTheatre,
) -> None:
    """Req 4.4, 4.5, 4.6: a CRLF text include is still a literal block whose content lines hold
    its text character for character, so each line ends with ``\\r\\n`` in the raw bytes."""
    out = _show_include(make_theatre, "p: file:p.txt\nq: 1\n", b"one\r\ntwo\r\n")

    assert out == b"p: |\n  one\r\n  two\r\nq: 1\n"


def test_an_include_with_unfit_characters_stays_a_literal_block(
    make_theatre: MakeTheatre,
) -> None:
    """Req 4.5, 4.6: a text include holding a control character is printed literally,
    unescaped, inside a literal block."""
    assert _show_include(make_theatre, "p: file:p.txt\n", "bell\a\n") == b"p: |\n  bell\a\n"


def test_a_tab_is_kept_inside_a_literal_block(make_theatre: MakeTheatre) -> None:
    """Req 4.4, 4.6: a TAB in a literal block's line is written as is."""
    assert _show_include(make_theatre, "p: file:p.txt\n", "a\tb\n\tc\n") == b"p: |\n  a\tb\n  \tc\n"


def test_a_multi_line_string_that_is_not_an_include_is_a_literal_block(
    make_theatre: MakeTheatre,
) -> None:
    """Req 4.4: a double-quoted source string with line feeds is printed as a literal block."""
    config = 'a: "a\\nb\\n"\nb: "x\\n\\n"\nc: "  y\\nz"\n'

    assert _show(make_theatre, config) == b"a: |\n  a\n  b\nb: |+\n  x\n\nc: |2-\n    y\n  z\n"


def test_a_multi_line_string_with_unfit_characters_is_double_quoted(
    make_theatre: MakeTheatre,
) -> None:
    """Req 4.5, 4.6: a non-include string a literal block cannot hold (a carriage return, a
    control character) is printed double-quoted with escapes."""
    config = 'a: "a\\r\\nb"\nb: "x\\ny\\a"\nc: "one\\r"\n'

    assert _show(make_theatre, config) == b'a: "a\\r\\nb"\nb: "x\\ny\\a"\nc: "one\\r"\n'


def test_only_the_header_line_of_a_literal_block_holds_the_scalar_source(
    make_theatre: MakeTheatre,
) -> None:
    """Req 4.4 (with 5.2): the header line carries the text include's file as its source and the
    content lines carry none."""
    theatre = make_theatre("p: file:p.txt\n", files={".troupe/p.txt": "a\nb\n"})
    document = _load(theatre)

    out = render_yaml(document.root, label=lambda file: file.path.name)

    assert out == b"p: | # from p.txt\n  a\n  b\n"


_LITERAL_TEXTS = [
    "line one\nline two\n",
    "line one\nline two",
    "a\n\n",
    "a\n\n\n",
    "\n",
    "\n\n",
    "a\n\nb\n",
    "  a\nb\n",
    "\n  a\nb\n",
    "   \na\n",
    " x",
    "  a\n\n",
    "hello",
    "true",
    "",
    "a\tb\n\tc\n",
    "# not a comment\n- not a list\nkey: not a mapping\n",
]


@pytest.mark.parametrize("text", _LITERAL_TEXTS)
def test_literal_blocks_read_back_exactly(make_theatre: MakeTheatre, text: str) -> None:
    """Req 4.1, 4.4: every LF literal block, as a value, a list item, a value in a list item and
    a nested value, loads back from the output as the same string."""
    config = (
        "p: file:p.txt\nl:\n  - file:p.txt\n  - k: file:p.txt\n    m: file:p.txt\n"
        "outer:\n  inner:\n    p: file:p.txt\n"
    )
    original = _load(make_theatre(config, files={".troupe/p.txt": text}))
    out = render_yaml(original.root, label=None)

    reread = _load(make_theatre(out, name="readback"))

    before = flatten_leaves(original.root)
    after = flatten_leaves(reread.root)
    assert [leaf.path for leaf in after] == [leaf.path for leaf in before]
    assert all(scalar_equal(a.value, b.value) for a, b in zip(after, before, strict=True))
    assert all(leaf.value == text for leaf in after)


def test_non_include_multi_line_strings_read_back_exactly(make_theatre: MakeTheatre) -> None:
    """Req 4.1, 4.4, 4.5: literal blocks and double-quoted fallbacks for non-include multi-line
    strings load back as the same strings."""
    config = 'a: "a\\nb\\n"\nb: "x\\n\\n"\nc: "  y\\nz"\nd: "a\\r\\nb"\ne: "x\\ny\\a"\n'
    original = _load(make_theatre(config))
    out = render_yaml(original.root, label=None)

    reread = _load(make_theatre(out, name="readback"))

    before = flatten_leaves(original.root)
    after = flatten_leaves(reread.root)
    assert [leaf.path for leaf in after] == [leaf.path for leaf in before]
    assert all(scalar_equal(a.value, b.value) for a, b in zip(after, before, strict=True))


# --- keys ---------------------------------------------------------------------------------------


def test_keys_that_would_not_read_back_as_the_same_string_are_quoted(
    make_theatre: MakeTheatre,
) -> None:
    """Req 4.8: keys ``true``, ``null``, ``1.5``, ``5``, ``a: b``, ``<<`` and an empty key are
    quoted; a key that reads back plain is plain."""
    config = (
        "'true': 1\n'null': 2\n'1.5': 3\n'5': 4\n'a: b': 5\n'<<': 6\n'': 7\n"
        '"plain_key": 8\n"x\\ty": 9\n"b\\a": 10\ncafé: 11\n'
    )

    assert _show(make_theatre, config) == (
        b"'true': 1\n'null': 2\n'1.5': 3\n'5': 4\n'a: b': 5\n'<<': 6\n'': 7\n"
        b"plain_key: 8\n'x\ty': 9\n\"b\\a\": 10\n" + "café: 11\n".encode()
    )


# --- whole output and read-back -----------------------------------------------------------------

_RICH = """\
# A rich configuration exercising every layout and scalar rule.
name: "demo"
version: 0o17
ratio: 1.5
big: 1e16
off: False
none: ~
inf: -.inf
nan: .nan
empty_map: {}
empty_list: []
quoted:
  t: 'true'
  five: '5'
  blank: ''
  colon: 'a: b'
  tab: "x\\ty"
  bell: "b\\a"
  accent: café
keys: {'true': a, 'null': b, '1.5': c, '<<': d, '': e, plain_key: f}
agents:
  - name: lead
    tools: [read, write]
    limits: {cpu: 2, mem: []}
  - - nested
    - [deep, {x: 1}]
  - {}
  - 42
"""


def test_output_has_no_markers_trailing_spaces_and_one_final_newline(
    make_theatre: MakeTheatre,
) -> None:
    """Req 3.7: no ``---``/``...``, no trailing whitespace, exactly one final newline."""
    out = _show(make_theatre, _RICH).decode()

    lines = out.split("\n")
    assert out.endswith("\n") and not out.endswith("\n\n")
    assert all(line == line.rstrip() for line in lines)
    assert "---" not in lines and "..." not in lines
    assert "#" not in out


def test_rich_output_reads_back_to_the_same_leaves(make_theatre: MakeTheatre) -> None:
    """Req 4.1, 4.2, 4.3, 4.8: the output loaded as a root file gives the same leaf paths, order
    and typed values, and renders again to the same bytes."""
    original = _load(make_theatre(_RICH))
    out = render_yaml(original.root, label=None)

    reread = _load(make_theatre(out, name="readback"))

    before = flatten_leaves(original.root)
    after = flatten_leaves(reread.root)
    assert [leaf.path for leaf in after] == [leaf.path for leaf in before]
    assert all(scalar_equal(a.value, b.value) for a, b in zip(after, before, strict=True))
    assert render_yaml(reread.root, label=None) == out


# --- source comments ----------------------------------------------------------------------------

_SOURCED_ROOT = """\
name: demo
count: 5
agents: file:agents/team.yml
prompt: file:p.txt
empty_map: {}
lists:
  - x
  - []
  - - nested
  - - []
  - inner: {a: 1}
  - k: v
    m: file:p.txt
greeting: file:agents/greeting.yml
"""

_SOURCED_FILES: dict[str, str | bytes] = {
    ".troupe/p.txt": "hello",
    ".troupe/agents/team.yml": (
        "# the team\nlead:\n  role: lead\n  tags: [a, b]\n  none: []\nnote: file:note.txt\n"
    ),
    ".troupe/agents/note.txt": "line one\nline two\n",
    ".troupe/agents/greeting.yml": "'hi'\n",
}

_SOURCED_LABELLED = (
    b"name: demo # from .troupe/troupe.yml\n"
    b"count: 5 # from .troupe/troupe.yml\n"
    b"agents:\n"
    b"  lead:\n"
    b"    role: lead # from .troupe/agents/team.yml\n"
    b"    tags:\n"
    b"      - a # from .troupe/agents/team.yml\n"
    b"      - b # from .troupe/agents/team.yml\n"
    b"    none: []\n"
    b"  note: | # from .troupe/agents/note.txt\n"
    b"    line one\n"
    b"    line two\n"
    b"prompt: |- # from .troupe/p.txt\n"
    b"  hello\n"
    b"empty_map: {}\n"
    b"lists:\n"
    b"  - x # from .troupe/troupe.yml\n"
    b"  - []\n"
    b"  - - nested # from .troupe/troupe.yml\n"
    b"  - - []\n"
    b"  - inner:\n"
    b"      a: 1 # from .troupe/troupe.yml\n"
    b"  - k: v # from .troupe/troupe.yml\n"
    b"    m: |- # from .troupe/p.txt\n"
    b"      hello\n"
    b"greeting: hi # from .troupe/agents/greeting.yml\n"
)


def _labeler(theatre: Path) -> Callable[[SourceFile], str]:
    return lambda file: source_label(file, theatre)


def test_scalar_lines_end_with_the_file_their_value_is_written_in(
    make_theatre: MakeTheatre,
) -> None:
    """Req 5.1, 5.2, 5.3: ``key: value``, ``- value`` and literal header lines end with
    `` # from <file>``: the root file, the YAML include holding the scalar (including a scalar
    YAML include), or the included text file for a text include (not the file holding
    ``file:``); ``key:`` lines, ``{}``/``[]`` lines, a compact dash line opening a nested
    collection and literal content lines carry no comment."""
    theatre = make_theatre(_SOURCED_ROOT, files=_SOURCED_FILES)
    document = _load(theatre)

    out = render_yaml(document.root, label=_labeler(theatre))

    assert out == _SOURCED_LABELLED


_SOURCE_SUFFIX = re.compile(r" # from [^\n]*$", re.MULTILINE)


@pytest.mark.parametrize(
    ("config", "files"),
    [(_SOURCED_ROOT, _SOURCED_FILES), (_RICH, {})],
    ids=["includes", "rich"],
)
def test_removing_the_source_comments_gives_the_unlabelled_output(
    make_theatre: MakeTheatre, config: str, files: dict[str, str | bytes]
) -> None:
    """Req 5.5: deleting every `` # from <label>`` suffix from the labelled output gives the
    output without labels, byte for byte; every scalar line had one."""
    theatre = make_theatre(config, files=files)
    document = _load(theatre)

    labelled = render_yaml(document.root, label=_labeler(theatre)).decode()
    plain = render_yaml(document.root, label=None).decode()

    stripped, count = _SOURCE_SUFFIX.subn("", labelled)
    assert stripped == plain
    assert count == len(flatten_leaves(document.root))


def _show_scalar(
    make_theatre: MakeTheatre,
    config: str,
    path: str,
    files: dict[str, str | bytes] | None = None,
    *,
    labelled: bool = True,
) -> bytes:
    """Render the scalar at ``path`` alone, labelled relative to the theatre unless told not to."""
    theatre = make_theatre(config, files=files)
    node = resolve_path(_load(theatre), ConfigPath.parse(path))
    return render_yaml(node, label=_labeler(theatre) if labelled else None)


@pytest.mark.parametrize(
    ("written", "expected"),
    [
        ("5", b"5 # from .troupe/troupe.yml\n"),
        ("'true'", b"'true' # from .troupe/troupe.yml\n"),
        ("'--- a'", b"'--- a' # from .troupe/troupe.yml\n"),
        ("'...'", b"'...' # from .troupe/troupe.yml\n"),
        ("hello", b"hello # from .troupe/troupe.yml\n"),
        ("true", b"true # from .troupe/troupe.yml\n"),
        ("~", b"null # from .troupe/troupe.yml\n"),
        ("0o17", b"15 # from .troupe/troupe.yml\n"),
        ("1e16", b"1e+16 # from .troupe/troupe.yml\n"),
        ("-.inf", b"-.inf # from .troupe/troupe.yml\n"),
        ("''", b"'' # from .troupe/troupe.yml\n"),
        ('"b\\a"', b'"b\\a" # from .troupe/troupe.yml\n'),
    ],
    ids=[
        "int",
        "true-string",
        "document-marker",
        "document-end",
        "plain",
        "bool",
        "null",
        "octal",
        "float",
        "inf",
        "empty",
        "control",
    ],
)
def test_a_scalar_alone_is_a_one_line_document_with_its_source(
    make_theatre: MakeTheatre, written: str, expected: bytes
) -> None:
    """Req 2.6, 5.1: a scalar printed alone is a YAML scalar in the document-root context (a
    document marker such as ``--- a`` is quoted) followed by its source comment."""
    assert _show_scalar(make_theatre, f"v: {written}\n", "v") == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("hello", b"|- # from .troupe/p.txt\n  hello\n"),
        ("hello\n", b"| # from .troupe/p.txt\n  hello\n"),
        ("a\n\n", b"|+ # from .troupe/p.txt\n  a\n\n"),
        ("  a\nb\n", b"|2 # from .troupe/p.txt\n    a\n  b\n"),
        ("", b"|- # from .troupe/p.txt\n"),
        (b"one\r\ntwo\r\n", b"| # from .troupe/p.txt\n  one\r\n  two\r\n"),
    ],
    ids=["strip", "clip", "keep", "indicator", "empty", "crlf"],
)
def test_a_text_include_alone_is_a_root_literal_block_sourced_on_its_header(
    make_theatre: MakeTheatre, text: str | bytes, expected: bytes
) -> None:
    """Req 2.6, 5.2, 4.4: a text include printed alone is a literal block at the root, content
    indented two spaces, the included file's comment on the header only; a CRLF include keeps
    its ``\\r\\n``."""
    files: dict[str, str | bytes] = {".troupe/p.txt": text}

    assert _show_scalar(make_theatre, "p: file:p.txt\n", "p", files) == expected


def test_a_scalar_alone_in_a_yaml_include_names_that_include(make_theatre: MakeTheatre) -> None:
    """Req 2.6, 5.3: a scalar alone written in a YAML include is labelled with that include."""
    out = _show_scalar(make_theatre, _SOURCED_ROOT, "agents.lead.role", _SOURCED_FILES)

    assert out == b"lead # from .troupe/agents/team.yml\n"


def test_multi_line_strings_alone_are_literal_or_double_quoted(make_theatre: MakeTheatre) -> None:
    """Req 2.6, 4.4, 4.5: a non-include multi-line string alone is a root literal block when a
    literal block can hold it, otherwise double-quoted on one line with the comment after it."""
    config = 'a: "x\\ny"\nb: "x\\r\\ny"\n'

    assert _show_scalar(make_theatre, config, "a") == b"|- # from .troupe/troupe.yml\n  x\n  y\n"
    assert _show_scalar(make_theatre, config, "b") == b'"x\\r\\ny" # from .troupe/troupe.yml\n'


def test_a_scalar_alone_without_labels_is_a_plain_document(make_theatre: MakeTheatre) -> None:
    """Req 2.6, 4.1: without a labeler a scalar alone is printed with no comment, and the output
    reads back as a document whose root is the same value."""
    assert _show_scalar(make_theatre, "v: '--- a'\n", "v", labelled=False) == b"'--- a'\n"
    files: dict[str, str | bytes] = {".troupe/p.txt": "a\nb\n"}
    out = _show_scalar(make_theatre, "p: file:p.txt\n", "p", files, labelled=False)
    assert out == b"|\n  a\n  b\n"
