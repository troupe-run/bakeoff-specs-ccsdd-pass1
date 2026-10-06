"""Loading a single YAML file into the source-mapped model (Requirements 3.1-3.9).

Every test writes real YAML into a temporary theatre and loads it with ``load_config``. Span tests
check ruamel's marks empirically: ``text[span]`` must be the exact written source.
"""

import hashlib
import math
from pathlib import Path

import pytest

from support import MakeTheatre
from troupe.config.errors import ConfigLoadError, ExitCode
from troupe.config.loader import load_config
from troupe.config.model import (
    CollectionStyle,
    ConfigDocument,
    ConfigMapping,
    ConfigNode,
    ConfigScalar,
    ConfigSequence,
    LineEnding,
    MappingEntry,
    ScalarStyle,
    Span,
)

# A fixture with comments, blank lines, unusual key order, quoted keys, multibyte characters,
# every scalar style and both collection styles.
RICH = """\
# leading comment
zeta: plain value   # trailing comment
'single key': 'it''s'
"double key": "tab\\there"
é: café

unicode: "😀 ünï"
count: 0o17
truth: yes
nothing:
tilde: ~
list:
  - 1
  - "two"
  - [1, yes, 0.1, null, ~, 1e3]
flow: {a: 1, 'b': x}
literal: |
  line one
  line two
strip: |-
  kept
fold: >
  folded
  text

nested:
  inner: 'q'   # comment after
  multi: plain
    continued
last: 1_0
"""

# (key path, exact span text, style, value) for every plain and quoted scalar value in RICH.
RICH_SCALARS: list[tuple[tuple[str | int, ...], str, ScalarStyle, object]] = [
    (("zeta",), "plain value", ScalarStyle.PLAIN, "plain value"),
    (("single key",), "'it''s'", ScalarStyle.SINGLE_QUOTED, "it's"),
    (("double key",), '"tab\\there"', ScalarStyle.DOUBLE_QUOTED, "tab\there"),
    (("é",), "café", ScalarStyle.PLAIN, "café"),
    (("unicode",), '"😀 ünï"', ScalarStyle.DOUBLE_QUOTED, "😀 ünï"),
    (("count",), "0o17", ScalarStyle.PLAIN, 15),
    (("truth",), "yes", ScalarStyle.PLAIN, "yes"),
    (("tilde",), "~", ScalarStyle.PLAIN, None),
    (("list", 0), "1", ScalarStyle.PLAIN, 1),
    (("list", 1), '"two"', ScalarStyle.DOUBLE_QUOTED, "two"),
    (("list", 2, 0), "1", ScalarStyle.PLAIN, 1),
    (("list", 2, 1), "yes", ScalarStyle.PLAIN, "yes"),
    (("list", 2, 2), "0.1", ScalarStyle.PLAIN, 0.1),
    (("list", 2, 3), "null", ScalarStyle.PLAIN, None),
    (("list", 2, 4), "~", ScalarStyle.PLAIN, None),
    (("list", 2, 5), "1e3", ScalarStyle.PLAIN, 1000.0),
    (("flow", "a"), "1", ScalarStyle.PLAIN, 1),
    (("flow", "b"), "x", ScalarStyle.PLAIN, "x"),
    (("nested", "inner"), "'q'", ScalarStyle.SINGLE_QUOTED, "q"),
    (("nested", "multi"), "plain\n    continued", ScalarStyle.PLAIN, "plain continued"),
    (("last",), "1_0", ScalarStyle.PLAIN, "1_0"),
]

# (key path, header and content lines as written, style, value) for every block scalar in RICH.
RICH_BLOCKS: list[tuple[tuple[str, ...], str, ScalarStyle, str]] = [
    (("literal",), "|\n  line one\n  line two\n", ScalarStyle.LITERAL, "line one\nline two\n"),
    (("strip",), "|-\n  kept\n", ScalarStyle.LITERAL, "kept"),
    (("fold",), ">\n  folded\n  text\n", ScalarStyle.FOLDED, "folded text\n"),
]


def _node_at(document: ConfigDocument, *keys: str | int) -> ConfigNode:
    node: ConfigNode = document.root
    for key in keys:
        if isinstance(key, int):
            assert isinstance(node, ConfigSequence)
            node = node.items[key]
        else:
            assert isinstance(node, ConfigMapping)
            entry = node.get(key)
            assert entry is not None, key
            node = entry.value
    return node


def _scalar_at(document: ConfigDocument, *keys: str | int) -> ConfigScalar:
    node = _node_at(document, *keys)
    assert isinstance(node, ConfigScalar)
    return node


def _entry(mapping: ConfigNode, key: str) -> MappingEntry:
    assert isinstance(mapping, ConfigMapping)
    entry = mapping.get(key)
    assert entry is not None
    return entry


def _span_text(node: ConfigNode) -> str:
    return node.origin.file.text[node.origin.span.start : node.origin.span.end]


def _crlf(text: str) -> str:
    return text.replace("\n", "\r\n")


def _load(make_theatre: MakeTheatre, config: str | bytes, name: str = "troupe.yml") -> Path:
    theatre = make_theatre(config, config_name=name)
    return theatre / ".troupe" / name


def _snapshot(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        if path.is_file()
        else "<dir>"
        for path in sorted(root.rglob("*"))
    }


# --- spans, styles and typing ---------------------------------------------------------------


@pytest.mark.parametrize("line_ending", ["lf", "crlf"])
@pytest.mark.parametrize(("keys", "written", "style", "value"), RICH_SCALARS)
def test_plain_and_quoted_scalar_span_is_the_exact_written_text(
    make_theatre: MakeTheatre,
    line_ending: str,
    keys: tuple[str | int, ...],
    written: str,
    style: ScalarStyle,
    value: object,
) -> None:
    text = RICH if line_ending == "lf" else _crlf(RICH)
    expected_written = written if line_ending == "lf" else _crlf(written)
    document = load_config(_load(make_theatre, text))
    scalar = _scalar_at(document, *keys)
    assert _span_text(scalar) == expected_written
    assert scalar.style is style
    assert type(scalar.value) is type(value)
    assert scalar.value == value
    assert scalar.origin.file is document.root_file


@pytest.mark.parametrize("line_ending", ["lf", "crlf"])
@pytest.mark.parametrize(("keys", "written", "style", "value"), RICH_BLOCKS)
def test_block_scalar_span_starts_at_indicator_and_covers_all_content_lines(
    make_theatre: MakeTheatre,
    line_ending: str,
    keys: tuple[str, ...],
    written: str,
    style: ScalarStyle,
    value: str,
) -> None:
    text = RICH if line_ending == "lf" else _crlf(RICH)
    expected_written = written if line_ending == "lf" else _crlf(written)
    document = load_config(_load(make_theatre, text))
    scalar = _scalar_at(document, *keys)
    span = scalar.origin.span
    # Starts exactly at the indicator and covers header plus every content line (incl. the
    # final line break); the end may extend over trailing blank lines, so only a lower bound
    # and "nothing but blank lines after the content" are asserted.
    assert text[span.start : span.start + len(expected_written)] == expected_written
    assert text[span.start + len(expected_written) : span.end].strip("\r\n") == ""
    assert scalar.style is style
    # Line breaks inside scalar content are normalised to LF (YAML 1.2, 5.4).
    assert scalar.value == value


def test_block_scalar_span_may_extend_over_trailing_blank_lines(
    make_theatre: MakeTheatre,
) -> None:
    text = "a: |\n  a\n  b\n\n\nz: 1\n"
    document = load_config(_load(make_theatre, text))
    scalar = _scalar_at(document, "a")
    assert scalar.value == "a\nb\n"
    assert text[scalar.origin.span.start :].startswith("|\n  a\n  b\n")
    assert scalar.origin.span.end <= text.index("z: 1")


@pytest.mark.parametrize("line_ending", ["lf", "crlf"])
def test_key_style_and_key_span_match_the_written_key(
    make_theatre: MakeTheatre, line_ending: str
) -> None:
    text = RICH if line_ending == "lf" else _crlf(RICH)
    document = load_config(_load(make_theatre, text))
    root = document.root
    expected = {
        "zeta": ("zeta", ScalarStyle.PLAIN),
        "single key": ("'single key'", ScalarStyle.SINGLE_QUOTED),
        "double key": ('"double key"', ScalarStyle.DOUBLE_QUOTED),
        "é": ("é", ScalarStyle.PLAIN),
    }
    for key, (written, style) in expected.items():
        entry = _entry(root, key)
        span = entry.key_origin.span
        assert text[span.start : span.end] == written
        assert entry.key_style is style
        assert entry.key_origin.file is document.root_file


def test_mapping_entries_keep_document_order(make_theatre: MakeTheatre) -> None:
    document = load_config(_load(make_theatre, RICH))
    assert [entry.key for entry in document.root.entries] == [
        "zeta",
        "single key",
        "double key",
        "é",
        "unicode",
        "count",
        "truth",
        "nothing",
        "tilde",
        "list",
        "flow",
        "literal",
        "strip",
        "fold",
        "nested",
        "last",
    ]


def test_collection_styles_and_flow_spans_are_exact(make_theatre: MakeTheatre) -> None:
    document = load_config(_load(make_theatre, RICH))
    assert document.root.style is CollectionStyle.BLOCK
    block_list = _node_at(document, "list")
    assert isinstance(block_list, ConfigSequence)
    assert block_list.style is CollectionStyle.BLOCK
    assert _span_text(block_list).startswith("- 1\n")
    flow_list = _node_at(document, "list", 2)
    assert isinstance(flow_list, ConfigSequence)
    assert flow_list.style is CollectionStyle.FLOW
    assert _span_text(flow_list) == "[1, yes, 0.1, null, ~, 1e3]"
    flow_map = _node_at(document, "flow")
    assert isinstance(flow_map, ConfigMapping)
    assert flow_map.style is CollectionStyle.FLOW
    assert _span_text(flow_map) == "{a: 1, 'b': x}"
    assert _entry(flow_map, "b").key_style is ScalarStyle.SINGLE_QUOTED
    nested = _node_at(document, "nested")
    assert isinstance(nested, ConfigMapping)
    assert nested.style is CollectionStyle.BLOCK
    assert _span_text(nested).startswith("inner: 'q'")


def test_empty_value_is_a_plain_null(make_theatre: MakeTheatre) -> None:
    document = load_config(_load(make_theatre, RICH))
    scalar = _scalar_at(document, "nothing")
    assert scalar.value is None
    assert scalar.style is ScalarStyle.PLAIN


def test_plain_scalars_use_the_yaml_1_2_core_schema(make_theatre: MakeTheatre) -> None:
    text = (
        "on: on\nb: True\nc: FALSE\nd: null\ne: 0x1F\nf: 2001-12-14\ng: .inf\nh: -.inf\n"
        "i: .NaN\nj: '1'\nk: \"true\"\nl: |\n  1\nm: +1\nn: 012\n"
    )
    document = load_config(_load(make_theatre, text))
    values = {entry.key: entry.value for entry in document.root.entries}
    typed = {key: node.value for key, node in values.items() if isinstance(node, ConfigScalar)}
    assert typed["on"] == "on"
    assert typed["b"] is True
    assert typed["c"] is False
    assert typed["d"] is None
    assert typed["e"] == 31
    assert typed["f"] == "2001-12-14"
    assert typed["g"] == math.inf
    assert typed["h"] == -math.inf
    nan = typed["i"]
    assert isinstance(nan, float)
    assert math.isnan(nan)
    assert typed["j"] == "1"
    assert typed["k"] == "true"
    assert typed["l"] == "1\n"
    assert typed["m"] == 1
    assert typed["n"] == 12


def test_multibyte_characters_use_character_indices(make_theatre: MakeTheatre) -> None:
    text = "é: café\r\nk: 😀 v\r\n"
    document = load_config(_load(make_theatre, text))
    cafe = _scalar_at(document, "é")
    assert cafe.origin.span == Span(3, 7)
    emoji = _scalar_at(document, "k")
    assert _span_text(emoji) == "😀 v"
    assert emoji.origin.span == Span(12, 15)


def test_byte_order_mark_is_kept_and_spans_index_the_text_with_it(
    make_theatre: MakeTheatre,
) -> None:
    text = "\ufeffa: 1\n"
    document = load_config(_load(make_theatre, text))
    assert document.root_file.text == text
    entry = _entry(document.root, "a")
    assert entry.key_origin.span == Span(1, 2)
    assert _span_text(entry.value) == "1"


# --- folded scalars (3.8) -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("block", "value"),
    [
        (">\n  a\n  b\n", "a b\n"),
        (">-\n  x\n\n  y\n  z\n", "x\ny z"),
        (">\n  a\n    more\n  b\n", "a\n  more\nb\n"),
        (">+\n  x\n\n", "x\n\n"),
    ],
)
def test_folded_value_is_the_yaml_folded_text_without_fold_markers(
    make_theatre: MakeTheatre, block: str, value: str
) -> None:
    text = f"a: {block}"
    document = load_config(_load(make_theatre, text))
    scalar = _scalar_at(document, "a")
    assert scalar.style is ScalarStyle.FOLDED
    assert scalar.value == value
    assert "\x07" not in document.root_file.text
    assert text[scalar.origin.span.start :].startswith(block)


def test_folded_key_has_no_fold_markers(make_theatre: MakeTheatre) -> None:
    document = load_config(_load(make_theatre, "? >-\n  k\n  ey\n: 1\n"))
    entry = document.root.entries[0]
    assert entry.key == "k ey"
    assert entry.key_style is ScalarStyle.FOLDED


def test_double_quoted_bell_escape_keeps_its_bell_character(make_theatre: MakeTheatre) -> None:
    document = load_config(_load(make_theatre, 'a: "\\a"\nb: |\n  x\n'))
    assert _scalar_at(document, "a").value == "\x07"
    assert _scalar_at(document, "b").value == "x\n"


# --- source file record and line endings ----------------------------------------------------


@pytest.mark.parametrize(
    ("text", "line_ending"),
    [
        ("a: 1", LineEnding.NONE),
        ("a: 1\nb: 2\n", LineEnding.LF),
        ("a: 1\r\nb: 2\r\n", LineEnding.CRLF),
        ("a: 1\r\nb: 2\n", LineEnding.MIXED),
        ("a: 1\rb: 2\r", LineEnding.MIXED),
    ],
)
def test_line_ending_is_detected(
    make_theatre: MakeTheatre, text: str, line_ending: LineEnding
) -> None:
    document = load_config(_load(make_theatre, text))
    assert document.root_file.line_ending is line_ending
    assert document.root_file.text == text


@pytest.mark.parametrize("name", ["troupe.yml", "troupe.yaml"])
def test_source_file_records_the_opened_and_the_real_path(
    make_theatre: MakeTheatre, name: str
) -> None:
    config = _load(make_theatre, "a: 1\n", name)
    opened = config.parent / ".." / ".troupe" / name
    document = load_config(opened)
    assert document.root_file.path == opened
    assert document.root_file.real_path == config.resolve()
    assert document.files == (document.root_file,)
    assert document.root.origin.file is document.root_file


@pytest.mark.parametrize("text", ["", "# only a comment\n\n# another\n", "\r\n", "\ufeff"])
def test_empty_or_comment_only_root_is_an_empty_block_mapping(
    make_theatre: MakeTheatre, text: str
) -> None:
    document = load_config(_load(make_theatre, text))
    assert document.root.entries == ()
    assert document.root.style is CollectionStyle.BLOCK
    assert document.root.origin.span == Span(0, 0)
    assert document.root.origin.file is document.root_file
    assert document.root_file.text == text


# --- in-memory overrides ---------------------------------------------------------------------


def test_override_text_replaces_the_file_on_disk(make_theatre: MakeTheatre) -> None:
    config = _load(make_theatre, "a: 1\n")
    document = load_config(config, overrides={config.resolve(): "a: 2\r\n"})
    assert _scalar_at(document, "a").value == 2
    assert document.root_file.text == "a: 2\r\n"
    assert document.root_file.line_ending is LineEnding.CRLF
    assert config.read_text(encoding="utf-8") == "a: 1\n"


def test_overridden_file_is_never_read_from_disk(make_theatre: MakeTheatre) -> None:
    theatre = make_theatre(files={"other.txt": "x"})
    config = theatre / ".troupe" / "troupe.yml"
    document = load_config(config, overrides={config.resolve(): "b: yes\n"})
    assert _scalar_at(document, "b").value == "yes"
    assert not config.exists()


def test_override_for_another_path_is_ignored(make_theatre: MakeTheatre) -> None:
    config = _load(make_theatre, "a: 1\n")
    document = load_config(config, overrides={config.parent.resolve() / "x.yml": "a: 2\n"})
    assert _scalar_at(document, "a").value == 1


def test_errors_in_override_text_name_the_file(make_theatre: MakeTheatre) -> None:
    config = _load(make_theatre, "a: 1\n")
    with pytest.raises(ConfigLoadError) as info:
        load_config(config, overrides={config.resolve(): "a: 1\na: 2\n"})
    assert info.value.file == config
    assert info.value.path == "a"


# --- refusals (3.2, 3.3, 3.4, 3.7, 3.9; product-owner decisions H and I) ---------------------

REFUSALS: list[tuple[str, str | bytes, str | None, str]] = [
    ("non-utf8", b"a: caf\xe9\n", None, "UTF-8"),
    ("syntax", "a: [1, 2\n", None, "line"),
    ("mapping-in-plain", "a: b: c\n", None, "line 1"),
    ("multi-document", "a: 1\n---\nb: 2\n", None, "single document"),
    ("non-printable", "a: \x07\n", None, "#x0007"),
    ("duplicate-root-key", "a: 1\nb: 2\na: 3\n", "a", "duplicate key"),
    ("duplicate-nested-key", "a:\n  b: 1\n  b: 2\n", "a.b", "duplicate key"),
    ("duplicate-quoted-and-plain", 'a: 1\n"a": 2\n', "a", "duplicate key"),
    ("duplicate-equal-value", "x:\n  true: 1\n  True: 2\n", "x.True", "duplicate key"),
    ("duplicate-in-flow", "x: {a: 1, a: 2}\n", "x.a", "duplicate key"),
    ("tag-core", "a: !!str 5\n", "a", "explicit tag"),
    ("tag-custom", "a: 1\nb: !custom x\n", "b", "explicit tag"),
    ("tag-non-specific", "a: ! 5\n", "a", "explicit tag"),
    ("tag-verbatim", "a: !<tag:yaml.org,2002:str> 5\n", "a", "explicit tag"),
    ("tag-on-key", "!!str a: 1\n", "a", "explicit tag"),
    ("tag-on-nested-key", "a:\n  !!str b: 1\n", "a.b", "explicit tag"),
    ("tag-on-block-mapping", "a: !!map\n  b: 1\n", "a", "explicit tag"),
    ("tag-on-block-sequence", "a: !!seq\n- 1\n", "a", "explicit tag"),
    ("tag-on-flow-item", "a: [1, !!int 2]\n", "a.1", "explicit tag"),
    ("tag-on-root", "!!map\na: 1\n", None, "explicit tag"),
    ("anchor-on-key", "a:\n  &k b: 1\n", "a.b", "anchor on a mapping key is not supported"),
    ("anchor-after-tag", "a: !!str &x 5\n", "a", "explicit tag"),
    ("undefined-alias", "a: *x\n", None, "line 1"),
    ("merge-key", "<<: {a: 1}\n", "<<", "merge key"),
    ("nested-merge-key", "a:\n  b: 1\n  <<: {c: 2}\n", "a.<<", "merge key"),
    ("complex-key-sequence", "? [a]\n: 1\n", None, "key"),
    ("complex-key-nested", "a:\n  ? {b: 1}\n  : 2\n", "a", "key"),
    ("root-sequence", "- 1\n- 2\n", None, "mapping"),
    ("root-scalar", "just text\n", None, "mapping"),
    ("root-null-document", "---\n", None, "mapping"),
]


@pytest.mark.parametrize(
    ("content", "path", "problem"),
    [refusal[1:] for refusal in REFUSALS],
    ids=[refusal[0] for refusal in REFUSALS],
)
def test_refused_file_raises_a_load_error_naming_the_file_and_key_path(
    make_theatre: MakeTheatre, content: str | bytes, path: str | None, problem: str
) -> None:
    config = _load(make_theatre, content)
    before = _snapshot(config.parent.parent)
    with pytest.raises(ConfigLoadError) as info:
        load_config(config)
    error = info.value
    assert error.exit_code is ExitCode.LOAD
    assert error.file == config
    assert error.path == path
    assert problem in error.problem
    expected_prefix = f"{config}: {path}: " if path is not None else f"{config}: "
    assert str(error).startswith(expected_prefix)
    assert _snapshot(config.parent.parent) == before


def test_quoted_merge_key_text_is_an_ordinary_key(make_theatre: MakeTheatre) -> None:
    document = load_config(_load(make_theatre, '"<<": 1\n'))
    entry = _entry(document.root, "<<")
    assert entry.key_style is ScalarStyle.DOUBLE_QUOTED
    assert isinstance(entry.value, ConfigScalar)
    assert entry.value.value == 1


def test_missing_file_raises_a_load_error_naming_it(tmp_path: Path) -> None:
    config = tmp_path / ".troupe" / "troupe.yml"
    with pytest.raises(ConfigLoadError) as info:
        load_config(config)
    assert info.value.file == config
    assert info.value.exit_code is ExitCode.LOAD
    assert not (tmp_path / ".troupe").exists()


def test_unreadable_path_raises_a_load_error_naming_it(make_theatre: MakeTheatre) -> None:
    theatre = make_theatre(files={".troupe/troupe.yml/inner.txt": "x"})
    config = theatre / ".troupe" / "troupe.yml"
    with pytest.raises(ConfigLoadError) as info:
        load_config(config)
    assert info.value.file == config
    assert str(info.value).startswith(f"{config}: ")


# --- read-only (3.6) -------------------------------------------------------------------------


@pytest.mark.parametrize("line_ending", ["lf", "crlf"])
def test_loading_leaves_every_file_byte_identical(
    make_theatre: MakeTheatre, line_ending: str
) -> None:
    text = RICH if line_ending == "lf" else _crlf(RICH)
    config = _load(make_theatre, text)
    theatre = config.parent.parent
    before = _snapshot(theatre)
    raw_before = config.read_bytes()
    document = load_config(config)
    assert _snapshot(theatre) == before
    assert config.read_bytes() == raw_before
    assert document.root_file.text.encode("utf-8") == raw_before
