"""Unit tests of YamlOutput (``troupe.cli.yaml_output``): block YAML for mappings and lists.

Every node comes from a real theatre written to disk and loaded with ``load_config`` (the root
mapping, or a node reached with ``resolve_path``); expected outputs are exact bytes. Read-back
tests write the output as a fresh theatre's ``troupe.yml`` and load it with the same loader.

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

from pathlib import Path

import pytest

from support import MakeTheatre
from troupe.cli.yaml_output import render_yaml
from troupe.config.loader import load_config
from troupe.config.model import ConfigDocument
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
