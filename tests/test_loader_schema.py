"""Reading and enforcing the schema when the configuration loads (config-remote-schema; task 2.2).

Every test writes a real theatre with a real ``troupe.schema.json`` next to the root configuration
file and loads it with ``load_config`` (or runs the real CLI). A string whose own path the schema
gives ``"format": "uri"`` stays a string, in the root file and in included files (4.4, 4.6). The
expanded configuration (includes, overrides, aliases, merge keys and text includes as their
values) is validated; a configuration that does not match, or a schema that cannot be used, is a
load error (exit 4) naming the schema file, with no history state created and the schema file
untouched (5.1-5.4, 5.6). Without a schema file, loading is unchanged (5.4).

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

import hashlib
import json
from pathlib import Path

import pytest

from support import MakeTheatre, RunCli
from troupe.config.constants import SCHEMA_FILE_NAME
from troupe.config.errors import ConfigLoadError, ExitCode, SchemaViolationError
from troupe.config.loader import load_config, load_include
from troupe.config.model import (
    ConfigDocument,
    ConfigMapping,
    ConfigNode,
    ConfigScalar,
    ConfigSequence,
    IncludeKind,
)
from troupe.config.paths import ConfigPath
from troupe.config.schema import read_schema

CONFIG_NAMES = ["troupe.yml", "troupe.yaml"]

URI: dict[str, object] = {"type": "string", "format": "uri"}


def _theatre(
    make_theatre: MakeTheatre,
    config: str,
    schema: object,
    files: dict[str, str] | None = None,
    config_name: str = "troupe.yml",
) -> Path:
    written = dict(files or {})
    if schema is not None:
        text = schema if isinstance(schema, str) else json.dumps(schema)
        written[f".troupe/{SCHEMA_FILE_NAME}"] = text
    return make_theatre(config, config_name=config_name, files=written)


def _load(
    make_theatre: MakeTheatre,
    config: str,
    schema: object,
    files: dict[str, str] | None = None,
) -> ConfigDocument:
    return load_config(_theatre(make_theatre, config, schema, files) / ".troupe" / "troupe.yml")


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


def _violation(
    make_theatre: MakeTheatre, config: str, schema: object, **files: str
) -> SchemaViolationError:
    """Load a configuration the schema rejects; return the error after checking that it is a load
    error (exit 4) naming the schema file."""
    theatre = _theatre(make_theatre, config, schema, {f".troupe/{k}": v for k, v in files.items()})
    with pytest.raises(SchemaViolationError) as caught:
        load_config(theatre / ".troupe" / "troupe.yml")
    assert caught.value.exit_code == ExitCode.LOAD
    assert caught.value.file == theatre / ".troupe" / SCHEMA_FILE_NAME
    return caught.value


def _snapshot(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        if path.is_file()
        else "<dir>"
        for path in sorted(root.rglob("*"))
    }


# --- 4.4, 4.6: literals by schema ----------------------------------------------------------------


def test_a_uri_format_string_in_the_root_file_stays_a_string(make_theatre: MakeTheatre) -> None:
    """Req 4.4, 4.6: ``hook`` has ``format: uri``, so ``file:missing.md`` is its value and its
    target is never read (it does not exist); ``other`` has no format and is still included."""
    schema = {"properties": {"hook": URI}}
    document = _load(
        make_theatre,
        "hook: file:missing.md\nother: file:inc.md\n",
        schema,
        {".troupe/inc.md": "included text"},
    )

    hook = _scalar_at(document, "hook")
    assert (hook.value, hook.include, hook.literal) == ("file:missing.md", None, None)
    other = _scalar_at(document, "other")
    assert other.value == "included text"
    assert other.include is not None and other.include.kind is IncludeKind.TEXT


def test_a_uri_format_string_in_an_included_file_stays_a_string(make_theatre: MakeTheatre) -> None:
    """Req 4.4, 4.6: the path of a string in an included file is its path in the configuration,
    so ``sub.hook`` (written in sub.yml) is a literal by the schema, at any depth."""
    schema = {
        "properties": {
            "sub": {
                "properties": {
                    "hook": URI,
                    "deep": {"properties": {"list": {"items": URI}}},
                }
            }
        }
    }
    document = _load(
        make_theatre,
        "sub: file:sub.yml\n",
        schema,
        {".troupe/sub.yml": "hook: file:a.md\ndeep:\n  list: [file:b.md, file:c.md]\n"},
    )

    assert _scalar_at(document, "sub", "hook").value == "file:a.md"
    assert _scalar_at(document, "sub", "hook").include is None
    assert [_scalar_at(document, "sub", "deep", "list", i).value for i in (0, 1)] == [
        "file:b.md",
        "file:c.md",
    ]
    assert [file.path.name for file in document.files] == ["troupe.yml", "sub.yml"]


def test_additional_properties_give_the_format_to_every_other_key(
    make_theatre: MakeTheatre,
) -> None:
    """Req 4.4, 4.5: ``additionalProperties`` gives ``hooks.<any key>`` the ``uri`` format, while a
    key under ``properties`` without a format is still an include."""
    schema = {
        "properties": {
            "hooks": {"properties": {"local": {"type": "string"}}, "additionalProperties": URI}
        }
    }
    document = _load(
        make_theatre,
        "hooks:\n  a: file:x.md\n  local: file:inc.md\n",
        schema,
        {".troupe/inc.md": "text"},
    )

    assert _scalar_at(document, "hooks", "a").value == "file:x.md"
    assert _scalar_at(document, "hooks", "local").value == "text"


def test_another_format_does_not_make_a_literal(make_theatre: MakeTheatre) -> None:
    """Req 4.4: only ``format`` exactly ``uri`` makes a literal; ``uri-reference`` does not."""
    schema = {"properties": {"hook": {"type": "string", "format": "uri-reference"}}}
    document = _load(make_theatre, "hook: file:inc.md\n", schema, {".troupe/inc.md": "text"})

    assert _scalar_at(document, "hook").value == "text"


def test_an_alias_is_decided_at_its_own_path(make_theatre: MakeTheatre) -> None:
    """Req 4.4: a string is decided by its own path, so the anchored ``a`` (``format: uri``) is a
    literal while its alias ``b`` (no format) is the include."""
    schema = {"properties": {"a": URI}}
    document = _load(make_theatre, "a: &x file:inc.md\nb: *x\n", schema, {".troupe/inc.md": "text"})

    assert _scalar_at(document, "a").value == "file:inc.md"
    assert _scalar_at(document, "b").value == "text"


def test_the_schema_does_not_change_a_ref_value(make_theatre: MakeTheatre) -> None:
    """Req 4.4, 5.1: a ``$ref`` value is an include by construction; a ``uri`` format given to the
    mapping's path does not apply to it (the schema then validates the included mapping)."""
    schema = {"properties": {"svc": {"type": "object", "additionalProperties": URI}}}
    document = _load(
        make_theatre,
        "svc:\n  $ref: file:svc.yml\n",
        schema,
        {".troupe/svc.yml": "url: file:not-read.md\n"},
    )

    svc = _node_at(document, "svc")
    assert svc.include is not None and svc.include.kind is IncludeKind.YAML
    assert _scalar_at(document, "svc", "url").value == "file:not-read.md"


# --- 5.1, 5.2: validation of the expanded configuration -----------------------------------------


def test_a_valid_configuration_loads_and_carries_its_schema(make_theatre: MakeTheatre) -> None:
    """Req 5.1: a configuration that matches loads, and the document carries the schema read."""
    schema = {"type": "object", "properties": {"port": {"type": "integer"}}}
    theatre = _theatre(make_theatre, "port: 80\n", schema)
    config_dir = theatre / ".troupe"

    document = load_config(config_dir / "troupe.yml")

    assert document.schema == read_schema(config_dir)
    assert document.schema is not None
    assert document.schema.contents == schema


def test_included_values_are_validated(make_theatre: MakeTheatre) -> None:
    """Req 5.1, 5.2: values from an included file are validated, and the failure names the key
    path in the configuration."""
    schema = {"properties": {"sub": {"properties": {"port": {"type": "integer"}}}}}
    error = _violation(make_theatre, "sub: file:sub.yml\n", schema, **{"sub.yml": "port: eighty\n"})

    assert (error.key_path, error.detail) == ("sub.port", "'eighty' is not of type 'integer'")
    assert error.problem == "the configuration does not match the schema: " + error.detail


def test_overrides_are_validated_merged_and_without_ref(make_theatre: MakeTheatre) -> None:
    """Req 5.1: a mapping-form include is validated as the deep merge of the included mapping and
    its overrides, with no ``$ref`` key, so ``additionalProperties: false`` accepts it."""
    schema = {
        "properties": {
            "svc": {
                "type": "object",
                "properties": {"host": {"type": "string"}, "port": {"type": "integer"}},
                "required": ["host", "port"],
                "additionalProperties": False,
            }
        }
    }
    files = {".troupe/svc.yml": "host: a\nport: 1\n"}
    _load(make_theatre, "svc:\n  $ref: file:svc.yml\n  port: 2\n", schema, files)

    error = _violation(
        make_theatre, "svc:\n  $ref: file:svc.yml\n  port: two\n", schema, **{"svc.yml": "x: 1\n"}
    )
    assert error.key_path in {"svc", "svc.port"}


def test_overridden_values_are_validated(make_theatre: MakeTheatre) -> None:
    """Req 5.1, 5.2: the override, not the included value, is what is validated."""
    schema = {"properties": {"svc": {"properties": {"port": {"type": "integer"}}}}}
    error = _violation(
        make_theatre,
        "svc:\n  $ref: file:svc.yml\n  port: two\n",
        schema,
        **{"svc.yml": "port: 1\n"},
    )

    assert error.key_path == "svc.port"


def test_aliases_are_validated_as_their_expanded_values(make_theatre: MakeTheatre) -> None:
    """Req 5.1, 5.2: an alias is validated as the anchored value, at the alias's path."""
    schema = {"properties": {"other": {"properties": {"port": {"type": "string"}}}}}
    error = _violation(make_theatre, "base: &b\n  port: 1\nother: *b\n", schema)

    assert error.key_path == "other.port"


def test_merge_keys_are_validated_as_the_merged_mapping(make_theatre: MakeTheatre) -> None:
    """Req 5.1, 5.2: a mapping with ``<<`` is validated as its merged view: the merged keys are
    present and no ``<<`` key is, so ``additionalProperties: false`` accepts it."""
    schema = {
        "properties": {
            "m": {
                "required": ["port", "name"],
                "properties": {"port": {"type": "integer"}, "name": {"type": "string"}},
                "additionalProperties": False,
            }
        }
    }
    config = "base: &b\n  port: 1\nm:\n  <<: *b\n  name: x\n"
    _load(make_theatre, config, schema)

    stricter = {"properties": {"m": {"properties": {"port": {"type": "string"}}}}}
    error = _violation(make_theatre, config, stricter)
    assert error.key_path == "m.port"


def test_a_text_include_is_validated_as_its_text(make_theatre: MakeTheatre) -> None:
    """Req 5.1, 5.2: a text include is validated as its exact text."""
    schema = {"properties": {"prompt": {"type": "string", "maxLength": 5}}}
    error = _violation(make_theatre, "prompt: file:p.md\n", schema, **{"p.md": "much too long\n"})

    assert error.key_path == "prompt"
    assert error.detail == "'much too long\\n' is too long"


def test_a_literal_is_validated_as_its_string(make_theatre: MakeTheatre) -> None:
    """Req 4.1, 5.1: a ``!literal`` scalar is validated as its text, a string."""
    schema = {"properties": {"n": {"type": "integer"}}}
    error = _violation(make_theatre, "n: !literal 5\n", schema)

    assert (error.key_path, error.detail) == (
        "n",
        "'5' is not of type 'integer'",
    )


def test_a_failure_at_the_root_names_no_key_path(make_theatre: MakeTheatre) -> None:
    """Req 5.2: a failure of the root (here an empty file, an empty mapping) has no key path."""
    error = _violation(make_theatre, "", {"required": ["name"]})

    assert (error.key_path, error.detail) == (
        None,
        "'name' is a required property",
    )


def test_a_schema_that_cannot_be_used_is_a_load_error(make_theatre: MakeTheatre) -> None:
    """Req 5.3: loading reads the schema; an unusable one is a load error naming it."""
    theatre = _theatre(make_theatre, "a: 1\n", '{"type": 5}')

    with pytest.raises(ConfigLoadError) as caught:
        load_config(theatre / ".troupe" / "troupe.yml")

    assert caught.value.file == theatre / ".troupe" / SCHEMA_FILE_NAME
    assert "not a valid JSON Schema 2020-12" in caught.value.problem


def test_an_unresolvable_ref_met_by_validation_is_a_load_error(make_theatre: MakeTheatre) -> None:
    """Req 5.3: a ``$ref`` validation meets and cannot resolve is a load error naming the schema
    file; nothing is retrieved."""
    schema = {"properties": {"a": {"$ref": "#/$defs/missing"}}}
    theatre = _theatre(make_theatre, "a: 1\n", schema)

    with pytest.raises(ConfigLoadError) as caught:
        load_config(theatre / ".troupe" / "troupe.yml")

    assert not isinstance(caught.value, SchemaViolationError)
    assert caught.value.file == theatre / ".troupe" / SCHEMA_FILE_NAME
    assert "cannot resolve $ref" in caught.value.problem


# --- 5.4: no schema file -----------------------------------------------------------------------


def test_without_a_schema_file_loading_is_unchanged(make_theatre: MakeTheatre) -> None:
    """Req 5.4: without a schema file nothing is validated and no literal is found by schema."""
    document = _load(
        make_theatre, "hook: file:inc.md\nport: eighty\n", None, {".troupe/inc.md": "text"}
    )

    assert document.schema is None
    assert _scalar_at(document, "hook").value == "text"


# --- the included-content helper of writers ------------------------------------------------------


def test_load_include_decides_literals_at_the_given_path_with_the_schema(
    make_theatre: MakeTheatre,
) -> None:
    """Req 4.4, 4.6: content loaded for writers is decided like loaded content: at ``path`` with
    the schema, ``x.v`` is a literal; at the root without a schema the same string is an include."""
    schema = {"properties": {"x": {"properties": {"v": URI}}}}
    theatre = _theatre(
        make_theatre, "x: 1\n", schema, {".troupe/t.yml": "v: file:z.md\n", ".troupe/z.md": "z"}
    )
    document = load_config(theatre / ".troupe" / "troupe.yml")
    texts = {file.real_path: file.text for file in document.files}

    literal = load_include(
        document.root_file, "t.yml", texts, path=ConfigPath(("x",)), schema=document.schema
    )
    included = load_include(document.root_file, "t.yml", texts)

    assert isinstance(literal, ConfigMapping) and isinstance(included, ConfigMapping)
    assert _value(literal, "v") == "file:z.md"
    assert _value(included, "v") == "z"


def _value(mapping: ConfigMapping, key: str) -> object:
    entry = mapping.get(key)
    assert entry is not None and isinstance(entry.value, ConfigScalar)
    return entry.value.value


def test_a_new_include_merged_at_another_place_is_decided_by_the_schema(
    make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    """Req 4.4, 4.6: `set x.p file:t.yaml` writes the new include in the shared s.yaml; at `y`,
    which overrides `p`, t.yaml's content is loaded for the merge at `y.p`, where the schema gives
    `v` the ``uri`` format, so ``file:z.md`` (which does not exist) stays a string there too."""
    schema = {
        "additionalProperties": {
            "properties": {"p": {"properties": {"v": URI}}},
        }
    }
    theatre = _theatre(
        make_theatre,
        "x: file:s.yaml\ny:\n  $ref: file:s.yaml\n  p:\n    w: 9\n",
        schema,
        {".troupe/s.yaml": "p: 1\nq: 1\n", ".troupe/t.yaml": "v: file:z.md\n"},
    )

    result = run_cli("config", "set", "x.p", "file:t.yaml", "--theatre", str(theatre))

    assert result.exit_code == 0, result.stderr
    assert (theatre / ".troupe" / "s.yaml").read_text() == "p: file:t.yaml\nq: 1\n"
    shown = run_cli("config", "show", "y.p", "--theatre", str(theatre))
    assert (shown.exit_code, shown.stdout) == (0, b"v: file:z.md\nw: 9\n")


# --- 5.2, 5.3, 5.6: exit 4 through the CLI -----------------------------------------------------

CLI_FAILURES = [
    pytest.param(
        "sub: file:sub.yml\n",
        {"properties": {"sub": {"properties": {"port": {"type": "integer"}}}}},
        "sub.port: the configuration does not match the schema: "
        "'eighty' is not of type 'integer'\n",
        id="violation-in-include",
    ),
    pytest.param(
        "a: 1\n",
        {"required": ["name"]},
        "the configuration does not match the schema: 'name' is a required property\n",
        id="violation-at-root",
    ),
    pytest.param("a: 1\n", "{", "the schema file is not valid JSON", id="not-json"),
    pytest.param(
        "a: 1\n",
        {"$schema": "http://json-schema.org/draft-07/schema#"},
        "the schema's $schema must be",
        id="other-dialect",
    ),
    pytest.param(
        "a: 1\n",
        {"properties": {"a": {"$ref": "https://example.invalid/s.json"}}},
        "cannot resolve $ref",
        id="remote-ref",
    ),
]


@pytest.mark.parametrize("config_name", CONFIG_NAMES)
@pytest.mark.parametrize("command", ["show", "history"])
@pytest.mark.parametrize(("config", "schema", "message"), CLI_FAILURES)
def test_schema_failures_exit_4_naming_the_schema_file_and_changing_nothing(
    make_theatre: MakeTheatre,
    run_cli: RunCli,
    config_name: str,
    command: str,
    config: str,
    schema: object,
    message: str,
) -> None:
    """Req 5.2, 5.3, 5.6: an invalid configuration or an unusable schema exits 4 with nothing on
    stdout and stderr naming the schema file (and the failing key path); no history state is
    created and no file, the schema included, is created, changed or deleted."""
    theatre = _theatre(
        make_theatre, config, schema, {".troupe/sub.yml": "port: eighty\n"}, config_name
    )
    before = _snapshot(theatre)

    result = run_cli("config", command, "--theatre", str(theatre))

    assert result.exit_code == ExitCode.LOAD, result.stderr
    assert result.stdout == b""
    assert result.stderr.startswith(f"{theatre / '.troupe' / SCHEMA_FILE_NAME}: {message}")
    assert result.stderr.count("\n") == 1
    assert _snapshot(theatre) == before


@pytest.mark.parametrize("config_name", CONFIG_NAMES)
def test_a_valid_configuration_with_a_schema_literal_prints_the_string(
    make_theatre: MakeTheatre, run_cli: RunCli, config_name: str
) -> None:
    """Req 4.4, 4.6, 5.1: through the CLI, a configuration that matches loads, and a ``uri``
    string prints as the string; the schema file is never changed."""
    schema = {"properties": {"hook": URI, "n": {"type": "integer"}}}
    theatre = _theatre(make_theatre, "hook: file:inc.md\nn: 1\n", schema, None, config_name)
    schema_bytes = (theatre / ".troupe" / SCHEMA_FILE_NAME).read_bytes()

    result = run_cli("config", "get", "hook", "--theatre", str(theatre))

    assert (result.exit_code, result.stdout, result.stderr) == (0, b"file:inc.md\n", "")
    assert (theatre / ".troupe" / SCHEMA_FILE_NAME).read_bytes() == schema_bytes
