"""Marking the include strings of VALUE (config-remote-schema; task 3.1).

``mark_include_strings`` walks a parsed VALUE with each string's own path (PATH, then the keys and
indices below it). An include string (``file:``, ``https://``, ``http://``) is marked ``NONE`` when
the schema gives its own path ``format: uri`` (8.2), else ``INCLUDE`` with ``--include`` (9.3) and
``LITERAL`` without (8.1). Keys and other scalars are never include-marked (9.5). A mapping with a
``$ref`` key is refused without ``--include`` (8.6). The plain tree of a marked VALUE makes include
directives from ``INCLUDE`` marks only, so a literal equal to a loaded ``!literal`` string is a
no-op (8.5).

Every test loads a real theatre (with or without a real ``troupe.schema.json``) and passes the
loaded document's schema, as ``set`` will.

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

import json

import pytest

from support import MakeTheatre
from troupe.config.constants import SCHEMA_FILE_NAME
from troupe.config.errors import ExitCode, WriteRefusedError
from troupe.config.loader import load_config
from troupe.config.model import ConfigDocument, ConfigMapping, SchemaSource
from troupe.config.paths import ConfigPath
from troupe.config.writeback.compare import (
    IncludeDirective,
    new_to_plain,
    node_to_plain,
    plain_equal,
)
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


def test_unmarked_values_keep_the_file_prefix_rule() -> None:
    """Req 10.2: until ``set`` marks VALUE (task 3.5), an unmarked scalar keeps today's rule: a
    ``file:`` string is a directive (also as a ``$ref``) and a URL is a plain string."""
    path = ConfigPath.parse("p")
    assert parse_value("file:inc.yaml", path) == NewScalar("file:inc.yaml", None)
    assert _scalar(parse_value("file:inc.yaml", path)).mark is None
    assert new_to_plain(parse_value("file:inc.yaml", path)) == IncludeDirective(
        "file:inc.yaml", None
    )
    assert new_to_plain(parse_value("https://x", path)) == "https://x"
    ref = new_to_plain(parse_value("{$ref: file:inc.yaml, a: 1}", path))
    assert ref == IncludeDirective("file:inc.yaml", None)


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
