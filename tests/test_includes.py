"""Include recognition and the step 7 single-place names (config-remote-schema).

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

from pathlib import Path

import pytest

from troupe.config import constants
from troupe.config.errors import (
    ConfigLoadError,
    SchemaViolationError,
    TroupeError,
    WriteRefusedError,
)
from troupe.config.includes import include_is_yaml, is_include_string, is_remote_include
from troupe.config.model import (
    CollectionStyle,
    ConfigDocument,
    ConfigMapping,
    ConfigScalar,
    IncludeKind,
    IncludeRef,
    LineEnding,
    Origin,
    ScalarStyle,
    SchemaSource,
    SourceFile,
    Span,
)
from troupe.config.scalars import ScalarValue


def test_step_7_constants_hold_the_documented_names() -> None:
    """Req 1.1, 1.2: remote prefixes, literal tag, schema file, its dialect and the timeout."""
    assert constants.REMOTE_INCLUDE_PREFIXES == ("https://", "http://")
    assert constants.LITERAL_TAG == "!literal"
    assert constants.SCHEMA_FILE_NAME == "troupe.schema.json"
    assert constants.SCHEMA_DIALECT == "https://json-schema.org/draft/2020-12/schema"
    assert constants.REMOTE_FETCH_TIMEOUT_SECONDS == 10


@pytest.mark.parametrize(
    "value",
    [
        "file:inc.yaml",
        "file:",
        "https://example.test/shared.yml",
        "https://",
        "http://example.test/shared.txt",
        "http://",
    ],
)
def test_strings_starting_with_an_include_prefix_are_include_strings(value: str) -> None:
    """Req 1.1: a string starting with `file:`, `https://` or `http://` would be an include."""
    assert is_include_string(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "FILE:inc.yaml",
        "File:inc.yaml",
        "HTTPS://example.test/a.yml",
        "Https://example.test/a.yml",
        "HTTP://example.test/a.yml",
        "https:/example.test/a.yml",
        "http:example.test",
        "ftp://example.test/a.yml",
        " https://example.test/a.yml",
        " file:inc.yaml",
        "see https://example.test",
        "",
    ],
)
def test_prefixes_are_case_sensitive_and_must_start_the_string(value: str) -> None:
    """Req 1.1: include prefixes are case-sensitive and only count at the start of the string."""
    assert is_include_string(value) is False


@pytest.mark.parametrize("value", [None, True, False, 0, 1, 1.5])
def test_non_string_values_are_never_include_strings(value: ScalarValue) -> None:
    """Req 1.1: only string values can be include strings."""
    assert is_include_string(value) is False


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("https://example.test/a.yml", True),
        ("http://example.test/a.yml", True),
        ("https://", True),
        ("file:https://example.test/a.yml", False),
        ("file:inc.yaml", False),
        ("HTTPS://example.test/a.yml", False),
        ("HTTP://example.test/a.yml", False),
        ("ftp://example.test/a.yml", False),
    ],
)
def test_only_https_and_http_prefixes_are_remote(text: str, expected: bool) -> None:
    """Req 1.1: a remote include starts with `https://` or `http://` (case-sensitive)."""
    assert is_remote_include(text) is expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://example.test/shared.yml", True),
        ("https://example.test/shared.yaml", True),
        ("http://example.test/dir/shared.yaml", True),
        ("https://example.test/shared.yml?v=2", True),
        ("https://example.test/shared.yaml#section", True),
        ("https://example.test/shared.yml?format=txt#frag", True),
        ("https://example.test/shared.txt?file=a.yml", False),
        ("https://example.test/shared.txt#a.yaml", False),
        ("https://example.test/shared?x=.yml", False),
        ("https://example.test/shared.YML", False),
        ("https://example.test/shared.Yaml", False),
        ("https://example.test/shared.txt", False),
        ("https://example.test/shared", False),
        ("https://example.test/", False),
        ("https://example.test", False),
        ("https://example.test/shared.yml/", False),
        ("https://example.test/shared.json", False),
    ],
)
def test_remote_yaml_is_decided_by_the_url_path_suffix(url: str, expected: bool) -> None:
    """Req 1.2: the URL path, without query and fragment, ends `.yml`/`.yaml` (case-sensitive)."""
    assert include_is_yaml(url) is expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("file:inc.yml", True),
        ("file:inc.yaml", True),
        ("file:sub/dir/inc.yaml", True),
        ("file:../inc.yml", True),
        ("file:inc.YML", False),
        ("file:inc.Yaml", False),
        ("file:inc.txt", False),
        ("file:inc", False),
        ("file:inc.yml.txt", False),
        ("file:inc.yml?x", False),
        ("file:inc.yml#x", False),
        ("file:yml", False),
    ],
)
def test_file_includes_keep_the_name_suffix_rule(text: str, expected: bool) -> None:
    """Req 1.2: a `file:` target is YAML by its name's suffix, exactly as before step 7."""
    assert include_is_yaml(text) is expected


URL = "https://example.test/shared/base.yml?v=1"


def _local(tmp_path: Path) -> SourceFile:
    real = tmp_path / "inc.yaml"
    return SourceFile(
        path=Path("theatre/.troupe/../inc.yaml"),
        real_path=real,
        text="a: 1\n",
        line_ending=LineEnding.LF,
    )


def _remote() -> SourceFile:
    return SourceFile(
        path=Path(URL), real_path=Path(URL), text="a: 1\n", line_ending=LineEnding.LF, url=URL
    )


def _including() -> SourceFile:
    path = Path("theatre/.troupe/troupe.yaml")
    return SourceFile(path=path, real_path=path, text="x: 1\n", line_ending=LineEnding.LF)


def test_a_local_source_is_identified_by_its_real_path_and_shown_as_opened(tmp_path: Path) -> None:
    """Req 10.2: a local source keeps no URL, its key is the real path, it is shown as opened."""
    source = _local(tmp_path)
    assert source.url is None
    assert source.key == tmp_path / "inc.yaml"
    assert source.shown == Path("theatre/.troupe/../inc.yaml")


def test_a_remote_source_is_identified_and_shown_by_its_url() -> None:
    """Req 10.2: a remote source's key and shown name are its URL, not the stand-in path."""
    source = _remote()
    assert source.key == URL
    assert source.shown == URL
    assert isinstance(source.shown, str)


def test_include_string_is_file_prefix_and_spec_for_a_local_include(tmp_path: Path) -> None:
    """Req 10.2: a local include's whole string is `file:` + spec, as rebuilt before step 7."""
    directive = Origin(_including(), Span(3, 15))
    ref = IncludeRef(IncludeKind.YAML, "../inc.yaml", _local(tmp_path), directive)
    assert ref.string == "file:../inc.yaml"


def test_include_string_is_the_url_for_a_remote_include() -> None:
    """Req 10.2: a remote include's whole string is its URL (the spec), with no `file:` prefix."""
    directive = Origin(_including(), Span(3, 40))
    ref = IncludeRef(IncludeKind.YAML, URL, _remote(), directive)
    assert ref.string == URL


def test_new_model_fields_default_to_none(tmp_path: Path) -> None:
    """Req 10.2: existing constructions are unchanged; literal, schema and url default to none."""
    root_file = _including()
    scalar = ConfigScalar(1, ScalarStyle.PLAIN, Origin(root_file, Span(3, 4)))
    root = ConfigMapping((), CollectionStyle.BLOCK, Origin(root_file, Span(0, 0)))
    document = ConfigDocument(root_file, root, (root_file,))
    assert scalar.literal is None
    assert document.schema is None
    assert root_file.url is None


def test_a_scalar_and_a_document_carry_a_literal_span_and_a_schema() -> None:
    """Req 10.2: a scalar records its `!literal` span; a document the schema it was checked by."""
    root_file = _including()
    scalar = ConfigScalar(
        "file:x", ScalarStyle.PLAIN, Origin(root_file, Span(12, 18)), literal=Span(3, 11)
    )
    schema = SchemaSource(Path("theatre/.troupe/troupe.schema.json"), "{}", {})
    root = ConfigMapping((), CollectionStyle.BLOCK, Origin(root_file, Span(0, 0)))
    document = ConfigDocument(root_file, root, (root_file,), schema=schema)
    assert scalar.literal == Span(3, 11)
    assert document.schema is schema


@pytest.mark.parametrize("error_type", [ConfigLoadError, WriteRefusedError])
def test_an_error_names_a_url_as_its_file(error_type: type[TroupeError]) -> None:
    """Req 10.2: errors accept a URL as their file and name it in the message."""
    error = error_type("cannot be loaded", file=URL, path="a.b")
    assert error.file == URL
    assert str(error) == f"{URL}: a.b: cannot be loaded"


def test_a_schema_violation_keeps_its_key_path_and_detail() -> None:
    """Req 10.2: a schema violation is a load error (exit 4) keeping key path and detail."""
    schema = Path("theatre/.troupe/troupe.schema.json")
    error = SchemaViolationError("'x' is not of type 'integer'", file=schema, key_path="a.0.b")
    assert isinstance(error, ConfigLoadError)
    assert error.exit_code == 4
    assert error.key_path == "a.0.b"
    assert error.detail == "'x' is not of type 'integer'"
    assert str(error) == (
        f"{schema}: a.0.b: the configuration does not match the schema: "
        "'x' is not of type 'integer'"
    )


def test_a_schema_violation_at_the_root_has_no_key_path() -> None:
    """Req 10.2: a failure at the root names only the schema file and the problem."""
    schema = Path("troupe.schema.json")
    error = SchemaViolationError("'a' is a required property", file=schema, key_path=None)
    assert error.key_path is None
    assert str(error) == (
        "troupe.schema.json: the configuration does not match the schema: "
        "'a' is a required property"
    )
