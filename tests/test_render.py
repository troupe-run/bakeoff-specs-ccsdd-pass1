"""Output rendering for ``show`` and ``history`` (6.1-6.6, 8.2, 8.6, 8.7)."""

import json
import math
from typing import Any

import pytest

from troupe.cli.render import format_history_line, format_show_output
from troupe.config.history import HistoryOp, HistoryRecord
from troupe.config.scalars import ScalarValue


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("hello", b"hello\n"),
        ("hello\n", b"hello\n"),
        ("two\nlines", b"two\nlines\n"),
        ("crlf\r\n", b"crlf\r\n"),
        ("cr only\r", b"cr only\r\n"),
        ("", b"\n"),
        ("\n", b"\n"),
        ("ends twice\n\n", b"ends twice\n\n"),
        ("café ☃", "café ☃\n".encode()),
        ("true", b"true\n"),
        (True, b"true\n"),
        (False, b"false\n"),
        (None, b"null\n"),
        (0, b"0\n"),
        (42, b"42\n"),
        (-7, b"-7\n"),
        (255, b"255\n"),
        (0.2, b"0.2\n"),
        (1.0, b"1.0\n"),
        (-0.0, b"-0.0\n"),
        (1e100, b"1e+100\n"),
        (math.inf, b"inf\n"),
        (-math.inf, b"-inf\n"),
        (math.nan, b"nan\n"),
    ],
)
def test_show_output_formats_each_scalar(value: ScalarValue, expected: bytes) -> None:
    assert format_show_output(value) == expected


def test_show_output_is_bytes_with_booleans_never_printed_as_integers() -> None:
    assert isinstance(format_show_output(True), bytes)
    assert format_show_output(True) != format_show_output(1)
    assert format_show_output(False) != format_show_output(0)


def _reject_constant(token: str) -> Any:
    raise ValueError(f"non-JSON constant {token}")


def _strict_parse(line: bytes) -> Any:
    """Parse one line with a strict JSON parser (no NaN/Infinity tokens, UTF-8 input)."""
    text = line.decode("utf-8", errors="strict")
    return json.loads(text, parse_constant=_reject_constant)


def _record(
    old: ScalarValue, new: ScalarValue, op: HistoryOp = "set", path: str = "a.b"
) -> HistoryRecord:
    return HistoryRecord(seq=3, op=op, path=path, old=old, new=new)


def test_history_line_has_members_in_order_and_ends_with_one_newline() -> None:
    line = format_history_line(_record(old="x", new="y"))
    assert isinstance(line, bytes)
    assert line.endswith(b"\n")
    assert line.count(b"\n") == 1
    parsed = _strict_parse(line)
    assert list(parsed) == ["seq", "op", "path", "old", "new"]
    assert parsed == {"seq": 3, "op": "set", "path": "a.b", "old": "x", "new": "y"}


@pytest.mark.parametrize(
    "value",
    ["text", "", "multi\nline\r\n", "café ☃ \U0001f600", '"quoted" \\', None, 0, -12, 3.5],
)
def test_history_line_values_parse_back_equal(value: ScalarValue) -> None:
    parsed = _strict_parse(format_history_line(_record(old=value, new=value)))
    assert parsed["old"] == value
    assert parsed["new"] == value
    assert type(parsed["old"]) is type(value)


@pytest.mark.parametrize("value", [True, False])
def test_history_line_booleans_are_json_booleans(value: bool) -> None:
    line = format_history_line(_record(old=value, new=not value))
    parsed = _strict_parse(line)
    assert parsed["old"] is value
    assert parsed["new"] is (not value)
    assert b"true" in line
    assert b"false" in line


@pytest.mark.parametrize(
    ("value", "expected"),
    [(math.inf, ".inf"), (-math.inf, "-.inf"), (math.nan, ".nan")],
)
def test_history_line_non_finite_floats_are_canonical_yaml_strings(
    value: float, expected: str
) -> None:
    line = format_history_line(_record(op="import", old=None, new=value))
    parsed = _strict_parse(line)
    assert parsed == {"seq": 3, "op": "import", "path": "a.b", "old": None, "new": expected}
    removed = _strict_parse(format_history_line(_record(op="remove", old=value, new=None)))
    assert removed["old"] == expected
    assert removed["new"] is None


def test_history_line_keeps_finite_float_values_exactly() -> None:
    for value in (0.1, -0.0, 1e300, 5e-324):
        parsed = _strict_parse(format_history_line(_record(old=value, new=value)))
        assert parsed["new"] == value
        assert math.copysign(1.0, parsed["new"]) == math.copysign(1.0, value)


def test_history_line_path_and_strings_round_trip_with_special_characters() -> None:
    record = HistoryRecord(seq=1, op="import", path="café.☃", old=None, new="a\tb\x00")
    parsed = _strict_parse(format_history_line(record))
    assert parsed["path"] == "café.☃"
    assert parsed["new"] == "a\tb\x00"
