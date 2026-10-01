"""YAML 1.2 core-schema typing of plain scalars (Requirement 3.1)."""

import math

import pytest

from troupe.config.scalars import ScalarValue, resolve_plain_scalar

NAN = float("nan")

CORE_SCHEMA_TABLE: list[tuple[str, ScalarValue]] = [
    # null
    ("", None),
    ("~", None),
    ("null", None),
    ("Null", None),
    ("NULL", None),
    ("nULL", "nULL"),
    # booleans
    ("true", True),
    ("True", True),
    ("TRUE", True),
    ("false", False),
    ("False", False),
    ("FALSE", False),
    ("tRUE", "tRUE"),
    # YAML 1.1 booleans are strings under the 1.2 core schema
    ("yes", "yes"),
    ("no", "no"),
    ("on", "on"),
    ("off", "off"),
    ("y", "y"),
    # decimal integers
    ("0", 0),
    ("42", 42),
    ("-17", -17),
    ("+1", 1),
    ("012", 12),
    ("-0", 0),
    # octal and hexadecimal integers
    ("0o17", 15),
    ("0o0", 0),
    ("0x1F", 31),
    ("0xff", 255),
    ("0o8", "0o8"),
    ("0xG", "0xG"),
    ("0O17", "0O17"),
    ("0X1F", "0X1F"),
    ("-0o17", "-0o17"),
    ("+0x1F", "+0x1F"),
    ("0b101", "0b101"),
    # YAML 1.1 forms that are strings under the 1.2 core schema
    ("1_0", "1_0"),
    ("1_000.5", "1_000.5"),
    ("2001-12-14", "2001-12-14"),
    ("2001-12-14t21:59:43.10-05:00", "2001-12-14t21:59:43.10-05:00"),
    ("1:30", "1:30"),
    ("017_", "017_"),
    # floats
    ("1e3", 1000.0),
    ("1.5", 1.5),
    ("-1.5", -1.5),
    ("+.5", 0.5),
    (".5", 0.5),
    ("1.", 1.0),
    ("1.e2", 100.0),
    ("2E-2", 0.02),
    ("-1e+3", -1000.0),
    (".", "."),
    ("e3", "e3"),
    ("1e", "1e"),
    ("1.5.2", "1.5.2"),
    # infinities and NaN
    (".inf", math.inf),
    (".Inf", math.inf),
    (".INF", math.inf),
    ("+.inf", math.inf),
    ("-.inf", -math.inf),
    ("-.Inf", -math.inf),
    (".iNF", ".iNF"),
    ("inf", "inf"),
    (".nan", NAN),
    (".NaN", NAN),
    (".NAN", NAN),
    ("-.nan", "-.nan"),
    ("+.nan", "+.nan"),
    (".nAN", ".nAN"),
    ("nan", "nan"),
    # other strings, including near-misses with surrounding text
    ("hello", "hello"),
    ("1 2", "1 2"),
    (" 1", " 1"),
    ("1\n", "1\n"),
    ("true\n", "true\n"),
    ("file:x.yaml", "file:x.yaml"),
]


@pytest.mark.parametrize(("text", "expected"), CORE_SCHEMA_TABLE, ids=repr)
def test_plain_scalar_is_typed_by_the_core_schema(text: str, expected: ScalarValue) -> None:
    result = resolve_plain_scalar(text)

    assert type(result) is type(expected)
    if isinstance(expected, float) and math.isnan(expected):
        assert isinstance(result, float)
        assert math.isnan(result)
    else:
        assert result == expected


@pytest.mark.parametrize("text", ["1_0", "2001-12-14", "yes", "0o17", "1e3", ".inf", "true", "~"])
def test_results_are_plain_builtins(text: str) -> None:
    result = resolve_plain_scalar(text)

    assert type(result) in {str, int, float, bool, type(None)}


def test_string_result_is_the_text_unchanged() -> None:
    text = "  spaced text  "

    assert resolve_plain_scalar(text) == text
