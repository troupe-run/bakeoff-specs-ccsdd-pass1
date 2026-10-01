"""End-to-end validation of new collections, indentation and placement of `troupe config set` (5.4).

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


# ---- Requirement 8: new block collections and indentation ----


def test_new_mapping_uses_the_files_indentation_step(make_theatre: MakeTheatre) -> None:
    """Req 8.1, 8.2, 8.3: a new block mapping (and a list nested in it) is indented by the
    step of the first nested block mapping (4 here); without a list in the file, the `-`
    indicators sit one step right of their key."""
    _set_and_check(
        make_theatre,
        "a:\n    x: 1\nb: 2\n",
        "c",
        "{d: 1, e: [p, q]}",
        "a:\n    x: 1\nb: 2\nc:\n    d: 1\n    e:\n        - p\n        - q\n",
    )


def test_new_list_copies_flush_list_offset(make_theatre: MakeTheatre) -> None:
    """Req 8.1, 8.3: a new block list under a key puts its `-` at the offset of the file's
    first block list under a key (0 for flush lists)."""
    _set_and_check(
        make_theatre,
        "l:\n- a\nm: 1\n",
        "n",
        "[x, y]",
        "l:\n- a\nm: 1\nn:\n- x\n- y\n",
    )


def test_new_mapping_defaults_to_two_spaces(make_theatre: MakeTheatre) -> None:
    """Req 8.1, 8.2: a file without a nested block mapping uses an indentation step of 2."""
    _set_and_check(make_theatre, "a: 1\n", "b", "{c: 1}", "a: 1\nb:\n  c: 1\n")


def test_mapping_as_list_item_starts_on_dash_line(make_theatre: MakeTheatre) -> None:
    """Req 8.4: a mapping written as a block list item starts after `- ` on the item's line and
    its further entries align two columns right of the `-`."""
    _set_and_check(
        make_theatre,
        "l:\n  - a\n",
        "l.1",
        "{n: 1, m: 2}",
        "l:\n  - a\n  - n: 1\n    m: 2\n",
    )


# ---- Requirement 11.1, 11.2: placement of new block entries ----


@pytest.mark.parametrize(
    ("fixture", "path", "value", "expected"),
    [
        pytest.param(
            "a:\n  x: 1\n  # end of a\n\n# about b\nb: 2\n",
            "a.y",
            "2",
            "a:\n  x: 1\n  # end of a\n  y: 2\n\n# about b\nb: 2\n",
            id="below-trailing-comment-before-blank",
        ),
        pytest.param(
            "a:\n  x: 1\n# about b\nb: 2\n",
            "a.y",
            "2",
            "a:\n  x: 1\n  y: 2\n# about b\nb: 2\n",
            id="less-indented-comment-ends-collection",
        ),
        pytest.param(
            "a: 1\n# tail\n",
            "b",
            "2",
            "a: 1\n# tail\nb: 2\n",
            id="top-level-below-tail-comment",
        ),
    ],
)
def test_new_entry_goes_after_last_entry_and_its_comments(
    make_theatre: MakeTheatre, fixture: str, path: str, value: str, expected: str
) -> None:
    """Req 11.1, 11.2: a new entry goes after the last entry and below the comment lines
    indented at least as far as the entries; a less-indented comment ends the collection and
    the entry is inserted before blank lines not followed by a qualifying comment."""
    _set_and_check(make_theatre, fixture, path, value, expected)


# ---- Requirement 8.5, 8.6, 11.3: flow collections ----


@pytest.mark.parametrize(
    ("fixture", "path", "value", "expected"),
    [
        pytest.param(
            "a: {x: 1}  # c\n",
            "a.y",
            "[1, 2]",
            "a: {x: 1, y: [1, 2]} # c\n",
            id="flow-list-in-flow-mapping",
        ),
        pytest.param("l: [a, b,]\n", "l.2", "c", "l: [a, b, c,]\n", id="trailing-comma-kept"),
        pytest.param("m: {}\n", "m.k", "v", "m: {k: v}\n", id="into-empty-flow-mapping"),
        pytest.param(
            "m: {a: 1}\n",
            "m.b",
            '"l1\\nl2\\n"',
            'm: {a: 1, b: "l1\\nl2\\n"}\n',
            id="multi-line-string-double-quoted",
        ),
    ],
)
def test_flow_entry_appended_after_last_entry_on_its_line(
    make_theatre: MakeTheatre, fixture: str, path: str, value: str, expected: str
) -> None:
    """Req 8.5, 8.6, 11.3, 9.3: a new entry of a flow collection goes after the last entry on
    that entry's line, separated by `, `, keeping a trailing comma and the closing bracket; a
    new collection under a flow parent is written in flow style."""
    _set_and_check(make_theatre, fixture, path, value, expected)


# ---- Requirement 11.4, 11.5: scalar replaced by a block collection ----


@pytest.mark.parametrize(
    ("fixture", "path", "value", "expected"),
    [
        pytest.param(
            "a: 5  # note\nb: 1\n",
            "a",
            "{x: 1}",
            "a:    # note\n  x: 1\nb: 1\n",
            id="comment-kept-at-its-column",
        ),
        pytest.param(
            "a: 5\nb: 1\n", "a", "{x: 1}", "a:\n  x: 1\nb: 1\n", id="spaces-after-colon-removed"
        ),
        pytest.param("a:\nb: 1\n", "a", "[x]", "a:\n  - x\nb: 1\n", id="null-to-block-list"),
    ],
)
def test_scalar_to_block_collection_keeps_key_line(
    make_theatre: MakeTheatre, fixture: str, path: str, value: str, expected: str
) -> None:
    """Req 11.4, 11.5, 8.1, 8.3: a scalar replaced by a block collection keeps the key's line
    through its end-of-line comment (at its previous column), removes the scalar (and the
    spaces before it when no comment follows) and starts the collection on the next line."""
    _set_and_check(make_theatre, fixture, path, value, expected)
