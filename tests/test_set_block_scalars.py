"""End-to-end validation of literal and folded block scalars of `troupe config set` (5.5).

Every case runs the real ``troupe`` console script (``set_support``). PATH and VALUE are the
exact argv strings, written as Python literals; the fixture and the expected text are the exact
bytes of ``.troupe/troupe.yml`` (UTF-8). Each success case asserts exit 0, empty stdout, empty
stderr, the exact bytes of troupe.yml and that no other file was added, removed or changed. Each
refusal asserts exit 3, empty stdout, stderr starting with ``<troupe.yml>: <PATH>: `` and an
unchanged theatre.

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

from pathlib import Path

import pytest

from set_support import TreeSnapshot, assert_file_bytes, run_set, snapshot_tree
from support import CliResult, MakeTheatre

ROOT = ".troupe/troupe.yml"

FOLDED = "text: >\n  The quick brown fox\n  jumps over.\n\n  Second para here.\nnext: 1\n"
"""Fixture F of task 5.5: a folded value with two paragraphs, followed by a sibling key."""


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


def _refuse_and_check(
    make_theatre: MakeTheatre, fixture: str, path: str, value: str, reason: str
) -> None:
    """Assert exit 3 naming troupe.yml, PATH and ``reason`` (not, e.g., a verification mismatch)."""
    theatre = make_theatre(fixture)
    before = snapshot_tree(theatre)
    result = run_set(theatre, path, value)
    assert (result.exit_code, result.stdout) == (3, b""), result.stderr
    prefix = f"{theatre / ROOT}: {path}: "
    assert result.stderr.startswith(prefix), (prefix, result.stderr)
    assert reason in result.stderr[len(prefix) :], result.stderr
    assert snapshot_tree(theatre) == before


# ---- Requirement 9: literal blocks ----


@pytest.mark.parametrize(
    ("fixture", "path", "value", "expected"),
    [
        pytest.param("a: x\n", "a", '"one\\ntwo\\n"', "a: |\n  one\n  two\n", id="clip"),
        pytest.param("a: x\n", "a", '"one\\ntwo"', "a: |-\n  one\n  two\n", id="strip"),
        pytest.param("a: x\n", "a", '"one\\n\\n"', "a: |+\n  one\n\n", id="keep"),
    ],
)
def test_multi_line_string_over_plain_scalar_becomes_literal_block(
    make_theatre: MakeTheatre, fixture: str, path: str, value: str, expected: str
) -> None:
    """Req 9.1, 9.2: a multi-line string replacing a plain scalar in a block mapping is written
    as a literal block, `|` for one trailing line break, `|-` for none, `|+` for more, with
    content indented one step past the key and empty lines as bare line breaks."""
    _set_and_check(make_theatre, fixture, path, value, expected)


def test_new_key_with_multi_line_string_becomes_literal_block(make_theatre: MakeTheatre) -> None:
    """Req 9.1, 9.2: a new key whose value is a multi-line string (no trailing line break) is
    written as a `|-` literal block after the last entry."""
    _set_and_check(make_theatre, "a: 1\n", "b", '"l1\\nl2"', "a: 1\nb: |-\n  l1\n  l2\n")


def test_new_key_in_flow_parent_with_multi_line_string_is_double_quoted(
    make_theatre: MakeTheatre,
) -> None:
    """Req 9.3: a multi-line string written for a new key of a flow mapping is double-quoted."""
    _set_and_check(make_theatre, "m: {a: 1}\n", "m.b", '"x\\ny\\n"', 'm: {a: 1, b: "x\\ny\\n"}\n')


@pytest.mark.parametrize(
    ("fixture", "path", "value", "expected"),
    [
        pytest.param(
            "a: |  # hdr\n  old\nb: 1\n",
            "a",
            '"new\\nlines\\n"',
            "a: |  # hdr\n  new\n  lines\nb: 1\n",
            id="header-comment-kept",
        ),
        pytest.param("a: |\n  old\n", "a", '"new"', "a: |-\n  new\n", id="clip-to-strip"),
        pytest.param(
            "a: |-\n    deep\n", "a", '"x\\ny"', "a: |-\n    x\n    y\n", id="indent-kept"
        ),
    ],
)
def test_literal_block_changed_to_string_keeps_header_and_indentation(
    make_theatre: MakeTheatre, fixture: str, path: str, value: str, expected: str
) -> None:
    """Req 9.4, 9.5: a literal block changed to another string keeps its indicator, its header
    comment and its content indentation; chomping changes only when the trailing line breaks
    require it (`|` → `|-` for a value without a final line break)."""
    _set_and_check(make_theatre, fixture, path, value, expected)


def test_literal_block_changed_to_non_string_is_written_plain(make_theatre: MakeTheatre) -> None:
    """Req 9.6: a block scalar set to a non-string is written plain in place of the indicator
    and its content lines are removed; the following key is untouched."""
    _set_and_check(make_theatre, "a: |\n  t\nb: 1\n", "a", "5", "a: 5\nb: 1\n")


@pytest.mark.parametrize(
    ("fixture", "path", "value", "expected"),
    [
        pytest.param(
            "a: x\n",
            "a",
            '"  indented\\nx\\n"',
            'a: "  indented\\nx\\n"\n',
            id="over-plain",
        ),
        pytest.param(
            "a: |\n  t\nb: 1\n",
            "a",
            '"  x\\ny\\n"',
            'a: "  x\\ny\\n"\nb: 1\n',
            id="over-literal-block",
        ),
    ],
)
def test_string_not_fitting_a_literal_block_is_double_quoted(
    make_theatre: MakeTheatre, fixture: str, path: str, value: str, expected: str
) -> None:
    """Req 9.7: a multi-line string whose first line starts with a space cannot be a literal
    block (no indentation indicator) and is written double-quoted, also over a block scalar,
    whose content lines are then removed."""
    _set_and_check(make_theatre, fixture, path, value, expected)


# ---- Requirement 10: folded blocks ----


def test_folded_changed_paragraph_is_rewrapped_unchanged_paragraph_kept(
    make_theatre: MakeTheatre,
) -> None:
    """Req 10.1, 10.2, 10.4, 10.5, 9.4, 9.5: the unchanged first paragraph keeps its bytes (its
    line breaks are not re-wrapped although its joined text is longer than the width); the
    changed second paragraph is re-wrapped greedily at the width of its old longest line (17)."""
    _set_and_check(
        make_theatre,
        FOLDED,
        "text",
        '"The quick brown fox jumps over.\\nSecond para changed now and longer.\\n"',
        "text: >\n  The quick brown fox\n  jumps over.\n\n"
        "  Second para\n  changed now and\n  longer.\nnext: 1\n",
    )


def test_folded_new_paragraph_is_wrapped_at_longest_old_line(make_theatre: MakeTheatre) -> None:
    """Req 10.1, 10.3, 10.4: both existing paragraphs are kept byte for byte; the added third
    paragraph has no counterpart and is wrapped at the old value's longest line (19)."""
    _set_and_check(
        make_theatre,
        FOLDED,
        "text",
        '"The quick brown fox jumps over.\\nSecond para here.\\n'
        'Extra paragraph text that wraps.\\n"',
        "text: >\n  The quick brown fox\n  jumps over.\n\n  Second para here.\n\n"
        "  Extra paragraph\n  text that wraps.\nnext: 1\n",
    )


def test_folded_unchanged_paragraph_keeps_its_own_line_breaks(make_theatre: MakeTheatre) -> None:
    """Req 10.1, 10.2, 10.4: an unchanged paragraph keeps its old line breaks although greedy
    re-wrapping at its width (11) would join `aa bb cc dd`; the changed paragraph is wrapped at
    its own old width (3)."""
    _set_and_check(
        make_theatre,
        "t: >\n  aa bb\n  cc dd ee ff\n\n  old\n",
        "t",
        '"aa bb cc dd ee ff\\nnew text\\n"',
        "t: >\n  aa bb\n  cc dd ee ff\n\n  new\n  text\n",
    )


def test_folded_word_longer_than_width_takes_its_own_line(make_theatre: MakeTheatre) -> None:
    """Req 10.2, 10.5: a word longer than the paragraph's width (5) goes on a line of its own;
    the other words are still packed greedily."""
    _set_and_check(
        make_theatre,
        "t: >\n  ab cd\n  ef\n",
        "t",
        '"ab extraordinary cd ef\\n"',
        "t: >\n  ab\n  extraordinary\n  cd ef\n",
    )


def test_folded_change_introducing_more_indented_line_is_refused(
    make_theatre: MakeTheatre,
) -> None:
    """Req 10.6: a new value whose paragraph starts with spaces would need a more-indented line
    in the folded block: exit 3, nothing written."""
    _refuse_and_check(
        make_theatre,
        FOLDED,
        "text",
        '"The quick brown fox jumps over.\\n  indented\\n"',
        "'  indented' would be more-indented in a folded block",
    )


def test_folded_with_existing_more_indented_line_refuses_change(
    make_theatre: MakeTheatre,
) -> None:
    """Req 10.7: an existing folded value holding a more-indented line cannot be changed: exit
    3, nothing written."""
    _refuse_and_check(
        make_theatre,
        "t: >\n  a\n    more\n  b\n",
        "t",
        '"x\\n"',
        "the folded value has more-indented lines",
    )
