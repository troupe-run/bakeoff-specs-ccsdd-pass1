"""Loading anchors (and, from task 2.2, aliases) into the source-mapped model (config-aliases 1).

Every test writes a real theatre and loads it with ``load_config``. Task 2.1: an anchor ``&name``
on any value node is accepted and recorded as ``Anchor(name, span of the property)``, and the
node's own span is its value without the property (research.md, Decision 3): the first key for a
block mapping, the first ``-`` for a block list, the next token for scalars and flow collections,
and empty at the property's end for an empty null. An anchor on a mapping key and explicit tags
stay refused. Until task 2.2 builds alias places, every alias is refused ("aliases are not
supported yet"), so that no alias silently loads as a plain copy.

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

import hashlib
from pathlib import Path

import pytest

from support import MakeTheatre
from troupe.config.errors import ConfigLoadError, ExitCode
from troupe.config.loader import load_config
from troupe.config.model import (
    Anchor,
    CollectionStyle,
    ConfigDocument,
    ConfigMapping,
    ConfigNode,
    ConfigScalar,
    ConfigSequence,
    IncludeKind,
    Span,
)


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


def _text(node: ConfigNode) -> str:
    return node.origin.file.text[node.origin.span.start : node.origin.span.end]


def _snapshot(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        if path.is_file()
        else "<dir>"
        for path in sorted(root.rglob("*"))
    }


# --- 1.1, 1.5: anchors on values are recorded, and value spans exclude the property ----------

# (case id, file text, key path of the anchored node, anchor span, the value span's text,
#  where the value span starts)
ANCHORED_VALUES: list[tuple[str, str, tuple[str | int, ...], Span, str, int]] = [
    ("scalar", "a: &x 1\n", ("a",), Span(3, 5), "1", 6),
    ("single-quoted", "a: &x 'q'\n", ("a",), Span(3, 5), "'q'", 6),
    ("double-quoted", 'a: &x "q"\n', ("a",), Span(3, 5), '"q"', 6),
    ("literal", "a: &x |\n  t\nb: 2\n", ("a",), Span(3, 5), "|\n  t\n", 6),
    ("folded", "a: &x >\n  t\n", ("a",), Span(3, 5), ">\n  t\n", 6),
    ("scalar-on-next-line", "a: &x\n  1\n", ("a",), Span(3, 5), "1", 8),
    ("scalar-after-comment", "a: &x  # c\n  1\n", ("a",), Span(3, 5), "1", 13),
    ("block-mapping", "a: &x\n  b: 1\n  c: 2\n", ("a",), Span(3, 5), "b: 1\n  c: 2\n", 8),
    ("block-mapping-after-comment", "a: &x  # c\n\n  b: 1\n", ("a",), Span(3, 5), "b: 1\n", 14),
    ("block-mapping-quoted-key", "a: &x\n  'b': 1\n", ("a",), Span(3, 5), "'b': 1\n", 8),
    ("block-list", "a: &x\n  - 1\n  - 2\n", ("a",), Span(3, 5), "- 1\n  - 2\n", 8),
    ("indentless-block-list", "a: &x\n- 1\n", ("a",), Span(3, 5), "- 1\n", 6),
    ("flow-mapping", "a: &x {b: 1}\n", ("a",), Span(3, 5), "{b: 1}", 6),
    ("flow-list", "a: &x [1, 2]\n", ("a",), Span(3, 5), "[1, 2]", 6),
    ("empty-flow-mapping", "a: &x {}\n", ("a",), Span(3, 5), "{}", 6),
    ("list-item", "a:\n  - 1\n  - &x 2\n", ("a", 1), Span(13, 15), "2", 16),
    ("list-item-mapping", "a:\n  - &x\n    b: 1\n", ("a", 0), Span(7, 9), "b: 1\n", 14),
    ("flow-list-item", "a: [&x 1, 2]\n", ("a", 0), Span(4, 6), "1", 7),
    ("flow-mapping-value", "a: {b: &x 1}\n", ("a", "b"), Span(7, 9), "1", 10),
    ("nested", "a:\n  b:\n    c: &deep x\n", ("a", "b", "c"), Span(15, 20), "x", 21),
    ("long-name", "a: &my-anchor.1 v\n", ("a",), Span(3, 15), "v", 16),
]


@pytest.mark.parametrize(
    ("text", "keys", "anchor_span", "value_text", "value_start"),
    [case[1:] for case in ANCHORED_VALUES],
    ids=[case[0] for case in ANCHORED_VALUES],
)
def test_anchor_on_a_value_is_recorded_and_the_value_span_excludes_the_property(
    make_theatre: MakeTheatre,
    text: str,
    keys: tuple[str | int, ...],
    anchor_span: Span,
    value_text: str,
    value_start: int,
) -> None:
    """Req 1.1, 1.5: an anchor on a scalar, mapping or list value (block or flow, at any depth,
    as a mapping value or a list item) loads; the node records ``Anchor(name, span)`` and its own
    span starts at the value, after the property."""
    document = load_config(_load(make_theatre, text))

    node = _node_at(document, *keys)

    name = text[anchor_span.start + 1 : anchor_span.end]
    assert node.anchor == Anchor(name, anchor_span)
    assert text[anchor_span.start : anchor_span.end] == f"&{name}"
    assert node.origin.span.start == value_start
    assert _text(node) == value_text
    assert node.alias is None


def test_anchored_values_load_with_the_values_written_at_the_anchor(
    make_theatre: MakeTheatre,
) -> None:
    """Req 1.1: anchored nodes keep their values and styles; only the anchor is added."""
    document = load_config(
        _load(make_theatre, "s: &s 1\nm: &m\n  k: v\nl: &l [1, two]\nf: &f {k: 'q'}\n")
    )

    scalar = _node_at(document, "s")
    mapping = _node_at(document, "m")
    sequence = _node_at(document, "l")
    flow = _node_at(document, "f")
    assert isinstance(scalar, ConfigScalar) and scalar.value == 1
    assert isinstance(mapping, ConfigMapping)
    assert mapping.style is CollectionStyle.BLOCK
    assert [(entry.key, _text(entry.value)) for entry in mapping.entries] == [("k", "v")]
    assert isinstance(sequence, ConfigSequence)
    assert sequence.style is CollectionStyle.FLOW
    assert [_text(item) for item in sequence.items] == ["1", "two"]
    assert isinstance(flow, ConfigMapping) and flow.style is CollectionStyle.FLOW
    assert [_text(entry.value) for entry in flow.entries] == ["'q'"]
    for node in (scalar, mapping, sequence, flow):
        assert node.anchor is not None


def test_anchored_empty_null_has_an_empty_span_at_the_end_of_the_property(
    make_theatre: MakeTheatre,
) -> None:
    """Req 1.1: an anchored empty null (``a: &x`` with nothing after it) is null, and its span is
    empty at the end of the property, not at the next key."""
    document = load_config(_load(make_theatre, "a: &x\nb: 2\n"))

    node = _node_at(document, "a")

    assert isinstance(node, ConfigScalar)
    assert node.value is None
    assert node.anchor == Anchor("x", Span(3, 5))
    assert node.origin.span == Span(5, 5)
    assert _text(_node_at(document, "b")) == "2"


def test_anchored_empty_null_in_a_flow_mapping_has_an_empty_span_after_the_property(
    make_theatre: MakeTheatre,
) -> None:
    """Req 1.1, 1.5: in flow style too, an anchored empty null's span is empty at the end of the
    property."""
    document = load_config(_load(make_theatre, "a: {k: &x , m: 1}\n"))

    node = _node_at(document, "a", "k")

    assert isinstance(node, ConfigScalar) and node.value is None
    assert node.anchor == Anchor("x", Span(7, 9))
    assert node.origin.span == Span(9, 9)


def test_anchor_on_the_root_mapping_is_recorded_and_its_span_starts_at_the_first_key(
    make_theatre: MakeTheatre,
) -> None:
    """Req 1.1: an anchor on the document's root mapping is accepted and recorded; the root's span
    starts at its first key."""
    document = load_config(_load(make_theatre, "&root\na: 1\nb: 2\n"))

    assert document.root.anchor == Anchor("root", Span(0, 5))
    assert document.root.origin.span.start == 6
    assert [entry.key for entry in document.root.entries] == ["a", "b"]


def test_nodes_without_anchors_record_none(make_theatre: MakeTheatre) -> None:
    """Req 1.1: only the anchored node records an anchor; its children and siblings do not."""
    document = load_config(_load(make_theatre, "a: &x\n  b: 1\nc: 2\n"))

    assert _node_at(document, "a").anchor is not None
    assert _node_at(document, "a", "b").anchor is None
    assert _node_at(document, "c").anchor is None
    assert document.root.anchor is None
    assert all(entry.value.alias is None for entry in document.root.entries)


def test_anchor_in_an_included_file_is_recorded_in_that_file(make_theatre: MakeTheatre) -> None:
    """Req 1.5: anchors load in included files; the anchor and value spans are in that file."""
    theatre = make_theatre(
        "inc: file:sub/inc.yml\n", files={".troupe/sub/inc.yml": "a: &x\n  b: 1\n"}
    )
    document = load_config(_config(theatre))

    node = _node_at(document, "inc", "a")

    assert node.anchor == Anchor("x", Span(3, 5))
    assert node.origin.file.path == theatre / ".troupe" / "sub/inc.yml"
    assert _text(node) == "b: 1\n"


def test_anchor_on_an_include_string_is_recorded_on_the_included_node(
    make_theatre: MakeTheatre,
) -> None:
    """Req 1.5: an anchor on an include string loads; the included node records the anchor and
    the include's directive span excludes the property."""
    theatre = make_theatre(
        "a: &i file:inc.yml\nt: &t file:note.txt\n",
        files={".troupe/inc.yml": "k: v\n", ".troupe/note.txt": "hello\n"},
    )
    document = load_config(_config(theatre))
    text = document.root_file.text

    included = _node_at(document, "a")
    note = _node_at(document, "t")

    assert isinstance(included, ConfigMapping)
    assert included.anchor == Anchor("i", Span(3, 5))
    assert included.include is not None
    directive = included.include.directive.span
    assert text[directive.start : directive.end] == "file:inc.yml"
    assert isinstance(note, ConfigScalar) and note.value == "hello\n"
    assert note.include is not None and note.include.kind is IncludeKind.TEXT
    assert note.anchor == Anchor("t", Span(22, 24))
    directive = note.include.directive.span
    assert text[directive.start : directive.end] == "file:note.txt"


def test_anchor_on_a_mapping_form_include_is_recorded_on_the_included_node(
    make_theatre: MakeTheatre,
) -> None:
    """Req 1.5: an anchor on a ``$ref`` mapping loads, bare or with overrides; the included node
    records the anchor, and the ``$ref`` mapping's span excludes the property."""
    theatre = make_theatre(
        "bare: &b\n  $ref: file:inc.yml\nover: &o\n  $ref: file:inc.yml\n  k: w\n",
        files={".troupe/inc.yml": "k: v\nj: 1\n"},
    )
    document = load_config(_config(theatre))

    bare = _node_at(document, "bare")
    over = _node_at(document, "over")

    assert bare.anchor == Anchor("b", Span(6, 8))
    assert over.anchor == Anchor("o", Span(36, 38))
    assert isinstance(over, ConfigMapping)
    assert [(entry.key, _text(entry.value)) for entry in over.entries] == [("k", "w"), ("j", "1")]
    for node in (bare, over):
        assert node.include is not None and node.include.form is not None
        assert _text(node.include.form).startswith("$ref: file:inc.yml")


# --- 1.4: an anchor on a mapping key stays refused; tags stay refused ------------------------

KEY_ANCHORS: list[tuple[str, str, str]] = [
    ("nested-key", "a:\n  &k b: 1\n", "a.b"),
    ("root-key", "&k a: 1\n", "a"),
    ("flow-key", "a: {&k b: 1}\n", "a.b"),
    ("key-in-anchored-mapping", "a: &x\n  &k b: 1\n", "a.b"),
]


@pytest.mark.parametrize(
    ("text", "key_path"),
    [case[1:] for case in KEY_ANCHORS],
    ids=[case[0] for case in KEY_ANCHORS],
)
def test_anchor_on_a_mapping_key_is_a_load_error_naming_the_file_and_key_path(
    make_theatre: MakeTheatre, text: str, key_path: str
) -> None:
    """Req 1.4: an anchor written on a mapping key is refused (exit 4), naming the file and the
    key path."""
    config = _load(make_theatre, text)

    with pytest.raises(ConfigLoadError) as info:
        load_config(config)

    error = info.value
    assert error.exit_code is ExitCode.LOAD
    assert (error.file, error.path) == (config, key_path)
    assert error.problem == "anchor on a mapping key is not supported"


TAGGED_ANCHORS: list[tuple[str, str, str | None, str]] = [
    ("core-tag-before-anchor", "a: !!str &x 5\n", "a", "'!!str'"),
    ("core-tag-after-anchor", "a: &x !!str 5\n", "a", "'!!str'"),
    ("custom-tag-before-anchor", "a: !custom &x 5\n", "a", "'!custom'"),
    ("non-specific-tag-before-anchor", "a: ! &x 5\n", "a", "'!'"),
    ("non-specific-tag-after-anchor", "a: &x ! 5\n", "a", "'!'"),
    ("verbatim-tag-before-anchor", "a: !<tag:yaml.org,2002:str> &x 5\n", "a", "'!<"),
    ("verbatim-tag-after-anchor", "a: &x !<tag:yaml.org,2002:str> 5\n", "a", "'!<"),
    ("tag-on-anchored-mapping", "a: &x !!map\n  b: 1\n", "a", "'!!map'"),
    ("tag-on-anchored-list-item", "a:\n  - ! &x 5\n", "a.0", "'!'"),
    ("tag-on-anchored-root", "! &x\na: 1\n", None, "'!'"),
]


@pytest.mark.parametrize(
    ("text", "key_path", "tag"),
    [case[1:] for case in TAGGED_ANCHORS],
    ids=[case[0] for case in TAGGED_ANCHORS],
)
def test_explicit_tag_next_to_an_anchor_is_still_refused(
    make_theatre: MakeTheatre, text: str, key_path: str | None, tag: str
) -> None:
    """Req 1.1: accepting anchors does not let explicit tags through, whichever property comes
    first (config-foundation 3.7 stays in force)."""
    config = _load(make_theatre, text)

    with pytest.raises(ConfigLoadError) as info:
        load_config(config)

    error = info.value
    assert error.exit_code is ExitCode.LOAD
    assert (error.file, error.path) == (config, key_path)
    assert error.problem.startswith(f"explicit tag {tag}")


# --- interim (until task 2.2): every alias is refused -----------------------------------------

INTERIM_ALIASES: list[tuple[str, str, str | None]] = [
    ("mapping-value", "a: &x 1\nb: *x\n", "b"),
    ("list-item", "a: &x 1\nl:\n  - 0\n  - *x\n", "l.1"),
    ("flow-item", "a: &x 1\nl: [*x]\n", "l.0"),
    ("nested-value", "d: &d\n  m: 1\nc:\n  s: *d\n", "c.s"),
    ("inside-anchored-node", "a: &a 1\nd: &d\n  m: *a\n", "d.m"),
    ("ref-value", "i: &i file:inc.yml\nr:\n  $ref: *i\n", "r.$ref"),
    ("mapping-key", "a: &x k\nm:\n  *x : 1\n", "m"),
]


@pytest.mark.parametrize(
    ("text", "key_path"),
    [case[1:] for case in INTERIM_ALIASES],
    ids=[case[0] for case in INTERIM_ALIASES],
)
def test_alias_is_a_load_error_until_alias_places_are_built(
    make_theatre: MakeTheatre, text: str, key_path: str | None
) -> None:
    """Req 1.1, 1.6: until alias places are built (task 2.2), an alias is refused (exit 4) naming
    the file and its key path, so it never loads as a plain copy of its anchored node."""
    config = _load(make_theatre, text, files={".troupe/inc.yml": "k: v\n"})
    before = _snapshot(config.parent.parent)

    with pytest.raises(ConfigLoadError) as info:
        load_config(config)

    error = info.value
    assert error.exit_code is ExitCode.LOAD
    assert (error.file, error.path) == (config, key_path)
    name = text.split("*", 1)[1].split()[0].rstrip("]")
    assert error.problem.startswith(f"alias '*{name}'")
    assert error.problem.endswith("aliases are not supported yet")
    assert _snapshot(config.parent.parent) == before


def test_alias_in_an_included_file_names_the_included_file(make_theatre: MakeTheatre) -> None:
    """Req 1.5: the interim alias refusal names the included file that holds the alias."""
    theatre = make_theatre(
        "inc: file:sub/inc.yml\n", files={".troupe/sub/inc.yml": "a: &x 1\nb: *x\n"}
    )

    with pytest.raises(ConfigLoadError) as info:
        load_config(_config(theatre))

    assert info.value.file == theatre / ".troupe" / "sub/inc.yml"
    assert info.value.path == "inc.b"
    assert "aliases are not supported yet" in info.value.problem


# --- 1.6: loading changes no file -------------------------------------------------------------


def test_loading_anchors_changes_no_file(make_theatre: MakeTheatre) -> None:
    """Req 1.6: loading files with anchors (root and included) creates, changes, renames and
    deletes nothing."""
    theatre = make_theatre(
        "&r\na: &x 1\nm: &m\n  k: v\ninc: &i file:sub/inc.yml\n",
        files={".troupe/sub/inc.yml": "l: &l\n  - 1\n"},
    )
    before = _snapshot(theatre)

    document = load_config(_config(theatre))

    assert _node_at(document, "inc", "l").anchor is not None
    assert _snapshot(theatre) == before
