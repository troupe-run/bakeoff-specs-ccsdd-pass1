"""The configuration's JSON Schema: reading it, the ``format: uri`` walker and validation.

This module is the only JSON Schema code (config-remote-schema Requirements 4.4, 4.5, 5.1-5.5).

* :func:`read_schema` reads ``<config dir>/troupe.schema.json``. An absent file means no schema;
  a file that cannot be used is a :class:`ConfigLoadError` naming it.
* :func:`uri_format_at` says whether the schema gives a path's subschema ``"format": "uri"``. It
  walks ``properties``, ``items`` (digit-only segments) and ``additionalProperties`` only, each
  only when it is an object subschema; ``$ref``, combinators, ``patternProperties`` and
  ``prefixItems`` are never followed.
* :func:`validate` checks the expanded configuration with the 2020-12 validator. ``format`` is an
  annotation (no format checker). One failure, the best match, is reported. No ``$ref`` is ever
  retrieved: an unresolvable one, local or remote, is a :class:`ConfigLoadError` naming the schema
  file.
"""

import json
import os
from pathlib import Path

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, best_match
from referencing import Registry
from referencing.exceptions import NoSuchAnchor, PointerToNowhere, Unresolvable

from troupe.config.constants import PATH_SEPARATOR, SCHEMA_DIALECT, SCHEMA_FILE_NAME
from troupe.config.errors import ConfigLoadError, SchemaViolationError
from troupe.config.model import ConfigMapping, ConfigNode, ConfigScalar, JsonValue, SchemaSource
from troupe.config.paths import ConfigPath, is_index_segment

_URI_FORMAT = "uri"
_NOT_A_SCHEMA = "the schema must be a JSON object or boolean"


def read_schema(config_dir: Path) -> SchemaSource | None:
    """Read and check ``config_dir/troupe.schema.json``; ``None`` when the file does not exist.

    Raises :class:`ConfigLoadError` naming the schema file when it is not a regular file, cannot
    be read, is not UTF-8 or JSON, is not an object or boolean, has a ``$schema`` other than the
    2020-12 dialect, or is not a valid 2020-12 schema.
    """
    path = config_dir / SCHEMA_FILE_NAME
    if not os.path.lexists(path):
        return None
    if not path.is_file():
        raise ConfigLoadError("the schema file does not exist or is not a regular file", file=path)
    try:
        data = path.read_bytes()
    except OSError as error:
        reason = error.strerror or type(error).__name__
        raise ConfigLoadError(f"cannot read the schema file: {reason}", file=path) from error
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ConfigLoadError(
            f"the schema file is not valid UTF-8 (invalid byte at offset {error.start})", file=path
        ) from error
    try:
        contents: JsonValue = json.loads(text)
    except json.JSONDecodeError as error:
        raise ConfigLoadError(
            f"the schema file is not valid JSON: {error.msg} "
            f"(line {error.lineno}, column {error.colno})",
            file=path,
        ) from error
    if not isinstance(contents, dict | bool):
        raise ConfigLoadError(_NOT_A_SCHEMA, file=path)
    if isinstance(contents, dict) and "$schema" in contents:
        dialect = contents["$schema"]
        if dialect != SCHEMA_DIALECT:
            raise ConfigLoadError(
                f"the schema's $schema must be {SCHEMA_DIALECT!r}, not {dialect!r}", file=path
            )
    try:
        Draft202012Validator.check_schema(contents)
    except SchemaError as error:
        raise ConfigLoadError(
            f"the schema is not a valid JSON Schema 2020-12 document: {error.message}", file=path
        ) from error
    return SchemaSource(path=path, text=text, contents=contents)


def uri_format_at(schema: SchemaSource | None, path: ConfigPath) -> bool:
    """Whether the subschema the schema gives ``path`` has ``format`` exactly ``"uri"``."""
    if schema is None:
        return False
    subschema = schema.contents
    for segment in path.segments:
        if not isinstance(subschema, dict):
            return False
        subschema = _step(subschema, segment)
    return isinstance(subschema, dict) and subschema.get("format") == _URI_FORMAT


def _step(subschema: dict[str, JsonValue], segment: str) -> JsonValue:
    """The first object subschema of ``properties[segment]``, ``items`` (digit-only segments)
    and ``additionalProperties``; ``None`` when there is none."""
    properties = subschema.get("properties")
    candidates: list[JsonValue] = []
    if isinstance(properties, dict):
        candidates.append(properties.get(segment))
    if is_index_segment(segment):
        candidates.append(subschema.get("items"))
    candidates.append(subschema.get("additionalProperties"))
    for candidate in candidates:
        if isinstance(candidate, dict):
            return candidate
    return None


def plain_json(node: ConfigNode) -> JsonValue:
    """Return the plain JSON value of the expanded configuration node ``node``."""
    if isinstance(node, ConfigScalar):
        return node.value
    if isinstance(node, ConfigMapping):
        return {entry.key: plain_json(entry.value) for entry in node.entries}
    return [plain_json(item) for item in node.items]


def validate(schema: SchemaSource, root: ConfigMapping) -> None:
    """Validate the expanded configuration ``root`` against ``schema``.

    Raises :class:`SchemaViolationError` (the schema file, the dotted key path of the best
    matching failure or ``None`` at the root, and its message) when it does not match, and
    :class:`ConfigLoadError` naming the schema file when validation meets a ``$ref`` that
    cannot be resolved. Nothing is retrieved over the network.
    """
    # An explicit empty registry: without one, jsonschema falls back to its deprecated resolver,
    # which retrieves remote ``$ref`` targets over HTTP.
    contents = schema.contents
    if not isinstance(contents, dict | bool):  # read_schema never returns one
        raise ConfigLoadError(_NOT_A_SCHEMA, file=schema.path)
    validator = Draft202012Validator(contents, registry=Registry())
    try:
        error = best_match(validator.iter_errors(plain_json(root)))
    except Unresolvable as unresolvable:
        raise ConfigLoadError(_unresolvable_problem(unresolvable), file=schema.path) from None
    if error is None:
        return
    segments = [str(part) for part in error.absolute_path]
    key_path = PATH_SEPARATOR.join(segments) if segments else None
    raise SchemaViolationError(error.message, file=schema.path, key_path=key_path)


def _unresolvable_problem(error: Unresolvable) -> str:
    """Describe a ``$ref`` that cannot be resolved, without the schema's contents."""
    # jsonschema wraps the referencing error and chains the original as its cause.
    cause = error.__cause__ if isinstance(error.__cause__, Unresolvable) else error
    if isinstance(cause, PointerToNowhere):
        return f"cannot resolve $ref: the JSON pointer {cause.ref!r} points to nothing"
    if isinstance(cause, NoSuchAnchor):
        return f"cannot resolve $ref: there is no anchor {cause.anchor!r}"
    return f"cannot resolve $ref {cause.ref!r}: other schemas are never retrieved"
