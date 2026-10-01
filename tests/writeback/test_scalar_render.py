"""Unit tests of the ScalarRender (``troupe.config.writeback.scalar_render``): plain and
single-quoted representability, style choice and fallback, double-quoted escaping, keys.

Every rendering that is meant to be written into a file is re-loaded with the real loader
(``load_config``) in its context to prove it reads back as the intended value.

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

from pathlib import Path
from typing import Final

import pytest

from troupe.config.errors import ExitCode, WriteRefusedError
from troupe.config.loader import load_config
from troupe.config.model import ConfigMapping, ConfigScalar, ConfigSequence, ScalarStyle
from troupe.config.paths import ConfigPath
from troupe.config.scalars import ScalarValue
from troupe.config.writeback.scalar_render import (
    Context,
    QuoteStyle,
    can_represent,
    choose_style,
    quote_style_of,
    render_key,
    render_scalar,
)
from troupe.config.writeback.value import NewScalar

PLAIN: Final = ScalarStyle.PLAIN
SINGLE: Final = ScalarStyle.SINGLE_QUOTED
DOUBLE: Final = ScalarStyle.DOUBLE_QUOTED
QUOTE_STYLES: tuple[QuoteStyle, ...] = (PLAIN, SINGLE, DOUBLE)
PATH = ConfigPath.parse("agents.coder.model")

# Each context with the file texts that place a rendering ``{r}`` there.
VALUE_PLACEMENTS: dict[Context, tuple[str, ...]] = {
    Context.BLOCK_VALUE: ("k: {r}\n", "l:\n  - {r}\n", "m:\n  n: {r}  # c\n"),
    Context.FLOW_VALUE: ("k: [{r}, x]\n", "k: {{a: {r}, b: x}}\n"),
}
# Key placements: (file text, whether the key is nested one level below the root).
KEY_PLACEMENTS: dict[Context, tuple[tuple[str, bool], ...]] = {
    Context.BLOCK_KEY: (("{r}: v\n", False), ("m:\n  {r}: v  # c\n", True)),
    Context.FLOW_KEY: (("k: {{{r}: v, z: w}}\n", True),),
}


def _load_root(tmp_path: Path, text: str) -> ConfigMapping:
    target = tmp_path / "troupe.yml"
    target.write_bytes(text.encode("utf-8"))
    return load_config(target).root


def _first_value(root: ConfigMapping) -> ConfigScalar:
    node = root.entries[0].value
    while not isinstance(node, ConfigScalar):
        node = node.entries[0].value if isinstance(node, ConfigMapping) else node.items[0]
    return node


def _first_key(root: ConfigMapping, nested: bool) -> str:
    entry = root.entries[0]
    if nested:
        assert isinstance(entry.value, ConfigMapping)
        entry = entry.value.entries[0]
    return entry.key


def _reloaded_values(tmp_path: Path, rendered: str, context: Context) -> list[ScalarValue]:
    values: list[ScalarValue] = []
    for template in VALUE_PLACEMENTS[context]:
        values.append(_first_value(_load_root(tmp_path, template.format(r=rendered))).value)
    return values


def _reloaded_keys(tmp_path: Path, rendered: str, context: Context) -> list[str]:
    return [
        _first_key(_load_root(tmp_path, template.format(r=rendered)), nested)
        for template, nested in KEY_PLACEMENTS[context]
    ]


def _string(text: str, context: Context, preferred: QuoteStyle | None) -> str:
    return render_scalar(NewScalar(text, None), context, preferred, PATH)


# --- representability --------------------------------------------------------------------------

REPRESENTABILITY: list[tuple[str, Context, bool, bool]] = [
    # text, context, plain?, single?
    ("x", Context.BLOCK_VALUE, True, True),
    ("hello world", Context.BLOCK_VALUE, True, True),
    ("0.2", Context.BLOCK_VALUE, False, True),
    ("5", Context.BLOCK_VALUE, False, True),
    ("0x1F", Context.BLOCK_VALUE, False, True),
    (".inf", Context.BLOCK_VALUE, False, True),
    ("true", Context.BLOCK_VALUE, False, True),
    ("False", Context.BLOCK_VALUE, False, True),
    ("~", Context.BLOCK_VALUE, False, True),
    ("null", Context.BLOCK_VALUE, False, True),
    ("", Context.BLOCK_VALUE, False, True),
    ("a: b", Context.BLOCK_VALUE, False, True),
    (" x", Context.BLOCK_VALUE, False, True),
    ("x ", Context.BLOCK_VALUE, False, True),
    ("a #b", Context.BLOCK_VALUE, False, True),
    ("#b", Context.BLOCK_VALUE, False, True),
    ("a#b", Context.BLOCK_VALUE, True, True),
    ("a\tb", Context.BLOCK_VALUE, False, True),
    ("a\nb", Context.BLOCK_VALUE, False, False),
    ("a\rb", Context.BLOCK_VALUE, False, False),
    ("bell\a", Context.BLOCK_VALUE, False, False),
    ("a\x85b", Context.BLOCK_VALUE, False, False),
    ("a\x7fb", Context.BLOCK_VALUE, False, False),
    ("a\ufffeb", Context.BLOCK_VALUE, False, False),
    ("- x", Context.BLOCK_VALUE, False, True),
    ("-x", Context.BLOCK_VALUE, True, True),
    ("[x]", Context.BLOCK_VALUE, False, True),
    ("{x}", Context.BLOCK_VALUE, False, True),
    ("x,y", Context.BLOCK_VALUE, True, True),
    ("x]", Context.BLOCK_VALUE, True, True),
    ("&a", Context.BLOCK_VALUE, False, True),
    ("*a", Context.BLOCK_VALUE, False, True),
    ("!x", Context.BLOCK_VALUE, False, True),
    ("@x", Context.BLOCK_VALUE, False, True),
    ("`x", Context.BLOCK_VALUE, False, True),
    ("'x'", Context.BLOCK_VALUE, False, True),
    ('"x"', Context.BLOCK_VALUE, False, True),
    ("it's", Context.BLOCK_VALUE, True, True),
    ("a:", Context.BLOCK_VALUE, False, True),
    ("a:b", Context.BLOCK_VALUE, True, True),
    ("? x", Context.BLOCK_VALUE, False, True),
    ("%x", Context.BLOCK_VALUE, False, True),
    ("é", Context.BLOCK_VALUE, True, True),
    ("a\u2028b", Context.BLOCK_VALUE, True, True),
    ("file:inc.yaml", Context.BLOCK_VALUE, True, True),
    ("x,y", Context.FLOW_VALUE, False, True),
    ("[x]", Context.FLOW_VALUE, False, True),
    ("x]", Context.FLOW_VALUE, False, True),
    ("x}", Context.FLOW_VALUE, False, True),
    ("{x", Context.FLOW_VALUE, False, True),
    ("x", Context.FLOW_VALUE, True, True),
    ("a b", Context.FLOW_VALUE, True, True),
    ("0.2", Context.FLOW_VALUE, False, True),
    ("a: b", Context.FLOW_VALUE, False, True),
    ("a\tb", Context.FLOW_VALUE, False, True),
    ("a #b", Context.FLOW_VALUE, False, True),
    ("x", Context.BLOCK_KEY, True, True),
    ("true", Context.BLOCK_KEY, True, True),
    ("1", Context.BLOCK_KEY, True, True),
    ("~", Context.BLOCK_KEY, True, True),
    ("", Context.BLOCK_KEY, False, True),
    ("a: b", Context.BLOCK_KEY, False, True),
    ("a #b", Context.BLOCK_KEY, False, True),
    (" x", Context.BLOCK_KEY, False, True),
    ("- x", Context.BLOCK_KEY, False, True),
    ("? x", Context.BLOCK_KEY, False, True),
    ("a\tb", Context.BLOCK_KEY, False, True),
    ("x,y", Context.BLOCK_KEY, True, True),
    ("x,y", Context.FLOW_KEY, False, True),
    # A plain ``<<`` key is a merge key, which the loader refuses.
    ("<<", Context.BLOCK_KEY, False, True),
    ("<<", Context.FLOW_KEY, False, True),
    ("<<", Context.BLOCK_VALUE, True, True),
    ("x", Context.FLOW_KEY, True, True),
    ("[x]", Context.FLOW_KEY, False, True),
]


@pytest.mark.parametrize(("text", "context", "plain", "single"), REPRESENTABILITY)
def test_representability_table(text: str, context: Context, plain: bool, single: bool) -> None:
    """Req 6.4, 6.5: plain only for one-line, unpadded, control-free text that reads back unchanged
    as a string in its position (no ``,[]{}`` in flow); single-quoted for one-line text without
    control characters other than TAB; double-quoted always."""
    assert can_represent(PLAIN, text, context) is plain
    assert can_represent(SINGLE, text, context) is single
    assert can_represent(DOUBLE, text, context) is True


@pytest.mark.parametrize(
    ("text", "context", "plain", "single"),
    # A written ``file:`` string is an include; whether it loads is the WriteVerifier's concern.
    [row for row in REPRESENTABILITY if not row[0].startswith("file:")],
)
def test_every_representable_style_reads_back(
    tmp_path: Path, text: str, context: Context, plain: bool, single: bool
) -> None:
    """Req 6.1, 6.4-6.6, 7.1: every style judged representable renders text that the loader reads
    back as exactly the same string (keys: the same key text) in its context."""
    styles = [style for style in QUOTE_STYLES if can_represent(style, text, context)]
    assert DOUBLE in styles
    for style in styles:
        if context in KEY_PLACEMENTS:
            rendered = render_key(text, context, style)
            assert _reloaded_keys(tmp_path, rendered, context) == [text] * len(
                KEY_PLACEMENTS[context]
            ), (style, rendered)
        else:
            rendered = _string(text, context, style)
            assert _reloaded_values(tmp_path, rendered, context) == [text] * len(
                VALUE_PLACEMENTS[context]
            ), (style, rendered)


# --- style choice --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "preferred", "expected"),
    [
        # A representable preferred style is kept (6.1, 7.1, 7.2).
        ("y", PLAIN, PLAIN),
        ("y", SINGLE, SINGLE),
        ("y", DOUBLE, DOUBLE),
        # From plain: single, else double (6.2).
        ("0.2", PLAIN, SINGLE),
        ("it's: here", PLAIN, SINGLE),
        ("a\tb", PLAIN, SINGLE),
        ("a\nb", PLAIN, DOUBLE),
        ("bell\a", PLAIN, DOUBLE),
        # From single: plain is never reached (it is stricter), so double (6.2).
        ("a\nb", SINGLE, DOUBLE),
        ("\x85", SINGLE, DOUBLE),
        # From double: always kept.
        ("a\nb", DOUBLE, DOUBLE),
        ("0.2", DOUBLE, DOUBLE),
        # No preferred style: the minimal style (7.3).
        ("y", None, PLAIN),
        ("0.2", None, SINGLE),
        ("a\tb", None, SINGLE),
        ("a\nb", None, DOUBLE),
    ],
)
def test_choose_style_keeps_preferred_or_falls_back(
    text: str, preferred: QuoteStyle | None, expected: QuoteStyle
) -> None:
    """Req 6.1, 6.2, 7.1-7.3: keep the given style if it can represent the text; otherwise plain,
    then single-quoted, then double-quoted."""
    assert choose_style(text, Context.BLOCK_VALUE, preferred) is expected


def test_choose_style_respects_flow_context() -> None:
    """Req 6.4: ``x,y`` is plain in a block value but single-quoted inside a flow collection."""
    assert choose_style("x,y", Context.BLOCK_VALUE, PLAIN) is PLAIN
    assert choose_style("x,y", Context.FLOW_VALUE, PLAIN) is SINGLE
    assert choose_style("x,y", Context.FLOW_KEY, None) is SINGLE


@pytest.mark.parametrize(
    ("value", "preferred", "expected"),
    [
        (NewScalar("y", None), SINGLE, "'y'"),
        (NewScalar('é"q', None), DOUBLE, '"é\\"q"'),
        (NewScalar("0.2", None), PLAIN, "'0.2'"),
        (NewScalar("it's: here", None), PLAIN, "'it''s: here'"),
        (NewScalar("bell\a", None), PLAIN, '"bell\\a"'),
        (NewScalar("\x85", None), DOUBLE, '"\\N"'),
        (NewScalar("q", None), None, "q"),
        (NewScalar("file:inc.yaml", None), None, "file:inc.yaml"),
        (NewScalar("file:inc.yaml", None), PLAIN, "file:inc.yaml"),
        (NewScalar("file:inc.yaml", None), SINGLE, "'file:inc.yaml'"),
        # Non-strings: plain, in the spelling given in VALUE, whatever the preferred style (6.3).
        (NewScalar(5, "5"), SINGLE, "5"),
        (NewScalar(31, "0x1F"), DOUBLE, "0x1F"),
        (NewScalar(0.2, "0.2"), SINGLE, "0.2"),
        (NewScalar(True, "true"), DOUBLE, "true"),
        (NewScalar(None, "~"), SINGLE, "~"),
        (NewScalar(None, "null"), None, "null"),
        (NewScalar(None, ""), DOUBLE, ""),
    ],
)
def test_render_scalar(value: NewScalar, preferred: QuoteStyle | None, expected: str) -> None:
    """Req 2.7, 6.1-6.3, 6.5, 6.6: strings in the chosen style (``file:`` strings like any other),
    non-strings plain with their VALUE spelling (an empty spelling stays empty)."""
    assert render_scalar(value, Context.BLOCK_VALUE, preferred, PATH) == expected


def test_render_scalar_flow_value_quotes_flow_indicators() -> None:
    """Req 6.4, 7.2: ``x,y`` set as a flow list item is single-quoted although plain is
    preferred."""
    assert render_scalar(NewScalar("x,y", None), Context.FLOW_VALUE, PLAIN, PATH) == "'x,y'"


def test_non_strings_read_back_with_their_type(tmp_path: Path) -> None:
    """Req 6.3: plain spellings of non-strings read back as the same typed value."""
    cases: list[tuple[NewScalar, ScalarValue]] = [
        (NewScalar(31, "0x1F"), 31),
        (NewScalar(0.2, "0.2"), 0.2),
        (NewScalar(True, "true"), True),
        (NewScalar(None, "~"), None),
    ]
    for value, expected in cases:
        for context in VALUE_PLACEMENTS:
            rendered = render_scalar(value, context, SINGLE, PATH)
            assert _reloaded_values(tmp_path, rendered, context) == [expected] * len(
                VALUE_PLACEMENTS[context]
            )


def test_non_string_without_spelling_is_a_programming_error() -> None:
    """Req 6.3: a non-string must carry its VALUE spelling; none is invented."""
    with pytest.raises(ValueError, match="spelling"):
        render_scalar(NewScalar(5, None), Context.BLOCK_VALUE, None, PATH)


# --- escaping ------------------------------------------------------------------------------------

NAMED_ESCAPES = {
    0x00: "\\0",
    0x07: "\\a",
    0x08: "\\b",
    0x09: "\\t",
    0x0A: "\\n",
    0x0B: "\\v",
    0x0C: "\\f",
    0x0D: "\\r",
    0x1B: "\\e",
    0x85: "\\N",
}
CONTROL_CODES = [*range(0x00, 0x20), *range(0x7F, 0xA0)]


@pytest.mark.parametrize("code", CONTROL_CODES, ids=lambda code: f"U+{code:04X}")
def test_double_quoted_escape_table(tmp_path: Path, code: int) -> None:
    """Req 6.6: each control character is escaped by its named escape where one exists and as an
    upper-case ``\\xHH`` otherwise, and reads back as the same character."""
    text = f"a{chr(code)}b"
    expected_escape = NAMED_ESCAPES.get(code, f"\\x{code:02X}")
    rendered = _string(text, Context.BLOCK_VALUE, DOUBLE)
    assert rendered == f'"a{expected_escape}b"'
    assert _reloaded_values(tmp_path, rendered, Context.BLOCK_VALUE) == [text] * 3
    assert _reloaded_values(tmp_path, rendered, Context.FLOW_VALUE) == [text] * 2


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("back\\slash", '"back\\\\slash"'),
        ('say "hi"', '"say \\"hi\\""'),
        ("é", '"é"'),
        ("a\u2028b", '"a\u2028b"'),
        ("a\u2029b", '"a\u2029b"'),
        ("\xa0nbsp", '"\xa0nbsp"'),
        ("emoji \U0001f600", '"emoji \U0001f600"'),
        ("it's # [x], {y}: z", '"it\'s # [x], {y}: z"'),
        ("\ufeffbom", '"\ufeffbom"'),
        # Not control characters, but not printable in YAML either: escaped so the file stays
        # loadable (YAML requires it).
        ("a\ufffeb", '"a\\uFFFEb"'),
        ("a\uffffb", '"a\\uFFFFb"'),
    ],
)
def test_double_quoted_writes_other_characters_literally(
    tmp_path: Path, text: str, expected: str
) -> None:
    """Req 6.6: only ``\\``, ``"`` and characters YAML cannot hold literally are escaped; every
    other character, non-ASCII included, is written literally and reads back unchanged."""
    rendered = _string(text, Context.BLOCK_VALUE, DOUBLE)
    assert rendered == expected
    assert _reloaded_values(tmp_path, rendered, Context.BLOCK_VALUE) == [text] * 3
    assert _reloaded_values(tmp_path, rendered, Context.FLOW_VALUE) == [text] * 2


@pytest.mark.parametrize(
    ("text", "expected"),
    [("it's", "'it''s'"), ("''", "''''''"), ("a\tb", "'a\tb'"), ("", "''"), ("é", "'é'")],
)
def test_single_quoted_doubles_quotes(tmp_path: Path, text: str, expected: str) -> None:
    """Req 6.5: single-quoted text writes each ``'`` as ``''`` and keeps TAB literally."""
    rendered = _string(text, Context.BLOCK_VALUE, SINGLE)
    assert rendered == expected
    assert _reloaded_values(tmp_path, rendered, Context.BLOCK_VALUE) == [text] * 3


@pytest.mark.parametrize("text", ["\ud800", "a\udfffb"])
def test_lone_surrogate_is_refused_naming_path(text: str) -> None:
    """Req 6.6: a lone surrogate (from a ``\\ud800`` escape in VALUE) cannot be written as UTF-8;
    the write is refused naming PATH instead of producing an unreadable file."""
    with pytest.raises(WriteRefusedError) as caught:
        _string(text, Context.BLOCK_VALUE, DOUBLE)
    assert caught.value.exit_code is ExitCode.WRITE_REFUSED
    assert caught.value.path == "agents.coder.model"
    assert "surrogate" in caught.value.problem


def test_lone_surrogate_key_is_refused_naming_path() -> None:
    """Req 6.6, 7.1: a key holding a lone surrogate is refused like a value."""
    with pytest.raises(WriteRefusedError) as caught:
        render_key("k\ud800", Context.BLOCK_KEY, None, path=PATH)
    assert caught.value.path == "agents.coder.model"


# --- keys ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "context", "preferred", "expected"),
    [
        ("c", Context.BLOCK_KEY, DOUBLE, '"c"'),
        ("c", Context.BLOCK_KEY, SINGLE, "'c'"),
        ("c", Context.BLOCK_KEY, PLAIN, "c"),
        ("c", Context.BLOCK_KEY, None, "c"),
        # Keys are textual: ``true``, ``1`` and ``~`` keys stay plain.
        ("true", Context.BLOCK_KEY, PLAIN, "true"),
        ("1", Context.BLOCK_KEY, None, "1"),
        ("a: b", Context.BLOCK_KEY, PLAIN, "'a: b'"),
        ("it's: x", Context.BLOCK_KEY, None, "'it''s: x'"),
        ("a\nb", Context.BLOCK_KEY, SINGLE, '"a\\nb"'),
        ("x,y", Context.BLOCK_KEY, None, "x,y"),
        ("x,y", Context.FLOW_KEY, None, "'x,y'"),
        ('q"', Context.FLOW_KEY, DOUBLE, '"q\\""'),
        # A plain ``<<`` key would be a merge key: quoted instead.
        ("<<", Context.BLOCK_KEY, PLAIN, "'<<'"),
        ("<<", Context.BLOCK_KEY, None, "'<<'"),
        ("<<", Context.FLOW_KEY, PLAIN, "'<<'"),
        ("<<", Context.FLOW_KEY, None, "'<<'"),
    ],
)
def test_render_key(
    tmp_path: Path, key: str, context: Context, preferred: QuoteStyle | None, expected: str
) -> None:
    """Req 7.1, 7.3, 7.4: a new key copies the preceding key's style when it can represent it,
    otherwise the minimal style; the result reads back as the same key text."""
    rendered = render_key(key, context, preferred)
    assert rendered == expected
    assert _reloaded_keys(tmp_path, rendered, context) == [key] * len(KEY_PLACEMENTS[context])


# --- existing styles from the model --------------------------------------------------------------


@pytest.mark.parametrize(
    ("style", "expected"),
    [
        (ScalarStyle.PLAIN, PLAIN),
        (ScalarStyle.SINGLE_QUOTED, SINGLE),
        (ScalarStyle.DOUBLE_QUOTED, DOUBLE),
        (ScalarStyle.LITERAL, None),
        (ScalarStyle.FOLDED, None),
        (None, None),
    ],
)
def test_quote_style_of(style: ScalarStyle | None, expected: QuoteStyle | None) -> None:
    """Req 6.1, 7.2, 7.3: plain/single/double styles are preferred styles; literal, folded and no
    style (a text-include sibling) give no preferred quote style."""
    assert quote_style_of(style) is expected


def test_styles_taken_from_the_loaded_model(tmp_path: Path) -> None:
    """Req 6.1, 7.1, 7.2: key and value styles read by the loader feed the style choice directly."""
    root = _load_root(tmp_path, "\"a\": 'x'\nb: |\n  text\n")
    first, second = root.entries
    assert isinstance(first.value, ConfigScalar)
    assert isinstance(second.value, ConfigScalar)
    assert render_key("c", Context.BLOCK_KEY, quote_style_of(first.key_style)) == '"c"'
    value = NewScalar("y", None)
    assert render_scalar(value, Context.BLOCK_VALUE, quote_style_of(first.value.style), PATH) == (
        "'y'"
    )
    assert render_scalar(value, Context.BLOCK_VALUE, quote_style_of(second.value.style), PATH) == (
        "y"
    )


def test_sequence_item_style_feeds_choice(tmp_path: Path) -> None:
    """Req 7.2: the preceding list item's style is kept for a new item."""
    root = _load_root(tmp_path, "l:\n  - 'a'\n")
    sequence = root.entries[0].value
    assert isinstance(sequence, ConfigSequence)
    item = sequence.items[0]
    assert isinstance(item, ConfigScalar)
    preferred = quote_style_of(item.style)
    assert render_scalar(NewScalar("b", None), Context.BLOCK_VALUE, preferred, PATH) == "'b'"
