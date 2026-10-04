"""Output rendering for ``get``/``show`` scalars and ``history`` (6.1-6.6, 8.2, 8.6, 8.7).

config-lineage 1.1, 1.2, 1.5, 1.7: every history line ends with the five lineage members.
"""

import json
import math
from typing import Any

import pytest

from support import FIXED_LINEAGE, step1_members
from troupe.cli.render import format_history_line, format_show_output
from troupe.config.history import HistoryOp, HistoryRecord
from troupe.config.lineage import Actor, Lineage
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
    return HistoryRecord(seq=3, op=op, path=path, old=old, new=new, lineage=FIXED_LINEAGE)


def test_history_line_has_members_in_order_and_ends_with_one_newline() -> None:
    line = format_history_line(_record(old="x", new="y"))
    assert isinstance(line, bytes)
    assert line.endswith(b"\n")
    assert line.count(b"\n") == 1
    assert step1_members(line) == {"seq": 3, "op": "set", "path": "a.b", "old": "x", "new": "y"}


def test_history_line_is_exactly_step1_members_then_lineage_members() -> None:
    """config-lineage 1.1, 1.2, 1.7: the exact line, lineage after ``new`` in its fixed order."""
    line = format_history_line(_record(old="x", new="y"))
    assert line == (
        b'{"seq": 3, "op": "set", "path": "a.b", "old": "x", "new": "y", '
        b'"actor": {"kind": "human", "id": "alice"}, "via": "cli", '
        b'"at": "2026-10-04T09:05:07.123456Z", "reason": "a fixed reason", '
        b'"change": "fixed-change"}\n'
    )
    parsed = _strict_parse(line)
    assert list(parsed) == [
        "seq",
        "op",
        "path",
        "old",
        "new",
        "actor",
        "via",
        "at",
        "reason",
        "change",
    ]
    assert list(parsed["actor"]) == ["kind", "id"]


def test_history_line_prints_null_actor_id_and_null_reason_as_json_null() -> None:
    """config-lineage 1.2, 1.5: an unknown id and an absent reason are JSON ``null``."""
    lineage = Lineage(
        actor=Actor(kind="unknown", id=None),
        via="import",
        at="2026-01-02T03:04:05.000000Z",
        reason=None,
        change="c-1",
    )
    record = HistoryRecord(seq=1, op="import", path="a", old=None, new=1, lineage=lineage)
    line = format_history_line(record)
    assert line.endswith(
        b'"actor": {"kind": "unknown", "id": null}, "via": "import", '
        b'"at": "2026-01-02T03:04:05.000000Z", "reason": null, "change": "c-1"}\n'
    )
    parsed = _strict_parse(line)
    assert parsed["actor"] == {"kind": "unknown", "id": None}
    assert parsed["reason"] is None


@pytest.mark.parametrize(
    "reason",
    ['say "hi"', "two\nlines\r\n", "café ☃ \U0001f600", "back\\slash\ttab", ""],
)
def test_history_line_reason_with_special_characters_stays_one_strict_json_line(
    reason: str,
) -> None:
    """config-lineage 1.5: any reason text is printed as a JSON string on one line, verbatim."""
    lineage = Lineage(
        actor=Actor(kind="agent", id="planner:v2"),
        via="cli",
        at="2026-01-02T03:04:05.000000Z",
        reason=reason,
        change="c-2",
    )
    record = HistoryRecord(seq=2, op="set", path="a", old=1, new=2, lineage=lineage)
    line = format_history_line(record)
    assert line.count(b"\n") == 1
    parsed = _strict_parse(line)
    assert parsed["reason"] == reason
    assert parsed["actor"] == {"kind": "agent", "id": "planner:v2"}


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
    assert step1_members(line) == {
        "seq": 3,
        "op": "import",
        "path": "a.b",
        "old": None,
        "new": expected,
    }
    removed = _strict_parse(format_history_line(_record(op="remove", old=value, new=None)))
    assert removed["old"] == expected
    assert removed["new"] is None


def test_history_line_keeps_finite_float_values_exactly() -> None:
    for value in (0.1, -0.0, 1e300, 5e-324):
        parsed = _strict_parse(format_history_line(_record(old=value, new=value)))
        assert parsed["new"] == value
        assert math.copysign(1.0, parsed["new"]) == math.copysign(1.0, value)


def test_history_line_path_and_strings_round_trip_with_special_characters() -> None:
    record = HistoryRecord(
        seq=1, op="import", path="café.☃", old=None, new="a\tb\x00", lineage=FIXED_LINEAGE
    )
    parsed = _strict_parse(format_history_line(record))
    assert parsed["path"] == "café.☃"
    assert parsed["new"] == "a\tb\x00"
