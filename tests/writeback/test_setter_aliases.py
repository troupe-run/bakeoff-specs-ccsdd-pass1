"""Tests of the set use case with alias expansion (config-aliases task 4.3).

``set_value(..., expand_alias=True)`` expands one alias at a time in memory, reloads, re-checks,
then runs the usual set on the expanded configuration and writes every changed file once. Every
test opens a real theatre (``open_theatre``) and calls the use case directly (the CLI flag is task
4.4). Failures are injected at the OS boundary (``os.replace``). Every test asserts the exact
bytes of the files and the history.

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

import os
from pathlib import Path
from typing import Any

import pytest

from set_support import assert_file_bytes, snapshot_tree
from support import CLI_CONTEXT, MakeTheatre, Step1Record, step1
from troupe.config.errors import ExitCode, WriteRefusedError
from troupe.config.history import SetChange
from troupe.config.loader import load_config
from troupe.config.model import ConfigMapping, ConfigNode
from troupe.config.paths import ConfigPath
from troupe.config.theatre import Theatre, open_theatre
from troupe.config.writeback import expand
from troupe.config.writeback.compare import node_to_plain
from troupe.config.writeback.setter import SetOutcome, set_value
from troupe.config.writeback.value import parse_value

ISSUE_2 = "defaults: &d\n  model: opus\ncoder:\n  settings: *d\nreviewer:\n  settings: *d\n"
"""The reporter's file of tracker issue #2."""


def _set(theatre: Theatre, path: str, value: str, *, expand: bool = True) -> SetOutcome:
    config_path = ConfigPath.parse(path)
    return set_value(
        theatre, config_path, parse_value(value, config_path), CLI_CONTEXT, expand_alias=expand
    )


def _node(node: ConfigNode, path: str) -> ConfigNode:
    for segment in ConfigPath.parse(path).segments:
        assert isinstance(node, ConfigMapping)
        node = next(entry.value for entry in node.entries if entry.key == segment)
    return node


def _fail_replace(monkeypatch: pytest.MonkeyPatch, failing: dict[str, int]) -> list[str]:
    """Make ``os.replace`` fail for the n-th rename onto each named target (1-based).

    Returns the list of rename targets (file names) in call order, failed ones included.
    """
    original = os.replace
    calls: list[str] = []

    def replace(src: Any, dst: Any, *args: Any, **kwargs: Any) -> None:
        name = Path(dst).name
        calls.append(name)
        if failing.get(name) == calls.count(name):
            raise PermissionError(13, "Permission denied (injected)")
        original(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "replace", replace)
    return calls


def test_set_in_the_copy_leaves_the_other_alias_reading_the_anchor(
    make_theatre: MakeTheatre,
) -> None:
    """Req 7.7, 10.2, 10.3, 10.4: on the issue #2 file, `coder.settings.model sonnet` with
    expansion writes the copy with the new value, keeps the anchor and the reviewer's alias, records
    only the changed leaf, and the reviewer still reads the anchor's value through its alias."""
    directory = make_theatre(ISSUE_2)
    config_file = directory / ".troupe" / "troupe.yml"
    with open_theatre(directory) as theatre:
        before = step1(theatre.history.records())
        outcome = _set(theatre, "coder.settings.model", "sonnet")
        records = step1(theatre.history.records())

    assert_file_bytes(
        config_file,
        "defaults: &d\n  model: opus\ncoder:\n  settings:  # expanded from *d by troupe\n"
        "    model: sonnet\nreviewer:\n  settings: *d\n",
    )
    change = SetChange(ConfigPath.parse("coder.settings.model"), "opus", "sonnet")
    assert outcome == SetOutcome(written=(config_file,), changes=(change,))
    assert records == (
        *before,
        Step1Record(len(before) + 1, "set", "coder.settings.model", "opus", "sonnet"),
    )
    after = load_config(config_file)
    reviewer = _node(after.root, "reviewer.settings")
    assert reviewer.alias is not None and reviewer.alias.name == "d"
    assert node_to_plain(reviewer) == {"model": "opus"}
    assert _node(after.root, "coder.settings").alias is None


def test_nested_aliases_are_expanded_in_two_rounds_outermost_first(
    make_theatre: MakeTheatre,
) -> None:
    """Req 8.1, 7.7, 10.4: PATH `a.x.m` reached through `*d` and then the copy's inner `*e` expands
    both, each with its own comment, and sets the value in the inner copy; the anchors are
    unchanged and only the set leaf is recorded."""
    text = "e: &e\n  m: 1\nd: &d\n  x: *e\na: *d\n"
    directory = make_theatre(text)
    config_file = directory / ".troupe" / "troupe.yml"
    with open_theatre(directory) as theatre:
        outcome = _set(theatre, "a.x.m", "2")

    assert_file_bytes(
        config_file,
        "e: &e\n  m: 1\nd: &d\n  x: *e\na:  # expanded from *d by troupe\n"
        "  x:  # expanded from *e by troupe\n    m: 2\n",
    )
    assert outcome.changes == (SetChange(ConfigPath.parse("a.x.m"), 1, 2),)
    after = load_config(config_file)
    inner = _node(after.root, "d.x")
    assert inner.alias is not None and inner.alias.name == "e"


def test_replacement_expands_only_the_aliases_whose_values_change(
    make_theatre: MakeTheatre,
) -> None:
    """Req 8.2, 7.7: replacing `c` changes only the values reached through `*e`; `*e` is expanded
    and set, `*d` (all values unchanged) stays an alias."""
    text = "d: &d\n  m: 1\ne: &e\n  n: 1\nc:\n  s: *d\n  t: *e\n"
    directory = make_theatre(text)
    config_file = directory / ".troupe" / "troupe.yml"
    with open_theatre(directory) as theatre:
        outcome = _set(theatre, "c", "{s: {m: 1}, t: {n: 2}}")

    assert_file_bytes(
        config_file,
        "d: &d\n  m: 1\ne: &e\n  n: 1\nc:\n  s: *d\n  t:  # expanded from *e by troupe\n    n: 2\n",
    )
    assert outcome.changes == (SetChange(ConfigPath.parse("c.t.n"), 1, 2),)


def test_no_op_with_expansion_writes_the_copy_and_records_no_event(
    make_theatre: MakeTheatre,
) -> None:
    """Req 8.6, 10.4: VALUE equal to the current value through an alias still expands the alias
    (the copy holds the same value) and records no event."""
    directory = make_theatre(ISSUE_2)
    config_file = directory / ".troupe" / "troupe.yml"
    with open_theatre(directory) as theatre:
        before = step1(theatre.history.records())
        outcome = _set(theatre, "coder.settings.model", "opus")
        records = step1(theatre.history.records())

    assert_file_bytes(
        config_file,
        "defaults: &d\n  model: opus\ncoder:\n  settings:  # expanded from *d by troupe\n"
        "    model: opus\nreviewer:\n  settings: *d\n",
    )
    assert outcome == SetOutcome(written=(config_file,), changes=())
    assert records == before


def test_no_alias_on_path_with_expansion_is_the_usual_set(make_theatre: MakeTheatre) -> None:
    """Req 6.3, 10.4: with expansion requested and no alias on PATH, a no-op writes nothing and a
    change is the usual set."""
    directory = make_theatre(ISSUE_2)
    config_file = directory / ".troupe" / "troupe.yml"
    with open_theatre(directory) as theatre:
        assert _set(theatre, "defaults.model", "opus") == SetOutcome(written=(), changes=())
        outcome = _set(theatre, "defaults.model", "sonnet")

    assert_file_bytes(config_file, ISSUE_2.replace("opus", "sonnet"))
    assert outcome.written == (config_file,)


def test_write_failure_after_an_expansion_restores_the_original_files(
    make_theatre: MakeTheatre, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Req 10.3, 10.2: the expansion (troupe.yml) and the set (the shared text include t.txt) are
    written together; when writing t.txt fails, troupe.yml is restored to its original bytes (not
    to the expanded text), the error names PATH and t.txt, and no event is recorded."""
    text = (
        "defaults: &d\n  model: opus\n  notes: file:t.txt\ncoder:\n  settings: *d\n"
        "reviewer:\n  settings: *d\n"
    )
    directory = make_theatre(text, files={".troupe/t.txt": "old\n"})
    config_dir = directory / ".troupe"
    snapshot = snapshot_tree(directory)
    with open_theatre(directory) as theatre:
        before = step1(theatre.history.records())
        calls = _fail_replace(monkeypatch, {"t.txt": 1})
        with pytest.raises(WriteRefusedError) as caught:
            _set(theatre, "coder.settings.notes", "new")
        monkeypatch.undo()
        records = step1(theatre.history.records())

    assert calls == ["troupe.yml", "t.txt", "troupe.yml"]  # write, write, restore
    error = caught.value
    assert error.exit_code is ExitCode.WRITE_REFUSED
    assert error.file == config_dir / "t.txt"
    assert error.path == "coder.settings.notes"
    assert records == before
    assert snapshot_tree(directory) == snapshot


def test_expansion_and_shared_include_write_both_files(make_theatre: MakeTheatre) -> None:
    """Req 10.2, 10.3, 7.7: the same set without a failure writes the expansion into troupe.yml
    and VALUE into the shared t.txt, so the change appears at every place including t.txt."""
    text = (
        "defaults: &d\n  model: opus\n  notes: file:t.txt\ncoder:\n  settings: *d\n"
        "reviewer:\n  settings: *d\n"
    )
    directory = make_theatre(text, files={".troupe/t.txt": "old\n"})
    config_dir = directory / ".troupe"
    with open_theatre(directory) as theatre:
        outcome = _set(theatre, "coder.settings.notes", "new")

    assert_file_bytes(
        config_dir / "troupe.yml",
        "defaults: &d\n  model: opus\n  notes: file:t.txt\n"
        "coder:\n  settings:  # expanded from *d by troupe\n"
        "    model: opus\n    notes: file:t.txt\n"
        "reviewer:\n  settings: *d\n",
    )
    assert_file_bytes(config_dir / "t.txt", "new")
    assert outcome.written == (config_dir / "troupe.yml", config_dir / "t.txt")
    assert {str(change.path) for change in outcome.changes} == {
        "defaults.notes",
        "coder.settings.notes",
        "reviewer.settings.notes",
    }


@pytest.mark.parametrize(
    ("text", "path", "problem"),
    [
        # 8.4: the alias sits in a flow mapping; PATH is deeper than the alias place `c.s`.
        (
            "d: &d\n  m: 1\nc: {s: *d}\n",
            "c.s.m",
            "alias '*d' cannot be expanded inside a flow collection",
        ),
        # 8.5: the alias's line already has a comment.
        (
            "d: &d\n  m: 1\nc:\n  s: *d  # shared\n",
            "c.s.m",
            "the line of alias '*d' already has an end-of-line comment",
        ),
        # 9.2: the anchored node holds a merge key, so a copy would write `<<`.
        (
            "b: &b\n  k: 1\nd: &d\n  <<: *b\n  m: 1\nc:\n  s: *d\n",
            "c.s.m",
            "alias '*d' cannot be expanded: value comes from merge key '<<' at c.s.<<; merge keys "
            "are never written, and --expand-alias cannot expand them",
        ),
    ],
)
def test_expander_refusal_names_the_users_path(
    make_theatre: MakeTheatre, text: str, path: str, problem: str
) -> None:
    """Req 8.4, 8.5, 9.2: an expander refusal is re-raised with the user's PATH (not the alias
    place's path), keeping its problem and file; no file changes and nothing is recorded."""
    directory = make_theatre(text)
    snapshot = snapshot_tree(directory)
    with open_theatre(directory) as theatre:
        before = step1(theatre.history.records())
        with pytest.raises(WriteRefusedError) as caught:
            _set(theatre, path, "2")
        records = step1(theatre.history.records())

    error = caught.value
    assert error.exit_code is ExitCode.WRITE_REFUSED
    assert error.path == path
    assert error.file == directory / ".troupe" / "troupe.yml"
    assert error.problem == problem
    assert records == before
    assert snapshot_tree(directory) == snapshot


def test_merge_key_is_still_refused_with_expansion(make_theatre: MakeTheatre) -> None:
    """Req 9.2: a value received from a merge key is refused with expansion requested too."""
    directory = make_theatre("d: &d\n  m: 1\nc:\n  <<: *d\n")
    snapshot = snapshot_tree(directory)
    with open_theatre(directory) as theatre, pytest.raises(WriteRefusedError) as caught:
        _set(theatre, "c.m", "2")

    error = caught.value
    assert error.path == "c.m"
    assert error.problem == (
        "value comes from merge key '<<' at c.<<; merge keys are never written, and "
        "--expand-alias cannot expand them"
    )
    assert snapshot_tree(directory) == snapshot


def test_an_expansion_that_makes_no_progress_stops_the_loop(
    make_theatre: MakeTheatre, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Req 10.1: an expansion round that leaves the same alias in place stops the set with a
    refusal (exit 3, naming the alias's file and PATH) instead of looping, and writes nothing."""
    directory = make_theatre(ISSUE_2)
    snapshot = snapshot_tree(directory)
    monkeypatch.setattr(expand, "expand_alias", lambda document, site: {})
    with open_theatre(directory) as theatre:
        before = step1(theatre.history.records())
        with pytest.raises(WriteRefusedError) as caught:
            _set(theatre, "coder.settings.model", "sonnet")
        records = step1(theatre.history.records())

    error = caught.value
    assert error.exit_code is ExitCode.WRITE_REFUSED
    assert error.file == directory / ".troupe" / "troupe.yml"
    assert error.path == "coder.settings.model"
    assert error.problem == "alias '*d' is still an alias after its expansion"
    assert records == before
    assert snapshot_tree(directory) == snapshot


REDEFINED = "e: &e\n  m: 1\nd: &d\n  x: *e\n  k: 1\nf: &e\n  m: 9\na: *d\n"
"""`*e` inside `&d` refers to the first `&e`; copied below the second `&e`, it would refer to
that one, so the copy of `*d` would change `a.x.m` from 1 to 9."""


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("a.y", "5"),  # a change (a new key in the copy)
        ("a.k", "1"),  # a no-op (8.6)
    ],
)
def test_expansion_that_would_change_values_elsewhere_is_refused(
    make_theatre: MakeTheatre, path: str, value: str
) -> None:
    """Req 7.5, 8.6, 10.2, 10.4: when an alias copied into the copy would refer to an anchor
    redefined before the alias place, the expansion would change values off PATH; the set is
    refused (exit 3) naming the expanded alias, its file and PATH, with no file change and no
    event, whether VALUE changes the value or not."""
    directory = make_theatre(REDEFINED)
    snapshot = snapshot_tree(directory)
    with open_theatre(directory) as theatre:
        before = step1(theatre.history.records())
        with pytest.raises(WriteRefusedError) as caught:
            _set(theatre, path, value)
        records = step1(theatre.history.records())

    error = caught.value
    assert error.exit_code is ExitCode.WRITE_REFUSED
    assert error.file == directory / ".troupe" / "troupe.yml"
    assert error.path == path
    assert error.problem == (
        "expanding alias '*d' would change values elsewhere (an alias in its copy would refer "
        "to an anchor redefined before this place)"
    )
    assert records == before
    assert snapshot_tree(directory) == snapshot
