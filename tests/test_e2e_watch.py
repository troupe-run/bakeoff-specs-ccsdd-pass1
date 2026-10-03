"""``troupe config watch`` observing hand edits, end to end through the real console script.

Every test starts the real command in a subprocess through ``watch_support.WatchProcess`` and
asserts the exact ``troupe config history`` records (``history`` never reconciles, so it is a safe
observer). A positive assertion polls ``history`` (at most every 100 ms) against a 2 s deadline
measured from the edit; a negative assertion waits 2.5 s and then checks that nothing was recorded.
Every test ends by stopping ``watch`` with SIGTERM and checking exit 0, stdout exactly the one
readiness line, and an empty stderr (4.5, 4.6).

Most edits are written atomically (a temporary file renamed over the target) so that ``watch``
never sees a half-written file; quick successive states may be reconciled together (5.5), and
these tests assert only the final recorded configuration. One root-file edit is an ordinary
in-place write.
"""

import functools
import os
import signal
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from support import write_theatre
from troupe.config.constants import CONFIG_DIR_NAME
from watch_support import HistoryEvent, WatchProcess

NEGATIVE_WAIT_SECONDS = 2.5
"""How long a test waits before asserting that nothing was recorded."""

ROOT = "# root of the demo theatre\nname: demo\nagents: file:agents.yml\nprompt: file:prompt.txt\n"
AGENTS = "lead:\n  model: opus\n  retries: 3\nhelpers: file:sub/helpers.yml\n"
HELPERS = "count: 1\n"
PROMPT = "You are helpful.\n"

IMPORTS: list[HistoryEvent] = [
    {"seq": 1, "op": "import", "path": "name", "old": None, "new": "demo"},
    {"seq": 2, "op": "import", "path": "agents.lead.model", "old": None, "new": "opus"},
    {"seq": 3, "op": "import", "path": "agents.lead.retries", "old": None, "new": 3},
    {"seq": 4, "op": "import", "path": "agents.helpers.count", "old": None, "new": 1},
    {"seq": 5, "op": "import", "path": "prompt", "old": None, "new": "You are helpful.\n"},
]
"""The history ``watch`` records at start-up for the default theatre."""


def _theatre(tmp_path: Path, root: str = ROOT) -> Path:
    """A theatre whose root includes a YAML file (which includes another) and a text file."""
    return write_theatre(
        tmp_path / "theatre",
        root,
        files={
            ".troupe/agents.yml": AGENTS,
            ".troupe/sub/helpers.yml": HELPERS,
            ".troupe/prompt.txt": PROMPT,
        },
    )


def _config(theatre: Path, name: str = "troupe.yml") -> Path:
    return theatre / CONFIG_DIR_NAME / name


def _write_atomically(target: Path, content: str) -> None:
    """Save like an editor: write a new file next to ``target``, then rename it over ``target``."""
    temporary = target.with_name(f".{target.name}.edit-tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, target)


def _started(watch: WatchProcess, expected: list[HistoryEvent]) -> list[HistoryEvent]:
    """Wait for the readiness line, check it and the start-up history; return that history."""
    assert watch.wait_ready() == f"watching {watch.theatre}\n".encode()
    history = watch.history()
    assert history == expected
    return history


def _recorded_within_2_seconds(
    watch: WatchProcess, edit: Callable[[], object], before: list[HistoryEvent]
) -> list[HistoryEvent]:
    """Make ``edit`` and wait (2 s from the edit) until history grows; return the new events."""
    since = time.monotonic()
    edit()
    events = watch.wait_for_history(lambda events: len(events) > len(before), since=since)
    assert events[: len(before)] == before
    return events[len(before) :]


def _nothing_recorded(
    watch: WatchProcess, edit: Callable[[], object], before: list[HistoryEvent]
) -> None:
    """Make ``edit``, wait 2.5 s, and check that the history is unchanged."""
    edit()
    time.sleep(NEGATIVE_WAIT_SECONDS)
    assert watch.history() == before


def _stop_cleanly(watch: WatchProcess) -> None:
    """Req 4.5, 4.6: SIGTERM exits 0; stdout is exactly the one line; nothing on stderr."""
    assert watch.stop(signal.SIGTERM) == 0
    assert watch.stdout == f"watching {watch.theatre}\n".encode()
    assert watch.stderr == ""


def _set(seq: int, path: str, old: object, new: object) -> HistoryEvent:
    return {"seq": seq, "op": "set", "path": path, "old": old, "new": new}


def _remove(seq: int, path: str, old: object) -> HistoryEvent:
    return {"seq": seq, "op": "remove", "path": path, "old": old, "new": None}


# ---- 4.3, 4.4, 4.5, 5.1, 5.2: an edit made the moment the line appears is recorded ----


def test_in_place_edit_made_the_moment_the_line_appears_is_recorded(tmp_path: Path) -> None:
    """Req 4.3, 4.4, 4.5, 5.1, 5.2: no edit is made before the line ``watching <dir>`` appears;
    an ordinary in-place rewrite of the root made immediately after it (nothing in between) is
    recorded within 2 s, so the history is exactly the start-up import followed by that one
    ``set``; stdout stays exactly the one line until SIGTERM."""
    theatre = _theatre(tmp_path)
    config = _config(theatre)

    with WatchProcess(str(theatre), cwd=tmp_path) as watch:
        assert watch.wait_ready() == f"watching {theatre}\n".encode()
        since = time.monotonic()
        config.write_text(ROOT.replace("name: demo", "name: prod"), encoding="utf-8")
        expected = [*IMPORTS, _set(6, "name", "demo", "prod")]
        events = watch.wait_for_history(lambda events: len(events) >= len(expected), since=since)
        assert events == expected
        _stop_cleanly(watch)


# ---- 5.1, 2.8: edits to the root and to every kind of included file ----


EDITS = {
    "root": (
        "troupe.yml",
        ROOT.replace("name: demo", "name: prod"),
        _set(6, "name", "demo", "prod"),
    ),
    "yaml-include": (
        "agents.yml",
        AGENTS.replace("opus", "sonnet"),
        _set(6, "agents.lead.model", "opus", "sonnet"),
    ),
    "nested-include": ("sub/helpers.yml", "count: 2\n", _set(6, "agents.helpers.count", 1, 2)),
    "text-include": (
        "prompt.txt",
        "You are terse.\n",
        _set(6, "prompt", "You are helpful.\n", "You are terse.\n"),
    ),
}


@pytest.mark.parametrize("which", list(EDITS))
def test_edit_to_root_or_any_include_is_recorded_within_2_seconds(
    tmp_path: Path, which: str
) -> None:
    """Req 5.1, 2.1, 2.8: an edit to the root, a YAML include, an include inside an include, or a
    text include is recorded within 2 s as exactly one ``set`` event for the changed leaf."""
    theatre = _theatre(tmp_path)
    relative, content, expected = EDITS[which]
    target = theatre / CONFIG_DIR_NAME / relative

    with WatchProcess(str(theatre), cwd=tmp_path) as watch:
        before = _started(watch, IMPORTS)
        new = _recorded_within_2_seconds(watch, lambda: _write_atomically(target, content), before)
        assert new == [expected]
        _stop_cleanly(watch)


# ---- 5.1: the 2 s bound holds wherever in the watcher's poll cycle the edit falls ----

PHASE_BANDS = 5
"""Sessions the phase sweep is split into (so that pytest-xdist can run them side by side)."""

PHASE_STEP_SECONDS = 0.1
"""Spacing of the idle times the sweep covers; together they span 0 to 2.4 s."""

EDITS_PER_BAND = 5


@pytest.mark.parametrize("band", range(PHASE_BANDS))
def test_edit_after_any_idle_time_is_recorded_within_2_seconds(tmp_path: Path, band: int) -> None:
    """Req 5.1: after each record, ``watch`` is left idle for a different time before the next
    edit, so that the edits fall at different points of its poll cycle (the five bands together
    cover idle times 0 to 2.4 s in 0.1 s steps); every edit is still recorded within 2 s of the
    edit, as exactly one ``set``. A watcher that checks the files less often than about every
    2 s misses this bound for the edits that fall just after one of its checks."""
    theatre = _theatre(tmp_path)
    config = _config(theatre)
    idle_times = [
        PHASE_STEP_SECONDS * (band + PHASE_BANDS * step) for step in range(EDITS_PER_BAND)
    ]

    with WatchProcess(str(theatre), cwd=tmp_path) as watch:
        before = _started(watch, IMPORTS)
        previous = "demo"
        for number, idle in enumerate(idle_times, start=1):
            time.sleep(idle)
            value = f"v{number}"
            content = ROOT.replace("name: demo", f"name: {value}")
            new = _recorded_within_2_seconds(
                watch, functools.partial(_write_atomically, config, content), before
            )
            assert new == [_set(len(before) + 1, "name", previous, value)], f"idle {idle:.1f} s"
            before += new
            previous = value
        _stop_cleanly(watch)


# ---- 5.2: replace by rename, and renaming the root between .yml and .yaml ----


def test_save_by_writing_a_new_file_and_renaming_it_over_the_old_one_is_recorded(
    tmp_path: Path,
) -> None:
    """Req 5.2, 5.1: a save that writes a new file and renames it over the root is recorded."""
    theatre = _theatre(tmp_path)
    config = _config(theatre)
    replacement = config.with_name("troupe.yml.new")

    def save() -> None:
        replacement.write_text(ROOT.replace("name: demo", "name: renamed"), encoding="utf-8")
        os.replace(replacement, config)

    with WatchProcess(str(theatre), cwd=tmp_path) as watch:
        before = _started(watch, IMPORTS)
        new = _recorded_within_2_seconds(watch, save, before)
        assert new == [_set(6, "name", "demo", "renamed")]
        _stop_cleanly(watch)


def test_renaming_the_root_from_yml_to_yaml_is_followed(tmp_path: Path) -> None:
    """Req 5.2, 2.4, 5.1: renaming ``troupe.yml`` to ``troupe.yaml`` records nothing (no leaf
    differs), and a later edit to ``troupe.yaml`` is recorded within 2 s."""
    theatre = _theatre(tmp_path)
    old = _config(theatre, "troupe.yml")
    new_root = _config(theatre, "troupe.yaml")

    with WatchProcess(str(theatre), cwd=tmp_path) as watch:
        before = _started(watch, IMPORTS)
        _nothing_recorded(watch, lambda: os.rename(old, new_root), before)
        assert watch.stderr_so_far() == ""
        new = _recorded_within_2_seconds(
            watch,
            lambda: _write_atomically(new_root, ROOT.replace("name: demo", "name: yaml")),
            before,
        )
        assert new == [_set(6, "name", "demo", "yaml")]
        _stop_cleanly(watch)


# ---- 3.1, 3.2, 3.4: includes added by hand are followed ----


def test_hand_added_yaml_include_records_its_leaves_and_is_then_followed(tmp_path: Path) -> None:
    """Req 3.1, 3.4, 2.2, 2.6: adding a YAML include records a ``set`` per leaf it brings (with
    ``old`` null, in document order, including the leaf from its nested include); later edits to
    the newly included file and to the file it includes are each recorded within 2 s."""
    theatre = _theatre(tmp_path, "name: demo\n")
    config = _config(theatre)
    agents = theatre / CONFIG_DIR_NAME / "agents.yml"
    helpers = theatre / CONFIG_DIR_NAME / "sub" / "helpers.yml"

    with WatchProcess(str(theatre), cwd=tmp_path) as watch:
        before = _started(watch, IMPORTS[:1])
        added = _recorded_within_2_seconds(
            watch,
            lambda: _write_atomically(config, "name: demo\nagents: file:agents.yml\n"),
            before,
        )
        assert added == [
            _set(2, "agents.lead.model", None, "opus"),
            _set(3, "agents.lead.retries", None, 3),
            _set(4, "agents.helpers.count", None, 1),
        ]
        before += added
        edited = _recorded_within_2_seconds(
            watch, lambda: _write_atomically(agents, AGENTS.replace("3", "4")), before
        )
        assert edited == [_set(5, "agents.lead.retries", 3, 4)]
        before += edited
        nested = _recorded_within_2_seconds(
            watch, lambda: _write_atomically(helpers, "count: 7\n"), before
        )
        assert nested == [_set(6, "agents.helpers.count", 1, 7)]
        _stop_cleanly(watch)


def test_hand_added_text_include_records_its_text_and_is_then_followed(tmp_path: Path) -> None:
    """Req 3.2, 3.4, 2.8: adding a text include records one ``set`` with the file's exact text;
    a later edit to that text file is recorded within 2 s."""
    theatre = _theatre(tmp_path, "name: demo\n")
    config = _config(theatre)
    prompt = theatre / CONFIG_DIR_NAME / "prompt.txt"

    with WatchProcess(str(theatre), cwd=tmp_path) as watch:
        before = _started(watch, IMPORTS[:1])
        added = _recorded_within_2_seconds(
            watch,
            lambda: _write_atomically(config, "name: demo\nprompt: file:prompt.txt\n"),
            before,
        )
        assert added == [_set(2, "prompt", None, PROMPT)]
        before += added
        edited = _recorded_within_2_seconds(
            watch, lambda: _write_atomically(prompt, "Be brief.\n\n"), before
        )
        assert edited == [_set(3, "prompt", PROMPT, "Be brief.\n\n")]
        _stop_cleanly(watch)


# ---- 3.3, 3.5: includes removed by hand are no longer followed ----


def test_hand_removed_include_records_removes_and_its_file_is_then_ignored(tmp_path: Path) -> None:
    """Req 3.3, 3.5, 2.3, 2.6: removing an include records a ``remove`` per leaf it contributed
    (oldest first); later edits to the no-longer-included file, and to the file it included,
    record nothing within 2.5 s."""
    theatre = _theatre(tmp_path)
    config = _config(theatre)
    agents = theatre / CONFIG_DIR_NAME / "agents.yml"
    helpers = theatre / CONFIG_DIR_NAME / "sub" / "helpers.yml"

    with WatchProcess(str(theatre), cwd=tmp_path) as watch:
        before = _started(watch, IMPORTS)
        removed = _recorded_within_2_seconds(
            watch,
            lambda: _write_atomically(config, ROOT.replace("agents: file:agents.yml\n", "")),
            before,
        )
        assert removed == [
            _remove(6, "agents.lead.model", "opus"),
            _remove(7, "agents.lead.retries", 3),
            _remove(8, "agents.helpers.count", 1),
        ]
        before += removed

        def edit_former_includes() -> None:
            _write_atomically(agents, AGENTS.replace("opus", "haiku"))
            _write_atomically(helpers, "count: 99\n")

        _nothing_recorded(watch, edit_former_includes, before)
        _stop_cleanly(watch)


# ---- 2.4, 5.6, 5.7: edits that change no leaf record nothing ----


def _comment_only_root(theatre: Path) -> None:
    _write_atomically(
        _config(theatre), ROOT.replace("# root of the demo theatre", "# renamed comment")
    )


def _comment_only_include(theatre: Path) -> None:
    _write_atomically(theatre / CONFIG_DIR_NAME / "agents.yml", "# agents\n" + AGENTS)


def _formatting_only_root(theatre: Path) -> None:
    _write_atomically(
        _config(theatre),
        "# root of the demo theatre\n\nname:   'demo'\nprompt: \"file:prompt.txt\"\n"
        "agents:    file:agents.yml\n",
    )


def _formatting_only_include(theatre: Path) -> None:
    _write_atomically(
        theatre / CONFIG_DIR_NAME / "agents.yml",
        "lead: {retries: 3, model: \"opus\"}\nhelpers: 'file:sub/helpers.yml'\n",
    )


def _touch_all(theatre: Path) -> None:
    for relative in ("troupe.yml", "agents.yml", "sub/helpers.yml", "prompt.txt"):
        os.utime(theatre / CONFIG_DIR_NAME / relative)


def _rewrite_identical_bytes(theatre: Path) -> None:
    for relative in ("troupe.yml", "agents.yml", "sub/helpers.yml", "prompt.txt"):
        target = theatre / CONFIG_DIR_NAME / relative
        target.write_bytes(target.read_bytes())


def _unrelated_file_in_config_dir(theatre: Path) -> None:
    (theatre / CONFIG_DIR_NAME / "other.yml").write_text("name: elsewhere\n", encoding="utf-8")
    (theatre / CONFIG_DIR_NAME / "notes.txt").write_text("not included\n", encoding="utf-8")


def _unrelated_file_in_theatre(theatre: Path) -> None:
    (theatre / "README.md").write_text("# demo\n", encoding="utf-8")
    (theatre / "troupe.yml").write_text("name: outside\n", encoding="utf-8")


NO_LEAF_CHANGES: dict[str, Callable[[Path], None]] = {
    "comment-only-root": _comment_only_root,
    "comment-only-include": _comment_only_include,
    "formatting-only-root": _formatting_only_root,
    "formatting-only-include": _formatting_only_include,
    "touch": _touch_all,
    "identical-bytes": _rewrite_identical_bytes,
    "unrelated-in-config-dir": _unrelated_file_in_config_dir,
    "unrelated-in-theatre": _unrelated_file_in_theatre,
}


@pytest.mark.parametrize("which", list(NO_LEAF_CHANGES))
def test_edit_that_changes_no_leaf_records_nothing(tmp_path: Path, which: str) -> None:
    """Req 2.4, 5.6, 5.7: comment-only, formatting-only, touch-only and identical-bytes edits to
    watched files, and edits to files that are neither the config file nor included, record
    nothing within 2.5 s."""
    theatre = _theatre(tmp_path)

    with WatchProcess(str(theatre), cwd=tmp_path) as watch:
        before = _started(watch, IMPORTS)
        _nothing_recorded(watch, lambda: NO_LEAF_CHANGES[which](theatre), before)
        _stop_cleanly(watch)
