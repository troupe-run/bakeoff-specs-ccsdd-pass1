"""Loading anchors and aliases into the source-mapped model (config-aliases 1).

Every test writes a real theatre and loads it with ``load_config``. Task 2.1: an anchor ``&name``
on any value node is accepted and recorded as ``Anchor(name, span of the property)``, and the
node's own span is its value without the property (research.md, Decision 3): the first key for a
block mapping, the first ``-`` for a block list, the next token for scalars and flow collections,
and empty at the property's end for an empty null. An anchor on a mapping key and explicit tags
other than ``&name !literal`` on a scalar (config-remote-schema 4.1, 4.2) stay refused.

Task 2.2: at each alias ``*name`` the anchored node is built again for the alias's path, an alias
place (research.md, Decision 2). It and its descendants carry the anchored node's origins, and its
root records ``AliasRef(name, the *name token, the node built at the anchor)``. An alias used as a
key, an alias inside its own anchored node and an undefined alias are refused.

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

import dataclasses
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
    Origin,
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


def _text_at(text: str, span: Span) -> str:
    return text[span.start : span.end]


def _scalar(node: ConfigNode) -> object:
    assert isinstance(node, ConfigScalar)
    return node.value


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
    ("literal-tag-before-anchor", "a: !literal &x 5\n", "a", "'!literal'"),
    ("literal-tag-on-anchored-mapping", "a: &x !literal\n  b: 1\n", "a", "'!literal'"),
    ("literal-tag-on-anchored-list", "a: &x !literal [1]\n", "a", "'!literal'"),
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
    first (config-foundation 3.7 stays in force). Only ``&x !literal`` on a scalar loads
    (config-remote-schema 4.1, tested in test_loader_literals.py); ``!literal`` before the anchor
    or on a collection is refused (config-remote-schema 4.2)."""
    config = _load(make_theatre, text)

    with pytest.raises(ConfigLoadError) as info:
        load_config(config)

    error = info.value
    assert error.exit_code is ExitCode.LOAD
    assert (error.file, error.path) == (config, key_path)
    assert error.problem.startswith(f"explicit tag {tag}")


# --- 1.1, 1.5: alias places are the anchored node built again for their path (task 2.2) -------

# (case id, file text, key path of the anchored node, key path of the alias place, token span)
ALIAS_PLACES: list[tuple[str, str, tuple[str | int, ...], tuple[str | int, ...], Span]] = [
    ("mapping-value", "a: &x 1\nb: *x\n", ("a",), ("b",), Span(11, 13)),
    ("list-item", "a: &x 1\nl:\n  - 0\n  - *x\n", ("a",), ("l", 1), Span(21, 23)),
    ("flow-item", "a: &x 1\nl: [*x]\n", ("a",), ("l", 0), Span(12, 14)),
    ("flow-mapping-value", "a: &x [1, 2]\nf: {k: *x}\n", ("a",), ("f", "k"), Span(20, 22)),
    ("block-mapping", "d: &d\n  m: 1\nc:\n  s: *d\n", ("d",), ("c", "s"), Span(21, 23)),
    ("block-list", "l: &l\n  - 1\n  - 2\nm: *l\n", ("l",), ("m",), Span(21, 23)),
    ("flow-mapping-as-item", "f: &f {k: v}\nl:\n  - *f\n", ("f",), ("l", 0), Span(20, 22)),
    ("empty-null", "a: &x\nb: *x\n", ("a",), ("b",), Span(9, 11)),
    ("literal", "a: &x |\n  t\nb: *x\n", ("a",), ("b",), Span(15, 17)),
    (
        "deep",
        "a:\n  b:\n    c: &deep v\nx:\n  y:\n    - z: *deep\n",
        ("a", "b", "c"),
        ("x", "y", 0, "z"),
        Span(40, 45),
    ),
]


@pytest.mark.parametrize(
    ("text", "anchor_keys", "alias_keys", "token"),
    [case[1:] for case in ALIAS_PLACES],
    ids=[case[0] for case in ALIAS_PLACES],
)
def test_alias_place_is_the_anchored_node_built_again_with_its_origins(
    make_theatre: MakeTheatre,
    text: str,
    anchor_keys: tuple[str | int, ...],
    alias_keys: tuple[str | int, ...],
    token: Span,
) -> None:
    """Req 1.1, 1.5: at an alias (mapping value, list item, block or flow, at any depth) the
    configuration holds the anchored node's value. The alias place and its descendants carry the
    anchored node's origins, and its root records ``AliasRef(name, *name token, anchored node)``."""
    document = load_config(_load(make_theatre, text))

    anchored = _node_at(document, *anchor_keys)
    place = _node_at(document, *alias_keys)

    assert place.alias is not None
    name = text[token.start + 1 : token.end]
    assert place.alias.name == name
    assert place.alias.token == Origin(document.root_file, token)
    assert text[token.start : token.end] == f"*{name}"
    assert place.alias.anchored is anchored
    assert anchored.alias is None
    assert dataclasses.replace(place, alias=None) == anchored
    assert place is not anchored


def test_values_inside_an_alias_place_carry_the_anchored_origins(
    make_theatre: MakeTheatre,
) -> None:
    """Req 1.1: the leaves of an alias place hold the anchored values, and their origins are the
    spans written inside the anchored node; only the alias place's root records the alias."""
    text = "defaults: &d\n  model: opus\n  tools: [a, b]\ncoder:\n  settings: *d\n"
    document = load_config(_load(make_theatre, text))

    model = _node_at(document, "coder", "settings", "model")
    tool = _node_at(document, "coder", "settings", "tools", 1)

    assert isinstance(model, ConfigScalar) and model.value == "opus"
    assert model.origin == _node_at(document, "defaults", "model").origin
    assert _text(model) == "opus"
    assert isinstance(tool, ConfigScalar) and tool.value == "b"
    assert tool.origin == _node_at(document, "defaults", "tools", 1).origin
    assert model.alias is None and tool.alias is None
    assert _node_at(document, "coder", "settings").alias is not None


def test_every_alias_of_one_anchor_is_its_own_alias_place(make_theatre: MakeTheatre) -> None:
    """Req 1.1: each alias of an anchor is an alias place with its own token."""
    text = "defaults: &d\n  model: opus\ncoder: *d\nreviewer: *d\n"
    document = load_config(_load(make_theatre, text))

    anchored = _node_at(document, "defaults")
    coder = _node_at(document, "coder")
    reviewer = _node_at(document, "reviewer")

    assert coder.alias is not None and reviewer.alias is not None
    assert coder.alias.anchored is anchored and reviewer.alias.anchored is anchored
    assert _text_at(text, coder.alias.token.span) == "*d"
    assert coder.alias.token.span.start == text.index("*d")
    assert reviewer.alias.token.span.start == text.rindex("*d")


def test_aliases_inside_an_anchored_node_are_alias_places_again_at_each_alias(
    make_theatre: MakeTheatre,
) -> None:
    """Req 1.1, 1.5: an alias inside an anchored node is an alias place at the anchor and again
    inside every alias place of that node, with the token written inside the anchored node."""
    text = "a: &a 1\nd: &d\n  m: *a\n  n: 2\ne: *d\n"
    document = load_config(_load(make_theatre, text))

    inner_at_anchor = _node_at(document, "d", "m")
    inner_at_place = _node_at(document, "e", "m")

    assert inner_at_anchor.alias is not None and inner_at_place.alias is not None
    assert inner_at_anchor.alias.token.span == Span(19, 21)
    assert inner_at_place.alias == inner_at_anchor.alias
    assert inner_at_place.alias.anchored is _node_at(document, "a")
    assert isinstance(inner_at_place, ConfigScalar) and inner_at_place.value == 1
    outer = _node_at(document, "e")
    assert outer.alias is not None and outer.alias.name == "d"
    assert _node_at(document, "e", "n").origin == _node_at(document, "d", "n").origin


def test_reused_anchor_name_refers_to_the_most_recent_definition(
    make_theatre: MakeTheatre,
) -> None:
    """Req 1.2: when an anchor name is defined twice, each alias uses the definition most recently
    written before it, including a redefinition inside another anchored node."""
    text = "a: &x 1\nb: *x\nc: &x 2\nd: *x\ne: &y {x: &x 3}\nf: *x\ng: *y\nh: *x\n"
    document = load_config(_load(make_theatre, text))

    values = {key: _node_at(document, key) for key in ("b", "d", "f", "h")}

    assert values["b"].alias is not None and values["b"].alias.anchored is _node_at(document, "a")
    assert values["d"].alias is not None and values["d"].alias.anchored is _node_at(document, "c")
    assert values["f"].alias is not None
    assert values["f"].alias.anchored is _node_at(document, "e", "x")
    assert values["h"].alias is not None
    assert values["h"].alias.anchored is _node_at(document, "e", "x")
    assert [_scalar(node) for node in values.values()] == [1, 2, 3, 3]
    assert _scalar(_node_at(document, "g", "x")) == 3


def test_alias_in_an_included_file_is_built_in_that_file(make_theatre: MakeTheatre) -> None:
    """Req 1.5: aliases load in included files; the token and the origins are in that file."""
    theatre = make_theatre(
        "inc: file:sub/inc.yml\n", files={".troupe/sub/inc.yml": "a: &x\n  k: 1\nb: *x\n"}
    )
    document = load_config(_config(theatre))
    included = theatre / ".troupe" / "sub/inc.yml"

    place = _node_at(document, "inc", "b")

    assert place.alias is not None
    assert place.alias.token.file.path == included
    assert place.alias.token.span == Span(16, 18)
    assert place.origin.file.path == included
    assert _scalar(_node_at(document, "inc", "b", "k")) == 1


def test_alias_of_an_include_string_has_the_included_value(make_theatre: MakeTheatre) -> None:
    """Req 1.5: an alias of an include string (YAML or text) has the include's value, and the
    alias place is the include built again (its include and anchor as at the anchor)."""
    theatre = make_theatre(
        "a: &i file:inc.yml\nb: *i\nt: &t file:note.txt\nu: [*t]\n",
        files={".troupe/inc.yml": "k: v\n", ".troupe/note.txt": "hello\n"},
    )
    document = load_config(_config(theatre))

    yaml_place = _node_at(document, "b")
    text_place = _node_at(document, "u", 0)

    assert isinstance(yaml_place, ConfigMapping)
    assert yaml_place.alias is not None and yaml_place.alias.anchored is _node_at(document, "a")
    assert dataclasses.replace(yaml_place, alias=None) == _node_at(document, "a")
    assert _scalar(_node_at(document, "b", "k")) == "v"
    assert yaml_place.include is not None and yaml_place.include.kind is IncludeKind.YAML
    assert isinstance(text_place, ConfigScalar) and text_place.value == "hello\n"
    assert text_place.include is not None and text_place.include.kind is IncludeKind.TEXT
    assert text_place.alias is not None and text_place.alias.name == "t"


def test_alias_of_a_mapping_form_include_has_the_include_value(make_theatre: MakeTheatre) -> None:
    """Req 1.5: an alias of a ``$ref`` mapping, bare or with overrides, has the include's value,
    with its ``$ref`` mapping kept as written at the anchor."""
    theatre = make_theatre(
        "bare: &b\n  $ref: file:inc.yml\nover: &o\n  $ref: file:inc.yml\n  k: w\nx: *b\ny: *o\n",
        files={".troupe/inc.yml": "k: v\nj: 1\n"},
    )
    document = load_config(_config(theatre))

    for anchor_key, alias_key in (("bare", "x"), ("over", "y")):
        anchored = _node_at(document, anchor_key)
        place = _node_at(document, alias_key)
        assert place.alias is not None and place.alias.anchored is anchored
        assert dataclasses.replace(place, alias=None) == anchored
        assert place.include is not None and place.include.form is not None
    assert [_scalar(_node_at(document, "y", key)) for key in ("k", "j")] == ["w", 1]


def test_alias_as_a_ref_value_loads_the_include(make_theatre: MakeTheatre) -> None:
    """Req 1.5: an alias of an include string used as a ``$ref`` value loads that include."""
    theatre = make_theatre(
        "i: &i file:inc.yml\nr:\n  $ref: *i\n  j: 2\n", files={".troupe/inc.yml": "k: v\n"}
    )
    document = load_config(_config(theatre))

    place = _node_at(document, "r")

    assert isinstance(place, ConfigMapping)
    assert [(entry.key, _scalar(entry.value)) for entry in place.entries] == [("k", "v"), ("j", 2)]
    assert place.include is not None and place.include.form is not None
    ref = place.include.form.get("$ref")
    assert ref is not None and ref.value.alias is not None and ref.value.alias.name == "i"


def test_anchored_node_with_an_include_includes_it_again_at_each_alias(
    make_theatre: MakeTheatre,
) -> None:
    """Req 1.5: an include inside an anchored node is included again at each alias place, like
    any include place; the included file is read once."""
    theatre = make_theatre(
        "d: &d\n  sub: file:inc.yml\n  n: 1\ne: *d\nl:\n  - *d\n",
        files={".troupe/inc.yml": "k: v\n"},
    )
    document = load_config(_config(theatre))

    at_anchor = _node_at(document, "d", "sub")
    at_places = [_node_at(document, "e", "sub"), _node_at(document, "l", 0, "sub")]

    for node in at_places:
        assert isinstance(node, ConfigMapping) and node.include is not None
        assert node == at_anchor and node is not at_anchor
        assert _scalar(node.entries[0].value) == "v"
    assert [file.path.name for file in document.files] == ["troupe.yml", "inc.yml"]


def test_ref_overrides_keep_the_included_anchor_and_alias(make_theatre: MakeTheatre) -> None:
    """Req 1.1, 1.5: in the merged mapping of a ``$ref`` include with overrides, a nested mapping
    merged with an override keeps the included side's anchor and alias."""
    theatre = make_theatre(
        "x:\n  $ref: file:inc.yml\n  base:\n    m: 3\n  c:\n    n: 4\n",
        files={".troupe/inc.yml": "base: &b\n  m: 1\n  n: 2\nc: *b\n"},
    )
    document = load_config(_config(theatre))
    included = theatre / ".troupe" / "inc.yml"

    base = _node_at(document, "x", "base")
    place = _node_at(document, "x", "c")

    assert isinstance(base, ConfigMapping) and base.layers is not None
    assert base.anchor == Anchor("b", Span(6, 8))
    assert [(entry.key, _scalar(entry.value)) for entry in base.entries] == [("m", 3), ("n", 2)]
    assert isinstance(place, ConfigMapping) and place.layers is not None
    assert place.alias is not None and place.alias.name == "b"
    assert place.alias.token.file.path == included
    assert place.alias.anchored is base.layers.base
    assert [(entry.key, _scalar(entry.value)) for entry in place.entries] == [("m", 1), ("n", 4)]


# --- 1.3, 1.4: refused aliases -----------------------------------------------------------------

# (case id, file text, key path, problem)
REFUSED_ALIASES: list[tuple[str, str, str | None, str]] = [
    ("key", "a: &x k\nm:\n  *x : 1\n", "m.k", "alias '*x' used as a mapping key is not supported"),
    ("root-key", "a: &x k\n*x : 1\n", "k", "alias '*x' used as a mapping key is not supported"),
    (
        "flow-key",
        "a: &x k\nm: {*x : 1}\n",
        "m.k",
        "alias '*x' used as a mapping key is not supported",
    ),
    (
        "key-of-a-list",
        "a: &x [1]\nm:\n  *x : 1\n",
        "m",
        "alias '*x' used as a mapping key is not supported",
    ),
    ("own-mapping", "d: &d\n  m: *d\n", "d.m", "alias '*d' refers to its own anchored node"),
    ("own-list", "l: &l\n  - *l\n", "l.0", "alias '*l' refers to its own anchored node"),
    (
        "own-deep",
        "d: &d\n  a:\n    b: [*d]\n",
        "d.a.b.0",
        "alias '*d' refers to its own anchored node",
    ),
    ("own-root", "&r\na: *r\n", "a", "alias '*r' refers to its own anchored node"),
    (
        "own-at-alias-place",
        "a: &a {b: 1}\nd: &d\n  e: *a\n  f: [*d]\n",
        "d.f.0",
        "alias '*d' refers to its own anchored node",
    ),
]


@pytest.mark.parametrize(
    ("text", "key_path", "problem"),
    [case[1:] for case in REFUSED_ALIASES],
    ids=[case[0] for case in REFUSED_ALIASES],
)
def test_alias_as_a_key_or_inside_its_own_anchored_node_is_a_load_error(
    make_theatre: MakeTheatre, text: str, key_path: str | None, problem: str
) -> None:
    """Req 1.4, 1.6: an alias used as a mapping key, or written inside its own anchored node, is
    refused (exit 4) naming the file and the key path, and no file is changed."""
    config = _load(make_theatre, text)
    before = _snapshot(config.parent.parent)

    with pytest.raises(ConfigLoadError) as info:
        load_config(config)

    error = info.value
    assert error.exit_code is ExitCode.LOAD
    assert (error.file, error.path) == (config, key_path)
    assert error.problem == problem
    assert _snapshot(config.parent.parent) == before


def test_recursive_alias_in_an_included_file_names_that_file(make_theatre: MakeTheatre) -> None:
    """Req 1.4, 1.5: a recursive alias in an included file names the included file."""
    theatre = make_theatre("inc: file:inc.yml\n", files={".troupe/inc.yml": "d: &d\n  m: *d\n"})

    with pytest.raises(ConfigLoadError) as info:
        load_config(_config(theatre))

    assert (info.value.file, info.value.path) == (theatre / ".troupe" / "inc.yml", "inc.d.m")
    assert info.value.problem == "alias '*d' refers to its own anchored node"


# (case id, root file, included files, file named, alias name)
UNDEFINED_ALIASES: list[tuple[str, str, dict[str, str], str, str]] = [
    ("no-anchor", "a: *x\n", {}, "troupe.yml", "x"),
    ("anchor-after-the-alias", "a: *x\nb: &x 1\n", {}, "troupe.yml", "x"),
    (
        "anchor-only-in-the-included-file",
        "inc: file:inc.yml\nb: *x\n",
        {".troupe/inc.yml": "a: &x 1\n"},
        "troupe.yml",
        "x",
    ),
    (
        "anchor-only-in-the-including-file",
        "a: &x 1\ninc: file:inc.yml\n",
        {".troupe/inc.yml": "b: *x\n"},
        "inc.yml",
        "x",
    ),
]


@pytest.mark.parametrize(
    ("text", "files", "named", "name"),
    [case[1:] for case in UNDEFINED_ALIASES],
    ids=[case[0] for case in UNDEFINED_ALIASES],
)
def test_undefined_alias_is_a_load_error_naming_the_file_and_the_alias(
    make_theatre: MakeTheatre, text: str, files: dict[str, str], named: str, name: str
) -> None:
    """Req 1.3: an alias with no anchor of that name before it in the same file (aliases are
    file-local) is refused (exit 4) naming the file and the alias."""
    theatre = make_theatre(text, files=files)
    before = _snapshot(theatre)

    with pytest.raises(ConfigLoadError) as info:
        load_config(_config(theatre))

    assert info.value.exit_code is ExitCode.LOAD
    assert info.value.file == theatre / ".troupe" / named
    assert f"undefined alias '{name}'" in info.value.problem
    assert _snapshot(theatre) == before


# --- 1.6: loading changes no file -------------------------------------------------------------


def test_loading_anchors_changes_no_file(make_theatre: MakeTheatre) -> None:
    """Req 1.6: loading files with anchors and aliases (root and included) creates, changes,
    renames and deletes nothing."""
    theatre = make_theatre(
        "&r\na: &x 1\nm: &m\n  k: v\ninc: &i file:sub/inc.yml\nb: *x\nc: *i\n",
        files={".troupe/sub/inc.yml": "l: &l\n  - 1\nn: *l\n"},
    )
    before = _snapshot(theatre)

    document = load_config(_config(theatre))

    assert _node_at(document, "inc", "l").anchor is not None
    assert _node_at(document, "c", "n").alias is not None
    assert _snapshot(theatre) == before
