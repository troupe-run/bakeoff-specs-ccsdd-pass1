"""Loading ``!literal`` scalars (config-remote-schema 4.1-4.3, 4.6; task 2.1).

Every test writes a real theatre and loads it with ``load_config`` (or runs the real CLI). A scalar
tagged exactly ``!literal`` loads as its text, in any style and without plain-scalar typing, records
the tag's span in ``ConfigScalar.literal``, and its own span excludes the tag, as for an anchor
(``&a !literal x`` loads). It is never an include. ``!literal`` on a mapping, a list or a mapping
key, ``!literal`` written before an anchor, a ``$ref`` value tagged ``!literal`` and every other tag
stay load errors (exit 4) naming the file, the key path and the tag.

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

import hashlib
from pathlib import Path

import pytest

from support import MakeTheatre, RunCli
from troupe.config.errors import ConfigLoadError, ExitCode
from troupe.config.loader import load_config
from troupe.config.model import (
    ConfigDocument,
    ConfigMapping,
    ConfigNode,
    ConfigScalar,
    ConfigSequence,
    ScalarStyle,
    Span,
)

CONFIG_NAMES = ["troupe.yml", "troupe.yaml"]


def _config(theatre: Path, name: str = "troupe.yml") -> Path:
    return theatre / ".troupe" / name


def _load(make_theatre: MakeTheatre, text: str, files: dict[str, str] | None = None) -> Path:
    return _config(make_theatre(text, files=files))


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


def _text_at(text: str, span: Span) -> str:
    return text[span.start : span.end]


def _snapshot(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        if path.is_file()
        else "<dir>"
        for path in sorted(root.rglob("*"))
    }


# --- 4.1: values in every style, without typing ------------------------------------------------

# (case id, file text, expected value, expected style, the value as written without the tag)
LITERAL_VALUES: list[tuple[str, str, str, ScalarStyle, str]] = [
    ("plain-int", "a: !literal 5\n", "5", ScalarStyle.PLAIN, "5"),
    ("plain-bool", "a: !literal true\n", "true", ScalarStyle.PLAIN, "true"),
    ("plain-null", "a: !literal null\n", "null", ScalarStyle.PLAIN, "null"),
    ("plain-tilde", "a: !literal ~\n", "~", ScalarStyle.PLAIN, "~"),
    ("plain-float", "a: !literal 1e3\n", "1e3", ScalarStyle.PLAIN, "1e3"),
    ("plain-url", "a: !literal https://x/a.yml\n", "https://x/a.yml", ScalarStyle.PLAIN, "https://x/a.yml"),
    ("single-quoted", "a: !literal 'it''s'\n", "it's", ScalarStyle.SINGLE_QUOTED, "'it''s'"),
    ("double-quoted", 'a: !literal "t\\tx"\n', "t\tx", ScalarStyle.DOUBLE_QUOTED, '"t\\tx"'),
    ("literal-block", "a: !literal |\n  o\n  t\n", "o\nt\n", ScalarStyle.LITERAL, "|\n  o\n  t\n"),
    ("folded-block", "a: !literal >\n  o\n  t\n", "o t\n", ScalarStyle.FOLDED, ">\n  o\n  t\n"),
    ("comment-after-tag", "a: !literal # note\n  x\n", "x", ScalarStyle.PLAIN, "x"),
]  # fmt: skip


@pytest.mark.parametrize(
    ("text", "value", "style", "written"),
    [case[1:] for case in LITERAL_VALUES],
    ids=[case[0] for case in LITERAL_VALUES],
)
def test_literal_scalar_loads_as_its_text_in_every_style(
    make_theatre: MakeTheatre, text: str, value: str, style: ScalarStyle, written: str
) -> None:
    """Req 4.1: a scalar tagged ``!literal`` loads as a string whose value is its text, in any
    style and without plain-scalar typing; the tag's span is recorded and the node's own span is
    the value as written without the tag."""
    document = load_config(_load(make_theatre, text))

    scalar = _scalar_at(document, "a")
    assert scalar.value == value
    assert isinstance(scalar.value, str)
    assert scalar.style is style
    assert scalar.include is None
    assert scalar.literal is not None
    assert _text_at(text, scalar.literal) == "!literal"
    assert _text_at(text, scalar.origin.span) == written


@pytest.mark.parametrize(
    ("text", "tag_end"),
    [
        ("a: !literal\nb: 1\n", 11),
        ("a: !literal   \nb: 1\n", 11),
        ("a: !literal # note\nb: 1\n", 11),
        ("l:\n  - !literal\n  - x\n", 15),
    ],
    ids=["end-of-line", "trailing-blanks", "comment", "list-item"],
)
def test_empty_literal_scalar_is_the_empty_string_at_the_tags_end(
    make_theatre: MakeTheatre, text: str, tag_end: int
) -> None:
    """Req 4.1: an empty ``!literal`` scalar is the empty string (not null); its span is empty at
    the end of the tag."""
    document = load_config(_load(make_theatre, text))

    scalar = _scalar_at(document, "a") if text.startswith("a") else _scalar_at(document, "l", 0)
    assert scalar.value == ""
    assert scalar.literal == Span(tag_end - len("!literal"), tag_end)
    assert scalar.origin.span == Span(tag_end, tag_end)


def test_literal_in_flow_collections_and_list_items(make_theatre: MakeTheatre) -> None:
    """Req 4.1: ``!literal`` works on scalars inside flow mappings, flow lists and block lists."""
    text = (
        "f: {k: !literal 1, u: !literal file:x.yml}\nl: [!literal yes, 2]\nb:\n  - !literal 0x1\n"
    )
    document = load_config(_load(make_theatre, text))

    cases: list[tuple[tuple[str | int, ...], str, str]] = [
        (("f", "k"), "1", "1"),
        (("f", "u"), "file:x.yml", "file:x.yml"),
        (("l", 0), "yes", "yes"),
        (("b", 0), "0x1", "0x1"),
    ]
    for keys, value, written in cases:
        scalar = _scalar_at(document, *keys)
        assert scalar.value == value, keys
        assert scalar.literal is not None and _text_at(text, scalar.literal) == "!literal"
        assert _text_at(text, scalar.origin.span) == written
    assert _scalar_at(document, "l", 1).value == 2
    assert _scalar_at(document, "l", 1).literal is None


# --- 4.1, 4.6: a literal is never an include ---------------------------------------------------


@pytest.mark.parametrize(
    "written",
    ["file:inc.yml", "'file:inc.yml'", '"file:notes.txt"', "|-\n  file:inc.yml"],
    ids=["plain", "single-quoted", "double-quoted-text", "literal-block"],
)
def test_literal_include_string_is_a_string_and_its_target_is_never_read(
    make_theatre: MakeTheatre, written: str
) -> None:
    """Req 4.1, 4.6: a ``!literal`` include string is loaded as the string itself; its target is
    not read (here it does not even exist) and the document lists only the root file."""
    config = _load(make_theatre, f"a: !literal {written}\n")

    document = load_config(config)

    scalar = _scalar_at(document, "a")
    assert isinstance(scalar.value, str) and scalar.value.startswith("file:")
    assert scalar.include is None
    assert [source.path for source in document.files] == [config]


def test_literal_next_to_an_untagged_include_leaves_the_include_working(
    make_theatre: MakeTheatre,
) -> None:
    """Req 4.1: only the tagged string is a literal; an untagged ``file:`` string still includes."""
    config = _load(
        make_theatre,
        "lit: !literal file:inc.yml\ninc: file:inc.yml\n",
        files={".troupe/inc.yml": "k: v\n"},
    )

    document = load_config(config)

    assert _scalar_at(document, "lit").value == "file:inc.yml"
    assert _scalar_at(document, "inc", "k").value == "v"


def test_literal_in_an_included_file_is_recorded_in_that_file(make_theatre: MakeTheatre) -> None:
    """Req 4.1: ``!literal`` loads inside an included YAML file; its spans lie in that file."""
    inc_text = "hook: !literal file:other.yml\n"
    config = _load(make_theatre, "a: file:inc.yml\n", files={".troupe/inc.yml": inc_text})

    document = load_config(config)

    scalar = _scalar_at(document, "a", "hook")
    assert scalar.value == "file:other.yml"
    assert scalar.include is None
    assert scalar.origin.file.path == config.parent / "inc.yml"
    assert scalar.literal is not None and _text_at(inc_text, scalar.literal) == "!literal"
    assert _text_at(inc_text, scalar.origin.span) == "file:other.yml"


# --- 4.1 with anchors and aliases --------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "written"),
    [
        ("a: &x !literal file:inc.yml\nb: *x\n", "file:inc.yml"),
        ("a: &x !literal 'file:inc.yml'\nb: *x\n", "'file:inc.yml'"),
        ("a: &x   !literal   file:inc.yml\nb: *x\n", "file:inc.yml"),
    ],
    ids=["plain", "quoted", "extra-blanks"],
)
def test_anchor_before_literal_loads_and_an_alias_of_it_is_a_literal(
    make_theatre: MakeTheatre, text: str, written: str
) -> None:
    """Req 4.1: ``&a !literal x`` loads; the anchor, the tag and the value have their own spans,
    and an alias of the anchored literal is the same literal string, never an include."""
    document = load_config(_load(make_theatre, text))

    anchored = _scalar_at(document, "a")
    assert anchored.value == "file:inc.yml"
    assert anchored.include is None
    assert anchored.anchor is not None and _text_at(text, anchored.anchor.span) == "&x"
    assert anchored.literal is not None and _text_at(text, anchored.literal) == "!literal"
    assert _text_at(text, anchored.origin.span) == written

    alias = _scalar_at(document, "b")
    assert alias.value == "file:inc.yml"
    assert alias.include is None
    assert alias.alias is not None and alias.alias.name == "x"
    assert alias.literal == anchored.literal
    assert alias.origin == anchored.origin


def test_empty_anchored_literal_is_the_empty_string_at_the_tags_end(
    make_theatre: MakeTheatre,
) -> None:
    """Req 4.1: ``&x !literal`` with nothing after it is the empty string, empty at the tag's
    end."""
    text = "a: &x !literal\nb: *x\n"
    document = load_config(_load(make_theatre, text))

    for key in ("a", "b"):
        scalar = _scalar_at(document, key)
        assert scalar.value == ""
        assert scalar.literal == Span(6, 14)
        assert scalar.origin.span == Span(14, 14)


# --- 4.1: readers print no tag -----------------------------------------------------------------


@pytest.mark.parametrize("config_name", CONFIG_NAMES)
def test_get_and_show_print_literal_values_without_the_tag(
    make_theatre: MakeTheatre, run_cli: RunCli, config_name: str
) -> None:
    """Req 4.1, 4.6: ``get`` prints a literal's text and ``show`` prints it as a string, never the
    tag and never an include's content."""
    theatre = make_theatre(
        "n: !literal 5\nhook: !literal file:inc.yml\n",
        config_name=config_name,
        files={".troupe/inc.yml": "secret: included\n"},
    )

    get_n = run_cli("config", "get", "n", "--theatre", str(theatre))
    get_hook = run_cli("config", "get", "hook", "--theatre", str(theatre))
    show = run_cli("config", "show", "--theatre", str(theatre))

    assert (get_n.exit_code, get_n.stdout, get_n.stderr) == (0, b"5\n", "")
    assert (get_hook.exit_code, get_hook.stdout, get_hook.stderr) == (0, b"file:inc.yml\n", "")
    assert show.exit_code == 0, show.stderr
    assert show.stdout == b"n: '5'\nhook: file:inc.yml\n"


# --- 4.2, 4.3: refusals ------------------------------------------------------------------------

# (case id, file text, key path, tag named in the problem)
LITERAL_REFUSALS: list[tuple[str, str, str | None, str]] = [
    ("on-block-mapping", "a: !literal\n  b: 1\n", "a", "'!literal'"),
    ("on-flow-mapping", "a: !literal {b: 1}\n", "a", "'!literal'"),
    ("on-block-list", "a: !literal\n- 1\n", "a", "'!literal'"),
    ("on-flow-list", "a: !literal [1]\n", "a", "'!literal'"),
    ("on-anchored-mapping", "a: &x !literal\n  b: 1\n", "a", "'!literal'"),
    ("on-root", "!literal\na: 1\n", None, "'!literal'"),
    ("on-key", "!literal a: 1\n", "a", "'!literal'"),
    ("on-nested-key", "a:\n  !literal b: 1\n", "a.b", "'!literal'"),
    ("before-anchor", "a: !literal &x 5\n", "a", "'!literal'"),
    ("before-anchor-in-list", "l:\n  - !literal &x 5\n", "l.0", "'!literal'"),
    ("ref-value", "a:\n  $ref: !literal file:inc.yml\n", "a", "'!literal'"),
    ("ref-value-anchored", "a:\n  $ref: &r !literal file:inc.yml\n", "a", "'!literal'"),
    ("other-local-tag", "a: !lit x\n", "a", "'!lit'"),
    ("longer-tag", "a: !literally x\n", "a", "'!literally'"),
    ("upper-case-tag", "a: !Literal x\n", "a", "'!Literal'"),
    ("core-tag", "a: !!str x\n", "a", "'!!str'"),
    ("verbatim-literal", "a: !<!literal> x\n", "a", "'!<!literal>'"),
    ("other-tag-after-anchor", "a: &x !custom 5\n", "a", "'!custom'"),
]


@pytest.mark.parametrize(
    ("text", "key_path", "tag"),
    [case[1:] for case in LITERAL_REFUSALS],
    ids=[case[0] for case in LITERAL_REFUSALS],
)
def test_refused_tag_forms_are_load_errors_naming_file_key_path_and_tag(
    make_theatre: MakeTheatre, text: str, key_path: str | None, tag: str
) -> None:
    """Req 4.2, 4.3: ``!literal`` on a mapping, a list or a key, before an anchor, as a ``$ref``
    value, and every other tag are refused (exit 4) naming the file, the key path and the tag;
    nothing is written."""
    config = _load(make_theatre, text, files={".troupe/inc.yml": "k: v\n"})
    before = _snapshot(config.parent.parent)

    with pytest.raises(ConfigLoadError) as info:
        load_config(config)

    error = info.value
    assert error.exit_code is ExitCode.LOAD
    assert (error.file, error.path) == (config, key_path)
    assert tag in error.problem
    assert _snapshot(config.parent.parent) == before


def test_literal_before_an_anchor_says_to_write_the_anchor_first(
    make_theatre: MakeTheatre,
) -> None:
    """Req 4.2: the refusal of ``!literal &a`` shows the accepted order ``&a !literal``."""
    config = _load(make_theatre, "a: !literal &x 5\n")

    with pytest.raises(ConfigLoadError) as info:
        load_config(config)

    assert "&x !literal" in info.value.problem


def test_ref_value_tagged_literal_says_a_ref_needs_an_include_string(
    make_theatre: MakeTheatre,
) -> None:
    """Req 4.3: a ``$ref`` value tagged ``!literal`` is refused because ``$ref`` needs an include
    string."""
    config = _load(make_theatre, "a:\n  $ref: !literal file:inc.yml\n  k: 1\n")

    with pytest.raises(ConfigLoadError) as info:
        load_config(config)

    assert info.value.problem.startswith("`$ref` must be an include string")


@pytest.mark.parametrize("config_name", CONFIG_NAMES)
@pytest.mark.parametrize(
    ("content", "key_path"),
    [
        ("a: !literal\n  b: 1\n", "a"),
        ("a: !literal &x 5\n", "a"),
        ("a:\n  $ref: !literal file:inc.yml\n", "a"),
    ],
    ids=["on-mapping", "before-anchor", "ref-value"],
)
def test_refused_literal_forms_exit_4_through_the_cli(
    make_theatre: MakeTheatre, run_cli: RunCli, config_name: str, content: str, key_path: str
) -> None:
    """Req 4.2, 4.3: through the CLI, refused ``!literal`` forms exit 4 with nothing on stdout
    and one stderr line naming the file, the key path and the tag; no history state is created."""
    theatre = make_theatre(content, config_name=config_name, files={".troupe/inc.yml": "k: v\n"})
    config = theatre / ".troupe" / config_name

    result = run_cli("config", "show", "--theatre", str(theatre))

    assert result.exit_code == 4, result.stderr
    assert result.stdout == b""
    assert result.stderr.startswith(f"{config}: {key_path}: ")
    assert "'!literal'" in result.stderr
    assert sorted(path.name for path in (theatre / ".troupe").iterdir()) == sorted(
        [config_name, "inc.yml"]
    )
