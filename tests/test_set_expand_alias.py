"""``troupe config set --expand-alias`` end to end through the real console script (config-aliases).

Each case runs the real ``troupe`` console script on a real theatre and asserts:

* the exit code, stdout and stderr exactly;
* the exact bytes of every changed file, and that every other file is byte-identical, with no file
  added or removed (7.6, 10.1);
* the history: the events the ``set`` added are exactly the ``set`` events of the leaf values it
  changed, with ``via`` ``cli``, the actor from ``TROUPE_ACTOR``, the ``--reason`` TEXT (or
  ``null``) and one change id; an expansion itself records nothing, and a reconciling ``show``
  afterwards records nothing either (6.4, 8.6, 10.4).

The option takes no value and may stand before, between or after PATH and VALUE, together with
``--reason`` and ``--in-include``; ``--expand-alias=TEXT`` is a usage error (exit 2) that changes
no file and no history state (6.1, 6.2). Refusals (8.4, 8.5, 9.2, 7.5) exit 3 naming the file and
the user's PATH, and leave every file and the history unchanged.

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

import json
from pathlib import Path
from typing import Any, Final

import pytest

from set_support import TreeSnapshot, assert_file_bytes, run_config, snapshot_tree
from support import LINEAGE_KEYS, STEP1_KEYS, CliResult, MakeTheatre
from troupe.cli.actor import TROUPE_ACTOR_VARIABLE
from troupe.config.constants import CONFIG_DIR_NAME, HISTORY_STORE_NAME

ROOT: Final = ".troupe/troupe.yml"
ACTOR: Final = "agent:planner"
ACTOR_JSON: Final = {"kind": "agent", "id": "planner"}
COMMENT: Final = "  # expanded from *{name} by troupe"

ISSUE_2: Final = "defaults: &d\n  model: opus\ncoder:\n  settings: *d\nreviewer:\n  settings: *d\n"
"""The reporter's file of tracker issue #2."""

ISSUE_2_EXPANDED: Final = (
    "defaults: &d\n  model: opus\n"
    "coder:\n  settings:  # expanded from *d by troupe\n    model: sonnet\n"
    "reviewer:\n  settings: *d\n"
)
"""The issue #2 file after ``set --expand-alias coder.settings.model sonnet``."""

ISSUE_2_EVENT: Final = ("set", "coder.settings.model", "opus", "sonnet")

type Line = dict[str, Any]
"""One ``history`` line parsed as JSON."""

SetEvent = tuple[str, str, object, object]
"""The members of one recorded event this module checks: ``(op, path, old, new)``."""


@pytest.fixture(autouse=True)
def _declared_actor(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every command in this module runs as a declared actor (no developer shell leakage)."""
    monkeypatch.setenv(TROUPE_ACTOR_VARIABLE, ACTOR)


# ---- helpers ----


def _set(theatre: Path, *args: str) -> CliResult:
    return run_config("set", *args, "--theatre", str(theatre), cwd=theatre)


def _get(theatre: Path, path: str) -> bytes:
    result = run_config("get", path, "--theatre", str(theatre), cwd=theatre)
    assert (result.exit_code, result.stderr) == (0, ""), result.stderr
    return result.stdout


def _history(theatre: Path) -> list[Line]:
    """Every ``troupe config history`` line, parsed in full (``history`` never reconciles)."""
    result = run_config("history", "--theatre", str(theatre), cwd=theatre)
    assert (result.exit_code, result.stderr) == (0, ""), result.stderr
    lines: list[Line] = []
    for raw in result.stdout.decode("utf-8").splitlines():
        parsed = json.loads(raw)
        assert isinstance(parsed, dict) and tuple(parsed) == STEP1_KEYS + LINEAGE_KEYS, raw
        lines.append(parsed)
    return lines


def _imported(theatre: Path) -> int:
    """Import the theatre (a ``show`` records every leaf); return the number of history lines."""
    result = run_config("show", "--theatre", str(theatre), cwd=theatre)
    assert (result.exit_code, result.stderr) == (0, ""), result.stderr
    lines = _history(theatre)
    assert lines and {line["op"] for line in lines} == {"import"}
    return len(lines)


def _history_state(theatre: Path) -> dict[str, bytes]:
    directory = theatre / CONFIG_DIR_NAME
    return {p.name: p.read_bytes() for p in sorted(directory.glob(f"{HISTORY_STORE_NAME}*"))}


def _assert_set_events(
    theatre: Path, start: int, expected: list[SetEvent], reason: str | None = None
) -> None:
    """Assert that the history lines after the first ``start`` are exactly ``expected``: ``set``
    events with cli lineage (actor, ``reason``), one change; a reconciling ``show`` records
    nothing afterwards."""
    added = _history(theatre)[start:]
    assert [(line["op"], line["path"], line["old"], line["new"]) for line in added] == expected
    assert all(line["via"] == "cli" for line in added), added
    assert all(line["actor"] == ACTOR_JSON for line in added), added
    assert all(line["reason"] == reason for line in added), added
    assert len({line["change"] for line in added}) <= 1, added
    reconcile = run_config("show", "--theatre", str(theatre), cwd=theatre)
    assert (reconcile.exit_code, reconcile.stderr) == (0, ""), reconcile.stderr
    assert len(_history(theatre)) == start + len(expected)


def _assert_only_root_changed(theatre: Path, before: TreeSnapshot, expected: str) -> None:
    """Assert that the root file holds exactly ``expected`` and every other file is unchanged,
    with no file added or removed."""
    assert_file_bytes(theatre / ROOT, expected)
    after = snapshot_tree(theatre)
    assert set(after) == set(before)
    assert {k: v for k, v in after.items() if k != ROOT} == {
        k: v for k, v in before.items() if k != ROOT
    }


def _check(
    make_theatre: MakeTheatre,
    config: str,
    args: tuple[str, ...],
    expected: str,
    events: list[SetEvent],
    *,
    reason: str | None = None,
) -> Path:
    """Run ``set ARGS`` on a fresh imported theatre; assert success, the bytes and the events."""
    theatre = make_theatre(config)
    start = _imported(theatre)
    before = snapshot_tree(theatre)
    result = _set(theatre, *args)
    assert (result.exit_code, result.stdout, result.stderr) == (0, b"", ""), result.stderr
    _assert_only_root_changed(theatre, before, expected)
    _assert_set_events(theatre, start, events, reason)
    return theatre


def _refused(make_theatre: MakeTheatre, config: str, path: str, value: str, problem: str) -> None:
    """Run ``set --expand-alias PATH VALUE`` after an import; assert exit 3, the stderr naming the
    root file and the user's PATH, and that no file and no history state changed."""
    theatre = make_theatre(config)
    _imported(theatre)
    state = _history_state(theatre)
    before = snapshot_tree(theatre)

    result = _set(theatre, "--expand-alias", path, value)

    assert (result.exit_code, result.stdout) == (3, b""), result.stderr
    assert result.stderr == f"{theatre / ROOT}: {path}: {problem}\n"
    assert snapshot_tree(theatre) == before
    assert _history_state(theatre) == state


# ---- 6.4, 7.1, 7.7, 10.4: the issue #2 file ----


@pytest.mark.parametrize("reason", [None, "coder needs its own model"], ids=["plain", "reason"])
def test_issue_2_set_in_the_copy(make_theatre: MakeTheatre, reason: str | None) -> None:
    """Req 6.4, 7.1, 7.2, 7.6, 7.7, 10.1, 10.4: ``set --expand-alias coder.settings.model sonnet``
    on the issue #2 file writes exactly the copy under ``coder`` with VALUE set in it; ``defaults``
    and ``reviewer`` are byte-identical, ``reviewer.settings.model`` still reads ``opus``, and one
    ``set`` event is recorded with cli lineage and the ``--reason`` TEXT."""
    option = () if reason is None else ("--reason", reason)
    theatre = _check(
        make_theatre,
        ISSUE_2,
        ("--expand-alias", "coder.settings.model", "sonnet", *option),
        ISSUE_2_EXPANDED,
        [ISSUE_2_EVENT],
        reason=reason,
    )
    assert _get(theatre, "reviewer.settings.model") == b"opus\n"
    assert _get(theatre, "coder.settings.model") == b"sonnet\n"
    assert _get(theatre, "defaults.model") == b"opus\n"


# ---- 8.1: nested aliases ----


def test_nested_aliases_each_get_their_own_comment(make_theatre: MakeTheatre) -> None:
    """Req 8.1, 7.5, 7.7, 10.4: PATH reached through ``*d`` and then the copy's inner ``*e``
    expands both, outermost first, each with its own comment; the anchors and ``d``'s inner alias
    are unchanged and only the set leaf is recorded."""
    text = "e: &e\n  m: 1\nd: &d\n  x: *e\na: *d\n"
    theatre = _check(
        make_theatre,
        text,
        ("a.x.m", "2", "--expand-alias"),
        "e: &e\n  m: 1\nd: &d\n  x: *e\n"
        f"a:{COMMENT.format(name='d')}\n  x:{COMMENT.format(name='e')}\n    m: 2\n",
        [("set", "a.x.m", 1, 2)],
    )
    assert _get(theatre, "d.x.m") == b"1\n"


# ---- 8.3, 7.3: a list-item alias and a scalar alias ----


def test_list_item_alias_gets_the_comment_after_the_dash(make_theatre: MakeTheatre) -> None:
    """Req 8.3, 7.2, 7.7: a block list item ``- *d`` is expanded with the comment two spaces after
    the ``-`` and the copy's entries below it; the other item stays an alias."""
    text = "d: &d\n  m: 1\n  n: 2\nl:\n  - *d\n  - *d\n"
    _check(
        make_theatre,
        text,
        ("--expand-alias", "l.0.m", "5"),
        f"d: &d\n  m: 1\n  n: 2\nl:\n  -{COMMENT.format(name='d')}\n    m: 5\n    n: 2\n  - *d\n",
        [("set", "l.0.m", 1, 5)],
    )


def test_scalar_alias_is_written_on_the_key_line(make_theatre: MakeTheatre) -> None:
    """Req 7.3, 7.7, 6.4: a scalar alias ``other: *n`` set with ``--expand-alias`` gets VALUE on the
    key's line followed by the comment; the anchored scalar keeps ``&n`` and its value."""
    text = "n: &n foo\nother: *n\nthird: *n\n"
    theatre = _check(
        make_theatre,
        text,
        ("other", "bar", "--expand-alias"),
        f"n: &n foo\nother: bar{COMMENT.format(name='n')}\nthird: *n\n",
        [("set", "other", "foo", "bar")],
    )
    assert _get(theatre, "third") == b"foo\n"


def test_empty_anchored_mapping_gains_the_key_in_the_copy(make_theatre: MakeTheatre) -> None:
    """Req 7.3, 7.7: an alias to an empty anchored mapping is copied as ``{}`` on the key's line
    before the comment; the new key is then set in that copy under the usual rules (a key added
    to a flow mapping, the comment kept at its column with at least one space, config-set-writeback
    Requirement 12), and the anchored ``{}`` is unchanged."""
    text = "z: 0\nd: &d {}\nc:\n  s: *d\n"
    _check(
        make_theatre,
        text,
        ("--expand-alias", "c.s.k", "1"),
        "z: 0\nd: &d {}\nc:\n  s: {k: 1} # expanded from *d by troupe\n",
        [("set", "c.s.k", None, 1)],
    )


# ---- 8.6: a no-op still expands ----


def test_no_op_with_the_flag_still_expands_and_records_no_event(make_theatre: MakeTheatre) -> None:
    """Req 8.6, 10.4: VALUE equal to the current value through an alias still writes the copy
    (holding the same value) and records no event."""
    _check(
        make_theatre,
        ISSUE_2,
        ("--expand-alias", "coder.settings.model", "opus", "--reason", "copy only"),
        ISSUE_2_EXPANDED.replace("sonnet", "opus"),
        [],
    )


# ---- 6.3: no alias on PATH ----


@pytest.mark.parametrize(
    ("config", "path", "value", "expected", "events"),
    [
        pytest.param(
            ISSUE_2,
            "defaults.model",
            "sonnet",
            ISSUE_2.replace("opus", "sonnet"),
            [
                ("set", "coder.settings.model", "opus", "sonnet"),
                ("set", "defaults.model", "opus", "sonnet"),
                ("set", "reviewer.settings.model", "opus", "sonnet"),
            ],
            id="at-the-anchor",
        ),
        pytest.param(
            "# plain\na:\n  x: 1  # c\nb: 2\n",
            "a.p.q",
            "1",
            "# plain\na:\n  x: 1  # c\n  p:\n    q: 1\nb: 2\n",
            [("set", "a.p.q", None, 1)],
            id="no-alias",
        ),
    ],
)
@pytest.mark.parametrize("with_option", [True, False], ids=["expand-alias", "plain"])
def test_flag_without_an_alias_on_path_is_the_usual_set(
    make_theatre: MakeTheatre,
    config: str,
    path: str,
    value: str,
    expected: str,
    events: list[SetEvent],
    with_option: bool,
) -> None:
    """Req 6.3, 6.4: when PATH is not reached through an alias, ``--expand-alias`` writes exactly
    the bytes and records exactly the events of the set without it."""
    option = ("--expand-alias",) if with_option else ()
    theatre = make_theatre(config)
    start = _imported(theatre)
    before = snapshot_tree(theatre)
    result = _set(theatre, *option, path, value)
    assert (result.exit_code, result.stdout, result.stderr) == (0, b"", ""), result.stderr
    _assert_only_root_changed(theatre, before, expected)
    added = _history(theatre)[start:]
    assert sorted((line["op"], line["path"], line["old"], line["new"]) for line in added) == events
    assert all(line["via"] == "cli" and line["reason"] is None for line in added), added


def test_flag_with_a_no_op_and_no_alias_writes_nothing(make_theatre: MakeTheatre) -> None:
    """Req 6.3: a no-op set not reached through an alias writes no file and records no event with
    ``--expand-alias``, as without it."""
    theatre = make_theatre(ISSUE_2)
    _imported(theatre)
    state = _history_state(theatre)
    before = snapshot_tree(theatre)
    result = _set(theatre, "defaults.model", "opus", "--expand-alias")
    assert (result.exit_code, result.stdout, result.stderr) == (0, b"", "")
    assert snapshot_tree(theatre) == before
    assert _history_state(theatre) == state


# ---- 6.1: positions and combinations ----


@pytest.mark.parametrize(
    "layout",
    [
        pytest.param(("--expand-alias", "P", "V"), id="before"),
        pytest.param(("P", "--expand-alias", "V"), id="between"),
        pytest.param(("P", "V", "--expand-alias"), id="after"),
        pytest.param(("--expand-alias", "--reason", "R", "P", "V"), id="before-reason"),
        pytest.param(("P", "--expand-alias", "V", "--reason=R"), id="between-reason"),
        pytest.param(("--reason=R", "P", "V", "--expand-alias"), id="after-reason"),
        pytest.param(("--in-include", "P", "--expand-alias", "V", "--reason", "R"), id="all"),
        pytest.param(("P", "V", "--expand-alias", "--in-include", "--expand-alias"), id="twice"),
    ],
)
def test_option_anywhere_together_with_reason_and_in_include(
    make_theatre: MakeTheatre, layout: tuple[str, ...]
) -> None:
    """Req 6.1, 6.4: ``--expand-alias`` takes no value and works before, between or after PATH and
    VALUE (also given twice), together with ``--reason`` and ``--in-include``; the bytes are those
    of the issue #2 expansion and the reason is on the one ``set`` event."""
    reason = "own model for the coder"
    with_reason = "R" in layout or "--reason=R" in layout
    names = {
        "R": reason,
        "--reason=R": f"--reason={reason}",
        "P": "coder.settings.model",
        "V": "sonnet",
    }
    args = tuple(names.get(arg, arg) for arg in layout)
    _check(
        make_theatre,
        ISSUE_2,
        args,
        ISSUE_2_EXPANDED,
        [ISSUE_2_EVENT],
        reason=reason if with_reason else None,
    )


def test_new_key_in_the_copy_with_in_include(make_theatre: MakeTheatre) -> None:
    """Req 6.1, 7.7: with ``--in-include`` and ``--expand-alias`` a new key below the alias goes
    into the copy (no include involved, so placement is the usual one)."""
    _check(
        make_theatre,
        ISSUE_2,
        ("--in-include", "coder.settings.temperature", "0.3", "--expand-alias"),
        "defaults: &d\n  model: opus\n"
        f"coder:\n  settings:{COMMENT.format(name='d')}\n    model: opus\n    temperature: 0.3\n"
        "reviewer:\n  settings: *d\n",
        [("set", "coder.settings.temperature", None, 0.3)],
    )


@pytest.mark.parametrize("attached", ["x", "", "true"])
def test_expand_alias_with_a_value_is_a_usage_error(
    make_theatre: MakeTheatre, attached: str
) -> None:
    """Req 6.2: ``--expand-alias=TEXT`` exits 2 with empty stdout and a usage message on stderr,
    leaves every file byte-identical and creates no history state on a fresh theatre."""
    theatre = make_theatre(ISSUE_2)
    before = snapshot_tree(theatre)

    result = _set(theatre, "coder.settings.model", "sonnet", f"--expand-alias={attached}")

    assert result.exit_code == 2, result
    assert result.stdout == b""
    assert result.stderr == (
        f"troupe config set: argument --expand-alias: ignored explicit argument {attached!r}\n"
    )
    assert snapshot_tree(theatre) == before
    assert _history_state(theatre) == {}


def test_expand_alias_with_a_value_on_an_imported_theatre_changes_no_history(
    make_theatre: MakeTheatre,
) -> None:
    """Req 6.2: on an imported theatre, ``--expand-alias=TEXT`` leaves the history state bytes and
    every file unchanged."""
    theatre = make_theatre(ISSUE_2)
    _imported(theatre)
    state = _history_state(theatre)
    before = snapshot_tree(theatre)

    result = _set(theatre, "--expand-alias=x", "coder.settings.model", "sonnet")

    assert (result.exit_code, result.stdout) == (2, b"")
    assert "--expand-alias" in result.stderr
    assert snapshot_tree(theatre) == before
    assert _history_state(theatre) == state


def test_set_help_mentions_the_option(make_theatre: MakeTheatre) -> None:
    """Req 6.1: ``set --help`` lists ``--expand-alias`` (without a metavar: it takes no value)."""
    theatre = make_theatre(ISSUE_2)
    result = run_config("set", "--help", cwd=theatre)
    assert (result.exit_code, result.stderr) == (0, ""), result.stderr
    text = result.stdout.decode("utf-8")
    assert "--expand-alias " in text or "--expand-alias\n" in text
    assert "--expand-alias=" not in text and "--expand-alias TEXT" not in text


# ---- 8.4, 8.5, 9.2, 7.5: refusals change nothing ----


@pytest.mark.parametrize(
    ("config", "path", "problem"),
    [
        pytest.param(
            "d: &d\n  m: 1\nc: {s: *d}\n",
            "c.s.m",
            "alias '*d' cannot be expanded inside a flow collection",
            id="flow-mapping",
        ),
        pytest.param(
            "d: &d\n  m: 1\nc: [*d]\n",
            "c.0.m",
            "alias '*d' cannot be expanded inside a flow collection",
            id="flow-list",
        ),
        pytest.param(
            "d: &d\n  m: 1\nc:\n  s: *d  # shared\n",
            "c.s.m",
            "the line of alias '*d' already has an end-of-line comment",
            id="comment",
        ),
        pytest.param(
            "d: &d\n  m: 1\nc:\n  - *d  # shared\n",
            "c.0.m",
            "the line of alias '*d' already has an end-of-line comment",
            id="comment-list-item",
        ),
    ],
)
def test_flow_and_comment_refusals_change_nothing(
    make_theatre: MakeTheatre, config: str, path: str, problem: str
) -> None:
    """Req 8.4, 8.5: an alias to expand inside a flow collection, or on a line that already has an
    end-of-line comment, exits 3 naming the file, the user's PATH and the alias, with no file and
    no history state changed."""
    _refused(make_theatre, config, path, "2", problem)


def test_merge_key_is_still_refused_with_the_flag(make_theatre: MakeTheatre) -> None:
    """Req 9.2: a value a mapping receives from its merge key is refused with ``--expand-alias``
    too, naming the file, PATH and the merge key, with no file and no history state changed."""
    _refused(
        make_theatre,
        "d: &d\n  m: 1\nc:\n  <<: *d\n  own: 1\n",
        "c.m",
        "2",
        "value comes from merge key '<<' at c.<<; merge keys are never written, and "
        "--expand-alias cannot expand them",
    )


def test_copy_of_a_node_holding_a_merge_key_is_refused(make_theatre: MakeTheatre) -> None:
    """Req 9.1, 9.2: expanding an alias whose anchored node holds a merge key (a copy would write
    ``<<``) is refused naming the file and the user's PATH, with nothing changed."""
    _refused(
        make_theatre,
        "b: &b\n  k: 1\nd: &d\n  <<: *b\n  m: 1\nc:\n  s: *d\n",
        "c.s.m",
        "2",
        "alias '*d' cannot be expanded: value comes from merge key '<<' at c.s.<<; merge keys "
        "are never written, and --expand-alias cannot expand them",
    )


REDEFINED: Final = "e: &e\n  m: 1\nd: &d\n  x: *e\n  k: 1\nf: &e\n  m: 9\na: *d\n"
"""``*e`` inside ``&d`` refers to the first ``&e``; copied below the second ``&e``, it would refer
to that one, so the copy of ``*d`` would change ``a.x.m`` from 1 to 9."""


@pytest.mark.parametrize(("path", "value"), [("a.y", "5"), ("a.k", "1")], ids=["change", "no-op"])
def test_expansion_below_a_redefined_anchor_is_refused(
    make_theatre: MakeTheatre, path: str, value: str
) -> None:
    """Req 7.5, 10.2: an expansion whose copied inner alias would refer to an anchor redefined
    before the alias place is refused (exit 3) naming the file, PATH and the expanded alias, with
    no file and no history state changed, whether VALUE changes the value or not."""
    _refused(
        make_theatre,
        REDEFINED,
        path,
        value,
        "expanding alias '*d' would change values elsewhere (an alias in its copy would refer "
        "to an anchor redefined before this place)",
    )
