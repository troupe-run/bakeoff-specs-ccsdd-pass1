"""Loading merge keys into the merged view (config-aliases 2, task 2.3).

Every test writes a real theatre and loads it with ``load_config``. A plain ``<<`` key whose value
is a mapping, an alias of a mapping or a list of those is merged into its mapping (research.md,
Decision 4): the loaded mapping is the merged view (its own entries in written order, and at the
place of the ``<<`` entry the merged keys it does not write itself, earlier sources first, marked
``via_merge``), and ``merge.written`` keeps the mapping as written, with its ``<<`` entry. Any
other value, and a ``<<`` key among the overrides of a mapping-form include, are load errors
naming the file and the key path of the merge key. A quoted ``<<`` stays an ordinary key.

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

import hashlib
from collections.abc import Iterator
from pathlib import Path

import pytest

from support import MakeTheatre
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
from troupe.config.paths import ROOT, ConfigPath


def _config(theatre: Path) -> Path:
    return theatre / ".troupe" / "troupe.yml"


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


def _mapping_at(document: ConfigDocument, *keys: str | int) -> ConfigMapping:
    node = _node_at(document, *keys)
    assert isinstance(node, ConfigMapping)
    return node


def _plain(node: ConfigNode) -> object:
    """The plain value of ``node`` (mappings as lists of pairs, to keep the order)."""
    if isinstance(node, ConfigScalar):
        return node.value
    if isinstance(node, ConfigSequence):
        return [_plain(item) for item in node.items]
    return [(entry.key, _plain(entry.value)) for entry in node.entries]


def _marks(mapping: ConfigMapping) -> list[tuple[str, bool]]:
    return [(entry.key, entry.via_merge) for entry in mapping.entries]


def _text(node: ConfigNode) -> str:
    return node.origin.file.text[node.origin.span.start : node.origin.span.end]


def _paths(node: ConfigNode, path: ConfigPath = ROOT) -> Iterator[ConfigPath]:
    yield path
    if isinstance(node, ConfigMapping):
        for entry in node.entries:
            yield from _paths(entry.value, path.child(entry.key))
    elif isinstance(node, ConfigSequence):
        for index, item in enumerate(node.items):
            yield from _paths(item, path.child(index))


def _snapshot(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        if path.is_file()
        else "<dir>"
        for path in sorted(root.rglob("*"))
    }


# --- 2.1, 2.2: merge semantics and order -------------------------------------------------------

# (case id, file text, key path of the mapping with the merge key, its merged view, via_merge marks)
MERGES: list[tuple[str, str, tuple[str, ...], object, list[tuple[str, bool]]]] = [
    ("inline-at-root", "<<: {a: 1}\n", (), [("a", 1)], [("a", True)]),
    (
        "nested-inline",
        "a:\n  b: 1\n  <<: {c: 2}\n",
        ("a",),
        [("b", 1), ("c", 2)],
        [("b", False), ("c", True)],
    ),
    (
        "alias-with-own-keys",
        "d: &d {model: opus, tools: x, retries: 1}\ncoder:\n  name: c\n  <<: *d\n  model: sonnet\n",
        ("coder",),
        [("name", "c"), ("tools", "x"), ("retries", 1), ("model", "sonnet")],
        [("name", False), ("tools", True), ("retries", True), ("model", False)],
    ),
    (
        "list-earlier-first",
        "a: &a {x: 1, y: 1}\nb: &b {y: 2, z: 2}\nm:\n  <<: [*a, *b]\n  own: 0\n",
        ("m",),
        [("x", 1), ("y", 1), ("z", 2), ("own", 0)],
        [("x", True), ("y", True), ("z", True), ("own", False)],
    ),
    (
        "list-of-inline-and-alias",
        "a: &a {p: 2, q: 2}\nm:\n  <<: [{p: 1}, *a]\n",
        ("m",),
        [("p", 1), ("q", 2)],
        [("p", True), ("q", True)],
    ),
    (
        "block-list-of-sources",
        "a: &a {p: 1}\nm:\n  first: 0\n  <<:\n    - *a\n    - {r: 3}\n",
        ("m",),
        [("first", 0), ("p", 1), ("r", 3)],
        [("first", False), ("p", True), ("r", True)],
    ),
    (
        "empty-list",
        "m:\n  a: 1\n  <<: []\n",
        ("m",),
        [("a", 1)],
        [("a", False)],
    ),
    (
        "flow-mapping",
        "d: &d {k: 1, own: 9}\nm: {own: 2, <<: *d}\n",
        ("m",),
        [("own", 2), ("k", 1)],
        [("own", False), ("k", True)],
    ),
    (
        "nested-merges",
        "base: &base {k: 1}\nmid: &mid\n  <<: *base\n  m: 2\nleaf:\n  <<: *mid\n  m: 3\n",
        ("leaf",),
        [("k", 1), ("m", 3)],
        [("k", True), ("m", False)],
    ),
    (
        "anchored-mapping-with-merge",
        "base: &base {k: 1}\nmid: &mid\n  <<: *base\n  m: 2\n",
        ("mid",),
        [("k", 1), ("m", 2)],
        [("k", True), ("m", False)],
    ),
    (
        "nested-value-is-not-deep-merged",
        "d: &d {s: {a: 1, b: 1}}\nm:\n  <<: *d\n  s: {a: 2}\n",
        ("m",),
        [("s", [("a", 2)])],
        [("s", False)],
    ),
]


@pytest.mark.parametrize(
    ("text", "keys", "merged", "marks"),
    [case[1:] for case in MERGES],
    ids=[case[0] for case in MERGES],
)
def test_merge_key_is_merged_into_its_mapping(
    make_theatre: MakeTheatre,
    text: str,
    keys: tuple[str, ...],
    merged: object,
    marks: list[tuple[str, bool]],
) -> None:
    """Req 2.1, 2.2, 2.4: the mapping's value is its own keys plus every merged key it does not
    write itself (earlier sources first), placed where ``<<`` is written; merged entries are
    marked ``via_merge`` and no ``<<`` key is loaded."""
    document = load_config(_load(make_theatre, text))

    mapping = _mapping_at(document, *keys)

    assert _plain(mapping) == merged
    assert _marks(mapping) == marks
    assert mapping.merge is not None
    assert mapping.get("<<") is None


def test_merged_view_keeps_the_mapping_as_written(make_theatre: MakeTheatre) -> None:
    """Req 2.1, 2.2: ``merge.written`` is the mapping as written, with its plain ``<<`` entry in
    written order and its value built as usual (an alias place here); ``merge.key_origin`` is the
    ``<<`` key token. The merged view keeps the written mapping's style and origin."""
    text = "d: &d {k: 1}\nm:\n  own: 0\n  <<: *d\n  last: 2\n"
    config = _load(make_theatre, text)
    document = load_config(config)

    mapping = _mapping_at(document, "m")

    assert mapping.merge is not None
    written = mapping.merge.written
    assert [(entry.key, entry.key_style) for entry in written.entries] == [
        ("own", ScalarStyle.PLAIN),
        ("<<", ScalarStyle.PLAIN),
        ("last", ScalarStyle.PLAIN),
    ]
    assert not any(entry.via_merge for entry in written.entries)
    source = written.entries[1].value
    assert isinstance(source, ConfigMapping)
    assert source.alias is not None and source.alias.name == "d"
    assert mapping.merge.key_origin.file.path == config
    assert mapping.merge.key_origin.span == Span(text.index("<<"), text.index("<<") + 2)
    assert (mapping.style, mapping.origin) == (written.style, written.origin)
    assert mapping.merge.written.merge is None


def test_merged_entries_are_the_source_entries_with_their_origins(
    make_theatre: MakeTheatre,
) -> None:
    """Req 2.1: a merged entry carries the source's key and value as written at the anchored
    node (its origins point there), only marked ``via_merge``."""
    text = "d: &d\n  model: opus\nm:\n  <<: *d\n"
    document = load_config(_load(make_theatre, text))

    entry = _mapping_at(document, "m").get("model")
    at_anchor = _mapping_at(document, "d").get("model")

    assert entry is not None and at_anchor is not None
    assert entry.via_merge and not at_anchor.via_merge
    assert entry.key_origin == at_anchor.key_origin
    assert entry.value == at_anchor.value
    assert _text(entry.value) == "opus"


def test_merge_source_whose_nested_values_hold_includes_is_merged(
    make_theatre: MakeTheatre,
) -> None:
    """Req 2.1: a merge source that is a plain mapping (or an alias of one) is merged even when
    its nested values are includes; the merged entries hold the included values."""
    theatre = make_theatre(
        "d: &d\n  sub: file:base.yml\nm:\n  <<: *d\nn:\n  <<: {t: file:base.yml}\n",
        files={".troupe/base.yml": "k: 1\n"},
    )
    document = load_config(_config(theatre))

    assert _plain(_mapping_at(document, "m")) == [("sub", [("k", 1)])]
    assert _marks(_mapping_at(document, "m")) == [("sub", True)]
    assert _plain(_mapping_at(document, "n")) == [("t", [("k", 1)])]
    assert _node_at(document, "n", "t").include is not None


def test_quoted_merge_key_text_stays_an_ordinary_key(make_theatre: MakeTheatre) -> None:
    """Req 2.4: a ``<<`` key written single- or double-quoted is an ordinary key with its value,
    not a merge key."""
    document = load_config(_load(make_theatre, "'<<': {a: 1}\nm: {\"<<\": 2}\n"))

    root = document.root
    assert root.merge is None
    assert _plain(root) == [("<<", [("a", 1)]), ("m", [("<<", 2)])]
    assert _mapping_at(document, "m").merge is None


def test_no_path_of_the_loaded_configuration_has_a_merge_key_segment(
    make_theatre: MakeTheatre,
) -> None:
    """Req 2.4: no path of a configuration with merge keys (root, nested, through aliases, lists
    of sources and included files) contains a ``<<`` segment."""
    theatre = make_theatre(
        "<<: {top: 1}\nd: &d\n  k: 1\n  n:\n    <<: {deep: 2}\n"
        "m:\n  <<: [*d, {x: [1, {y: 2}]}]\ninc: file:inc.yml\n",
        files={".troupe/inc.yml": "e: &e {p: 1}\nq:\n  <<: *e\n"},
    )
    document = load_config(_config(theatre))

    paths = [str(path) for path in _paths(document.root)]

    assert "m.n.deep" in paths and "inc.q.p" in paths and "top" in paths and "m.x.1.y" in paths
    assert not [path for path in paths if "<<" in path.split(".")]


def test_merge_key_in_an_included_file_is_merged(make_theatre: MakeTheatre) -> None:
    """Req 2.1, 2.2: an inline merge written in an included file is merged there, and the merge
    key's origin names the included file."""
    theatre = make_theatre(
        "inc: file:sub/inc.yml\n", files={".troupe/sub/inc.yml": "<<: {a: 1}\nb: 2\n"}
    )
    document = load_config(_config(theatre))

    mapping = _mapping_at(document, "inc")

    assert _plain(mapping) == [("a", 1), ("b", 2)]
    assert _marks(mapping) == [("a", True), ("b", False)]
    assert mapping.merge is not None
    assert mapping.merge.key_origin.file.path == theatre / ".troupe" / "sub/inc.yml"
    assert mapping.include is not None


def test_ref_overrides_keep_the_included_merge_key_provenance(make_theatre: MakeTheatre) -> None:
    """Req 2.1, 2.2: in the merged mapping of a ``$ref`` include with overrides, the included
    side's merge keys (at the included root and in a nested mapping merged with an override) are
    kept: ``merge`` and the marks of merged entries survive, an overridden merged key is the
    override's own entry."""
    theatre = make_theatre(
        "x:\n  $ref: file:inc.yml\n  m:\n    own: 5\n    a: 7\n",
        files={".troupe/inc.yml": "<<: {r: 1}\nbase: &b\n  a: 1\n  c: 1\nm:\n  <<: *b\n  own: 2\n"},
    )
    document = load_config(_config(theatre))

    root = _mapping_at(document, "x")
    nested = _mapping_at(document, "x", "m")

    assert root.layers is not None and root.merge is not None
    assert root.merge is root.layers.base.merge
    assert _marks(root) == [("r", True), ("base", False), ("m", False)]
    assert nested.layers is not None and nested.merge is not None
    assert nested.merge is nested.layers.base.merge
    assert _plain(nested) == [("a", 7), ("c", 1), ("own", 5)]
    assert _marks(nested) == [("a", False), ("c", True), ("own", False)]


# --- 2.3: refused merge keys --------------------------------------------------------------------

_NEEDS = "merge key '<<' needs a mapping, an alias of a mapping or a list of those"

# (case id, root file, included files, file named, key path, problem)
REFUSED_MERGES: list[tuple[str, str, dict[str, str], str, str, str]] = [
    ("scalar", "<<: 5\n", {}, "troupe.yml", "<<", f"{_NEEDS}; found a scalar"),
    (
        "nested-scalar",
        "a:\n  b: 1\n  <<: x\n",
        {},
        "troupe.yml",
        "a.<<",
        f"{_NEEDS}; found a scalar",
    ),
    ("null", "m:\n  <<:\n", {}, "troupe.yml", "m.<<", f"{_NEEDS}; found a scalar"),
    (
        "alias-of-a-scalar",
        "s: &s 1\nm:\n  <<: *s\n",
        {},
        "troupe.yml",
        "m.<<",
        f"{_NEEDS}; found a scalar",
    ),
    (
        "alias-of-a-list",
        "l: &l [{a: 1}]\nm:\n  <<: *l\n",
        {},
        "troupe.yml",
        "m.<<",
        f"{_NEEDS}; found an alias of a list",
    ),
    (
        "list-with-a-scalar",
        "a: &a {k: 1}\nm:\n  <<: [*a, 2]\n",
        {},
        "troupe.yml",
        "m.<<",
        f"{_NEEDS}; item 1 is a scalar",
    ),
    (
        "list-with-a-list",
        "m:\n  <<: [{k: 1}, [{j: 2}]]\n",
        {},
        "troupe.yml",
        "m.<<",
        f"{_NEEDS}; item 1 is a list",
    ),
    (
        "text-include",
        "m:\n  <<: file:t.txt\n",
        {".troupe/t.txt": "k: 1\n"},
        "troupe.yml",
        "m.<<",
        f"{_NEEDS}; found an include",
    ),
    (
        "in-an-included-file",
        "inc: file:inc.yml\n",
        {".troupe/inc.yml": "a:\n  <<: 1\n"},
        "inc.yml",
        "inc.a.<<",
        f"{_NEEDS}; found a scalar",
    ),
    (
        "among-ref-overrides",
        "x:\n  $ref: file:inc.yml\n  <<: {a: 1}\n",
        {".troupe/inc.yml": "k: 1\n"},
        "troupe.yml",
        "x.<<",
        "merge key '<<' is not supported among the overrides of a mapping-form include",
    ),
    (
        "among-ref-overrides-before-ref",
        "x:\n  <<: {a: 1}\n  $ref: file:inc.yml\n",
        {".troupe/inc.yml": "k: 1\n"},
        "troupe.yml",
        "x.<<",
        "merge key '<<' is not supported among the overrides of a mapping-form include",
    ),
    (
        "among-root-ref-overrides",
        "$ref: file:inc.yml\n<<: {a: 1}\n",
        {".troupe/inc.yml": "k: 1\n"},
        "troupe.yml",
        "<<",
        "merge key '<<' is not supported among the overrides of a mapping-form include",
    ),
    ("duplicate", "<<: {a: 1}\n<<: {b: 2}\n", {}, "troupe.yml", "<<", "duplicate key in mapping"),
    (
        "include-string",
        "m:\n  <<: file:base.yml\n",
        {".troupe/base.yml": "k: 1\n"},
        "troupe.yml",
        "m.<<",
        f"{_NEEDS}; found an include",
    ),
    (
        "quoted-include-string",
        'm:\n  <<: "file:base.yml"\n',
        {".troupe/base.yml": "k: 1\n"},
        "troupe.yml",
        "m.<<",
        f"{_NEEDS}; found an include",
    ),
    (
        "include-string-in-a-list",
        'm:\n  <<: [{a: 1}, "file:base.yml"]\n',
        {".troupe/base.yml": "k: 1\n"},
        "troupe.yml",
        "m.<<",
        f"{_NEEDS}; item 1 is an include",
    ),
    (
        "bare-ref-mapping",
        'm:\n  <<: {$ref: "file:base.yml"}\n',
        {".troupe/base.yml": "k: 1\n"},
        "troupe.yml",
        "m.<<",
        f"{_NEEDS}; found an include",
    ),
    (
        "ref-mapping-with-overrides",
        'm:\n  <<: {$ref: "file:base.yml", a: 9}\n',
        {".troupe/base.yml": "k: 1\n"},
        "troupe.yml",
        "m.<<",
        f"{_NEEDS}; found an include",
    ),
    (
        "ref-mapping-in-a-list",
        "m:\n  <<:\n    - $ref: file:base.yml\n",
        {".troupe/base.yml": "k: 1\n"},
        "troupe.yml",
        "m.<<",
        f"{_NEEDS}; item 0 is an include",
    ),
    (
        "alias-of-an-anchored-include-string",
        'i: &q "file:base.yml"\nv: {<<: *q}\n',
        {".troupe/base.yml": "k: 1\n"},
        "troupe.yml",
        "v.<<",
        f"{_NEEDS}; found an include",
    ),
    (
        "anchored-include-string-as-the-value",
        'w: {<<: &q "file:base.yml"}\n',
        {".troupe/base.yml": "k: 1\n"},
        "troupe.yml",
        "w.<<",
        f"{_NEEDS}; found an include",
    ),
    (
        "alias-of-an-anchored-ref-mapping",
        "r: &r\n  $ref: file:base.yml\nv:\n  <<: [*r]\n",
        {".troupe/base.yml": "k: 1\n"},
        "troupe.yml",
        "v.<<",
        f"{_NEEDS}; item 0 is an include",
    ),
    (
        "include-in-an-included-file",
        "inc: file:inc.yml\n",
        {".troupe/inc.yml": "m:\n  <<: file:base.yml\n", ".troupe/base.yml": "k: 1\n"},
        "inc.yml",
        "inc.m.<<",
        f"{_NEEDS}; found an include",
    ),
]


@pytest.mark.parametrize(
    ("text", "files", "named", "key_path", "problem"),
    [case[1:] for case in REFUSED_MERGES],
    ids=[case[0] for case in REFUSED_MERGES],
)
def test_refused_merge_key_is_a_load_error_naming_the_file_and_key_path(
    make_theatre: MakeTheatre,
    text: str,
    files: dict[str, str],
    named: str,
    key_path: str,
    problem: str,
) -> None:
    """Req 2.3, 1.6: a merge key whose value is not a mapping, an alias of a mapping or a list of
    those, and a merge key among the overrides of a mapping-form include, are refused (exit 4)
    naming the file and the key path of the merge key; no file is changed."""
    theatre = make_theatre(text, files=files)
    before = _snapshot(theatre)

    with pytest.raises(ConfigLoadError) as info:
        load_config(_config(theatre))

    error = info.value
    assert error.exit_code is ExitCode.LOAD
    assert (error.file, error.path) == (theatre / ".troupe" / named, key_path)
    assert error.problem == problem
    assert _snapshot(theatre) == before


def test_loading_merge_keys_changes_no_file(make_theatre: MakeTheatre) -> None:
    """Req 1.6: loading files with merge keys (root and included) creates, changes, renames and
    deletes nothing."""
    theatre = make_theatre(
        "d: &d {k: 1}\nm:\n  <<: *d\ninc: file:sub/inc.yml\n",
        files={".troupe/sub/inc.yml": "<<: [{a: 1}, {b: 2}]\n"},
    )
    before = _snapshot(theatre)

    document = load_config(_config(theatre))

    assert _mapping_at(document, "inc").merge is not None
    assert _snapshot(theatre) == before
