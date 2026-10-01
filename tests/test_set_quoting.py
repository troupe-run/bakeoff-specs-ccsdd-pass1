"""End-to-end validation of scalar quoting and sibling styles of `troupe config set` (5.3).

Every case runs the real ``troupe`` console script (``set_support``). PATH and VALUE are the
exact argv strings, written as Python literals; the fixture and the expected text are the exact
bytes of ``.troupe/troupe.yml`` (UTF-8). Each case asserts exit 0, empty stdout, empty stderr,
the exact bytes of troupe.yml and that no other file was added, removed or changed.

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

from pathlib import Path

import pytest

from set_support import TreeSnapshot, assert_file_bytes, run_set, snapshot_tree
from support import CliResult, MakeTheatre

ROOT = ".troupe/troupe.yml"


def _assert_success(result: CliResult) -> None:
    assert (result.exit_code, result.stdout, result.stderr) == (0, b"", ""), result.stderr


def _assert_only_root_changed(theatre: Path, before: TreeSnapshot, expected: str) -> None:
    """Assert troupe.yml holds exactly ``expected`` and every other file is unchanged."""
    assert_file_bytes(theatre / ROOT, expected)
    after = snapshot_tree(theatre)
    assert set(after) == set(before)
    assert {k: v for k, v in after.items() if k != ROOT} == {
        k: v for k, v in before.items() if k != ROOT
    }


def _set_and_check(
    make_theatre: MakeTheatre, fixture: str, path: str, value: str, expected: str
) -> None:
    theatre = make_theatre(fixture)
    before = snapshot_tree(theatre)
    result = run_set(theatre, path, value)
    _assert_success(result)
    _assert_only_root_changed(theatre, before, expected)


# ---- Requirement 6: quoting style of changed scalars ----


@pytest.mark.parametrize(
    ("fixture", "path", "value", "expected"),
    [
        pytest.param("a: 'x'\n", "a", "y", "a: 'y'\n", id="single-quoted-kept"),
        pytest.param('a: "x"\n', "a", 'é"q', 'a: "é\\"q"\n', id="double-quoted-kept-escaped"),
    ],
)
def test_changed_scalar_keeps_its_representable_style(
    make_theatre: MakeTheatre, fixture: str, path: str, value: str, expected: str
) -> None:
    """Req 6.1, 6.6: a changed scalar keeps its single/double quotes when they can represent
    the new string; double quotes escape `"` and write `é` literally."""
    _set_and_check(make_theatre, fixture, path, value, expected)


@pytest.mark.parametrize(
    ("fixture", "path", "value", "expected"),
    [
        pytest.param("a: x\n", "a", '"0.2"', "a: '0.2'\n", id="plain-cannot-hold-number-string"),
        pytest.param(
            "a: x\n", "a", '"it\'s: here"', "a: 'it''s: here'\n", id="plain-falls-to-single"
        ),
        pytest.param("a: x\n", "a", '"bell\\a"', 'a: "bell\\a"\n', id="control-falls-to-double"),
        pytest.param('a: "x"\n', "a", '"\\N"', 'a: "\\N"\n', id="next-line-escape"),
        pytest.param("l: [a, b]\n", "l.0", "x,y", "l: ['x,y', b]\n", id="comma-in-flow"),
    ],
)
def test_unrepresentable_style_falls_back_plain_single_double(
    make_theatre: MakeTheatre, fixture: str, path: str, value: str, expected: str
) -> None:
    """Req 6.2, 6.4, 6.5, 6.6: when the existing style cannot hold the string, the minimal style
    is used (plain, else single-quoted with `''`, else double-quoted with `\\a`/`\\N` escapes);
    a string that reads back as a number or contains `,` inside a flow collection is not plain."""
    _set_and_check(make_theatre, fixture, path, value, expected)


@pytest.mark.parametrize(
    ("fixture", "path", "value", "expected"),
    [
        pytest.param("a: 'x'\n", "a", "5", "a: 5\n", id="int-over-single-quoted"),
        pytest.param("a: x\n", "a", "0x1F", "a: 0x1F\n", id="hex-spelling-kept"),
    ],
)
def test_non_strings_are_written_plain_in_their_value_spelling(
    make_theatre: MakeTheatre, fixture: str, path: str, value: str, expected: str
) -> None:
    """Req 6.3: integers are written plain in VALUE's spelling, even over a quoted scalar."""
    _set_and_check(make_theatre, fixture, path, value, expected)


# ---- Requirement 7: style of new keys and new values ----


def test_new_key_copies_last_key_quoting(make_theatre: MakeTheatre) -> None:
    """Req 7.1: a new key is written in the quoting style of the mapping's last key."""
    _set_and_check(make_theatre, '"a": 1\n', "c", "3", '"a": 1\n"c": 3\n')


@pytest.mark.parametrize(
    ("fixture", "path", "value", "expected"),
    [
        pytest.param('a: "x"\n', "b", "y", 'a: "x"\nb: "y"\n', id="new-key-value"),
        pytest.param("l:\n  - 'a'\n", "l.1", "b", "l:\n  - 'a'\n  - 'b'\n", id="new-list-item"),
    ],
)
def test_new_string_value_copies_preceding_sibling_style(
    make_theatre: MakeTheatre, fixture: str, path: str, value: str, expected: str
) -> None:
    """Req 7.2: a new string value for a new key or list item takes the quoting of the
    preceding sibling scalar."""
    _set_and_check(make_theatre, fixture, path, value, expected)


def test_new_value_after_collection_sibling_is_minimal(make_theatre: MakeTheatre) -> None:
    """Req 7.3: when the preceding sibling is a collection, the new string is written minimal
    (plain), even though VALUE spelled it double-quoted."""
    _set_and_check(make_theatre, "a:\n  x: 1\n", "b", '"q"', "a:\n  x: 1\nb: q\n")


def test_strings_inside_new_collection_are_minimal(make_theatre: MakeTheatre) -> None:
    """Req 7.4: keys and strings inside a newly written collection are minimal, ignoring the
    double-quoted sibling `a` and VALUE's own quotes."""
    _set_and_check(make_theatre, 'a: "x"\n', "c", '{k: "v"}', 'a: "x"\nc:\n  k: v\n')
