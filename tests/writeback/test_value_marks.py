"""Marking the include strings of VALUE (config-remote-schema; task 3.1).

``mark_include_strings`` walks a parsed VALUE with each string's own path (PATH, then the keys and
indices below it). An include string (``file:``, ``https://``, ``http://``) is marked ``NONE`` when
the schema gives its own path ``format: uri`` (8.2), else ``INCLUDE`` with ``--include`` (9.3) and
``LITERAL`` without (8.1). Keys and other scalars are never include-marked (9.5). A mapping with a
``$ref`` key is refused without ``--include`` (8.6). The plain tree of a marked VALUE makes include
directives from ``INCLUDE`` marks only, so a literal equal to a loaded ``!literal`` string is a
no-op (8.5).

Writing marked strings (task 3.2): a ``LITERAL`` string is written ``!literal``, one space and the
string in its usual style, double-quoted when it has a line break and never as a block scalar
(8.3), in block values, flow values and list items. Replacing an existing ``!literal`` scalar
replaces it whole, tag included, keeping an anchor written before it (8.4). A text include's new
content is written untagged (8.8), and an alias expansion copies a ``!literal`` scalar with its tag
(10.2, config-aliases 7.4).

Every test loads a real theatre (with or without a real ``troupe.schema.json``) and passes the
loaded document's schema, as ``set`` will.

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

import json
from pathlib import Path

import pytest

from support import MakeTheatre
from troupe.config.constants import SCHEMA_FILE_NAME
from troupe.config.errors import ExitCode, WriteRefusedError
from troupe.config.loader import load_config
from troupe.config.model import ConfigDocument, ConfigMapping, ScalarStyle, SchemaSource
from troupe.config.paths import ConfigPath
from troupe.config.writeback.aliases import alias_sites
from troupe.config.writeback.compare import (
    IncludeDirective,
    new_to_plain,
    node_to_plain,
    plain_equal,
)
from troupe.config.writeback.expand import expand_alias
from troupe.config.writeback.planner import plan_patch
from troupe.config.writeback.scalar_render import Context, render_scalar
from troupe.config.writeback.target import resolve_target
from troupe.config.writeback.value import (
    NewMapping,
    NewScalar,
    NewSequence,
    NewValue,
    ScalarMark,
    mark_include_strings,
    parse_value,
)

URI: dict[str, object] = {"type": "string", "format": "uri"}

# The schema gives ``u``, ``p.b.<index>``, ``p.m.<any>`` and ``r.$ref`` the format ``uri``.
SCHEMA: dict[str, object] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "properties": {
        "u": URI,
        "p": {
            "properties": {
                "b": {"items": URI},
                "m": {"additionalProperties": URI},
            }
        },
        "r": {"properties": {"$ref": URI}},
    },
}


def _document(
    make_theatre: MakeTheatre,
    config: str = "a: 1\n",
    schema: dict[str, object] | None = None,
    files: dict[str, str] | None = None,
) -> ConfigDocument:
    written = dict(files or {})
    if schema is not None:
        written[f".troupe/{SCHEMA_FILE_NAME}"] = json.dumps(schema)
    theatre = make_theatre(config, files=written)
    return load_config(theatre / ".troupe" / "troupe.yml")


def _mark(value: str, path: str, schema: SchemaSource | None, *, include: bool = False) -> NewValue:
    config_path = ConfigPath.parse(path)
    return mark_include_strings(
        parse_value(value, config_path), config_path, schema, include=include
    )


def _scalar(value: NewValue) -> NewScalar:
    assert isinstance(value, NewScalar)
    return value


# --- marks of scalars ------------------------------------------------------------------------

INCLUDE_STRINGS = ["file:inc.yaml", "https://x.example/c.yml", "http://x.example/t.txt"]


@pytest.mark.parametrize("text", INCLUDE_STRINGS)
@pytest.mark.parametrize("with_schema", [False, True])
def test_include_string_is_literal_without_include_and_include_with_it(
    make_theatre: MakeTheatre, text: str, with_schema: bool
) -> None:
    """Req 8.1, 9.3: an include string at a path without ``format: uri`` is ``LITERAL`` without
    ``--include`` and ``INCLUDE`` with it, with or without a schema file."""
    schema = _document(make_theatre, schema=SCHEMA if with_schema else None).schema
    assert (schema is not None) is with_schema
    without = _scalar(_mark(text, "hook", schema))
    assert without == NewScalar(text, None, ScalarMark.LITERAL)
    with_include = _scalar(_mark(text, "hook", schema, include=True))
    assert with_include == NewScalar(text, None, ScalarMark.INCLUDE)


@pytest.mark.parametrize("text", INCLUDE_STRINGS)
@pytest.mark.parametrize("include", [False, True])
def test_include_string_at_a_uri_format_path_is_not_marked(
    make_theatre: MakeTheatre, text: str, include: bool
) -> None:
    """Req 8.2: when the schema gives the string's own path ``format: uri`` it is ``NONE`` (a plain
    value, a schema literal), with or without ``--include``."""
    schema = _document(make_theatre, schema=SCHEMA).schema
    assert _scalar(_mark(text, "u", schema, include=include)).mark is ScalarMark.NONE


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("plain", "plain"),
        ("'FILE:x.yaml'", "FILE:x.yaml"),
        ("'Https://x'", "Https://x"),
        ("' file:x'", " file:x"),
        ("ftp://x", "ftp://x"),
        ("12", 12),
        ("true", True),
        ("~", None),
    ],
)
@pytest.mark.parametrize("include", [False, True])
def test_other_scalars_are_marked_none(
    make_theatre: MakeTheatre, value: str, expected: object, include: bool
) -> None:
    """Req 8.1, 9.5: a string that is not an include string (prefixes are case-sensitive) and every
    non-string is ``NONE``, so ``--include`` without an include string changes nothing."""
    schema = _document(make_theatre).schema
    marked = _scalar(_mark(value, "hook", schema, include=include))
    assert marked.mark is ScalarMark.NONE
    assert marked.value == expected


# --- nested strings and keys -----------------------------------------------------------------


def test_nested_strings_are_marked_by_their_own_path(make_theatre: MakeTheatre) -> None:
    """Req 8.1, 8.2: strings nested in flow mappings and lists are marked by their own path:
    ``p.b.0`` and ``p.m.k`` (any index of ``b``, any key of ``m``) have ``format: uri``
    (``NONE``), every other include string is ``LITERAL``; keys stay unmarked strings and other
    scalars are ``NONE``."""
    schema = _document(make_theatre, schema=SCHEMA).schema
    marked = _mark(
        "{a: https://x, b: [file:y.yml, 3], m: {k: http://k}, "
        "'https://key': plain, c: [[file:deep.txt]]}",
        "p",
        schema,
    )
    literal, none = ScalarMark.LITERAL, ScalarMark.NONE
    assert marked == NewMapping(
        (
            ("a", NewScalar("https://x", None, literal)),
            (
                "b",
                NewSequence(
                    (
                        NewScalar("file:y.yml", None, none),
                        NewScalar(3, "3", none),
                    )
                ),
            ),
            ("m", NewMapping((("k", NewScalar("http://k", None, none)),))),
            ("https://key", NewScalar("plain", None, none)),
            ("c", NewSequence((NewSequence((NewScalar("file:deep.txt", None, literal),)),))),
        )
    )


def test_nested_strings_with_include_are_include_marked(make_theatre: MakeTheatre) -> None:
    """Req 9.3, 8.2: with ``--include`` every nested include string is ``INCLUDE`` except those at
    a ``format: uri`` path."""
    schema = _document(make_theatre, schema=SCHEMA).schema
    marked = _mark("{a: https://x, b: [file:y.yml], c: [file:z.yml]}", "p", schema, include=True)
    assert isinstance(marked, NewMapping)
    a, b, c = (value for _, value in marked.entries)
    assert _scalar(a).mark is ScalarMark.INCLUDE
    assert isinstance(b, NewSequence)
    assert isinstance(c, NewSequence)
    assert [_scalar(item).mark for item in (*b.items, *c.items)] == [
        ScalarMark.NONE,
        ScalarMark.INCLUDE,
    ]


# --- the $ref refusal ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "{$ref: file:inc.yaml, a: 1}",
        "{$ref: https://x/c.yml}",
        "{$ref: 3}",
        "[1, {k: {$ref: file:inc.yaml}}]",
    ],
)
def test_ref_mapping_without_include_is_refused(make_theatre: MakeTheatre, value: str) -> None:
    """Req 8.6: a mapping with a ``$ref`` key anywhere in VALUE, without ``--include``, is refused
    (exit 3) naming PATH and mentioning ``--include``."""
    schema = _document(make_theatre).schema
    with pytest.raises(WriteRefusedError) as caught:
        _mark(value, "p.q", schema)
    error = caught.value
    assert error.exit_code is ExitCode.WRITE_REFUSED
    assert error.path == "p.q"
    assert error.file is None
    assert str(error) == "p.q: a `$ref` mapping is an include; give --include to write it"


def test_ref_mapping_with_include_marks_its_ref_string(make_theatre: MakeTheatre) -> None:
    """Req 8.6, 9.3: with ``--include`` a ``$ref`` mapping is accepted and its ``$ref`` string is
    ``INCLUDE``; its overrides are marked by their own paths."""
    schema = _document(make_theatre).schema
    marked = _mark("{$ref: file:inc.yaml, a: https://x}", "p", schema, include=True)
    assert marked == NewMapping(
        (
            ("$ref", NewScalar("file:inc.yaml", None, ScalarMark.INCLUDE)),
            ("a", NewScalar("https://x", None, ScalarMark.INCLUDE)),
        )
    )


# --- plain trees -----------------------------------------------------------------------------


@pytest.mark.parametrize("text", INCLUDE_STRINGS)
def test_plain_tree_makes_directives_from_include_marks_only(text: str) -> None:
    """Req 8.1, 8.2, 9.3: ``INCLUDE`` becomes a contentless directive; ``LITERAL`` and ``NONE``
    strings are plain values."""
    assert new_to_plain(NewScalar(text, None, ScalarMark.INCLUDE)) == IncludeDirective(text, None)
    assert new_to_plain(NewScalar(text, None, ScalarMark.LITERAL)) == text
    assert new_to_plain(NewScalar(text, None, ScalarMark.NONE)) == text


def test_plain_tree_of_ref_mapping_by_mark() -> None:
    """Req 8.6, 9.3: a ``$ref`` mapping is a directive with overrides only when its ``$ref`` string
    is ``INCLUDE``; a ``NONE`` (schema literal) ``$ref`` string leaves an ordinary mapping."""
    overrides = (("a", NewScalar(1, "1", ScalarMark.NONE)),)
    included = NewMapping(
        (("$ref", NewScalar("https://x/c.yml", None, ScalarMark.INCLUDE)), *overrides)
    )
    plain = new_to_plain(included)
    assert plain == IncludeDirective("https://x/c.yml", None)
    assert isinstance(plain, IncludeDirective)
    assert plain.overrides == {"a": 1}
    literal = NewMapping((("$ref", NewScalar("file:c.yml", None, ScalarMark.NONE)), *overrides))
    assert new_to_plain(literal) == {"$ref": "file:c.yml", "a": 1}


def test_plain_tree_of_nested_marked_value(make_theatre: MakeTheatre) -> None:
    """Req 8.1, 8.2: the plain tree of a marked nested VALUE keeps every literal as a string."""
    schema = _document(make_theatre, schema=SCHEMA).schema
    marked = _mark("{a: https://x, b: [file:y.yml], c: [file:z.yml]}", "p", schema)
    assert new_to_plain(marked) == {"a": "https://x", "b": ["file:y.yml"], "c": ["file:z.yml"]}


def test_parsed_values_are_marked_none_and_stay_plain_until_marked() -> None:
    """Req 8.1, 10.2: ``parse_value`` marks every scalar ``NONE`` (the design default; the
    temporary unmarked state is gone), so an include string is a plain value in the plain tree
    until ``mark_include_strings`` marks it ``INCLUDE``; there is no ``file:`` prefix fallback."""
    path = ConfigPath.parse("p")
    assert parse_value("file:inc.yaml", path) == NewScalar("file:inc.yaml", None, ScalarMark.NONE)
    assert new_to_plain(parse_value("file:inc.yaml", path)) == "file:inc.yaml"
    assert new_to_plain(parse_value("https://x", path)) == "https://x"
    ref = new_to_plain(parse_value("{$ref: file:inc.yaml, a: 1}", path))
    assert ref == {"$ref": "file:inc.yaml", "a": 1}


# --- no-op comparisons -----------------------------------------------------------------------


def _loaded_at(document: ConfigDocument, key: str) -> object:
    assert isinstance(document.root, ConfigMapping)
    (entry,) = (entry for entry in document.root.entries if entry.key == key)
    return node_to_plain(entry.value)


@pytest.mark.parametrize("text", INCLUDE_STRINGS)
def test_literal_equal_to_a_loaded_literal_is_a_no_op(make_theatre: MakeTheatre, text: str) -> None:
    """Req 8.5: VALUE equal to a loaded ``!literal`` string is the same configuration; marked
    ``INCLUDE`` it differs."""
    document = _document(make_theatre, f"hook: !literal {text}\n")
    loaded = _loaded_at(document, "hook")
    assert loaded == text
    literal = new_to_plain(_mark(text, "hook", document.schema))
    assert plain_equal(literal, loaded, ordered=False)
    assert plain_equal(literal, loaded, ordered=True)
    include = new_to_plain(_mark(text, "hook", document.schema, include=True))
    assert not plain_equal(include, loaded, ordered=False)


def test_schema_literal_equal_to_a_loaded_schema_literal_is_a_no_op(
    make_theatre: MakeTheatre,
) -> None:
    """Req 8.2, 8.5: a string at a ``format: uri`` path equals the loaded schema-literal string,
    with or without ``--include``."""
    document = _document(make_theatre, "u: https://x\n", schema=SCHEMA)
    loaded = _loaded_at(document, "u")
    assert loaded == "https://x"
    for include in (False, True):
        value = new_to_plain(_mark("https://x", "u", document.schema, include=include))
        assert plain_equal(value, loaded, ordered=False)


def test_include_vs_literal_against_a_loaded_include(make_theatre: MakeTheatre) -> None:
    """Req 8.1, 8.5, 9.3: over a loaded ``file:`` include, the ``INCLUDE``-marked same string is a
    no-op and the ``LITERAL``-marked one is a change."""
    document = _document(make_theatre, "inc: file:inc.yaml\n", files={".troupe/inc.yaml": "k: 1\n"})
    loaded = _loaded_at(document, "inc")
    assert isinstance(loaded, IncludeDirective)
    include = new_to_plain(_mark("file:inc.yaml", "inc", document.schema, include=True))
    literal = new_to_plain(_mark("file:inc.yaml", "inc", document.schema))
    assert plain_equal(include, loaded, ordered=False)
    assert not plain_equal(literal, loaded, ordered=False)


# --- writing marked strings (task 3.2) -----------------------------------------------------------


def _config_file(
    make_theatre: MakeTheatre, config: str, files: dict[str, str] | None = None
) -> Path:
    return make_theatre(config, files=files) / ".troupe" / "troupe.yml"


def _planned(
    make_theatre: MakeTheatre,
    config: str,
    path: str,
    value: str,
    *,
    include: bool = False,
    files: dict[str, str] | None = None,
) -> dict[str, str]:
    """Plan ``set PATH VALUE`` with VALUE marked as ``set`` marks it; return the new text of every
    patched file by its name, after proving that the patched texts load back to the intended
    configuration (so a literal is not loaded as an include)."""
    config_file = _config_file(make_theatre, config, files)
    document = load_config(config_file)
    config_path = ConfigPath.parse(path)
    marked = mark_include_strings(
        parse_value(value, config_path), config_path, document.schema, include=include
    )
    request = resolve_target(document, config_path, marked)
    patches = plan_patch(document, request)
    texts = {patch.file.key: patch.new_text for patch in patches}
    reloaded = load_config(config_file, texts)
    assert plain_equal(node_to_plain(reloaded.root), request.intended, ordered=True)
    return {patch.file.path.name: patch.new_text for patch in patches}


def _root(make_theatre: MakeTheatre, config: str, path: str, value: str) -> str:
    patched = _planned(make_theatre, config, path, value)
    assert list(patched) == ["troupe.yml"]
    return patched["troupe.yml"]


def test_render_scalar_tags_a_literal_string_only() -> None:
    """Req 8.1, 8.3: a ``LITERAL`` string is ``!literal``, one space and the string in the chosen
    style (a preferred style that can hold it is kept); other marks are written untagged."""
    path = ConfigPath.parse("a")
    literal = NewScalar("https://x", None, ScalarMark.LITERAL)
    assert render_scalar(literal, Context.BLOCK_VALUE, None, path) == "!literal https://x"
    assert render_scalar(literal, Context.FLOW_VALUE, None, path) == "!literal https://x"
    single = render_scalar(literal, Context.BLOCK_VALUE, ScalarStyle.SINGLE_QUOTED, path)
    assert single == "!literal 'https://x'"
    broken = NewScalar("file:a\nb", None, ScalarMark.LITERAL)
    assert render_scalar(broken, Context.BLOCK_VALUE, None, path) == '!literal "file:a\\nb"'
    for mark in (ScalarMark.NONE, ScalarMark.INCLUDE):
        plain = NewScalar("https://x", None, mark)
        assert render_scalar(plain, Context.BLOCK_VALUE, None, path) == "https://x"


@pytest.mark.parametrize(
    ("config", "path", "value", "expected"),
    [
        ("a: 1\nkey: old\n", "key", "https://x", "a: 1\nkey: !literal https://x\n"),
        ("a: 1\n", "key", "https://x", "a: 1\nkey: !literal https://x\n"),
        ("key: 'old'\n", "key", "file:x", "key: !literal 'file:x'\n"),
        ("p: {a: 1}\n", "p.a", "https://x", "p: {a: !literal https://x}\n"),
        ("p: {q: 1}\n", "p.a", "https://x", "p: {q: 1, a: !literal https://x}\n"),
        ("p: 1\n", "p", "{a: https://x}", "p:\n  a: !literal https://x\n"),
        ("q: 1\np: []\n", "p", "[1, 'file:x']", "q: 1\np: [1, !literal file:x]\n"),
        ("l:\n- 1\n", "l.0", "file:x", "l:\n- !literal file:x\n"),
        ("l:\n- 1\n", "l", "[1, 'file:x']", "l:\n- 1\n- !literal file:x\n"),
        ("l: [1]\n", "l.1", "file:x", "l: [1, !literal file:x]\n"),
        ("p: 1\n", "p", "{a: [file:x, 2]}", "p:\n  a:\n    - !literal file:x\n    - 2\n"),
        ("p: 1\n", "p", "[{a: file:x}]", "p:\n  - a: !literal file:x\n"),
    ],
    ids=[
        "block-value",
        "new-key",
        "keeps-quote-style",
        "flow-value",
        "flow-new-entry",
        "nested-mapping",
        "flow-list",
        "list-item",
        "appended-item",
        "flow-item",
        "nested-list",
        "list-of-mappings",
    ],
)
def test_literal_strings_are_written_tagged(
    make_theatre: MakeTheatre, config: str, path: str, value: str, expected: str
) -> None:
    """Req 8.1, 8.3: every literal-marked string, in a block value, a flow value or a list item,
    is written ``!literal``, one space and the string; it loads back as the same string."""
    assert _root(make_theatre, config, path, value) == expected


def test_include_marked_and_schema_literal_strings_are_written_plain(
    make_theatre: MakeTheatre,
) -> None:
    """Req 8.2, 9.3: an ``INCLUDE`` string and a schema-literal string get no tag."""
    patched = _planned(
        make_theatre,
        "a: 1\n",
        "inc",
        "file:inc.yaml",
        include=True,
        files={".troupe/inc.yaml": "k: 1\n"},
    )
    assert patched == {"troupe.yml": "a: 1\ninc: file:inc.yaml\n"}
    theatre = make_theatre("u: old\n", files={f".troupe/{SCHEMA_FILE_NAME}": json.dumps(SCHEMA)})
    document = load_config(theatre / ".troupe" / "troupe.yml")
    path = ConfigPath.parse("u")
    marked = mark_include_strings(
        parse_value("https://x", path), path, document.schema, include=False
    )
    (patch,) = plan_patch(document, resolve_target(document, path, marked))
    assert patch.new_text == "u: https://x\n"


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        ("a: 1\nkey: old\n", 'a: 1\nkey: !literal "file:a\\nb"\n'),
        ("key: |\n  old\n  text\nz: 1\n", 'key: !literal "file:a\\nb"\nz: 1\n'),
        ("key: >-\n  old\nz: 1\n", 'key: !literal "file:a\\nb"\nz: 1\n'),
        ("l:\n- old\n", 'l:\n- !literal "file:a\\nb"\n'),
    ],
    ids=["over-plain", "over-literal-block", "over-folded-block", "list-item"],
)
def test_literal_string_with_a_line_break_is_double_quoted_never_a_block(
    make_theatre: MakeTheatre, config: str, expected: str
) -> None:
    """Req 8.3: a literal string with a line break is written double-quoted on its line, also over
    an existing block scalar, which is replaced rather than rewritten in place."""
    path = "l.0" if config.startswith("l:") else "key"
    assert _root(make_theatre, config, path, '"file:a\\nb"') == expected


@pytest.mark.parametrize(
    ("config", "value", "expected"),
    [
        ("a: !literal https://old\nb: 1\n", "https://new", "a: !literal https://new\nb: 1\n"),
        ("a: !literal https://old\n", "5", "a: 5\n"),
        ("a: !literal 'https://old'\n", "plain", "a: 'plain'\n"),
        ("a: !literal https://old  # c\n", "5", "a: 5".ljust(25) + "# c\n"),
        ("a: &x !literal https://old\n", "5", "a: &x 5\n"),
        (
            "a: &x !literal https://old  # c\n",
            "https://n",
            "a: &x !literal https://n    # c\n",
        ),
        ("a: !literal\nb: 1\n", "5", "a: 5\nb: 1\n"),
        ("a: !literal  # c\n", "5", "a: 5".ljust(13) + "# c\n"),
        ("a: !literal https://old  # c\n", "{k: 1}", "a:".ljust(25) + "# c\n  k: 1\n"),
        ("a: &x !literal https://old\n", "[1]", "a: &x\n  - 1\n"),
        ("p: {a: !literal https://old, b: 1}\n", "{a: 2, b: 1}", "p: {a: 2, b: 1}\n"),
        ("p: {a: &x !literal https://old}\n", "{a: 2}", "p: {a: &x 2}\n"),
        ("l:\n- !literal file:old\n", "[2]", "l:\n- 2\n"),
        ("l:\n- &x !literal file:old  # c\n", "[2]", "l:\n" + "- &x 2".ljust(24) + "# c\n"),
        ("l: [!literal file:old, 1]\n", "[2, 1]", "l: [2, 1]\n"),
        ("l: [1, !literal file:old]\n", "[1]", "l: [1]\n"),
        ("l: [!literal file:old, 1]\n", "[1]", "l: [1]\n"),
    ],
    ids=[
        "literal-again",
        "non-string",
        "keeps-quote-style",
        "eol-comment",
        "anchor",
        "anchor-comment-literal",
        "empty",
        "empty-comment",
        "by-collection",
        "anchor-by-collection",
        "flow-value",
        "flow-anchor",
        "list-item",
        "list-item-anchor-comment",
        "flow-item",
        "flow-item-removed",
        "first-flow-item-removed",
    ],
)
def test_existing_plain_literal_is_replaced_with_its_tag(
    make_theatre: MakeTheatre, config: str, value: str, expected: str
) -> None:
    """Req 8.4, 8.1: replacing an existing ``!literal`` scalar replaces the whole scalar, tag
    included, keeps an anchor written before the tag and an end-of-line comment (at its column,
    as for any replaced scalar), and tags the new string only when it is a literal itself; a
    removed ``!literal`` list item goes with its tag."""
    path = config.split(":", 1)[0]
    assert _root(make_theatre, config, path, value) == expected


@pytest.mark.parametrize(
    ("config", "value", "expected"),
    [
        ("a: !literal |\n  file:x\n  y\nb: 1\n", "z", "a: z\nb: 1\n"),
        ("a: !literal |  # c\n  file:x\nb: 1\n", "z", "a: z".ljust(15) + "# c\nb: 1\n"),
        ("a: !literal |-\n  file:x\nb: 1\n", "file:y", "a: !literal file:y\nb: 1\n"),
        ("a: &x !literal >\n  file:x\nb: 1\n", "5", "a: &x 5\nb: 1\n"),
        (
            "a: &x !literal |  # c\n  file:x\nb: 1\n",
            "https://n",
            "a: &x !literal https://n # c\nb: 1\n",
        ),
        ("a: !literal |\n  file:x\nb: 1\n", '"two\\nlines\\n"', "a: |\n  two\n  lines\nb: 1\n"),
        ("a: &x !literal |\n  file:x\nb: 1\n", '"t\\nu"', "a: &x |-\n  t\n  u\nb: 1\n"),
        ("l:\n- !literal |\n  file:x\n- 1\n", "[2, 1]", "l:\n- 2\n- 1\n"),
        ("l:\n- !literal |2\n   file:x\n   \n- 1\n", "[2, 1]", "l:\n- 2\n- 1\n"),
    ],
    ids=[
        "by-plain",
        "comment",
        "by-literal",
        "anchor-folded",
        "anchor-comment-literal",
        "by-multi-line",
        "anchor-by-multi-line",
        "list-item",
        "list-item-indentation-indicator",
    ],
)
def test_existing_block_literal_is_replaced_whole(
    make_theatre: MakeTheatre, config: str, value: str, expected: str
) -> None:
    """Req 8.4, 8.3: an existing ``!literal`` block scalar is replaced as a whole (tag, header and
    content), never rewritten in place; an anchor before the tag and an end-of-line comment after
    the header stay, and a new multi-line string that is no literal becomes a new untagged
    block."""
    path = config.split(":", 1)[0]
    assert _root(make_theatre, config, path, value) == expected


def test_text_include_content_is_written_untagged(make_theatre: MakeTheatre) -> None:
    """Req 8.8: a literal-marked string that becomes a text include's content is written as text,
    untagged; the including file does not change."""
    patched = _planned(
        make_theatre, "t: file:t.txt\n", "t", "https://x", files={".troupe/t.txt": "old\n"}
    )
    assert patched == {"t.txt": "https://x"}


def _expanded(make_theatre: MakeTheatre, config: str, path: str) -> str:
    """Expand the alias on ``path``; prove the expanded text loads to the same configuration (a
    copy that lost its tag would load as an include)."""
    config_file = _config_file(make_theatre, config)
    document = load_config(config_file)
    config_path = ConfigPath.parse(path)
    request = resolve_target(document, config_path, parse_value("x", config_path))
    (site,) = alias_sites(document, request, expand=True)
    texts = expand_alias(document, site)
    reloaded = load_config(config_file, texts)
    assert plain_equal(node_to_plain(reloaded.root), node_to_plain(document.root), ordered=True)
    (text,) = texts.values()
    return text


@pytest.mark.parametrize(
    ("config", "path", "expected"),
    [
        (
            "a: &x !literal https://x  # c\nb: *x\n",
            "b",
            "a: &x !literal https://x  # c\nb: !literal https://x  # expanded from *x by troupe\n",
        ),
        (
            "a: &x !literal 'file:x'\nl:\n- *x\n",
            "l.0",
            "a: &x !literal 'file:x'\nl:\n- !literal 'file:x'  # expanded from *x by troupe\n",
        ),
        (
            "a: &x !literal\nb: *x\n",
            "b",
            "a: &x !literal\nb: !literal  # expanded from *x by troupe\n",
        ),
        (
            'a: &x !literal "file:x\n  y"\nb: *x\n',
            "b",
            'a: &x !literal "file:x\n  y"\nb: !literal "file:x y"  # expanded from *x by troupe\n',
        ),
        (
            "a: &x !literal |\n  file:x\nb: *x\n",
            "b",
            "a: &x !literal |\n  file:x\nb: !literal |  # expanded from *x by troupe\n  file:x\n",
        ),
        (
            "m: &m\n  u: !literal file:x\n  v: [!literal https://y]\nn: *m\n",
            "n",
            "m: &m\n  u: !literal file:x\n  v: [!literal https://y]\n"
            "n:  # expanded from *m by troupe\n  u: !literal file:x\n  v:\n    - !literal https://y\n",
        ),
    ],
    ids=["scalar", "list-item", "empty", "multi-line-flow", "block", "inside-a-collection"],
)
def test_alias_expansion_copies_a_literal_with_its_tag(
    make_theatre: MakeTheatre, config: str, path: str, expected: str
) -> None:
    """Req 10.2, 8.4: expanding an alias of an anchored ``!literal`` scalar (or of a collection
    holding one) writes the copy with its tag, so it still loads as a string, not an include."""
    assert _expanded(make_theatre, config, path) == expected
