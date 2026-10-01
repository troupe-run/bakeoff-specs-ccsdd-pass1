"""Unit tests of the CollectionRender (``troupe.config.writeback.collection_render``): new block
mapping entries and list items, nested block collections, compact list items, literal blocks,
empty collections, flow entries and flow collections, and sibling versus minimal styles.

Every rendering is checked byte-exactly and re-loaded with the real loader (``load_config``) in a
file that places it, to prove it reads back as the intended value.

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

from pathlib import Path
from typing import Final

import pytest

from troupe.config.errors import ExitCode, WriteRefusedError
from troupe.config.loader import load_config
from troupe.config.model import ScalarStyle
from troupe.config.paths import ConfigPath
from troupe.config.scalars import ScalarValue
from troupe.config.writeback.collection_render import (
    RenderContext,
    SiblingStyles,
    render_block_entry,
    render_block_value,
    render_flow_entry,
    render_flow_value,
)
from troupe.config.writeback.compare import PlainValue, node_to_plain
from troupe.config.writeback.value import NewMapping, NewScalar, NewSequence, NewValue

PLAIN: Final = ScalarStyle.PLAIN
SINGLE: Final = ScalarStyle.SINGLE_QUOTED
DOUBLE: Final = ScalarStyle.DOUBLE_QUOTED
PATH = ConfigPath.parse("a.b")
NO_SIBLINGS = SiblingStyles(key=None, value=None)


def _ctx(step: int = 2, dash_offset: int = 2, line_break: str = "\n") -> RenderContext:
    return RenderContext(step=step, dash_offset=dash_offset, line_break=line_break, path=PATH)


def s(text: str) -> NewScalar:
    return NewScalar(text, None)


def n(value: ScalarValue, spelling: str) -> NewScalar:
    return NewScalar(value, spelling)


def m(*entries: tuple[str, NewValue]) -> NewMapping:
    return NewMapping(tuple(entries))


def seq(*items: NewValue) -> NewSequence:
    return NewSequence(tuple(items))


# {d: 1, e: [p, q]}
D_E: Final = m(("d", n(1, "1")), ("e", seq(s("p"), s("q"))))
D_E_PLAIN: Final[PlainValue] = {"d": 1, "e": ["p", "q"]}


def _reload(tmp_path: Path, text: str) -> PlainValue:
    target = tmp_path / "troupe.yml"
    target.write_bytes(text.encode("utf-8"))
    return node_to_plain(load_config(target).root)


# --- block mapping entries ------------------------------------------------------------------------


def test_block_entry_step_four_dash_offset_four(tmp_path: Path) -> None:
    """Req 8.1, 8.3: nested mapping one step deeper, nested list dashes at the dash offset (4/4)."""
    rendered = render_block_entry("c", D_E, 0, NO_SIBLINGS, _ctx(4, 4))
    assert rendered == "c:\n    d: 1\n    e:\n        - p\n        - q\n"
    assert _reload(tmp_path, "a: 1\n" + rendered) == {"a": 1, "c": D_E_PLAIN}


def test_block_entry_step_two_dash_offset_zero(tmp_path: Path) -> None:
    """Req 8.1, 8.3: step 2 and indentless lists (dash offset 0)."""
    rendered = render_block_entry("c", D_E, 0, NO_SIBLINGS, _ctx(2, 0))
    assert rendered == "c:\n  d: 1\n  e:\n  - p\n  - q\n"
    assert _reload(tmp_path, rendered + "z: 1\n") == {"c": D_E_PLAIN, "z": 1}


def test_block_entry_at_nested_column(tmp_path: Path) -> None:
    """Req 8.1: an entry under an existing mapping starts at the entries column; nesting is
    relative to it."""
    value = m(("x", m(("y", s("v")))), ("l", seq(n(1, "1"))))
    rendered = render_block_entry("k", value, 3, NO_SIBLINGS, _ctx(3, 0))
    assert rendered == "   k:\n      x:\n         y: v\n      l:\n      - 1\n"
    assert _reload(tmp_path, "a:\n   b: 1\n" + rendered) == {
        "a": {"b": 1, "k": {"x": {"y": "v"}, "l": [1]}}
    }


def test_block_entry_scalar_values(tmp_path: Path) -> None:
    """Req 6.3, 8.1: a scalar value follows `: ` on its key line; non-strings keep spelling."""
    assert render_block_entry("k", n(31, "0x1F"), 0, NO_SIBLINGS, _ctx()) == "k: 0x1F\n"
    assert render_block_entry("k", s("hello world"), 0, NO_SIBLINGS, _ctx()) == "k: hello world\n"
    assert render_block_entry("k", s("true"), 0, NO_SIBLINGS, _ctx()) == "k: 'true'\n"
    assert _reload(tmp_path, "k: 0x1F\nt: 'true'\n") == {"k": 31, "t": "true"}


def test_block_entry_empty_null_spelling(tmp_path: Path) -> None:
    """Req 8.1: a null written with an empty spelling (`{a: }`) leaves the key line as `key:`,
    without a trailing space, also inside a new collection and as a list item."""
    assert render_block_entry("k", n(None, ""), 2, NO_SIBLINGS, _ctx()) == "  k:\n"
    rendered = render_block_entry(
        "c", m(("a", n(None, "")), ("b", n(1, "1"))), 0, NO_SIBLINGS, _ctx()
    )
    assert rendered == "c:\n  a:\n  b: 1\n"
    item = render_block_entry(None, n(None, ""), 2, NO_SIBLINGS, _ctx())
    assert item == "  -\n"
    compact = render_block_entry(None, m(("a", n(None, ""))), 2, NO_SIBLINGS, _ctx())
    assert compact == "  - a:\n"
    assert _reload(tmp_path, rendered + "l:\n" + item + compact) == {
        "c": {"a": None, "b": 1},
        "l": [None, {"a": None}],
    }


def test_block_entry_crlf_line_break(tmp_path: Path) -> None:
    """Req 13.2: every inserted break uses the given line break."""
    rendered = render_block_entry("c", D_E, 0, NO_SIBLINGS, _ctx(2, 2, "\r\n"))
    assert rendered == "c:\r\n  d: 1\r\n  e:\r\n    - p\r\n    - q\r\n"
    assert _reload(tmp_path, "a: 1\r\n" + rendered) == {"a": 1, "c": D_E_PLAIN}


# --- empty collections ----------------------------------------------------------------------------


def test_empty_collections_in_block_style(tmp_path: Path) -> None:
    """Req 8.6: empty mappings and lists are written `{}` and `[]` inline in block style."""
    assert render_block_entry("e", m(), 0, NO_SIBLINGS, _ctx()) == "e: {}\n"
    assert render_block_entry("e", seq(), 0, NO_SIBLINGS, _ctx()) == "e: []\n"
    assert render_block_entry(None, m(), 2, NO_SIBLINGS, _ctx()) == "  - {}\n"
    assert render_block_entry(None, seq(), 2, NO_SIBLINGS, _ctx()) == "  - []\n"
    nested = render_block_entry("c", m(("a", m()), ("b", seq(m(), seq()))), 0, NO_SIBLINGS, _ctx())
    assert nested == "c:\n  a: {}\n  b:\n    - {}\n    - []\n"
    assert render_block_value(m(), 0, False, _ctx()) == ("{}", "")
    assert render_block_value(seq(), 4, True, _ctx()) == ("[]", "")
    assert _reload(tmp_path, nested + "l:\n  - {}\n  - []\n") == {
        "c": {"a": {}, "b": [{}, []]},
        "l": [{}, []],
    }


def test_empty_collections_in_flow_style(tmp_path: Path) -> None:
    """Req 8.6: empty mappings and lists are `{}` and `[]` in flow style too."""
    assert render_flow_value(m(), _ctx()) == "{}"
    assert render_flow_value(seq(), _ctx()) == "[]"
    rendered = render_flow_value(m(("a", m()), ("b", seq(seq(), m()))), _ctx())
    assert rendered == "{a: {}, b: [[], {}]}"
    assert _reload(tmp_path, f"k: {rendered}\n") == {"k": {"a": {}, "b": [[], {}]}}


# --- block list items -----------------------------------------------------------------------------


def test_list_item_scalar(tmp_path: Path) -> None:
    """Req 8.1: a scalar list item is `- value` at the dash column."""
    assert render_block_entry(None, s("x y"), 2, NO_SIBLINGS, _ctx()) == "  - x y\n"
    assert render_block_entry(None, n(1.5, "1.5"), 0, NO_SIBLINGS, _ctx()) == "- 1.5\n"
    assert _reload(tmp_path, "l:\n  - a\n  - x y\n") == {"l": ["a", "x y"]}


def test_compact_list_item_mapping(tmp_path: Path) -> None:
    """Req 8.4: a mapping item starts after `- `; further entries two columns right of the `-`,
    its nested collections relative to its keys."""
    value = m(("a", n(1, "1")), ("b", m(("x", s("y")))), ("c", seq(s("p"))))
    rendered = render_block_entry(None, value, 2, NO_SIBLINGS, _ctx(4, 0))
    assert rendered == "  - a: 1\n    b:\n        x: y\n    c:\n    - p\n"
    assert _reload(tmp_path, "l:\n  - z\n" + rendered) == {
        "l": ["z", {"a": 1, "b": {"x": "y"}, "c": ["p"]}]
    }


def test_compact_list_item_lists(tmp_path: Path) -> None:
    """Req 8.4: a list item that is a list is compact at every level (`- - - a`)."""
    value = seq(seq(s("a"), s("b")), s("c"))
    rendered = render_block_entry(None, value, 0, NO_SIBLINGS, _ctx(4, 4))
    assert rendered == "- - - a\n    - b\n  - c\n"
    assert _reload(tmp_path, "l:\n" + rendered) == {"l": [[["a", "b"], "c"]]}


def test_compact_list_item_whose_first_entry_is_a_collection(tmp_path: Path) -> None:
    """Req 8.4: the first entry's own nested lines follow the compact first line."""
    value = m(("a", m(("b", n(1, "1")))), ("c", n(2, "2")))
    rendered = render_block_entry(None, value, 0, NO_SIBLINGS, _ctx(2, 2))
    assert rendered == "- a:\n    b: 1\n  c: 2\n"
    assert _reload(tmp_path, "l:\n" + rendered) == {"l": [{"a": {"b": 1}, "c": 2}]}


# --- multi-line strings ---------------------------------------------------------------------------


def test_multi_line_string_as_literal_block(tmp_path: Path) -> None:
    """Req 9.1, 9.2: a multi-line string under a key is a literal block, content one step right
    of the key, empty lines as bare breaks."""
    rendered = render_block_entry("t", s("one\n\ntwo\n"), 2, NO_SIBLINGS, _ctx(4, 4))
    assert rendered == "  t: |\n      one\n\n      two\n"
    assert _reload(tmp_path, "a:\n  b: 1\n" + rendered + "z: 1\n") == {
        "a": {"b": 1, "t": "one\n\ntwo\n"},
        "z": 1,
    }


def test_multi_line_string_chomping_and_list_items(tmp_path: Path) -> None:
    """Req 9.1, 9.2: `|-` for no final break, `|+` for several; list item content one step right
    of the `-`; CRLF breaks."""
    strip = render_block_entry(None, s("a\nb"), 2, NO_SIBLINGS, _ctx(2, 2))
    assert strip == "  - |-\n    a\n    b\n"
    keep = render_block_entry("k", s("x\n\n"), 0, NO_SIBLINGS, _ctx(2, 2, "\r\n"))
    assert keep == "k: |+\r\n  x\r\n\r\n"
    assert _reload(tmp_path, "l:\n" + strip) == {"l": ["a\nb"]}
    assert _reload(tmp_path, keep + "z: 1\r\n") == {"k": "x\n\n", "z": 1}


def test_multi_line_string_inside_new_collections(tmp_path: Path) -> None:
    """Req 9.1, 9.2, 8.4: literal blocks inside nested and compact collections indent relative to
    their own key."""
    value = seq(m(("t", s("x\ny\n")), ("u", n(1, "1"))))
    rendered = render_block_entry("l", value, 0, NO_SIBLINGS, _ctx(2, 0))
    assert rendered == "l:\n- t: |\n    x\n    y\n  u: 1\n"
    assert _reload(tmp_path, rendered) == {"l": [{"t": "x\ny\n", "u": 1}]}


def test_multi_line_string_that_does_not_fit_a_literal_block(tmp_path: Path) -> None:
    """Req 9.7: a string a literal block cannot hold is written double-quoted instead."""
    rendered = render_block_entry("t", s("  x\ny\n"), 0, NO_SIBLINGS, _ctx())
    assert rendered == 't: "  x\\ny\\n"\n'
    control = render_block_entry(None, s("a\nb\x01"), 2, NO_SIBLINGS, _ctx())
    assert control == '  - "a\\nb\\x01"\n'
    assert _reload(tmp_path, rendered + "l:\n" + control) == {"t": "  x\ny\n", "l": ["a\nb\x01"]}


# --- block values for replacements ----------------------------------------------------------------


def test_render_block_value_parts(tmp_path: Path) -> None:
    """Req 8.1, 8.3, 8.4, 9.1: (inline part, following lines) for a slot at an owner column."""
    ctx = _ctx(2, 0)
    assert render_block_value(n(5, "5"), 0, False, ctx) == ("5", "")
    assert render_block_value(m(("x", n(1, "1"))), 2, False, ctx) == ("", "    x: 1\n")
    assert render_block_value(seq(s("p")), 2, False, ctx) == ("", "  - p\n")
    assert render_block_value(s("one\ntwo\n"), 2, False, ctx) == ("|", "    one\n    two\n")
    compact = render_block_value(m(("x", n(1, "1")), ("y", n(2, "2"))), 2, True, ctx)
    assert compact == ("x: 1", "    y: 2\n")
    assert render_block_value(seq(s("p"), s("q")), 0, True, ctx) == ("- p", "  - q\n")
    inline, following = compact
    assert _reload(tmp_path, f"l:\n  - {inline}\n{following}") == {"l": [{"x": 1, "y": 2}]}


def test_render_block_value_not_compact(tmp_path: Path) -> None:
    """Req 8.4, 11.4: a list item collection that cannot start on the `-` line (its line keeps a
    comment) is written on the following lines, two columns right of the `-`."""
    value = m(("x", n(1, "1")), ("y", seq(s("p"))))
    inline, following = render_block_value(value, 2, True, _ctx(4, 0), compact=False)
    assert (inline, following) == ("", "    x: 1\n    y:\n    - p\n")
    lists = render_block_value(seq(s("p"), s("q")), 0, True, _ctx(), compact=False)
    assert lists == ("", "  - p\n  - q\n")
    assert _reload(tmp_path, "l:\n  -   # c\n" + following) == {"l": [{"x": 1, "y": ["p"]}]}


def test_render_block_value_preferred_style() -> None:
    """Req 6.1, 7.4: a preferred style applies to a top-level string only, never inside a
    collection."""
    assert render_block_value(s("x"), 0, False, _ctx(), preferred=DOUBLE) == ('"x"', "")
    nested = render_block_value(m(("k", s("x"))), 0, False, _ctx(), preferred=DOUBLE)
    assert nested == ("", "  k: x\n")


# --- sibling and minimal styles -------------------------------------------------------------------


def test_sibling_styles_apply_to_the_new_key_and_value(tmp_path: Path) -> None:
    """Req 7.1, 7.2: the new key and new string value copy the siblings' quoting styles."""
    rendered = render_block_entry("new", s("x y"), 2, SiblingStyles(DOUBLE, SINGLE), _ctx())
    assert rendered == "  \"new\": 'x y'\n"
    item = render_block_entry(None, s("x"), 2, SiblingStyles(None, DOUBLE), _ctx())
    assert item == '  - "x"\n'
    assert _reload(tmp_path, "m:\n  \"a\": 'b'\n" + rendered) == {"m": {"a": "b", "new": "x y"}}


def test_sibling_style_that_cannot_represent_falls_back_to_minimal(tmp_path: Path) -> None:
    """Req 7.1, 7.2: a sibling style that cannot represent the text gives the minimal style."""
    rendered = render_block_entry(
        "a: b", s("tab\there\x01"), 0, SiblingStyles(PLAIN, SINGLE), _ctx()
    )
    assert rendered == "'a: b': \"tab\\there\\x01\"\n"
    numeric = render_block_entry("k", n(2, "2"), 0, SiblingStyles(None, DOUBLE), _ctx())
    assert numeric == "k: 2\n"
    assert _reload(tmp_path, rendered + numeric) == {"a: b": "tab\there\x01", "k": 2}


def test_no_sibling_gives_minimal_style() -> None:
    """Req 7.3: without a sibling style, keys and strings are plain or minimally quoted."""
    assert render_block_entry("k", s("v"), 0, NO_SIBLINGS, _ctx()) == "k: v\n"
    assert render_block_entry("<<", s("- x"), 0, NO_SIBLINGS, _ctx()) == "'<<': '- x'\n"


def test_minimal_quoting_inside_new_collection_under_double_quoted_sibling(tmp_path: Path) -> None:
    """Req 7.4: only the first-level key gets the sibling style; everything inside the new
    collection is minimal."""
    value = m(("d", s("x")), ("e", seq(s("y"), s("z w"), s("1"))), ("f", m(("g h", s("q")))))
    rendered = render_block_entry("c", value, 2, SiblingStyles(DOUBLE, DOUBLE), _ctx())
    assert (
        rendered
        == "  \"c\":\n    d: x\n    e:\n      - y\n      - z w\n      - '1'\n    f:\n      g h: q\n"
    )
    assert _reload(tmp_path, 'm:\n  "a": "b"\n' + rendered) == {
        "m": {"a": "b", "c": {"d": "x", "e": ["y", "z w", "1"], "f": {"g h": "q"}}}
    }
    item = render_block_entry(None, m(("k", s("v"))), 2, SiblingStyles(None, DOUBLE), _ctx())
    assert item == "  - k: v\n"
    flow = render_flow_entry("c", value, SiblingStyles(DOUBLE, DOUBLE), _ctx())
    assert flow == "\"c\": {d: x, e: [y, z w, '1'], f: {g h: q}}"


# --- flow -----------------------------------------------------------------------------------------


def test_flow_rendering_of_a_nested_value(tmp_path: Path) -> None:
    """Req 8.5, 9.3: flow collections use `, ` and `: `; multi-line strings are double-quoted;
    flow indicators force quoting."""
    value = m(
        ("d", n(1, "1")),
        ("e", seq(s("p"), s("q"), m(("r", s("s"))))),
        ("s", s("a\nb\n")),
        ("t", s("x, y")),
        ("n", n(None, "")),
        ("u", n(None, "~")),
    )
    rendered = render_flow_value(value, _ctx())
    assert rendered == "{d: 1, e: [p, q, {r: s}], s: \"a\\nb\\n\", t: 'x, y', n: , u: ~}"
    expected: PlainValue = {
        "d": 1,
        "e": ["p", "q", {"r": "s"}],
        "s": "a\nb\n",
        "t": "x, y",
        "n": None,
        "u": None,
    }
    assert _reload(tmp_path, f"k: {rendered}\n") == {"k": expected}
    assert _reload(tmp_path, f"k: [{rendered}]\n") == {"k": [expected]}
    assert render_flow_value(s("a\nb"), _ctx()) == '"a\\nb"'


def test_flow_entries_with_sibling_styles(tmp_path: Path) -> None:
    """Req 7.1, 7.2, 8.5, 9.3: a new flow entry copies sibling styles at its first level."""
    entry = render_flow_entry("k", s("v"), SiblingStyles(SINGLE, DOUBLE), _ctx())
    assert entry == "'k': \"v\""
    multi = render_flow_entry("t", s("one\ntwo"), SiblingStyles(PLAIN, SINGLE), _ctx())
    assert multi == 't: "one\\ntwo"'
    item = render_flow_entry(None, s("x"), SiblingStyles(None, SINGLE), _ctx())
    assert item == "'x'"
    nested = render_flow_entry(None, seq(s("a,b"), s("c")), SiblingStyles(None, DOUBLE), _ctx())
    assert nested == "['a,b', c]"
    keyed = render_flow_entry("a,b", m(), SiblingStyles(PLAIN, None), _ctx())
    assert keyed == "'a,b': {}"
    assert _reload(
        tmp_path, f"m: {{a: 1, {entry}, {multi}, {keyed}}}\nl: [x, {item}, {nested}]\n"
    ) == {
        "m": {"a": 1, "k": "v", "t": "one\ntwo", "a,b": {}},
        "l": ["x", "x", ["a,b", "c"]],
    }


# --- refusals -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        m(("ok", m(("bad\ud800", s("v"))))),
        seq(s("bad\ud800")),
    ],
)
def test_lone_surrogates_are_refused_naming_path(value: NewValue) -> None:
    """Req 14.9: a lone surrogate anywhere inside a new collection is refused naming PATH."""
    with pytest.raises(WriteRefusedError) as block:
        render_block_entry("k", value, 0, NO_SIBLINGS, _ctx())
    with pytest.raises(WriteRefusedError) as flow:
        render_flow_value(value, _ctx())
    for raised in (block, flow):
        assert raised.value.exit_code is ExitCode.WRITE_REFUSED
        assert "a.b" in str(raised.value)
