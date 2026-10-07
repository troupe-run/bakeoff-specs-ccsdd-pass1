"""Remote content and the schema through the readers and the watcher (config-remote-schema).

Task 2.5: ``show --sources`` labels a scalar written in remote content with its URL exactly as
written (3.2); path-not-found and wrong-kind errors name a remote source by its URL (2.2); the
watcher watches the local files, both root candidates and the schema file, never polls a URL, and
fetches remote content again on every reload (3.4); a failing fetch while watching is reported,
records nothing, and the watcher keeps running (3.5).

Every test serves real HTTP from the local ``remote_server`` fixture and uses a real theatre on
disk, the real ``troupe`` console script or the real :class:`Watcher` running in a background
thread; nothing reaches the internet and nothing is mocked.

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

from remote_support import RemoteServer
from support import CliResult, MakeTheatre, RunCli
from troupe.config.constants import CONFIG_DIR_NAME, CONFIG_FILE_NAMES, SCHEMA_FILE_NAME
from troupe.config.errors import ExitCode
from troupe.config.history import HistoryRecord
from troupe.config.store import HistoryStore
from troupe.config.theatre import open_theatre
from troupe.config.watch import Watcher

CONFIG_NAMES = ["troupe.yml", "troupe.yaml"]

HAPPENS_SECONDS = 2.0  # positive watch assertions: within 2 s of the change
NOTHING_SECONDS = 1.5  # negative watch assertions wait this long, then check
POLL_SECONDS = 0.1
JOIN_SECONDS = 10.0

SCHEMA_OPEN = '{"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object"}\n'
SCHEMA_NUMBER_A = (
    '{"$schema": "https://json-schema.org/draft/2020-12/schema",'
    ' "properties": {"a": {"type": "string"}}}\n'
)


# ---- helpers ----


def _show(run_cli: RunCli, theatre: Path, *args: str) -> CliResult:
    return run_cli("config", "show", *args, "--theatre", str(theatre))


def _config_dir(theatre: Path) -> Path:
    return theatre / CONFIG_DIR_NAME


def _records(theatre: Path) -> tuple[HistoryRecord, ...]:
    """The recorded history, read under the theatre lock without reconciling."""
    fd = os.open(_config_dir(theatre), os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        store = HistoryStore.open(_config_dir(theatre))
        try:
            return store.records()
        finally:
            store.close()
    finally:
        os.close(fd)


def _ops(records: tuple[HistoryRecord, ...]) -> list[tuple[str, str, object, object]]:
    return [(record.op, record.path, record.old, record.new) for record in records]


def _write(path: Path, text: str) -> None:
    """Save ``text`` atomically (new file renamed over the old)."""
    temporary = path.with_name(f".{path.name}.test-tmp")
    temporary.write_bytes(text.encode("utf-8"))
    os.replace(temporary, path)


def _wait_until(condition: Callable[[], bool], *, since: float, what: str) -> None:
    deadline = since + HAPPENS_SECONDS
    while not condition():
        if time.monotonic() >= deadline:
            pytest.fail(f"not within {HAPPENS_SECONDS} s: {what}")
        time.sleep(POLL_SECONDS)


def _wait_for_records(
    theatre: Path, expected_tail: list[tuple[str, str, object, object]], *, since: float
) -> None:
    _wait_until(
        lambda: _ops(_records(theatre))[-len(expected_tail) :] == expected_tail,
        since=since,
        what=f"recorded {expected_tail}",
    )


class RunningWatcher:
    """A started watcher whose loop runs in a background thread."""

    def __init__(self, watcher: Watcher, stop: threading.Event, reports: list[str]) -> None:
        self.watcher = watcher
        self.stop = stop
        self.reports = reports
        self.thread = threading.Thread(target=watcher.run, daemon=True)


@contextmanager
def running_watcher(theatre: Path) -> Iterator[RunningWatcher]:
    """Start a watcher on ``theatre``, run its loop in a thread, and always stop and join it."""
    stop = threading.Event()
    reports: list[str] = []
    watcher = Watcher(theatre, report=reports.append, stop_requested=stop.is_set)
    watcher.start()
    running = RunningWatcher(watcher, stop, reports)
    running.thread.start()
    try:
        yield running
    finally:
        stop.set()
        running.thread.join(JOIN_SECONDS)
        assert not running.thread.is_alive(), "the watcher did not stop"


# ---- readers (2.2, 3.2) ----


@pytest.mark.parametrize("config_name", CONFIG_NAMES)
def test_sources_labels_a_remote_scalar_with_its_url(
    make_theatre: MakeTheatre, run_cli: RunCli, remote_server: RemoteServer, config_name: str
) -> None:
    """Req 3.2: `show --sources` labels a scalar written in remote YAML, and a remote text include,
    with the URL exactly as written, and a local scalar with its local path."""
    shared = remote_server.route("/team/shared.yaml", "model: opus\nmax: 3\n") + "?rev=1"
    prompt = remote_server.route("/prompt.md", "be brief\n")
    theatre = make_theatre(
        f"name: demo\nshared: {shared}\nprompt: {prompt}\n", config_name=config_name
    )

    result = _show(run_cli, theatre, "--sources")

    assert (result.exit_code, result.stderr) == (0, ""), result.stderr
    assert (
        result.stdout
        == (
            f"name: demo # from .troupe/{config_name}\n"
            "shared:\n"
            f"  model: opus # from {shared}\n"
            f"  max: 3 # from {shared}\n"
            f"prompt: | # from {prompt}\n"
            "  be brief\n"
        ).encode()
    )


def test_sources_labels_remote_in_remote_with_the_inner_url(
    make_theatre: MakeTheatre, run_cli: RunCli, remote_server: RemoteServer
) -> None:
    """Req 3.2: a scalar in remote content included by remote content is labelled with the URL of
    the include whose content holds it."""
    inner = remote_server.route("/inner.yml", "deep: 1\n")
    outer = remote_server.route("/outer.yml", f"top: 2\nnested: {inner}\n")
    theatre = make_theatre(f"shared: {outer}\n")

    result = _show(run_cli, theatre, "shared", "--sources")

    assert (result.exit_code, result.stderr) == (0, ""), result.stderr
    assert result.stdout == (f"top: 2 # from {outer}\nnested:\n  deep: 1 # from {inner}\n").encode()


def test_path_not_found_in_remote_content_names_the_url(
    make_theatre: MakeTheatre, run_cli: RunCli, remote_server: RemoteServer
) -> None:
    """Req 2.2: a path that does not exist below remote content is refused naming the URL as the
    file holding the deepest existing part (never a mangled path)."""
    shared = remote_server.route("/shared.yaml", "model: opus\n")
    theatre = make_theatre(f"shared: {shared}\n")

    result = run_cli("config", "get", "shared.missing", "--theatre", str(theatre))

    assert result.exit_code == ExitCode.PATH
    assert result.stdout == b""
    assert result.stderr.startswith(f"{shared}: shared.missing: "), result.stderr


def test_wrong_kind_in_remote_content_names_the_url(
    make_theatre: MakeTheatre, run_cli: RunCli, remote_server: RemoteServer
) -> None:
    """Req 2.2: `get` of a mapping written in remote content names the URL as its file."""
    shared = remote_server.route("/shared.yaml", "agent:\n  model: opus\n")
    theatre = make_theatre(f"shared: {shared}\n")

    result = run_cli("config", "get", "shared.agent", "--theatre", str(theatre))

    assert result.exit_code == ExitCode.PATH
    assert result.stdout == b""
    assert result.stderr.startswith(f"{shared}: shared.agent: is a mapping"), result.stderr


# ---- watch (3.4, 3.5) ----


def test_watch_set_holds_local_files_root_candidates_and_schema_never_a_url(
    make_theatre: MakeTheatre, remote_server: RemoteServer
) -> None:
    """Req 3.4: the watch set is the local files of the load, both root candidates and the schema
    file (absent here); no URL stand-in is in it, and an idle watcher sends no request."""
    shared = remote_server.route("/shared.yaml", "model: opus\n")
    theatre = make_theatre(
        f"a: 1\nlocal: file:local.yml\nshared: {shared}\n", files={".troupe/local.yml": "x: 1\n"}
    )
    config_dir = _config_dir(theatre)
    with running_watcher(theatre) as running:
        requests = len(remote_server.requests)
        time.sleep(NOTHING_SECONDS)

        assert running.watcher.watch_set == frozenset(
            {config_dir / "local.yml", config_dir / SCHEMA_FILE_NAME}
            | {config_dir / name for name in CONFIG_FILE_NAMES}
        )
        assert len(remote_server.requests) == requests  # the URL is never polled
        assert running.reports == []


def test_schema_file_edit_reloads_and_fetches_again(
    make_theatre: MakeTheatre, remote_server: RemoteServer
) -> None:
    """Req 3.4: editing the schema file reloads the configuration, which fetches the remote
    content again; a schema the configuration breaks is reported and recorded nothing, and
    restoring it records the remote change seen meanwhile."""
    shared = remote_server.route("/shared.yaml", "model: opus\n")
    theatre = make_theatre(f"a: 1\nshared: {shared}\n")
    schema = _config_dir(theatre) / SCHEMA_FILE_NAME
    _write(schema, SCHEMA_OPEN)
    with open_theatre(theatre):
        pass
    with running_watcher(theatre) as running:
        before = _records(theatre)
        requests = len(remote_server.requests)

        broken = time.monotonic()
        _write(schema, SCHEMA_NUMBER_A)
        _wait_until(lambda: len(running.reports) == 1, since=broken, what="a schema report")
        assert running.reports[0].startswith(f"{schema}: "), running.reports[0]
        assert len(remote_server.requests) > requests  # the reload fetched again
        assert _records(theatre) == before

        remote_server.route("/shared.yaml", "model: sonnet\n")
        fixed = time.monotonic()
        _write(schema, SCHEMA_OPEN)
        _wait_for_records(theatre, [("set", "shared.model", "opus", "sonnet")], since=fixed)


def test_schema_file_created_while_watching_is_seen(
    make_theatre: MakeTheatre, remote_server: RemoteServer
) -> None:
    """Req 3.4: the schema file is watched while absent, so creating it reloads (and re-fetches)."""
    shared = remote_server.route("/shared.yaml", "model: opus\n")
    theatre = make_theatre(f"a: 1\nshared: {shared}\n")
    schema = _config_dir(theatre) / SCHEMA_FILE_NAME
    with running_watcher(theatre) as running:
        requests = len(remote_server.requests)
        created = time.monotonic()
        _write(schema, SCHEMA_NUMBER_A)

        _wait_until(lambda: len(running.reports) == 1, since=created, what="a schema report")
        assert running.reports[0].startswith(f"{schema}: "), running.reports[0]
        assert len(remote_server.requests) > requests


def test_failing_fetch_while_watching_is_reported_records_nothing_and_recovers(
    make_theatre: MakeTheatre, remote_server: RemoteServer
) -> None:
    """Req 3.4, 3.5: a local edit reloads and re-fetches; a failing fetch is reported naming the
    URL, records nothing, and the watcher keeps running and records once the fetch works."""
    shared = remote_server.route("/shared.yaml", "model: opus\n")
    theatre = make_theatre(f"a: 1\nshared: {shared}\n")
    config = _config_dir(theatre) / "troupe.yml"
    with open_theatre(theatre):
        pass
    with running_watcher(theatre) as running:
        before = _records(theatre)

        remote_server.route("/shared.yaml", "gone\n", status=404)
        broken = time.monotonic()
        _write(config, f"a: 2\nshared: {shared}\n")
        _wait_until(lambda: len(running.reports) == 1, since=broken, what="a fetch report")
        assert running.reports[0].startswith(f"{config}: shared: "), running.reports[0]
        assert shared in running.reports[0]
        time.sleep(NOTHING_SECONDS)
        assert _records(theatre) == before
        assert running.thread.is_alive()

        remote_server.route("/shared.yaml", "model: haiku\n")
        fixed = time.monotonic()
        _wait_until(
            lambda: (
                {("set", "a", 1, 2), ("set", "shared.model", "opus", "haiku")}
                <= set(_ops(_records(theatre)))
            ),
            since=fixed,
            what="the local edit and the remote change recorded",
        )
