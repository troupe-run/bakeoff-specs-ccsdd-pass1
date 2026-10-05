"""``troupe config watch`` racing other commands, failing loads, signals and clean-up, end to end.

Every test starts the real command in a subprocess through ``watch_support.WatchProcess`` and runs
the other commands (``set``, ``show``, ``history``) through the real console script. A positive
assertion polls ``history`` (at most every 100 ms) against a 2 s deadline measured from the edit; a
negative assertion waits 2.5 s and then checks. ``history`` never reconciles, so it is a safe
observer. Edits are made straight after the readiness line, with no ``history`` call first, so a
readiness race is not hidden.

Every test ends by stopping ``watch`` with a signal and checking exit 0, stdout exactly the one
readiness line and no traceback (4.5, 4.6), and then the clean-up (4.9, 7.1-7.3): the only files
anywhere under the test's directory are the files the test wrote, byte for byte (including an
include outside ``.troupe/``), plus ``.troupe/history.sqlite``; no SQLite ``-wal``/``-shm`` side
file (they exist only while the store is open, so their absence shows the store was closed), no
lock, temporary or other file, and a fresh ``history`` still reads the store.

Hand edits are written atomically (a temporary file renamed over the target), so neither ``watch``
nor a racing command ever reads a half-written file.
"""

import os
import signal
import subprocess
import threading
import time
from pathlib import Path

import pytest

from set_support import run_config, run_set
from support import step1_members, troupe_executable, write_theatre
from troupe.config.constants import CONFIG_DIR_NAME, HISTORY_STORE_NAME
from watch_support import HistoryEvent, WatchProcess

NEGATIVE_WAIT_SECONDS = 2.5
"""How long a test waits before asserting that nothing more was recorded."""

ROOT = (
    "name: demo\n"
    "agents: file:agents.yml\n"
    "shared: file:../shared/team.yml\n"
    "prompt: file:prompt.txt\n"
)
AGENTS = "lead:\n  model: opus\n  retries: 3\n"
TEAM = "size: 2\n"
PROMPT = "You are helpful.\n"

FILES = {
    ".troupe/troupe.yml": ROOT,
    ".troupe/agents.yml": AGENTS,
    "shared/team.yml": TEAM,
    ".troupe/prompt.txt": PROMPT,
}
"""The default theatre: a root, a YAML include, one outside ``.troupe/`` and a text include."""

IMPORTS: list[HistoryEvent] = [
    {"seq": 1, "op": "import", "path": "name", "old": None, "new": "demo"},
    {"seq": 2, "op": "import", "path": "agents.lead.model", "old": None, "new": "opus"},
    {"seq": 3, "op": "import", "path": "agents.lead.retries", "old": None, "new": 3},
    {"seq": 4, "op": "import", "path": "shared.size", "old": None, "new": 2},
    {"seq": 5, "op": "import", "path": "prompt", "old": None, "new": PROMPT},
]
"""The history ``watch`` records at start-up for the default theatre."""

STORE_FILE = f"{CONFIG_DIR_NAME}/{HISTORY_STORE_NAME}"


class Theatre:
    """A real theatre on disk, together with the exact bytes the test wrote to each user file."""

    def __init__(self, tmp_path: Path, files: dict[str, str]) -> None:
        self.base = tmp_path
        self.directory = write_theatre(tmp_path / "theatre", files=files)
        self.written: dict[str, bytes] = {
            relative: content.encode("utf-8") for relative, content in files.items()
        }

    def path(self, relative: str) -> Path:
        return self.directory / relative

    def edit(self, relative: str, content: str) -> None:
        """Save like an editor: write a new file next to the target, then rename it over it."""
        target = self.path(relative)
        temporary = target.with_name(f".{target.name}.edit-tmp")
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, target)
        self.written[relative] = content.encode("utf-8")

    def watch(self) -> WatchProcess:
        return WatchProcess(str(self.directory), cwd=self.base)

    def history(self) -> list[HistoryEvent]:
        """A fresh ``troupe config history`` run (not throttled: never used in a polling loop),
        each line projected onto its step-1 members."""
        result = run_config("history", "--theatre", str(self.directory), cwd=self.base)
        assert (result.exit_code, result.stderr) == (0, ""), result.stderr
        return [step1_members(line) for line in result.stdout.decode("utf-8").splitlines()]


def _theatre(tmp_path: Path, files: dict[str, str] | None = None) -> Theatre:
    return Theatre(tmp_path, dict(FILES if files is None else files))


def _set(seq: int, path: str, old: object, new: object) -> HistoryEvent:
    return {"seq": seq, "op": "set", "path": path, "old": old, "new": new}


def _ready(watch: WatchProcess) -> None:
    assert watch.wait_ready() == f"watching {watch.theatre}\n".encode()


def _stop(watch: WatchProcess, sig: signal.Signals = signal.SIGTERM, stderr: str = "") -> None:
    """Req 4.5, 4.6: the signal stops ``watch`` with exit 0; stdout is exactly the one line; stderr
    holds exactly the expected messages (so no traceback)."""
    assert watch.stop(sig) == 0, watch.stderr
    assert watch.stdout == f"watching {watch.theatre}\n".encode()
    assert watch.stderr == stderr
    assert "Traceback" not in watch.stderr


def _assert_clean(theatre: Theatre, *histories: list[HistoryEvent]) -> list[HistoryEvent]:
    """Req 4.9, 7.1, 7.2, 7.3: after the stop, the only files under the test's directory are the
    user files exactly as the test wrote them plus the history store file; no ``-wal``/``-shm``
    (the store is closed), no lock, temporary or other file. Only then (a ``history`` run would
    itself close a store left open and remove its side files) a fresh ``history`` reads the store,
    and its events are one of ``histories``, which are returned.
    """
    found: dict[str, bytes] = {}
    directories: set[str] = set()
    for directory, subdirectories, names in os.walk(theatre.directory):
        for subdirectory in subdirectories:
            directories.add(
                (Path(directory) / subdirectory).relative_to(theatre.directory).as_posix()
            )
        for name in names:
            file = Path(directory) / name
            found[file.relative_to(theatre.directory).as_posix()] = file.read_bytes()
    assert STORE_FILE in found, "the history store file is missing"
    del found[STORE_FILE]
    assert found == theatre.written
    expected_directories = {
        parent.as_posix()
        for relative in theatre.written
        for parent in Path(relative).parents
        if parent != Path(".")
    }
    assert directories == expected_directories
    assert sorted(os.listdir(theatre.base)) == ["theatre"]
    history = theatre.history()
    assert history in histories, f"unexpected history ({len(history)} events): {history!r}"
    return history


def _assert_store_closed_while_idle(theatre: Theatre) -> None:
    """Req 7.3: while ``watch`` sits idle between syncs (and no other command runs), it holds the
    history store closed: SQLite keeps ``-wal``/``-shm`` side files exactly while a connection is
    open, and there are none."""
    entries = sorted(os.listdir(theatre.path(CONFIG_DIR_NAME)))
    assert not [name for name in entries if name.startswith(f"{HISTORY_STORE_NAME}-")], entries


def _within_2_seconds(
    watch: WatchProcess, since: float, expected: list[HistoryEvent]
) -> list[HistoryEvent]:
    """Wait (2 s from ``since``) until the history is as long as ``expected``; check it."""
    events = watch.wait_for_history(lambda events: len(events) >= len(expected), since=since)
    assert events == expected
    return events


# ---- 6.1: a successful set while watch runs is recorded exactly once ----

HAND_EDIT_DELAYS = (0.0, 0.05, 0.1, 0.15, 0.2)
"""Pause between a hand edit and the following ``set``: before, during and after watch's sync."""

SETS_IN_A_ROW = [
    ("name", "prod", ".troupe/troupe.yml", ROOT.replace("name: demo", "name: prod")),
    ("agents.lead.model", "sonnet", ".troupe/agents.yml", AGENTS.replace("opus", "sonnet")),
    ("shared.size", "5", "shared/team.yml", "size: 5\n"),
    (
        "agents.lead.retries",
        "4",
        ".troupe/agents.yml",
        AGENTS.replace("opus", "sonnet").replace("3", "4"),
    ),
    ("name", "final", ".troupe/troupe.yml", ROOT.replace("name: demo", "name: final")),
]
"""``(PATH, VALUE, file, its expected content after the set)`` for five sets run back to back."""


def test_set_after_hand_edit_records_the_edit_once_then_exactly_the_sets_own_event(
    tmp_path: Path,
) -> None:
    """Req 6.1, 6.3, 6.4: while ``watch`` runs, a hand edit followed by a successful ``set`` of the
    same leaf (at five different delays, so ``set`` lands before, during and after ``watch``'s own
    sync) leaves exactly one reconcile ``set`` for the hand edit, whichever command recorded it,
    followed by exactly the set's own ``set`` with ``old`` chained, ``seq`` consecutive, and nothing
    more 2.5 s later; then, 4.9, 7.1-7.3, user files are as written and nothing else is left."""
    theatre = _theatre(tmp_path)
    expected = list(IMPORTS)
    previous = "demo"

    with theatre.watch() as watch:
        _ready(watch)
        for number, delay in enumerate(HAND_EDIT_DELAYS, start=1):
            hand, by_set = f"hand{number}", f"set{number}"
            theatre.edit(".troupe/troupe.yml", ROOT.replace("name: demo", f"name: {hand}"))
            time.sleep(delay)
            result = run_set(theatre.directory, "name", by_set)
            assert (result.exit_code, result.stdout, result.stderr) == (0, b"", ""), result.stderr
            content = ROOT.replace("name: demo", f"name: {by_set}")
            assert theatre.path(".troupe/troupe.yml").read_text(encoding="utf-8") == content
            theatre.written[".troupe/troupe.yml"] = content.encode("utf-8")
            seq = len(expected) + 1
            expected += [_set(seq, "name", previous, hand), _set(seq + 1, "name", hand, by_set)]
            previous = by_set
            assert watch.history() == expected, f"delay {delay} s"
        time.sleep(NEGATIVE_WAIT_SECONDS)
        assert watch.history() == expected
        _stop(watch)
    _assert_clean(theatre, expected)


def test_sets_in_a_row_each_record_exactly_their_own_event(tmp_path: Path) -> None:
    """Req 6.1, 6.4: while ``watch`` runs, five ``set`` commands run back to back, writing the root,
    a YAML include and an include outside ``.troupe/``; each records exactly its own ``set`` (seen
    straight after it returns), ``watch`` records nothing for their write-backs (none after 2.5 s),
    and the files hold exactly the set values; ``watch`` then idles with the store closed (7.3);
    then, 4.9, 7.1-7.3, nothing else is left."""
    theatre = _theatre(tmp_path)
    expected = list(IMPORTS)
    values: dict[str, object] = {event["path"]: event["new"] for event in IMPORTS}

    with theatre.watch() as watch:
        _ready(watch)
        for path, value, relative, content in SETS_IN_A_ROW:
            result = run_set(theatre.directory, path, value)
            assert (result.exit_code, result.stdout, result.stderr) == (0, b"", ""), result.stderr
            new = int(value) if value.isdigit() else value
            expected.append(_set(len(expected) + 1, path, values[path], new))
            values[path] = new
            assert watch.history() == expected, path
            assert theatre.path(relative).read_text(encoding="utf-8") == content
            theatre.written[relative] = content.encode("utf-8")
        time.sleep(NEGATIVE_WAIT_SECONDS)
        assert watch.history() == expected
        _assert_store_closed_while_idle(theatre)
        _stop(watch)
    _assert_clean(theatre, expected)


def test_sets_racing_watch_and_show_loops_each_record_exactly_their_own_event(
    tmp_path: Path,
) -> None:
    """Req 6.1, 6.3, 6.4: while ``watch`` runs and six threads run ``show`` back to back, ten
    ``set`` commands run one after the other; each records exactly its own ``set`` (seen straight
    after it returns) and no command records an event for its write-back, so after 2.5 s the
    history is the imports followed by exactly the ten sets, ``seq`` consecutive; every ``show``
    succeeds; then, 4.9, 7.1-7.3, nothing else is left."""
    theatre = _theatre(tmp_path)
    expected = list(IMPORTS)
    values = [f"r{number}" for number in range(1, 11)]

    with theatre.watch() as watch:
        _ready(watch)
        shows = _ShowLoops(theatre, "name")
        try:
            previous = "demo"
            for value in values:
                result = run_set(theatre.directory, "name", value)
                assert (result.exit_code, result.stderr) == (0, ""), result.stderr
                expected.append(_set(len(expected) + 1, "name", previous, value))
                previous = value
                assert watch.history() == expected, value
        finally:
            results = shows.stop()
        content = ROOT.replace("name: demo", f"name: {previous}")
        assert theatre.path(".troupe/troupe.yml").read_text(encoding="utf-8") == content
        theatre.written[".troupe/troupe.yml"] = content.encode("utf-8")
        assert len(results) >= SHOW_LOOPS
        shown = {f"{value}\n".encode() for value in ["demo", *values]}
        for code, stdout, stderr in results:
            assert (code, stderr) == (0, b""), stderr
            assert stdout in shown
        time.sleep(NEGATIVE_WAIT_SECONDS)
        assert watch.history() == expected
        _stop(watch)
    _assert_clean(theatre, expected)


# ---- 6.2: a refused set while watch runs records nothing for itself ----

SHARED_TWICE = {
    ".troupe/troupe.yml": "x: file:shared.yaml\ny: file:shared.yaml\nname: demo\n",
    ".troupe/shared.yaml": "k: 1\n",
}
SHARED_TWICE_IMPORTS: list[HistoryEvent] = [
    {"seq": 1, "op": "import", "path": "x.k", "old": None, "new": 1},
    {"seq": 2, "op": "import", "path": "y.k", "old": None, "new": 1},
    {"seq": 3, "op": "import", "path": "name", "old": None, "new": "demo"},
]


@pytest.mark.parametrize("hand_edit_first", [False, True], ids=["no-hand-edit", "after-hand-edit"])
def test_refused_set_records_nothing_for_itself(tmp_path: Path, hand_edit_first: bool) -> None:
    """Req 6.2, 6.3: while ``watch`` runs, a ``set`` that is refused (exit 3: it would change
    the YAML include root ``x``) records no event for itself and leaves every file unchanged,
    also 2.5 s later; a hand edit made just before it is recorded exactly once (by ``watch`` or
    by the set's reconcile-first); then, 4.9, 7.1-7.3, nothing else is left."""
    theatre = _theatre(tmp_path, SHARED_TWICE)
    expected = list(SHARED_TWICE_IMPORTS)

    with theatre.watch() as watch:
        _ready(watch)
        if hand_edit_first:
            theatre.edit(
                ".troupe/troupe.yml", SHARED_TWICE[".troupe/troupe.yml"].replace("demo", "hand")
            )
            expected.append(_set(4, "name", "demo", "hand"))
        result = run_set(theatre.directory, "x", "{k: 2}")
        assert (result.exit_code, result.stdout) == (3, b""), result.stderr
        assert result.stderr.startswith(f"{theatre.path('.troupe/shared.yaml')}: x: ")
        assert watch.history() == expected
        time.sleep(NEGATIVE_WAIT_SECONDS)
        assert watch.history() == expected
        _stop(watch)
    _assert_clean(theatre, expected)


# ---- 6.3, 6.4: watch racing repeated show commands on the same hand edit ----

MANY = 10
"""Leaves changed by each racing hand edit (so a batch is long enough to show interleaving)."""

SHOW_LOOPS = 6
"""Threads that each run ``show`` back to back, for as long as the race lasts."""

RACE_ROUNDS = 16


def _many_leaves(value: str) -> str:
    return "".join(f"k{index}: {value}\n" for index in range(MANY))


class _ShowLoops:
    """``SHOW_LOOPS`` threads each running the real ``show`` command again and again until stopped.

    Every run's exit code, stdout and stderr are kept, so the test can check that none failed.
    """

    def __init__(self, theatre: Theatre, path: str) -> None:
        self._argv = [
            str(troupe_executable()),
            *("config", "show", path, "--theatre", str(theatre.directory)),
        ]
        self._cwd = theatre.base
        self._stopping = threading.Event()
        self.results: list[tuple[int, bytes, bytes]] = []
        self._lock = threading.Lock()
        self._threads = [threading.Thread(target=self._loop) for _ in range(SHOW_LOOPS)]
        for thread in self._threads:
            thread.start()

    def _loop(self) -> None:
        while not self._stopping.is_set():
            done = subprocess.run(self._argv, cwd=self._cwd, capture_output=True, check=False)
            with self._lock:
                self.results.append((done.returncode, done.stdout, done.stderr))

    def stop(self) -> list[tuple[int, bytes, bytes]]:
        self._stopping.set()
        for thread in self._threads:
            thread.join(30)
        return self.results


def test_watch_and_concurrent_shows_record_each_hand_edit_exactly_once(tmp_path: Path) -> None:
    """Req 6.3, 6.4: while ``watch`` runs and six threads run ``show`` back to back on the same
    theatre, sixteen hand edits each changing ten leaves are made one after the other; whichever
    command reconciles an edit, it is recorded exactly once within 2 s of the edit, as one batch of
    ten ``set`` events in document order with consecutive ``seq`` numbers and nothing from another
    reconcile between them; every ``show`` succeeds (no store conflict); nothing more is recorded
    2.5 s after the last round; then, 4.9, 7.1-7.3, nothing else is left."""
    theatre = _theatre(tmp_path, {".troupe/troupe.yml": _many_leaves("v0")})
    expected: list[HistoryEvent] = [
        {"seq": index + 1, "op": "import", "path": f"k{index}", "old": None, "new": "v0"}
        for index in range(MANY)
    ]

    with theatre.watch() as watch:
        _ready(watch)
        shows = _ShowLoops(theatre, "k0")
        try:
            for round_number in range(1, RACE_ROUNDS + 1):
                old, new = f"v{round_number - 1}", f"v{round_number}"
                since = time.monotonic()
                theatre.edit(".troupe/troupe.yml", _many_leaves(new))
                start = len(expected) + 1
                expected += [_set(start + index, f"k{index}", old, new) for index in range(MANY)]
                _within_2_seconds(watch, since, expected)
        finally:
            results = shows.stop()
        assert len(results) >= SHOW_LOOPS
        values = {f"v{number}\n".encode() for number in range(RACE_ROUNDS + 1)}
        for code, stdout, stderr in results:
            assert (code, stderr) == (0, b""), stderr
            assert stdout in values
        time.sleep(NEGATIVE_WAIT_SECONDS)
        events = watch.history()
        assert events == expected
        assert [event["seq"] for event in events] == list(range(1, len(events) + 1))
        _stop(watch)
    _assert_clean(theatre, expected)


# ---- 5.3: files that cannot be loaded while watch runs ----

BROKEN = {
    "root": (".troupe/troupe.yml", "name: [demo\n", ".troupe/troupe.yml"),
    "outside-include": ("shared/team.yml", "size: [2\n", ".troupe/../shared/team.yml"),
}
"""``(file, invalid YAML, the file as named in the message relative to the theatre)``."""


@pytest.mark.parametrize("which", list(BROKEN))
def test_invalid_yaml_is_reported_once_records_nothing_and_recovers(
    tmp_path: Path, which: str
) -> None:
    """Req 5.3, 4.5: while ``watch`` runs, invalid YAML in the root or in an include outside
    ``.troupe/`` gives, within 2 s, one stderr message naming the file under the step 1 load-error
    rules; ``watch`` keeps running, records nothing and repeats no message over the following 2.5 s
    (many poll ticks); once the file is fixed the change is recorded within 2 s of the fix, and the
    history is then exactly the imports and that one ``set``, so nothing was recorded while the
    files could not load (``history`` itself cannot load them meanwhile); stdout stays exactly the
    one line; then, 4.9, 7.1-7.3, nothing else is left."""
    theatre = _theatre(tmp_path)
    relative, invalid, named = BROKEN[which]
    message_prefix = f"{theatre.directory}/{named}: invalid YAML: "

    with theatre.watch() as watch:
        _ready(watch)
        since = time.monotonic()
        theatre.edit(relative, invalid)
        while message_prefix not in watch.stderr_so_far():
            assert time.monotonic() - since < 2.0, watch.stderr_so_far()
            time.sleep(0.05)
        time.sleep(NEGATIVE_WAIT_SECONDS)  # ``history`` cannot load the files meanwhile
        stderr = watch.stderr_so_far()
        assert stderr.startswith(message_prefix), stderr
        assert stderr.count("\n") == 1 and stderr.endswith("\n"), stderr

        since = time.monotonic()
        if which == "root":
            theatre.edit(relative, ROOT.replace("name: demo", "name: fixed"))
            fixed = _set(6, "name", "demo", "fixed")
        else:
            theatre.edit(relative, "size: 7\n")
            fixed = _set(6, "shared.size", 2, 7)
        expected = _within_2_seconds(watch, since, [*IMPORTS, fixed])
        _stop(watch, stderr=stderr)
    _assert_clean(theatre, expected)


# ---- 4.5, 4.6, 7.2, 7.3: SIGINT and SIGTERM ----

SIGNALS = {"SIGINT": signal.SIGINT, "SIGTERM": signal.SIGTERM}


@pytest.mark.parametrize("name", list(SIGNALS))
def test_signal_while_idle_exits_0_and_leaves_only_the_users_files_and_the_store(
    tmp_path: Path, name: str
) -> None:
    """Req 4.5, 4.6, 4.9, 7.1, 7.2, 7.3: after recording a hand edit, ``watch`` sits idle with the
    store closed (no ``-wal``/``-shm``); SIGINT or SIGTERM then stops it with exit 0, no traceback,
    nothing but the one line on stdout; the store is closed, nothing else is left under
    ``.troupe/`` or outside it, every user file is as the test wrote it, and a fresh ``history``
    shows the recorded events."""
    theatre = _theatre(tmp_path)

    with theatre.watch() as watch:
        _ready(watch)
        since = time.monotonic()
        theatre.edit("shared/team.yml", "size: 3\n")
        expected = _within_2_seconds(watch, since, [*IMPORTS, _set(6, "shared.size", 2, 3)])
        time.sleep(0.5)
        _assert_store_closed_while_idle(theatre)
        _stop(watch, SIGNALS[name])
    _assert_clean(theatre, expected)


@pytest.mark.parametrize("delay", [0.0, 0.1, 0.2, 0.3])
@pytest.mark.parametrize("name", list(SIGNALS))
def test_signal_right_after_an_edit_exits_0_and_leaves_everything_clean(
    tmp_path: Path, name: str, delay: float
) -> None:
    """Req 4.6, 2.7, 7.2, 7.3: SIGINT or SIGTERM sent 0 to 0.3 s after a hand edit (before, while
    or after ``watch`` reconciles it) stops ``watch`` with exit 0 and no traceback; the history
    holds the edit's event or nothing for it, the store is closed and nothing else is left."""
    theatre = _theatre(tmp_path)

    with theatre.watch() as watch:
        _ready(watch)
        theatre.edit(".troupe/agents.yml", AGENTS.replace("opus", "haiku"))
        time.sleep(delay)
        _stop(watch, SIGNALS[name])
    _assert_clean(theatre, IMPORTS, [*IMPORTS, _set(6, "agents.lead.model", "opus", "haiku")])


# ---- 2.7: a signal during a large reconcile leaves all of its events or none ----

LARGE = 200
"""Leaves changed by the large hand edit."""

LARGE_DELAYS = [step * 0.05 for step in range(10)]
"""Pause between the large hand edit and SIGTERM: 0 to 0.45 s, across the 0.2 s poll cycle."""


def _large(value: int) -> str:
    return "".join(f"k{index:03d}: {value}\n" for index in range(LARGE))


@pytest.mark.parametrize("delay", LARGE_DELAYS)
def test_signal_during_a_large_reconcile_leaves_all_or_none_of_its_events(
    tmp_path: Path, delay: float
) -> None:
    """Req 2.7, 4.6, 7.2, 7.3: SIGTERM sent 0 to 0.45 s after a hand edit that changes 200 leaves
    stops ``watch`` with exit 0 and no traceback, and the history then holds either the whole batch
    of 200 ``set`` events (consecutive ``seq``, document order) or none of it; the store is closed
    and nothing else is left."""
    theatre = _theatre(tmp_path, {".troupe/troupe.yml": _large(0)})
    imports: list[HistoryEvent] = [
        {"seq": index + 1, "op": "import", "path": f"k{index:03d}", "old": None, "new": 0}
        for index in range(LARGE)
    ]
    batch = [_set(LARGE + index + 1, f"k{index:03d}", 0, 1) for index in range(LARGE)]

    with theatre.watch() as watch:
        _ready(watch)
        theatre.edit(".troupe/troupe.yml", _large(1))
        time.sleep(delay)
        _stop(watch)
    _assert_clean(theatre, imports, [*imports, *batch])
