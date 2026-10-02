"""The watcher loop in-process on real theatres.

config-file-sync 3.4, 3.5, 4.2, 4.4, 4.6, 4.8, 4.9, 5.1-5.7.

Each test builds a real theatre on disk, starts a :class:`Watcher` in the test thread (``start``)
and runs its loop in a background thread with a :class:`threading.Event` as the stop flag. The
watcher is always stopped and joined in teardown.

The history is observed the way ``history`` observes it, without reconciling: the test takes the
theatre's ``flock`` on its own descriptor of ``.troupe/`` (so it waits for a sync in progress) and
reads the store directly. It does not use ``open_theatre``, whose in-process guard refuses a second
holder of the same theatre. Positive timing assertions poll with a 2 s deadline measured from the
edit, no more often than every 100 ms; negative assertions wait 2.5 s and then check.

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

import fcntl
import os
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from support import MakeTheatre
from troupe.config import watch
from troupe.config.constants import CONFIG_DIR_NAME, HISTORY_STORE_NAME, WATCH_POLL_SECONDS
from troupe.config.errors import ConfigLoadError
from troupe.config.history import HistoryRecord
from troupe.config.recorded import recorded_leaves
from troupe.config.store import HistoryStore
from troupe.config.theatre import Theatre, open_theatre
from troupe.config.watch import Watcher

HAPPENS_SECONDS = 2.0  # 5.1, 5.3: reconciled within 2 s of the change
NOTHING_SECONDS = 2.5  # negative assertions wait this long, then check
HISTORY_POLL_SECONDS = 0.1  # never poll the history more often (each poll takes the lock)
JOIN_SECONDS = 10.0


class RunningWatcher:
    """A started watcher whose loop runs in a background thread."""

    def __init__(self, watcher: Watcher, stop: threading.Event, reports: list[str]) -> None:
        self.watcher = watcher
        self.stop = stop
        self.reports = reports
        self.thread = threading.Thread(target=watcher.run, daemon=True)

    def stop_and_join(self) -> None:
        self.stop.set()
        self.thread.join(JOIN_SECONDS)
        assert not self.thread.is_alive(), "the watcher did not stop"


@contextmanager
def running_watcher(
    directory: Path, *, poll_seconds: float | None = None
) -> Iterator[RunningWatcher]:
    """Start a watcher on ``directory``, run its loop in a thread, and always stop and join it."""
    stop = threading.Event()
    reports: list[str] = []
    if poll_seconds is None:
        watcher = Watcher(directory, report=reports.append, stop_requested=stop.is_set)
    else:
        watcher = Watcher(
            directory, report=reports.append, stop_requested=stop.is_set, poll_seconds=poll_seconds
        )
    watcher.start()
    running = RunningWatcher(watcher, stop, reports)
    running.thread.start()
    try:
        yield running
    finally:
        running.stop_and_join()


def _config_dir(theatre_dir: Path) -> Path:
    return theatre_dir / CONFIG_DIR_NAME


def _records(theatre_dir: Path) -> tuple[HistoryRecord, ...]:
    """The recorded history, read under the theatre lock without reconciling."""
    fd = os.open(_config_dir(theatre_dir), os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        store = HistoryStore.open(_config_dir(theatre_dir))
        try:
            return store.records()
        finally:
            store.close()
    finally:
        os.close(fd)  # closing the only descriptor releases the flock


def _ops(records: tuple[HistoryRecord, ...]) -> list[tuple[str, str, object, object]]:
    return [(record.op, record.path, record.old, record.new) for record in records]


def _imported(make_theatre: MakeTheatre, config: str, files: dict[str, str] | None = None) -> Path:
    theatre_dir = make_theatre(config, files=files)
    with open_theatre(theatre_dir):
        pass
    return theatre_dir


def _write(path: Path, text: str) -> None:
    """Save ``text`` atomically (new file renamed over the old), so the watcher never reads a
    half-written file; in-place rewrites are covered separately with :func:`_write_in_place`."""
    temporary = path.with_name(f".{path.name}.test-tmp")
    temporary.write_bytes(text.encode("utf-8"))
    os.replace(temporary, path)


def _write_in_place(path: Path, text: str) -> None:
    """Rewrite the existing file in place (truncate, then write)."""
    path.write_bytes(text.encode("utf-8"))


def _wait_for_records(
    theatre_dir: Path, expected_tail: list[tuple[str, str, object, object]], *, since: float
) -> tuple[HistoryRecord, ...]:
    """Poll the history until its last records are ``expected_tail``; fail 2 s after ``since``."""
    deadline = since + HAPPENS_SECONDS
    while True:
        records = _records(theatre_dir)
        if _ops(records)[-len(expected_tail) :] == expected_tail:
            return records
        if time.monotonic() >= deadline:
            pytest.fail(
                f"not recorded within {HAPPENS_SECONDS} s: {expected_tail}; got {_ops(records)}"
            )
        time.sleep(HISTORY_POLL_SECONDS)


def _wait_until(condition: Callable[[], bool], *, since: float, what: str) -> None:
    deadline = since + HAPPENS_SECONDS
    while not condition():
        if time.monotonic() >= deadline:
            pytest.fail(f"not within {HAPPENS_SECONDS} s: {what}")
        time.sleep(HISTORY_POLL_SECONDS)


def _live_leaves(theatre_dir: Path) -> dict[str, object]:
    """The recorded configuration: each live leaf's path and value."""
    return {leaf.path: leaf.value for leaf in recorded_leaves(_records(theatre_dir))}


def _user_files(theatre_dir: Path) -> dict[str, bytes]:
    """Every file under the theatre except the history store (and its SQLite side files)."""
    return {
        str(path.relative_to(theatre_dir)): path.read_bytes()
        for path in sorted(theatre_dir.rglob("*"))
        if path.is_file() and not path.name.startswith(HISTORY_STORE_NAME)
    }


@pytest.fixture
def sync_count(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Count the theatre opens (syncs) the watcher performs; opening is otherwise unchanged."""
    count = [0]

    def counting_open(directory: Path, *, reconcile: bool = True) -> Theatre:
        count[0] += 1
        return open_theatre(directory, reconcile=reconcile)

    monkeypatch.setattr(watch, "open_theatre", counting_open)
    return count


# ---- start (4.2, 4.8) ----


def test_start_reconciles_offline_edits(make_theatre: MakeTheatre) -> None:
    """Req 4.2: start reconciles the edits made while nothing was running, before watching."""
    theatre_dir = _imported(make_theatre, "a: 1\nb: 2\n")
    _write(_config_dir(theatre_dir) / "troupe.yml", "a: 5\nc: 3\n")

    watcher = Watcher(theatre_dir, report=lambda message: None, stop_requested=lambda: False)
    watcher.start()

    assert _ops(_records(theatre_dir))[2:] == [
        ("set", "a", 1, 5),
        ("set", "c", None, 3),
        ("remove", "b", 2, None),
    ]


def test_start_imports_a_never_imported_theatre(make_theatre: MakeTheatre) -> None:
    """Req 4.2: start imports a never-imported theatre and records no reconcile events."""
    theatre_dir = make_theatre("a: 1\n")

    Watcher(theatre_dir, report=lambda message: None, stop_requested=lambda: False).start()

    assert _ops(_records(theatre_dir)) == [("import", "a", None, 1)]


def test_start_raises_the_load_error_and_creates_nothing(make_theatre: MakeTheatre) -> None:
    """Req 4.8: a configuration that cannot be loaded at start raises and records nothing."""
    theatre_dir = make_theatre("a: [\n")
    reports: list[str] = []

    with pytest.raises(ConfigLoadError) as raised:
        Watcher(theatre_dir, report=reports.append, stop_requested=lambda: False).start()

    assert str(raised.value).startswith(f"{_config_dir(theatre_dir) / 'troupe.yml'}: ")
    assert reports == []
    assert not list(_config_dir(theatre_dir).glob(f"{HISTORY_STORE_NAME}*"))


# ---- observing edits (4.4, 5.1, 5.2, 5.5) ----


def test_edit_after_start_is_recorded_within_two_seconds(make_theatre: MakeTheatre) -> None:
    """Req 4.4, 5.1: an edit made right after start returns is recorded within 2 s."""
    theatre_dir = _imported(make_theatre, "a: 1\n")
    with running_watcher(theatre_dir) as running:
        edited = time.monotonic()
        _write(_config_dir(theatre_dir) / "troupe.yml", "a: 2\n")

        _wait_for_records(theatre_dir, [("set", "a", 1, 2)], since=edited)
        assert running.reports == []


def test_in_place_rewrite_is_recorded_within_two_seconds(make_theatre: MakeTheatre) -> None:
    """Req 5.1, 5.2: a file rewritten in place is reconciled within 2 s of the change.

    A sync may race the truncate-then-write and see an intermediate state, so only the final
    recorded value is asserted (5.5 allows recording intermediate states).
    """
    theatre_dir = _imported(make_theatre, "a: 1\n")
    config = _config_dir(theatre_dir) / "troupe.yml"
    inode = config.stat().st_ino
    with running_watcher(theatre_dir):
        edited = time.monotonic()
        _write_in_place(config, "a: 2\n")

        _wait_until(
            lambda: _ops(_records(theatre_dir))[-1][1:] in {("a", 1, 2), ("a", None, 2)},
            since=edited,
            what="a recorded as 2",
        )
    assert config.stat().st_ino == inode  # really rewritten in place


def test_edit_made_between_start_and_run_is_recorded(make_theatre: MakeTheatre) -> None:
    """Req 4.4: once start returns, a change made before the loop runs is still observed."""
    theatre_dir = _imported(make_theatre, "a: 1\n")
    stop = threading.Event()
    watcher = Watcher(theatre_dir, report=lambda message: None, stop_requested=stop.is_set)
    watcher.start()
    edited = time.monotonic()
    _write(_config_dir(theatre_dir) / "troupe.yml", "a: 2\n")
    thread = threading.Thread(target=watcher.run, daemon=True)
    thread.start()
    try:
        _wait_for_records(theatre_dir, [("set", "a", 1, 2)], since=edited)
    finally:
        stop.set()
        thread.join(JOIN_SECONDS)
    assert not thread.is_alive()


def test_edits_to_includes_at_any_depth_are_recorded(make_theatre: MakeTheatre) -> None:
    """Req 5.1: edits to a YAML include, a nested include and a text include are each recorded."""
    theatre_dir = _imported(
        make_theatre,
        "agents: file:agents.yml\nprompt: file:prompt.txt\n",
        files={
            ".troupe/agents.yml": "lead: file:sub/lead.yaml\nsolo: false\n",
            ".troupe/sub/lead.yaml": "model: opus\n",
            ".troupe/prompt.txt": "hello\n",
        },
    )
    config_dir = _config_dir(theatre_dir)
    with running_watcher(theatre_dir) as running:
        edited = time.monotonic()
        _write(config_dir / "agents.yml", "lead: file:sub/lead.yaml\nsolo: true\n")
        _wait_for_records(theatre_dir, [("set", "agents.solo", False, True)], since=edited)

        edited = time.monotonic()
        _write(config_dir / "sub" / "lead.yaml", "model: sonnet\n")
        _wait_for_records(
            theatre_dir, [("set", "agents.lead.model", "opus", "sonnet")], since=edited
        )

        edited = time.monotonic()
        _write(config_dir / "prompt.txt", "bye\n")
        _wait_for_records(theatre_dir, [("set", "prompt", "hello\n", "bye\n")], since=edited)
        assert running.reports == []


def test_replace_by_rename_is_recorded(make_theatre: MakeTheatre) -> None:
    """Req 5.2: a save that writes a new file and renames it over the old one is observed."""
    theatre_dir = _imported(make_theatre, "a: 1\n", files={".troupe/inc.yml": "x: 1\n"})
    config_dir = _config_dir(theatre_dir)
    _write(config_dir / "troupe.yml", "a: 1\ninc: file:inc.yml\n")
    with running_watcher(theatre_dir) as running:
        for target, text, expected in [
            ("troupe.yml", "a: 2\ninc: file:inc.yml\n", ("set", "a", 1, 2)),
            ("inc.yml", "x: 2\n", ("set", "inc.x", 1, 2)),
        ]:
            temporary = config_dir / f".{target}.editor-tmp"
            _write(temporary, text)
            edited = time.monotonic()
            os.replace(temporary, config_dir / target)
            _wait_for_records(theatre_dir, [expected], since=edited)
        assert running.reports == []


def test_root_rename_from_yml_to_yaml_is_followed(make_theatre: MakeTheatre) -> None:
    """Req 5.2: after ``troupe.yml`` is renamed to ``troupe.yaml``, edits to it are recorded."""
    theatre_dir = _imported(make_theatre, "a: 1\n")
    config_dir = _config_dir(theatre_dir)
    with running_watcher(theatre_dir) as running:
        renamed = time.monotonic()
        os.rename(config_dir / "troupe.yml", config_dir / "troupe.yaml")
        # Same leaves, so nothing is recorded; give the watcher time to see the rename.
        time.sleep(1.0)
        edited = time.monotonic()
        _write(config_dir / "troupe.yaml", "a: 2\n")

        records = _wait_for_records(theatre_dir, [("set", "a", 1, 2)], since=edited)
        assert len(records) == 2  # the import, then only the edit
        assert edited - renamed >= 1.0
        assert running.reports == []


def test_rename_with_changed_content_to_the_other_root_name_is_recorded(
    make_theatre: MakeTheatre,
) -> None:
    """Req 5.2: replacing ``troupe.yml`` by a ``troupe.yaml`` with new content is recorded."""
    theatre_dir = _imported(make_theatre, "a: 1\n")
    config_dir = _config_dir(theatre_dir)
    with running_watcher(theatre_dir):
        _write(config_dir / ".new", "a: 3\n")
        edited = time.monotonic()
        os.rename(config_dir / ".new", config_dir / "troupe.yaml")
        (config_dir / "troupe.yml").unlink()

        _wait_for_records(theatre_dir, [("set", "a", 1, 3)], since=edited)


def test_quick_succession_ends_with_the_recorded_configuration_equal_to_the_files(
    make_theatre: MakeTheatre,
) -> None:
    """Req 5.5: after quick edits, the last value is recorded within 2 s of the last edit."""
    theatre_dir = _imported(make_theatre, "a: 1\n")
    config = _config_dir(theatre_dir) / "troupe.yml"
    with running_watcher(theatre_dir):
        for value in range(2, 7):
            _write(config, f"a: {value}\n")
            time.sleep(0.03)
        edited = time.monotonic()
        _write(config, "a: 7\n")

        _wait_until(
            lambda: _records(theatre_dir)[-1].new == 7, since=edited, what="a recorded as 7"
        )
        records = _records(theatre_dir)
        assert [record.op for record in records[1:]] == ["set"] * (len(records) - 1)
        assert records[-1].path == "a"


def test_edit_made_while_a_sync_is_loading_is_recorded_at_the_next_tick(
    make_theatre: MakeTheatre, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Req 4.4, 5.1: the baseline is the snapshot taken before the load, so an edit landing
    after the files were loaded but before the sync ends is still recorded."""
    theatre_dir = _imported(make_theatre, "a: 1\n")
    config = _config_dir(theatre_dir) / "troupe.yml"
    edits = iter(["a: 3\n"])
    edited: list[float] = []

    def open_then_edit(directory: Path, *, reconcile: bool = True) -> Theatre:
        theatre = open_theatre(directory, reconcile=reconcile)  # loads and reconciles a: 2
        text = next(edits, None)
        if text is not None:
            edited.append(time.monotonic())
            _write(config, text)
        return theatre

    with running_watcher(theatre_dir):
        monkeypatch.setattr(watch, "open_theatre", open_then_edit)
        _write(config, "a: 2\n")
        _wait_until(lambda: bool(edited), since=time.monotonic(), what="a sync")

        _wait_for_records(theatre_dir, [("set", "a", 1, 2), ("set", "a", 2, 3)], since=edited[0])


def test_transient_content_read_by_a_sync_is_repaired_when_the_file_returns(
    make_theatre: MakeTheatre, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Req 4.4, 5.5, 5.7: the baseline is what the load read, so when a sync loads a transient
    state (the file truncated mid-save) and the file then returns to the bytes the sync's snapshot
    saw, the next tick sees the difference and the recorded configuration equals the files."""
    theatre_dir = _imported(make_theatre, "a: 1\nb: 2\n")
    config = _config_dir(theatre_dir) / "troupe.yml"
    transient: list[float] = []

    def load_mid_save(directory: Path, *, reconcile: bool = True) -> Theatre:
        if transient:
            return open_theatre(directory, reconcile=reconcile)
        saved = config.read_bytes()
        config.write_bytes(b"")  # a save truncates the file ...
        theatre = open_theatre(directory, reconcile=reconcile)  # ... the load reads it empty ...
        config.write_bytes(saved)  # ... and the save ends with the very same bytes
        transient.append(time.monotonic())
        return theatre

    with running_watcher(theatre_dir) as running:
        monkeypatch.setattr(watch, "open_theatre", load_mid_save)
        _write(config, "a: 1\nb: 3\n")
        _wait_until(lambda: bool(transient), since=time.monotonic(), what="the transient load")

        _wait_until(
            lambda: _live_leaves(theatre_dir) == {"a": 1, "b": 3},
            since=transient[0],
            what="the recorded configuration equal to the files",
        )
        assert running.reports == []


def test_in_place_rewrite_with_unchanged_content_leaves_the_record_equal_to_the_files(
    make_theatre: MakeTheatre,
) -> None:
    """Req 5.2, 5.7: rewriting a file in place with the same content, repeatedly, ends with the
    recorded configuration equal to the files (a sync may race a rewrite and see it truncated)."""
    theatre_dir = _imported(make_theatre, "a: 1\nb: 2\n")
    config = _config_dir(theatre_dir) / "troupe.yml"
    with running_watcher(theatre_dir):
        for _ in range(50):
            _write_in_place(config, "a: 1\nb: 2\n")
            time.sleep(0.01)
        edited = time.monotonic()

        _wait_until(
            lambda: _live_leaves(theatre_dir) == {"a": 1, "b": 2},
            since=edited,
            what="the recorded configuration equal to the files",
        )
        time.sleep(0.5)
        assert _live_leaves(theatre_dir) == {"a": 1, "b": 2}


# ---- includes added or removed by hand (3.4, 3.5) ----


def test_hand_added_include_is_followed_and_its_later_edits_are_recorded(
    make_theatre: MakeTheatre,
) -> None:
    """Req 3.4: a hand-added include's leaves are recorded and later edits to it are followed."""
    theatre_dir = _imported(make_theatre, "a: 1\n", files={".troupe/b.yml": "x: 1\ny: 2\n"})
    config_dir = _config_dir(theatre_dir)
    with running_watcher(theatre_dir) as running:
        edited = time.monotonic()
        _write(config_dir / "troupe.yml", "a: 1\nb: file:b.yml\n")
        _wait_for_records(
            theatre_dir, [("set", "b.x", None, 1), ("set", "b.y", None, 2)], since=edited
        )

        edited = time.monotonic()
        _write(config_dir / "b.yml", "x: 1\ny: 3\n")
        _wait_for_records(theatre_dir, [("set", "b.y", 2, 3)], since=edited)
        assert running.reports == []


def test_edit_to_a_hand_added_include_right_after_it_is_added_is_recorded(
    make_theatre: MakeTheatre,
) -> None:
    """Req 3.4: an edit to a newly included file made right after the include is not missed."""
    theatre_dir = _imported(make_theatre, "a: 1\n", files={".troupe/b.yml": "x: 1\n"})
    config_dir = _config_dir(theatre_dir)
    with running_watcher(theatre_dir):
        _write(config_dir / "troupe.yml", "a: 1\nb: file:b.yml\n")
        _write(config_dir / "b.yml", "x: 2\n")
        edited = time.monotonic()

        _wait_until(
            lambda: (
                _ops(_records(theatre_dir))[-1] in {("set", "b.x", None, 2), ("set", "b.x", 1, 2)}
            ),
            since=edited,
            what="b.x recorded as 2",
        )


def test_removed_include_file_is_no_longer_looked_at(
    make_theatre: MakeTheatre, sync_count: list[int]
) -> None:
    """Req 3.5: once an include is removed by hand, edits to its file cause no sync or event."""
    theatre_dir = _imported(
        make_theatre, "a: 1\nb: file:b.yml\n", files={".troupe/b.yml": "x: 1\n"}
    )
    config_dir = _config_dir(theatre_dir)
    with running_watcher(theatre_dir) as running:
        edited = time.monotonic()
        _write(config_dir / "troupe.yml", "a: 1\n")
        _wait_for_records(theatre_dir, [("remove", "b.x", 1, None)], since=edited)
        time.sleep(0.5)  # let the watch set settle
        syncs = sync_count[0]
        before = _records(theatre_dir)

        _write(config_dir / "b.yml", "x: 2\n")
        time.sleep(NOTHING_SECONDS)

        assert sync_count[0] == syncs
        assert _records(theatre_dir) == before
        assert running.reports == []


# ---- changes that record nothing (5.6, 5.7) ----


def test_unrelated_file_change_causes_no_sync_and_no_event(
    make_theatre: MakeTheatre, sync_count: list[int]
) -> None:
    """Req 5.6: a change to a file that is neither the config file nor an include is ignored."""
    theatre_dir = _imported(make_theatre, "a: 1\n", files={".troupe/other.yml": "a: 1\n"})
    config_dir = _config_dir(theatre_dir)
    with running_watcher(theatre_dir) as running:
        syncs = sync_count[0]
        before = _records(theatre_dir)

        _write(config_dir / "other.yml", "a: 2\n")
        _write(theatre_dir / "notes.txt", "x\n")
        time.sleep(NOTHING_SECONDS)

        assert sync_count[0] == syncs
        assert _records(theatre_dir) == before
        assert running.reports == []


def test_touch_and_leaf_preserving_rewrite_record_nothing(
    make_theatre: MakeTheatre, sync_count: list[int]
) -> None:
    """Req 5.7: a touch is not a change, and a rewrite with the same leaves records no event."""
    theatre_dir = _imported(make_theatre, "a: 1\n")
    config = _config_dir(theatre_dir) / "troupe.yml"
    with running_watcher(theatre_dir) as running:
        syncs = sync_count[0]
        before = _records(theatre_dir)

        os.utime(config, (time.time() + 10, time.time() + 10))
        time.sleep(1.0)
        assert sync_count[0] == syncs  # same bytes: not even a sync

        _write(config, "# a comment\na:   1\n")
        time.sleep(NOTHING_SECONDS)

        assert sync_count[0] > syncs  # the rewrite was looked at ...
        assert _records(theatre_dir) == before  # ... and recorded nothing
        assert running.reports == []


# ---- failures while watching (5.3, 5.4) ----


def test_half_written_yaml_is_reported_once_records_nothing_and_recovers(
    make_theatre: MakeTheatre,
) -> None:
    """Req 5.3: invalid YAML gives one report naming the file, no events, then recovery in 2 s."""
    theatre_dir = _imported(make_theatre, "a: 1\nb: 2\n")
    config = _config_dir(theatre_dir) / "troupe.yml"
    with running_watcher(theatre_dir) as running:
        before = _records(theatre_dir)
        broken = time.monotonic()
        _write(config, "a: [1,\n")
        _wait_until(lambda: len(running.reports) >= 1, since=broken, what="a report")
        time.sleep(1.0)  # several more ticks, each retrying the load

        assert len(running.reports) == 1
        assert running.reports[0].startswith(f"{config}: ")
        assert _records(theatre_dir) == before  # never read as "every leaf removed"

        fixed = time.monotonic()
        _write(config, "a: 3\nb: 2\n")
        _wait_for_records(theatre_dir, [("set", "a", 1, 3)], since=fixed)
        assert len(running.reports) == 1


def test_same_failure_after_a_recovery_is_reported_again(make_theatre: MakeTheatre) -> None:
    """Req 5.3: the report memory is cleared by a successful sync."""
    theatre_dir = _imported(make_theatre, "a: 1\n")
    config = _config_dir(theatre_dir) / "troupe.yml"
    with running_watcher(theatre_dir) as running:
        for value in (2, 3):
            reports_before = len(running.reports)
            broken = time.monotonic()
            _write(config, "a: [\n")
            _wait_until(
                lambda: len(running.reports) > reports_before,  # noqa: B023 - used in this round only
                since=broken,
                what="a report",
            )
            fixed = time.monotonic()
            _write(config, f"a: {value}\n")
            _wait_for_records(theatre_dir, [("set", "a", value - 1, value)], since=fixed)

        assert len(running.reports) == 2
        assert running.reports[0] == running.reports[1]


def test_missing_include_target_is_retried_until_it_appears(make_theatre: MakeTheatre) -> None:
    """Req 5.3: a missing include target is reported, and its later creation is reconciled."""
    theatre_dir = _imported(make_theatre, "a: 1\n")
    config_dir = _config_dir(theatre_dir)
    with running_watcher(theatre_dir) as running:
        broken = time.monotonic()
        _write(config_dir / "troupe.yml", "a: 1\nb: file:b.yml\n")
        _wait_until(lambda: len(running.reports) == 1, since=broken, what="a report")
        assert running.reports[0].startswith(f"{config_dir / 'troupe.yml'}: b: ")
        assert str(config_dir / "b.yml") in running.reports[0]

        created = time.monotonic()
        _write(config_dir / "b.yml", "x: 1\n")
        _wait_for_records(theatre_dir, [("set", "b.x", None, 1)], since=created)


def test_unreadable_history_store_is_reported_and_outstanding_edits_recorded_later(
    make_theatre: MakeTheatre,
) -> None:
    """Req 5.4: a store failure is reported naming the store; the edit is recorded once readable."""
    if os.geteuid() == 0:
        pytest.skip("root ignores file permissions")
    theatre_dir = _imported(make_theatre, "a: 1\n")
    config_dir = _config_dir(theatre_dir)
    store = config_dir / HISTORY_STORE_NAME
    with running_watcher(theatre_dir) as running:
        store.chmod(0)
        try:
            broken = time.monotonic()
            _write(config_dir / "troupe.yml", "a: 2\n")
            _wait_until(lambda: len(running.reports) >= 1, since=broken, what="a report")
            time.sleep(0.6)  # retried on several ticks, still reported once
        finally:
            store.chmod(0o644)
        restored = time.monotonic()

        assert len(running.reports) == 1
        assert running.reports[0].startswith(f"{store}: ")
        _wait_for_records(theatre_dir, [("set", "a", 1, 2)], since=restored)
        assert len(running.reports) == 1


# ---- stop (4.6 via the flag) and no user file changes (4.9) ----


def test_stop_returns_within_one_poll_interval(make_theatre: MakeTheatre) -> None:
    """Req 4.6: the loop sleeps in steps that check the stop flag, so a stop requested in the
    middle of a long sleep returns within one default poll interval, not at the sleep's end."""
    theatre_dir = _imported(make_theatre, "a: 1\n")
    with running_watcher(theatre_dir, poll_seconds=3.0) as running:
        time.sleep(0.3)  # the loop is now in its 3 s sleep
        requested = time.monotonic()
        running.stop_and_join()
        elapsed = time.monotonic() - requested

    assert elapsed < WATCH_POLL_SECONDS


def test_run_returns_at_once_when_stop_was_already_requested(
    make_theatre: MakeTheatre, sync_count: list[int]
) -> None:
    """Req 4.6: a stop requested before the loop runs ends it without another sync."""
    theatre_dir = _imported(make_theatre, "a: 1\n")
    watcher = Watcher(theatre_dir, report=lambda message: None, stop_requested=lambda: True)
    watcher.start()
    syncs = sync_count[0]
    _write(_config_dir(theatre_dir) / "troupe.yml", "a: 2\n")

    watcher.run()

    assert sync_count[0] == syncs


def test_watching_never_changes_user_files(make_theatre: MakeTheatre) -> None:
    """Req 4.9: the watcher creates, modifies, renames and deletes no configuration file."""
    theatre_dir = _imported(
        make_theatre,
        "# root\na:   1   # keep\nb: file:b.yml\n",
        files={".troupe/b.yml": "x: 'quoted'\n", ".troupe/text.txt": "t\n"},
    )
    config_dir = _config_dir(theatre_dir)
    with running_watcher(theatre_dir):
        edited = time.monotonic()
        _write(
            config_dir / "troupe.yml", "# root\na:   2   # keep\nb: file:b.yml\nt: file:text.txt\n"
        )
        _wait_for_records(
            theatre_dir, [("set", "a", 1, 2), ("set", "t", None, "t\n")], since=edited
        )
        _write(config_dir / "b.yml", "x: [\n")
        expected = _user_files(theatre_dir)
        time.sleep(1.0)
        assert _user_files(theatre_dir) == expected

    assert _user_files(theatre_dir) == expected
    leftovers = {path.name for path in config_dir.iterdir()} - {
        "troupe.yml",
        "b.yml",
        "text.txt",
        HISTORY_STORE_NAME,
    }
    assert leftovers == set()
