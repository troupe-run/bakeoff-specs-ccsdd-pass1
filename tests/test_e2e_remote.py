"""Remote content and the schema through the readers and the watcher (config-remote-schema).

Task 2.5: ``show --sources`` labels a scalar written in remote content with its URL exactly as
written (3.2); path-not-found and wrong-kind errors name a remote source by its URL (2.2); the
watcher watches the local files, both root candidates and the schema file, never polls a URL, and
fetches remote content again on every reload (3.4); a failing fetch while watching is reported,
records nothing, and the watcher keeps running (3.5).

Task 4.1: ``get``, ``show`` and ``show --sources`` over remote YAML and text includes in the scalar
and mapping forms, remote in remote, for ``troupe.yml`` and ``troupe.yaml`` (1.1-1.3, 3.1, 3.2);
``!literal`` and ``format: uri`` strings print as strings and are never requested (4.1, 4.4, 4.6);
every fetch failure and every schema failure exits 4 naming the file, key path and URL or the
schema file, and creates no history state (1.4, 1.5, 2.1-2.3, 5.2, 5.3, 5.6); ``history`` imports
and records remote leaves, the next command reconciles a remote change, and ``history`` fails on an
unreachable URL (3.3, 3.6); the real ``troupe config watch`` re-fetches on a local edit and
survives a failing fetch (3.4, 3.5).

Every test serves real HTTP from the local ``remote_server`` fixture and uses a real theatre on
disk, the real ``troupe`` console script or the real :class:`Watcher` running in a background
thread; nothing reaches the internet and nothing is mocked.

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

import fcntl
import json
import os
import socket
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from remote_support import RemoteServer
from support import CliResult, MakeTheatre, RunCli, step1_members
from troupe.config.constants import (
    CONFIG_DIR_NAME,
    CONFIG_FILE_NAMES,
    HISTORY_STORE_NAME,
    SCHEMA_FILE_NAME,
)
from troupe.config.errors import ExitCode
from troupe.config.history import HistoryRecord
from troupe.config.store import HistoryStore
from troupe.config.theatre import open_theatre
from troupe.config.watch import Watcher
from watch_support import HistoryEvent, WatchProcess

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


# ---- task 4.1: helpers ----


def _get(run_cli: RunCli, theatre: Path, path: str) -> CliResult:
    return run_cli("config", "get", path, "--theatre", str(theatre))


def _history(run_cli: RunCli, theatre: Path) -> CliResult:
    return run_cli("config", "history", "--theatre", str(theatre))


def _history_events(result: CliResult) -> list[dict[str, object]]:
    assert (result.exit_code, result.stderr) == (0, ""), result.stderr
    return [step1_members(line) for line in result.stdout.decode("utf-8").splitlines()]


def _snapshot(theatre: Path) -> dict[str, bytes]:
    """Every file below the theatre directory, by relative path, with its exact bytes."""
    return {
        str(path.relative_to(theatre)): path.read_bytes()
        for path in sorted(theatre.rglob("*"))
        if path.is_file()
    }


def _history_state(theatre: Path) -> list[str]:
    return sorted(path.name for path in _config_dir(theatre).glob(f"{HISTORY_STORE_NAME}*"))


def _ok(result: CliResult) -> bytes:
    assert (result.exit_code, result.stderr) == (0, ""), result.stderr
    return result.stdout


def _refused_url() -> str:
    """A URL on 127.0.0.1 whose port nothing listens on (bound, then released)."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return f"http://127.0.0.1:{port}/shared.yaml"


def _event(seq: int, op: str, path: str, old: object, new: object) -> dict[str, object]:
    return {"seq": seq, "op": op, "path": path, "old": old, "new": new}


# ---- task 4.1: readers over remote content (1.1, 1.2, 1.3, 1.6, 3.1, 3.2) ----


@pytest.mark.parametrize("config_name", CONFIG_NAMES)
def test_get_and_show_print_remote_yaml_and_text_in_both_forms(
    make_theatre: MakeTheatre, run_cli: RunCli, remote_server: RemoteServer, config_name: str
) -> None:
    """Req 1.1, 1.2, 1.6, 3.1, 3.2: a scalar-form remote YAML include, a mapping-form one with a
    local override and a remote text include print through `get`, `show`, `show PATH` and
    `show --sources`; each URL is fetched once per command and no file is created or changed."""
    shared = remote_server.route("/team/shared.yaml", "model: opus\nmax: 3\n")
    merged = remote_server.route("/merged.yml", "x: 1\ny: 2\n")
    prompt = remote_server.route("/prompt.md", "be brief\n")
    theatre = make_theatre(
        f"name: demo\nshared: {shared}\nmerged:\n  $ref: {merged}\n  y: 30\nprompt: {prompt}\n",
        config_name=config_name,
    )
    before = _snapshot(theatre)

    assert _ok(_get(run_cli, theatre, "shared.model")) == b"opus\n"
    assert _ok(_get(run_cli, theatre, "merged.x")) == b"1\n"
    assert _ok(_get(run_cli, theatre, "merged.y")) == b"30\n"
    assert _ok(_get(run_cli, theatre, "prompt")) == b"be brief\n"
    assert _ok(_show(run_cli, theatre, "shared")) == b"model: opus\nmax: 3\n"
    assert _ok(_show(run_cli, theatre, "merged")) == b"x: 1\ny: 30\n"
    requests = len(remote_server.requests)
    assert _ok(_show(run_cli, theatre)) == (
        b"name: demo\nshared:\n  model: opus\n  max: 3\nmerged:\n  x: 1\n  y: 30\n"
        b"prompt: |\n  be brief\n"
    )
    assert sorted(remote_server.paths()[requests:]) == [
        "/merged.yml",
        "/prompt.md",
        "/team/shared.yaml",
    ]
    assert _ok(_show(run_cli, theatre, "merged", "--sources")) == (
        f"x: 1 # from {merged}\ny: 30 # from .troupe/{config_name}\n".encode()
    )

    # Readers create the history state in .troupe/ only; no include file is ever written.
    after = _snapshot(theatre)
    assert {name: data for name, data in after.items() if HISTORY_STORE_NAME not in name} == before


@pytest.mark.parametrize("config_name", CONFIG_NAMES)
def test_get_and_show_follow_remote_in_remote_in_both_forms(
    make_theatre: MakeTheatre, run_cli: RunCli, remote_server: RemoteServer, config_name: str
) -> None:
    """Req 1.1, 1.3, 3.1, 3.2: remote content includes remote YAML (scalar form), remote YAML in
    mapping form with an override, and remote text, at depth; readers print the fetched values and
    label each scalar with the URL whose content holds it."""
    deepest = remote_server.route("/deepest.yaml", "leaf: true\n")
    inner = remote_server.route("/inner.yml", f"deep: 1\nbelow: {deepest}\n")
    base = remote_server.route("/base.yml", "p: 1\nq: 2\n")
    note = remote_server.route("/note.txt", "hello\n")
    outer = remote_server.route(
        "/outer.yml", f"top: 2\nnested: {inner}\nmapped:\n  $ref: {base}\n  q: 20\nnote: {note}\n"
    )
    theatre = make_theatre(f"shared: {outer}\n", config_name=config_name)

    assert _ok(_get(run_cli, theatre, "shared.nested.below.leaf")) == b"true\n"
    assert _ok(_get(run_cli, theatre, "shared.mapped.q")) == b"20\n"
    assert _ok(_get(run_cli, theatre, "shared.note")) == b"hello\n"
    assert _ok(_show(run_cli, theatre, "shared")) == (
        b"top: 2\nnested:\n  deep: 1\n  below:\n    leaf: true\nmapped:\n  p: 1\n  q: 20\n"
        b"note: |\n  hello\n"
    )
    assert (
        _ok(_show(run_cli, theatre, "shared", "--sources"))
        == (
            f"top: 2 # from {outer}\n"
            "nested:\n"
            f"  deep: 1 # from {inner}\n"
            "  below:\n"
            f"    leaf: true # from {deepest}\n"
            "mapped:\n"
            f"  p: 1 # from {base}\n"
            f"  q: 20 # from {outer}\n"
            f"note: | # from {note}\n"
            "  hello\n"
        ).encode()
    )


# ---- task 4.1: literals (4.1, 4.4, 4.6) ----


@pytest.mark.parametrize("config_name", CONFIG_NAMES)
def test_literal_and_format_uri_strings_print_as_strings_and_are_never_requested(
    make_theatre: MakeTheatre, run_cli: RunCli, remote_server: RemoteServer, config_name: str
) -> None:
    """Req 4.1, 4.4, 4.6: `!literal` strings (plain and quoted, local and in remote content) and
    strings whose schema path has `format: uri` (in the root, in remote content and as list items)
    load as strings, print without a tag and are never fetched: the server sees only the one real
    include. Every literal points at this server, so a regression would show in its log."""
    hook = remote_server.url("/hook.yaml")
    lit = remote_server.url("/lit.yml")
    quoted = remote_server.url("/quoted.yaml")
    endpoint = remote_server.url("/endpoint.yaml")
    note = remote_server.url("/note.yml")
    item = remote_server.url("/item.yaml")
    shared = remote_server.route("/shared.yaml", f"endpoint: {endpoint}\nnote: !literal {note}\n")
    schema = json.dumps(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "properties": {
                "hook": {"type": "string", "format": "uri"},
                "hooks": {"items": {"format": "uri"}},
                "shared": {"properties": {"endpoint": {"format": "uri"}}},
            },
        }
    )
    theatre = make_theatre(
        f"hook: {hook}\nhooks:\n  - {item}\nlit: !literal {lit}\nquoted: !literal '{quoted}'\n"
        f"shared: {shared}\n",
        config_name=config_name,
        files={f"{CONFIG_DIR_NAME}/{SCHEMA_FILE_NAME}": schema},
    )

    assert _ok(_get(run_cli, theatre, "hook")) == f"{hook}\n".encode()
    assert _ok(_get(run_cli, theatre, "hooks.0")) == f"{item}\n".encode()
    assert _ok(_get(run_cli, theatre, "lit")) == f"{lit}\n".encode()
    assert _ok(_get(run_cli, theatre, "quoted")) == f"{quoted}\n".encode()
    assert _ok(_get(run_cli, theatre, "shared.endpoint")) == f"{endpoint}\n".encode()
    assert _ok(_get(run_cli, theatre, "shared.note")) == f"{note}\n".encode()
    assert (
        _ok(_show(run_cli, theatre))
        == (
            f"hook: {hook}\nhooks:\n  - {item}\nlit: {lit}\nquoted: {quoted}\n"
            f"shared:\n  endpoint: {endpoint}\n  note: {note}\n"
        ).encode()
    )
    assert (
        _ok(_show(run_cli, theatre, "--sources"))
        == (
            f"hook: {hook} # from .troupe/{config_name}\n"
            f"hooks:\n  - {item} # from .troupe/{config_name}\n"
            f"lit: {lit} # from .troupe/{config_name}\n"
            f"quoted: {quoted} # from .troupe/{config_name}\n"
            "shared:\n"
            f"  endpoint: {endpoint} # from {shared}\n"
            f"  note: {note} # from {shared}\n"
        ).encode()
    )
    assert set(remote_server.paths()) == {"/shared.yaml"}


# ---- task 4.1: fetch failures (1.4, 1.5, 2.1, 2.2, 2.3) ----


FETCH_FAILURES = ["404", "refused", "bad-utf-8", "bad-yaml", "file-in-remote", "cycle"]


def _failing_fetch(name: str, remote_server: RemoteServer, config: str) -> tuple[str, str]:
    """The configuration text and the exact stderr start for one fetch failure case."""
    if name == "404":
        url = remote_server.route("/shared.yaml", "gone\n", status=404)
        return f"a: 1\nshared: {url}\n", f"{config}: shared: cannot fetch include {url}: HTTP 404"
    if name == "refused":
        url = _refused_url()
        return (
            f"a: 1\nshared: {url}\n",
            f"{config}: shared: cannot fetch include {url}: connection refused",
        )
    if name == "bad-utf-8":
        url = remote_server.route("/bad.yaml", b"model: \xff\n")
        return (
            f"a:\n  b: {url}\n",
            f"{config}: a.b: cannot fetch include {url}: the content is not valid UTF-8",
        )
    if name == "bad-yaml":
        url = remote_server.route("/bad.yaml", "a: [\n")
        return f"a:\n  b: {url}\n", f"{url}: invalid YAML: "
    if name == "file-in-remote":
        url = remote_server.route("/shared.yaml", "x:\n  y: file:z.yml\n")
        return (
            f"a: {url}\n",
            f"{url}: a.x.y: local includes are not allowed in remote content: file:z.yml",
        )
    assert name == "cycle"
    first = remote_server.url("/first.yaml")
    second = remote_server.route("/second.yml", f"back: {first}\n")
    remote_server.route("/first.yaml", f"next: {second}\n")
    return f"a: {first}\n", f"{first}: include cycle: {first} -> {second} -> {first}"


@pytest.mark.parametrize("command", ["get", "show", "history"])
@pytest.mark.parametrize("name", FETCH_FAILURES)
def test_fetch_failure_exits_4_naming_file_key_path_and_url_with_no_history_state(
    make_theatre: MakeTheatre, run_cli: RunCli, remote_server: RemoteServer, name: str, command: str
) -> None:
    """Req 1.4, 1.5, 2.1, 2.2, 2.3, 3.6: a 404, a refused connection, a body that is not UTF-8,
    remote YAML that does not load, a `file:` include in remote content and a remote cycle each
    stop `get`, `show` and `history` with exit 4, nothing on stdout, one stderr line naming the
    file (or URL), key path and URL, no file changed and no history state created."""
    theatre = make_theatre("{}\n")
    config = _config_dir(theatre) / "troupe.yml"
    text, expected = _failing_fetch(name, remote_server, str(config))
    config.write_text(text, encoding="utf-8")
    before = _snapshot(theatre)

    args = {"get": ("get", "a"), "show": ("show",), "history": ("history",)}[command]
    result = run_cli("config", *args, "--theatre", str(theatre))

    assert result.exit_code == ExitCode.LOAD
    assert result.stdout == b""
    assert result.stderr.startswith(expected), result.stderr
    assert result.stderr.count("\n") == 1, result.stderr
    assert _snapshot(theatre) == before
    assert _history_state(theatre) == []


# ---- task 4.1: schema failures (5.2, 5.3, 5.6) ----


SCHEMA_FAILURES = {
    "remote-value": (
        '{"properties": {"shared": {"properties": {"max": {"type": "string"}}}}}',
        "shared.max: the configuration does not match the schema: 3 is not of type 'string'",
    ),
    "root": (
        '{"type": "array"}',
        "the configuration does not match the schema: ",
    ),
    "not-json": ("{nope", "the schema file is not valid JSON: "),
    "not-a-schema": (
        '{"type": 5}',
        "the schema is not a valid JSON Schema 2020-12 document: ",
    ),
    "other-dialect": (
        '{"$schema": "http://json-schema.org/draft-07/schema#"}',
        "the schema's $schema must be 'https://json-schema.org/draft/2020-12/schema'",
    ),
    "unresolvable-ref": ('{"$ref": "#/$defs/nope"}', "cannot resolve $ref: "),
}


@pytest.mark.parametrize("name", list(SCHEMA_FAILURES))
def test_schema_failure_exits_4_naming_the_schema_file_with_no_history_state(
    make_theatre: MakeTheatre, run_cli: RunCli, remote_server: RemoteServer, name: str
) -> None:
    """Req 5.2, 5.3, 5.6: a configuration (here with a value from remote content) that does not
    validate, and a schema that cannot be used, stop the load with exit 4, nothing on stdout, one
    stderr line naming the schema file (and the key path of the failure), no file changed (the
    schema file included) and no history state created."""
    schema_text, expected = SCHEMA_FAILURES[name]
    shared = remote_server.route("/shared.yaml", "model: opus\nmax: 3\n")
    theatre = make_theatre(
        f"shared: {shared}\n", files={f"{CONFIG_DIR_NAME}/{SCHEMA_FILE_NAME}": schema_text}
    )
    schema = _config_dir(theatre) / SCHEMA_FILE_NAME
    before = _snapshot(theatre)

    for args in (("get", "shared.model"), ("show",), ("history",)):
        result = run_cli("config", *args, "--theatre", str(theatre))

        assert result.exit_code == ExitCode.LOAD, args
        assert result.stdout == b""
        assert result.stderr.startswith(f"{schema}: {expected}"), result.stderr
        assert result.stderr.count("\n") == 1, result.stderr
        assert _snapshot(theatre) == before
        assert _history_state(theatre) == []


# ---- task 4.1: history (3.3, 3.6) ----


def test_history_imports_remote_leaves_and_records_a_remote_change_as_reconcile_events(
    make_theatre: MakeTheatre, run_cli: RunCli, remote_server: RemoteServer
) -> None:
    """Req 3.3, 3.6: `history` on a fresh theatre loads (fetching) and imports the leaves of
    remote YAML, mapping-form and text includes; after the remote content changes, the next
    command reconciles it into set, add and remove events, and `history` prints them."""
    shared = remote_server.route("/shared.yaml", "model: opus\nmax: 3\n")
    merged = remote_server.route("/merged.yml", "x: 1\ny: 2\n")
    prompt = remote_server.route("/prompt.md", "be brief\n")
    theatre = make_theatre(
        f"name: demo\nshared: {shared}\nmerged:\n  $ref: {merged}\n  y: 30\nprompt: {prompt}\n"
    )

    imports = [
        _event(1, "import", "name", None, "demo"),
        _event(2, "import", "shared.model", None, "opus"),
        _event(3, "import", "shared.max", None, 3),
        _event(4, "import", "merged.x", None, 1),
        _event(5, "import", "merged.y", None, 30),
        _event(6, "import", "prompt", None, "be brief\n"),
    ]
    assert _history_events(_history(run_cli, theatre)) == imports

    remote_server.route("/shared.yaml", "model: sonnet\nnew: 1\n")
    remote_server.route("/prompt.md", "be terse\n")
    assert _ok(_get(run_cli, theatre, "name")) == b"demo\n"

    events = _history_events(_history(run_cli, theatre))
    assert events[: len(imports)] == imports
    assert sorted(
        ((event["op"], event["path"], event["old"], event["new"]) for event in events[6:]),
        key=repr,
    ) == sorted(
        [
            ("set", "shared.model", "opus", "sonnet"),
            ("set", "shared.new", None, 1),
            ("remove", "shared.max", 3, None),
            ("set", "prompt", "be brief\n", "be terse\n"),
        ],
        key=repr,
    )
    assert [event["seq"] for event in events] == list(range(1, 11))


def test_history_fails_on_an_unreachable_url_and_keeps_the_history_state(
    make_theatre: MakeTheatre, run_cli: RunCli, remote_server: RemoteServer
) -> None:
    """Req 2.1, 2.3, 3.6: once the shared URL answers 404, `history` loads the configuration
    too, exits 4 naming the file, key path and URL, prints nothing, and changes no file and no
    history state; once the URL answers again, `history` prints the earlier events."""
    shared = remote_server.route("/shared.yaml", "model: opus\n")
    theatre = make_theatre(f"a: 1\nshared: {shared}\n")
    imports = _history_events(_history(run_cli, theatre))
    before = _snapshot(theatre)

    remote_server.route("/shared.yaml", "gone\n", status=404)
    requests = len(remote_server.requests)
    result = _history(run_cli, theatre)

    config = _config_dir(theatre) / "troupe.yml"
    assert result.exit_code == ExitCode.LOAD
    assert result.stdout == b""
    assert result.stderr.startswith(f"{config}: shared: cannot fetch include {shared}: HTTP 404")
    assert remote_server.paths()[requests:] == ["/shared.yaml"]
    assert _snapshot(theatre) == before

    remote_server.route("/shared.yaml", "model: opus\n")
    assert _history_events(_history(run_cli, theatre)) == imports


# ---- task 4.1: the real watch command (3.4, 3.5) ----


def test_watch_command_refetches_on_a_local_edit_and_survives_a_failing_fetch(
    make_theatre: MakeTheatre, remote_server: RemoteServer, tmp_path: Path
) -> None:
    """Req 3.4, 3.5: the real `troupe config watch` does not poll the URL, so a remote change is
    recorded only when a local edit reloads (and re-fetches); a failing fetch on a later edit is
    reported on stderr naming the URL and records nothing, and the command keeps watching, so the
    next local edit with the URL answering again is recorded."""
    shared = remote_server.route("/shared.yaml", "model: opus\n")
    theatre = make_theatre(f"a: 1\nshared: {shared}\n")
    config = _config_dir(theatre) / "troupe.yml"
    imports: list[HistoryEvent] = [
        _event(1, "import", "a", None, 1),
        _event(2, "import", "shared.model", None, "opus"),
    ]

    with WatchProcess(str(theatre), cwd=tmp_path) as watch:
        assert watch.wait_ready() == f"watching {theatre}\n".encode()
        assert watch.history() == imports

        remote_server.route("/shared.yaml", "model: sonnet\n")
        requests = len(remote_server.requests)
        time.sleep(NOTHING_SECONDS)
        assert len(remote_server.requests) == requests  # never polled
        assert watch.history() == imports

        edited = time.monotonic()
        _write(config, f"a: 2\nshared: {shared}\n")
        reloaded = [
            *imports,
            _event(3, "set", "a", 1, 2),
            _event(4, "set", "shared.model", "opus", "sonnet"),
        ]
        events = watch.wait_for_history(lambda events: len(events) >= 4, since=edited)
        assert events == reloaded
        assert watch.stderr_so_far() == ""

        remote_server.route("/shared.yaml", "gone\n", status=404)
        broken = time.monotonic()
        _write(config, f"a: 3\nshared: {shared}\n")
        _wait_until(lambda: shared in watch.stderr_so_far(), since=broken, what="a fetch report")
        assert f"{config}: shared: cannot fetch include {shared}: HTTP 404" in (
            watch.stderr_so_far()
        )
        time.sleep(NOTHING_SECONDS)
        # `history` would fail on the 404 too, so read the store directly (it never reconciles).
        assert len(_records(theatre)) == len(events)

        remote_server.route("/shared.yaml", "model: haiku\n")
        fixed = time.monotonic()
        _write(config, f"a: 4\nshared: {shared}\n")
        final = watch.wait_for_history(lambda events: len(events) >= 6, since=fixed)
        assert final[:4] == events
        assert [event["seq"] for event in final] == list(range(1, 7))
        assert {
            (event["op"], event["path"], event["old"], event["new"]) for event in final[4:]
        } == {
            ("set", "a", 2, 4),
            ("set", "shared.model", "sonnet", "haiku"),
        }
