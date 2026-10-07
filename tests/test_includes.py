"""Include recognition and the step 7 single-place names (config-remote-schema).

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

import pytest

from troupe.config import constants
from troupe.config.includes import include_is_yaml, is_include_string, is_remote_include
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
