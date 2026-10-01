"""Unit tests of the ValueParser (``troupe.config.writeback.value.parse_value``).

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

import math

import pytest

from troupe.config.errors import ExitCode, UsageError
from troupe.config.paths import ConfigPath
from troupe.config.writeback.value import (
    NewMapping,
    NewScalar,
    NewSequence,
    NewValue,
    parse_value,
)

PATH = ConfigPath.parse("db.pool.size")


def _parse(text: str) -> NewValue:
    return parse_value(text, PATH)


def _refusal(text: str) -> UsageError:
    with pytest.raises(UsageError) as caught:
        _parse(text)
    error = caught.value
    assert error.exit_code is ExitCode.USAGE
    assert error.path == "db.pool.size"
    assert "db.pool.size" in str(error)
    return error


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("0.2", NewScalar(0.2, "0.2")),
        ("true", NewScalar(True, "true")),
        ("null", NewScalar(None, "null")),
        ("~", NewScalar(None, "~")),
        ("5", NewScalar(5, "5")),
        ("0x1F", NewScalar(31, "0x1F")),
        ("0o17", NewScalar(15, "0o17")),
        ("-1e3", NewScalar(-1000.0, "-1e3")),
        ("abc", NewScalar("abc", None)),
        ("1_0", NewScalar("1_0", None)),
        ("2001-12-14", NewScalar("2001-12-14", None)),
        ('"0.2"', NewScalar("0.2", None)),
        ("'true'", NewScalar("true", None)),
        ('""', NewScalar("", None)),
        ('"line one\\nline two\\n"', NewScalar("line one\nline two\n", None)),
        ("  0x1F  # comment", NewScalar(31, "0x1F")),
        ("file:agents.yml", NewScalar("file:agents.yml", None)),
    ],
)
def test_scalars_are_typed_by_the_core_schema_and_keep_their_spelling(
    text: str, expected: NewScalar
) -> None:
    """Req 1.2: plain scalars are typed by the core schema; quoted scalars are strings."""
    result = _parse(text)

    assert result == expected
    assert isinstance(result, NewScalar)
    assert type(result.value) is type(expected.value)


def test_special_floats_are_typed() -> None:
    """Req 1.2: ``.inf``/``-.inf``/``.nan`` are core-schema floats with their spelling."""
    assert _parse(".inf") == NewScalar(math.inf, ".inf")
    assert _parse("-.inf") == NewScalar(-math.inf, "-.inf")
    nan = _parse(".nan")
    assert isinstance(nan, NewScalar)
    assert isinstance(nan.value, float) and math.isnan(nan.value)
    assert nan.spelling == ".nan"


def test_flow_mapping_is_parsed_in_value_order() -> None:
    """Req 1.2: ``{a: 1}`` is a mapping; entries keep VALUE order."""
    assert _parse("{a: 1}") == NewMapping((("a", NewScalar(1, "1")),))
    assert _parse("{b: x, a: 'y', c: }") == NewMapping(
        (
            ("b", NewScalar("x", None)),
            ("a", NewScalar("y", None)),
            ("c", NewScalar(None, "")),
        )
    )


def test_flow_list_is_parsed() -> None:
    """Req 1.2: ``[a, b]`` is a list of strings."""
    assert _parse("[a, b]") == NewSequence((NewScalar("a", None), NewScalar("b", None)))
    assert _parse("[]") == NewSequence(())
    assert _parse("{}") == NewMapping(())


def test_nested_flow_collections_are_parsed_recursively() -> None:
    """Req 1.2: flow mappings and lists nest, across lines too."""
    assert _parse('{a: [1, {b: "x"}],\n c: [true, ~]}') == NewMapping(
        (
            (
                "a",
                NewSequence((NewScalar(1, "1"), NewMapping((("b", NewScalar("x", None)),)))),
            ),
            ("c", NewSequence((NewScalar(True, "true"), NewScalar(None, "~")))),
        )
    )


def test_keys_are_the_key_scalar_text() -> None:
    """Req 1.2: a key is the text of its scalar, whatever it would type as."""
    assert _parse("{1: a, 'true': b, \"x y\": c}") == NewMapping(
        (
            ("1", NewScalar("a", None)),
            ("true", NewScalar("b", None)),
            ("x y", NewScalar("c", None)),
        )
    )


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "# only a comment",
        "\n# one\n  # two\n",
        "a: 1",
        "- a\n- b",
        "|\n  text\n",
        ">\n  text\n",
        "{a: 1}: x",
        "a\n---\nb",
        "--- a\n--- b",
        "[a, b",
        "{a: 1",
        '"unterminated',
        "!!str 5",
        "!t x",
        "! x",
        "!!seq [a]",
        "[a, !!int 1]",
        "{a: !t x}",
        "{!!str k: 1}",
        "&a x",
        "[&a x, *a]",
        "{&k a: 1}",
        "*a",
        "[*a]",
        "a\udcff",
        "\udc80",
    ],
)
def test_values_that_are_not_a_single_flow_node_are_refused(text: str) -> None:
    """Req 1.3: invalid YAML, empty/comment-only, block nodes, several documents, tags, anchors,
    aliases and text that cannot be encoded as UTF-8 raise a usage error naming PATH."""
    error = _refusal(text)

    assert "VALUE is not a single flow node" in error.problem


@pytest.mark.parametrize(
    ("text", "key"),
    [
        ("{a: 1, a: 2}", "'a'"),
        ("{a: {b: 1, 'b': 2}}", "'b'"),
        ("[{x: 1, x: 2}]", "'x'"),
        ("{true: 1, True: 2}", "'True'"),
        ("{~: 1, null: 2}", "'null'"),
        ('{"": 1}', "''"),
        ("{: 1}", "''"),
        ("{[x]: 1}", "[x]"),
        ("{{a: 1}: 2}", "{a: 1}"),
    ],
)
def test_bad_keys_are_refused_naming_the_key(text: str, key: str) -> None:
    """Req 1.4: duplicate, empty or non-scalar keys raise a usage error naming PATH and the key."""
    error = _refusal(text)

    assert key in error.problem


@pytest.mark.parametrize("text", ["a\udcff", '"\udc80"', "[x, \udce9]"])
def test_text_that_cannot_be_encoded_as_utf8_is_refused_as_such(text: str) -> None:
    """Req 1.3: surrogate escapes from argv (bytes that are not UTF-8) are named as the problem."""
    error = _refusal(text)

    assert "cannot be encoded as UTF-8" in error.problem
