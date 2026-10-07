"""Reading the schema, the ``format: uri`` walker and validation (config-remote-schema).

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

import json
import os
from pathlib import Path

import pytest

from remote_support import RemoteServer
from support import MakeTheatre
from troupe.config.constants import SCHEMA_DIALECT, SCHEMA_FILE_NAME
from troupe.config.errors import ConfigLoadError, ExitCode, SchemaViolationError
from troupe.config.loader import load_config
from troupe.config.model import ConfigMapping, JsonValue, SchemaSource
from troupe.config.paths import ConfigPath
from troupe.config.schema import plain_json, read_schema, uri_format_at, validate


def _config_dir(make_theatre: MakeTheatre, schema: str | bytes | None, config: str = "") -> Path:
    files = {} if schema is None else {f".troupe/{SCHEMA_FILE_NAME}": schema}
    return make_theatre(config, files=files) / ".troupe"


def _schema(contents: JsonValue) -> SchemaSource:
    text = json.dumps(contents)
    return SchemaSource(path=Path("/nowhere") / SCHEMA_FILE_NAME, text=text, contents=contents)


def _root(make_theatre: MakeTheatre, config: str) -> ConfigMapping:
    theatre = make_theatre(config, name="config")
    return load_config(theatre / ".troupe" / "troupe.yml").root


# --- read_schema -------------------------------------------------------------------------------


def test_read_schema_returns_none_when_the_file_is_absent(make_theatre: MakeTheatre) -> None:
    """Req 5.4: without a schema file there is nothing to read, validate or find literals in."""
    config_dir = _config_dir(make_theatre, None)

    assert read_schema(config_dir) is None
    assert uri_format_at(None, ConfigPath(("a",))) is False


def test_read_schema_returns_the_text_and_parsed_contents(make_theatre: MakeTheatre) -> None:
    """Req 5.1: the schema file is read as JSON and kept with its exact text."""
    text = f'{{"$schema": "{SCHEMA_DIALECT}", "type": "object"}}\n'
    config_dir = _config_dir(make_theatre, text)

    schema = read_schema(config_dir)

    assert schema == SchemaSource(
        path=config_dir / SCHEMA_FILE_NAME,
        text=text,
        contents={"$schema": SCHEMA_DIALECT, "type": "object"},
    )


@pytest.mark.parametrize("text", ["true", "false", '{"type": "object"}'])
def test_read_schema_accepts_a_boolean_or_an_object_without_dialect(
    make_theatre: MakeTheatre, text: str
) -> None:
    """Req 5.1: a boolean schema and an object without ``$schema`` are 2020-12 schemas."""
    schema = read_schema(_config_dir(make_theatre, text))

    assert schema is not None
    assert schema.contents == json.loads(text)


REFUSALS = [
    pytest.param(b"\xff{}", "not valid UTF-8", id="not-utf8"),
    pytest.param("{", "not valid JSON", id="not-json"),
    pytest.param("[]", "must be a JSON object or boolean", id="list"),
    pytest.param('"x"', "must be a JSON object or boolean", id="string"),
    pytest.param("3", "must be a JSON object or boolean", id="number"),
    pytest.param(
        '{"$schema": "http://json-schema.org/draft-07/schema#"}',
        "http://json-schema.org/draft-07/schema#",
        id="other-dialect",
    ),
    pytest.param('{"$schema": 5}', "$schema", id="dialect-not-string"),
    pytest.param('{"type": 5}', "not a valid JSON Schema 2020-12", id="invalid-schema"),
    pytest.param('{"pattern": "("}', "not a valid JSON Schema 2020-12", id="invalid-regex"),
]


@pytest.mark.parametrize(("content", "problem"), REFUSALS)
def test_read_schema_refuses_an_unusable_file_naming_it(
    make_theatre: MakeTheatre, content: str | bytes, problem: str
) -> None:
    """Req 5.3: an unusable schema is a load error (exit 4) naming the schema file."""
    config_dir = _config_dir(make_theatre, content)

    with pytest.raises(ConfigLoadError) as caught:
        read_schema(config_dir)

    assert caught.value.exit_code == ExitCode.LOAD
    assert caught.value.file == config_dir / SCHEMA_FILE_NAME
    assert problem in caught.value.problem
    assert not isinstance(caught.value, SchemaViolationError)


def test_read_schema_refuses_a_directory_in_place_of_the_file(make_theatre: MakeTheatre) -> None:
    """Req 5.3: a schema path that is not a regular file is a load error naming it."""
    config_dir = _config_dir(make_theatre, None)
    (config_dir / SCHEMA_FILE_NAME).mkdir()

    with pytest.raises(ConfigLoadError) as caught:
        read_schema(config_dir)

    assert caught.value.file == config_dir / SCHEMA_FILE_NAME
    assert "not a regular file" in caught.value.problem


def test_read_schema_refuses_a_dangling_symlink(make_theatre: MakeTheatre) -> None:
    """Req 5.3: a schema link to nothing is not an absent schema but one that cannot be read."""
    config_dir = _config_dir(make_theatre, None)
    (config_dir / SCHEMA_FILE_NAME).symlink_to(config_dir / "missing.json")

    with pytest.raises(ConfigLoadError) as caught:
        read_schema(config_dir)

    assert caught.value.file == config_dir / SCHEMA_FILE_NAME


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads files without permission")
def test_read_schema_refuses_an_unreadable_file(make_theatre: MakeTheatre) -> None:
    """Req 5.3: a schema file that cannot be read is a load error naming it."""
    config_dir = _config_dir(make_theatre, "{}")
    (config_dir / SCHEMA_FILE_NAME).chmod(0)
    try:
        with pytest.raises(ConfigLoadError) as caught:
            read_schema(config_dir)
    finally:
        (config_dir / SCHEMA_FILE_NAME).chmod(0o644)

    assert caught.value.file == config_dir / SCHEMA_FILE_NAME
    assert "cannot read" in caught.value.problem


# --- uri_format_at -----------------------------------------------------------------------------

URI: JsonValue = {"type": "string", "format": "uri"}


def _path(text: str) -> ConfigPath:
    return ConfigPath.parse(text)


def test_walker_follows_properties() -> None:
    """Req 4.4, 4.5: ``properties`` leads to the subschema of a key, at any depth."""
    schema = _schema({"properties": {"a": {"properties": {"hook": URI, "other": {}}}}})

    assert uri_format_at(schema, _path("a.hook")) is True
    assert uri_format_at(schema, _path("a.other")) is False
    assert uri_format_at(schema, _path("a.missing")) is False
    assert uri_format_at(schema, _path("a")) is False


def test_walker_follows_items_for_a_digit_only_segment() -> None:
    """Req 4.5: a digit-only segment takes ``items``; a word segment never does."""
    schema = _schema({"properties": {"hooks": {"items": URI}}})

    assert uri_format_at(schema, _path("hooks.0")) is True
    assert uri_format_at(schema, _path("hooks.12")) is True
    assert uri_format_at(schema, _path("hooks.x")) is False


def test_walker_follows_additional_properties() -> None:
    """Req 4.5: a key without a ``properties`` entry takes ``additionalProperties``."""
    schema = _schema({"properties": {"a": {}}, "additionalProperties": URI})

    assert uri_format_at(schema, _path("b")) is True
    assert uri_format_at(schema, _path("a")) is False


def test_walker_prefers_properties_then_items_then_additional_properties() -> None:
    """Req 4.5: for each segment ``properties`` wins over ``items``, ``items`` over the rest."""
    schema = _schema(
        {
            "properties": {"0": URI},
            "items": {"format": "date"},
            "additionalProperties": {"format": "date"},
        }
    )
    second = _schema({"items": URI, "additionalProperties": {"format": "date"}})

    assert uri_format_at(schema, _path("0")) is True
    assert uri_format_at(schema, _path("1")) is False
    assert uri_format_at(second, _path("1")) is True


def test_walker_takes_the_next_keyword_when_an_entry_is_not_an_object() -> None:
    """Req 4.5: a boolean ``properties`` entry is not an object subschema, so the walk goes on."""
    schema = _schema({"properties": {"a": True}, "additionalProperties": URI})

    assert uri_format_at(schema, _path("a")) is True


@pytest.mark.parametrize(
    "contents",
    [
        pytest.param(True, id="true-root"),
        pytest.param({"properties": {"a": True}}, id="true-subschema"),
        pytest.param({"additionalProperties": True}, id="true-additional"),
        pytest.param({"$defs": {"u": URI}, "properties": {"a": {"$ref": "#/$defs/u"}}}, id="ref"),
        pytest.param({"properties": {"a": {"allOf": [URI]}}}, id="allOf"),
        pytest.param({"properties": {"a": {"anyOf": [URI]}}}, id="anyOf"),
        pytest.param({"properties": {"a": {"oneOf": [URI]}}}, id="oneOf"),
        pytest.param({"patternProperties": {"^a$": URI}}, id="patternProperties"),
        pytest.param({"prefixItems": [URI]}, id="prefixItems"),
        pytest.param({"properties": {"a": {"type": "string", "format": "URI"}}}, id="upper"),
        pytest.param({"properties": {"a": {"format": "uri-reference"}}}, id="uri-reference"),
    ],
)
def test_walker_stops_at_what_it_does_not_follow(contents: JsonValue) -> None:
    """Req 4.5: ``true``, ``$ref`` and combinators end the walk; only ``format`` "uri" counts."""
    schema = _schema(contents)

    assert uri_format_at(schema, _path("a")) is False
    assert uri_format_at(schema, _path("0")) is False


# --- plain_json and validate -------------------------------------------------------------------


def test_plain_json_is_the_expanded_configuration(make_theatre: MakeTheatre) -> None:
    """Req 5.1: validation sees the expanded value, includes and aliases replaced."""
    theatre = make_theatre(
        "a: &x {n: 1, f: 1.5, t: true, z: null}\nb: *x\nc: [s, file:part.yml]\n",
        files={".troupe/part.yml": "k: v\n"},
    )
    root = load_config(theatre / ".troupe" / "troupe.yml").root

    assert plain_json(root) == {
        "a": {"n": 1, "f": 1.5, "t": True, "z": None},
        "b": {"n": 1, "f": 1.5, "t": True, "z": None},
        "c": ["s", {"k": "v"}],
    }


def test_validate_accepts_a_matching_configuration(make_theatre: MakeTheatre) -> None:
    """Req 5.1: a configuration that matches the schema validates."""
    schema = _schema({"type": "object", "properties": {"n": {"type": "integer"}}})

    validate(schema, _root(make_theatre, "n: 3\n"))


def test_validate_reports_the_failing_key_path_and_message(make_theatre: MakeTheatre) -> None:
    """Req 5.2: the error names the schema file, the dotted key path and the validator text."""
    schema = _schema(
        {"properties": {"a": {"properties": {"list": {"items": {"type": "integer"}}}}}}
    )
    root = _root(make_theatre, "a:\n  list: [1, two]\n")

    with pytest.raises(SchemaViolationError) as caught:
        validate(schema, root)

    error = caught.value
    assert error.exit_code == ExitCode.LOAD
    assert error.file == schema.path
    assert error.key_path == "a.list.1"
    assert error.path == "a.list.1"
    assert error.detail == "'two' is not of type 'integer'"
    assert str(error) == (
        f"{schema.path}: a.list.1: the configuration does not match the schema: "
        "'two' is not of type 'integer'"
    )


def test_validate_names_no_key_path_for_a_root_failure(make_theatre: MakeTheatre) -> None:
    """Req 5.2: a failure of the root itself has no key path."""
    schema = _schema({"required": ["name"]})

    with pytest.raises(SchemaViolationError) as caught:
        validate(schema, _root(make_theatre, "other: 1\n"))

    assert caught.value.key_path is None
    assert caught.value.path is None
    assert caught.value.detail == "'name' is a required property"


def test_validate_reports_the_best_matching_failure(make_theatre: MakeTheatre) -> None:
    """Req 5.2: one failure is reported, the most relevant one."""
    schema = _schema({"properties": {"a": {"anyOf": [{"type": "integer"}, {"type": "null"}]}}})

    with pytest.raises(SchemaViolationError) as caught:
        validate(schema, _root(make_theatre, "a: text\n"))

    assert caught.value.key_path == "a"


def test_validate_does_not_assert_format(make_theatre: MakeTheatre) -> None:
    """Req 5.5: ``format`` is an annotation, so a non-URI string validates against ``uri``."""
    schema = _schema({"properties": {"hook": URI, "when": {"format": "date-time"}}})

    validate(schema, _root(make_theatre, "hook: not a uri at all\nwhen: never\n"))


def test_validate_uses_2020_12_semantics(make_theatre: MakeTheatre) -> None:
    """Req 5.1: 2020-12 keywords (``prefixItems``) apply during validation."""
    schema = _schema({"properties": {"pair": {"prefixItems": [{"type": "string"}]}}})

    with pytest.raises(SchemaViolationError) as caught:
        validate(schema, _root(make_theatre, "pair: [1]\n"))

    assert caught.value.key_path == "pair.0"


@pytest.mark.parametrize(
    ("contents", "ref"),
    [
        pytest.param({"properties": {"a": {"$ref": "#/$defs/x"}}}, "'/$defs/x'", id="pointer"),
        pytest.param({"properties": {"a": {"$ref": "#nowhere"}}}, "'nowhere'", id="anchor"),
        pytest.param({"properties": {"a": {"$ref": "other.json"}}}, "'other.json'", id="file"),
    ],
)
def test_validate_refuses_an_unresolvable_ref_naming_the_schema_file(
    make_theatre: MakeTheatre, contents: JsonValue, ref: str
) -> None:
    """Req 5.3: a ``$ref`` that cannot be resolved when validation meets it is a load error."""
    schema = _schema(contents)

    with pytest.raises(ConfigLoadError) as caught:
        validate(schema, _root(make_theatre, "a: 1\n"))

    assert not isinstance(caught.value, SchemaViolationError)
    assert caught.value.exit_code == ExitCode.LOAD
    assert caught.value.file == schema.path
    assert caught.value.path is None
    assert caught.value.problem.startswith("cannot resolve $ref")
    assert ref in caught.value.problem


def test_validate_never_retrieves_a_remote_ref(
    make_theatre: MakeTheatre, remote_server: RemoteServer
) -> None:
    """Req 5.3: a remote ``$ref`` is never fetched; it is an unresolvable ``$ref``."""
    url = remote_server.route("/defs.json", b'{"type": "integer"}')
    schema = _schema({"properties": {"a": {"$ref": url}}})

    with pytest.raises(ConfigLoadError) as caught:
        validate(schema, _root(make_theatre, "a: text\n"))

    assert caught.value.file == schema.path
    assert url in caught.value.problem
    assert remote_server.requests == []


def test_validate_does_not_meet_an_unused_unresolvable_ref(make_theatre: MakeTheatre) -> None:
    """Req 5.3: only a ``$ref`` that validation meets must resolve."""
    schema = _schema({"properties": {"a": {"$ref": "#/$defs/x"}}})

    validate(schema, _root(make_theatre, "b: 1\n"))
