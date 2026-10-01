"""End-to-end validation of end-of-line comment columns of `troupe config set` (5.6).

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


@pytest.mark.parametrize(
    ("fixture", "path", "value", "expected"),
    [
        pytest.param("a: 1    # c\nb: 2\n", "a", "100", "a: 100  # c\nb: 2\n", id="longer"),
        pytest.param("a: 1000 # c\n", "a", "1", "a: 1    # c\n", id="shorter"),
    ],
)
def test_comment_moves_back_to_its_previous_column(
    make_theatre: MakeTheatre, fixture: str, path: str, value: str, expected: str
) -> None:
    """Req 12.1, 12.4: when the value grows or shrinks, the end-of-line comment keeps its
    previous column and the following line is unchanged."""
    _set_and_check(make_theatre, fixture, path, value, expected)


def test_value_reaching_the_column_leaves_exactly_one_space(make_theatre: MakeTheatre) -> None:
    """Req 12.2, 12.4: a new value that passes the comment's column is separated from the comment
    by exactly one space; the next line is unchanged."""
    _set_and_check(make_theatre, "a: 1    # c\nb: 2\n", "a", "1234567", "a: 1234567 # c\nb: 2\n")


def test_columns_count_unicode_code_points(make_theatre: MakeTheatre) -> None:
    """Req 12.1, 12.2, 12.3: columns are counted in code points (`é` is one column, two bytes):
    `ééé` exactly reaches the old column, so one space remains."""
    _set_and_check(make_theatre, "name: é   # c\n", "name", "ééé", "name: ééé # c\n")


def test_hash_inside_quoted_value_is_not_the_comment(make_theatre: MakeTheatre) -> None:
    """Req 12.1: the `#` inside the old double-quoted value is not the comment; the real comment
    keeps its column after the quoted value shrinks (double quotes are kept)."""
    _set_and_check(make_theatre, 'a: "x # y"  # c\n', "a", "z", 'a: "z"      # c\n')


def test_only_the_changed_line_comment_moves(make_theatre: MakeTheatre) -> None:
    """Req 12.1, 12.4: only the comment on the changed line is adjusted; the aligned comment on
    the next line keeps its bytes."""
    _set_and_check(make_theatre, "a: 1   # x\nb: 2   # y\n", "a", "22", "a: 22  # x\nb: 2   # y\n")


@pytest.mark.parametrize(
    ("fixture", "value", "expected"),
    [
        pytest.param("name: é   # c\n", "ee", "name: ee  # c\n", id="multibyte-removed"),
        pytest.param("name: x   # c\n", "éé", "name: éé  # c\n", id="multibyte-added"),
    ],
)
def test_columns_are_code_points_not_bytes(
    make_theatre: MakeTheatre, fixture: str, value: str, expected: str
) -> None:
    """Req 12.1, 12.3: the comment column is counted in code points, not UTF-8 bytes (counting
    bytes would leave 3 resp. 1 spaces here)."""
    _set_and_check(make_theatre, fixture, "name", value, expected)
