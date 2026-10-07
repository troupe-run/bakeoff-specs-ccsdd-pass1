"""Sets on configurations with remote content (config-remote-schema).

Remote content is read-only (task 3.3): a change written there is refused (exit 3, no file
changed) naming the local include line, while local values, overridden remote values and new keys
below a remote include are written in the local file. ``write_atomically`` never writes a remote
source (an internal backstop). Every CLI test runs the real console script against the local
``remote_server`` fixture.

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from remote_support import RemoteServer
from set_support import run_config, run_set, snapshot_tree
from support import CLI_CONTEXT, MakeTheatre, TheatreFiles
from troupe.config.errors import ExitCode, WriteRefusedError
from troupe.config.model import LineEnding, SourceFile
from troupe.config.paths import ConfigPath
from troupe.config.theatre import open_theatre
from troupe.config.writeback.atomic import write_atomically
from troupe.config.writeback.remote_guard import REMOTE_READ_ONLY
from troupe.config.writeback.setter import set_value
from troupe.config.writeback.value import parse_value

CONFIG_NAMES = ["troupe.yml", "troupe.yaml"]


@pytest.mark.parametrize("config_name", CONFIG_NAMES)
@pytest.mark.parametrize(
    ("root", "path", "line"),
    [
        pytest.param("local: 1\ns: {url}\n", "s.a", 2, id="scalar-form"),
        pytest.param('local: 1\n\ns:\n  $ref: "{url}"\n  b: 2\n', "s.a", 4, id="ref-form-quoted"),
    ],
)
def test_set_refuses_a_change_of_remote_content(
    make_theatre: MakeTheatre,
    remote_server: RemoteServer,
    config_name: str,
    root: str,
    path: str,
    line: int,
) -> None:
    """Req 6.1: changing a value written in remote content exits 3 with nothing on stdout, stderr
    naming the local file, PATH, the include string exactly as written and its line, and no user
    file changed."""
    url = remote_server.route("/s.yml", "a: 1\n")
    text = root.format(url=url)
    theatre = make_theatre(text, config_name=config_name)
    before = snapshot_tree(theatre)
    written = text.splitlines()[line - 1].split(": ", 1)[1]

    result = run_set(theatre, path, "9")

    assert result.exit_code == ExitCode.WRITE_REFUSED, result.stderr
    assert result.stdout == b""
    assert result.stderr == (
        f"{theatre / '.troupe' / config_name}: {path}: "
        + REMOTE_READ_ONLY.format(string=written, line=line)
        + "\n"
    )
    assert url in result.stderr
    assert snapshot_tree(theatre) == before


@pytest.mark.parametrize("config_name", CONFIG_NAMES)
@pytest.mark.parametrize(
    ("root", "path", "value", "expected"),
    [
        pytest.param("local: 1\ns: {url}\n", "local", "2", "local: 2\ns: {url}\n", id="local"),
        pytest.param(
            "local: 1\ns: {url}\n",
            "s.new",
            "1",
            "local: 1\ns:\n  $ref: {url}\n  new: 1\n",
            id="new-key-converts-to-mapping-form",
        ),
        pytest.param(
            "s:\n  $ref: {url}\n  a: 5\n",
            "s.a",
            "6",
            "s:\n  $ref: {url}\n  a: 6\n",
            id="overridden-value",
        ),
    ],
)
def test_set_writes_local_changes_next_to_remote_content(
    make_theatre: MakeTheatre,
    remote_server: RemoteServer,
    config_name: str,
    root: str,
    path: str,
    value: str,
    expected: str,
) -> None:
    """Req 6.3, 6.4: a local value, an overridden remote value and a new key below a scalar-form
    remote include (converted to mapping form, the URL unchanged) are written in the local file."""
    url = remote_server.route("/s.yml", "a: 1\n")
    theatre = make_theatre(root.format(url=url), config_name=config_name)

    result = run_set(theatre, path, value)

    assert result.exit_code == ExitCode.OK, result.stderr
    assert (theatre / ".troupe" / config_name).read_text() == expected.format(url=url)


@pytest.mark.parametrize("config_name", CONFIG_NAMES)
@pytest.mark.parametrize(
    ("remote", "path"),
    [
        pytest.param("base: &b\n  k: 1\nuse: *b\n", "team.use.k", id="alias"),
        pytest.param(
            "base: &b\n  k: 1\nm:\n  <<: *b\n  own: 2\n", "team.m.k", id="merge-key-value"
        ),
    ],
)
def test_set_refuses_aliases_and_merge_keys_in_remote_content(
    make_theatre: MakeTheatre,
    remote_server: RemoteServer,
    config_name: str,
    remote: str,
    path: str,
) -> None:
    """Req 6.1: a set reached through an alias or a merge key written in remote content exits 3
    naming the local include line (never suggesting --expand-alias), and changes no file."""
    url = remote_server.route("/team.yml", remote)
    theatre = make_theatre(f"local: 1\nteam: {url}\n", config_name=config_name)
    before = snapshot_tree(theatre)

    result = run_set(theatre, path, "9")

    assert result.exit_code == ExitCode.WRITE_REFUSED, result.stderr
    assert result.stdout == b""
    assert result.stderr == (
        f"{theatre / '.troupe' / config_name}: {path}: "
        + REMOTE_READ_ONLY.format(string=url, line=2)
        + "\n"
    )
    assert "--expand-alias" not in result.stderr
    assert snapshot_tree(theatre) == before


@pytest.mark.parametrize("config_name", CONFIG_NAMES)
def test_set_not_found_below_remote_content_names_the_url(
    make_theatre: MakeTheatre, remote_server: RemoteServer, config_name: str
) -> None:
    """Req 6.1, 10.1: a PATH that does not exist below remote content exits 5 naming the remote
    source by its exact URL, and changes no file."""
    url = remote_server.route("/team.yml", "x: 1\n")
    theatre = make_theatre(f"team: {url}\n", config_name=config_name)
    before = snapshot_tree(theatre)

    result = run_set(theatre, "team.x.y", "9")

    assert result.exit_code == ExitCode.PATH, result.stderr
    assert result.stdout == b""
    assert result.stderr.startswith(f"{url}: team.x.y: ")
    assert "http:/1" not in result.stderr
    assert snapshot_tree(theatre) == before


def test_write_atomically_never_writes_a_remote_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Req 1.6, 6.1: the atomic writer refuses a remote source naming its URL, and creates no
    file, not even at the source's stand-in path."""
    monkeypatch.chdir(tmp_path)
    url = "http://127.0.0.1:9/shared.yml"
    remote = SourceFile(
        path=Path(url), real_path=Path(url), text="a: 1\n", line_ending=LineEnding.LF, url=url
    )

    with pytest.raises(WriteRefusedError) as caught:
        write_atomically(remote, "a: 2\n")

    assert caught.value.file == url
    assert caught.value.problem == "internal error: remote content is never written"
    assert list(tmp_path.rglob("*")) == []


@pytest.mark.parametrize("config_name", CONFIG_NAMES)
@pytest.mark.parametrize(
    ("root", "args", "expected"),
    [
        pytest.param(
            "local: 1\ns: {url}\n",
            ("s.new", "1"),
            "local: 1\ns:\n  $ref: {url}\n  new: 1\n",
            id="new-key-below-remote",
        ),
        pytest.param(
            "local: 1\ns: {url}\n", ("local", "2"), "local: 2\ns: {url}\n", id="local-value"
        ),
        pytest.param(
            "base: &b\n  k: 1\nuse: *b\ns: {url}\n",
            ("use.k", "2", "--expand-alias"),
            "base: &b\n  k: 1\nuse:  # expanded from *b by troupe\n  k: 2\ns: {url}\n",
            id="alias-expansion-reload",
        ),
    ],
)
def test_set_reuses_fetched_remote_content(
    make_theatre: MakeTheatre,
    remote_server: RemoteServer,
    config_name: str,
    root: str,
    args: tuple[str, ...],
    expected: str,
) -> None:
    """Req 10.3: a set (with its read-back and any alias-expansion reload) reuses the remote
    content fetched when the configuration was loaded: the URL is requested exactly once."""
    url = remote_server.route("/s.yml", "a: 1\n")
    theatre = make_theatre(root.format(url=url), config_name=config_name)

    result = run_config("set", *args, "--theatre", str(theatre), cwd=theatre)

    assert result.exit_code == ExitCode.OK, result.stderr
    assert (theatre / ".troupe" / config_name).read_text() == expected.format(url=url)
    assert remote_server.paths() == ["/s.yml"]


@pytest.mark.parametrize("config_name", CONFIG_NAMES)
def test_set_of_an_unfetchable_new_remote_include_is_refused_naming_the_url(
    make_theatre: MakeTheatre, remote_server: RemoteServer, config_name: str
) -> None:
    """Req 9.4, 10.3: an include VALUE whose URL cannot be fetched on read-back is refused (exit
    3) naming the root file, PATH and the URL; the new URL is requested once, the loaded remote
    content is not requested again, and no file is changed."""
    loaded = remote_server.route("/s.yml", "a: 1\n")
    missing = remote_server.url("/missing.yml")
    theatre = make_theatre(f"local: 1\ns: {loaded}\n", config_name=config_name)
    before = snapshot_tree(theatre)
    path = ConfigPath.parse("team")

    with open_theatre(theatre) as opened, pytest.raises(WriteRefusedError) as caught:
        set_value(opened, path, parse_value(missing, path), CLI_CONTEXT, include=True)

    error = caught.value
    assert error.exit_code is ExitCode.WRITE_REFUSED
    assert error.file == theatre / ".troupe" / config_name
    assert error.path == "team"
    assert error.problem.startswith("written value cannot be read back: ")
    assert missing in error.problem
    assert remote_server.paths() == ["/s.yml", "/missing.yml"]
    assert snapshot_tree(theatre) == before


# --- task 4.2: the read-only rules for remote content, end to end ---------------------------------


def _history(theatre: Path) -> list[dict[str, Any]]:
    """Every ``troupe config history`` line of ``theatre``, parsed, oldest first."""
    result = run_config("history", "--theatre", str(theatre), cwd=theatre)
    assert (result.exit_code, result.stderr) == (ExitCode.OK, ""), result.stderr
    return [json.loads(line) for line in result.stdout.decode("utf-8").splitlines()]


def _get(theatre: Path, path: str) -> str:
    """``troupe config get PATH`` of ``theatre`` (exit 0 asserted), as text."""
    result = run_config("get", path, "--theatre", str(theatre), cwd=theatre)
    assert (result.exit_code, result.stderr) == (ExitCode.OK, ""), result.stderr
    return result.stdout.decode("utf-8")


def _fill(text: str, remote_server: RemoteServer) -> str:
    """``text`` with every ``{base}`` replaced by the server's base URL."""
    return text.replace("{base}", remote_server.base_url)


@pytest.mark.parametrize("config_name", CONFIG_NAMES)
@pytest.mark.parametrize(
    ("root", "files", "routes", "args", "local", "written", "line"),
    [
        pytest.param(
            "x: 1\nteam: {base}/team.yml\n",
            {},
            {"/team.yml": "a: 1\nb: 2\n"},
            ("team.a", "9"),
            None,
            "{base}/team.yml",
            2,
            id="scalar-form-value",
        ),
        pytest.param(
            "x: 1\nteam:\n  $ref: {base}/team.yml  # shared\n  b: 5\n",
            {},
            {"/team.yml": "a: 1\nb: 2\n"},
            ("team.a", "9"),
            None,
            "{base}/team.yml",
            3,
            id="ref-form-value-next-to-an-override",
        ),
        pytest.param(
            "team: '{base}/team.yml?v=1#top'\n",
            {},
            {"/team.yml": "a: 1\n"},
            ("team.a", "9"),
            None,
            "'{base}/team.yml?v=1#top'",
            1,
            id="quoted-with-query-and-fragment",
        ),
        pytest.param(
            "team: {base}/team.yml\n",
            {},
            {"/team.yml": "m: {a: 1, b: 2}\n"},
            ("team.m", "{a: 1}"),
            None,
            "{base}/team.yml",
            1,
            id="mapping-replacement-removing-a-remote-key",
        ),
        pytest.param(
            "team: {base}/team.yml\n",
            {},
            {"/team.yml": "l: [1, 2]\n"},
            ("team.l", "[1, 3]"),
            None,
            "{base}/team.yml",
            1,
            id="list-replacement-changing-a-remote-item",
        ),
        pytest.param(
            "x: 1\nteam: {base}/team.yml\n",
            {},
            {"/team.yml": "a: 1\n"},
            ("team.new", "1", "--in-include"),
            None,
            "{base}/team.yml",
            2,
            id="in-include-new-key",
        ),
        pytest.param(
            "x: 1\nteam: {base}/team.yml\n",
            {},
            {"/team.yml": "inner: {base}/inner.yml\n", "/inner.yml": "a: 1\n"},
            ("team.inner.a", "9"),
            None,
            "{base}/team.yml",
            2,
            id="remote-in-remote-names-the-outermost-local-include",
        ),
        pytest.param(
            "x: 1\nteam: file:team.yml\n",
            {".troupe/team.yml": "own: 1\nshared: {base}/s.yml\n"},
            {"/s.yml": "a: 1\n"},
            ("team.shared.a", "9"),
            "team.yml",
            "{base}/s.yml",
            2,
            id="remote-below-a-local-file-include",
        ),
        pytest.param(
            "x: 1\nnotes: {base}/notes.txt\n",
            {},
            {"/notes.txt": "hi\n"},
            ("notes", "hello"),
            None,
            "{base}/notes.txt",
            2,
            id="text-include-scalar-form",
        ),
        pytest.param(
            "x: 1\nnotes:\n  $ref: {base}/notes.md?raw=1\n",
            {},
            {"/notes.md": "hi\n"},
            ("notes", "hello"),
            None,
            "{base}/notes.md?raw=1",
            3,
            id="text-include-ref-form",
        ),
        pytest.param(
            "x: 1\nteam: {base}/team.yml\n",
            {},
            {"/team.yml": "base: &b\n  k: 1\nuse: *b\n"},
            ("team.use.k", "9", "--expand-alias"),
            None,
            "{base}/team.yml",
            2,
            id="alias-expansion-in-remote-content",
        ),
    ],
)
def test_set_refuses_every_change_of_remote_content_and_records_nothing(
    make_theatre: MakeTheatre,
    remote_server: RemoteServer,
    config_name: str,
    root: str,
    files: dict[str, str],
    routes: dict[str, str],
    args: tuple[str, ...],
    local: str | None,
    written: str,
    line: int,
) -> None:
    """Req 6.1, 6.2, 6.6: changing a remote value, a collection replacement that changes or
    removes remote content, ``--in-include`` below a remote include, a remote text include, an
    alias expansion in remote content, and remote content reached through a remote or a local
    include each exit 3 with nothing on stdout and stderr naming the local file holding the
    (outermost local) include directive, PATH, the include string exactly as written there and
    its line; every file is byte-identical and the history is unchanged."""
    for path, body in routes.items():
        remote_server.route(path, _fill(body, remote_server))
    theatre_files: TheatreFiles = {name: _fill(body, remote_server) for name, body in files.items()}
    theatre = make_theatre(_fill(root, remote_server), config_name=config_name, files=theatre_files)
    history_before = _history(theatre)
    before = snapshot_tree(theatre)
    holder = theatre / ".troupe" / (config_name if local is None else local)
    include_string = _fill(written, remote_server)

    result = run_set(theatre, *args)

    assert result.exit_code == ExitCode.WRITE_REFUSED, result.stderr
    assert result.stdout == b""
    assert result.stderr == (
        f"{holder}: {args[0]}: " + REMOTE_READ_ONLY.format(string=include_string, line=line) + "\n"
    )
    assert snapshot_tree(theatre) == before
    assert _history(theatre) == history_before


@pytest.mark.parametrize("config_name", CONFIG_NAMES)
@pytest.mark.parametrize(
    ("root", "value"),
    [
        pytest.param("x: 1\nteam: {url}\n", "5", id="scalar-form-by-a-scalar"),
        pytest.param("x: 1\nteam: {url}\n", "{a: 2}", id="scalar-form-by-a-mapping"),
        pytest.param("x: 1\nteam:\n  $ref: {url}\n  b: 3\n", "5", id="ref-form-by-a-scalar"),
    ],
)
def test_set_refuses_replacing_a_remote_include_root(
    make_theatre: MakeTheatre,
    remote_server: RemoteServer,
    config_name: str,
    root: str,
    value: str,
) -> None:
    """Req 6.5, 6.6: replacing the whole value of a remote YAML include (its include root)
    follows the include-root rule: exit 3, nothing on stdout, stderr holding the include string,
    no file changed and no event recorded."""
    url = remote_server.route("/team.yml", "a: 1\nb: 2\n")
    theatre = make_theatre(root.format(url=url), config_name=config_name)
    history_before = _history(theatre)
    before = snapshot_tree(theatre)

    result = run_set(theatre, "team", value)

    assert result.exit_code == ExitCode.WRITE_REFUSED, result.stderr
    assert result.stdout == b""
    assert result.stderr == (
        f"{url}: team: the whole value comes from the include {url}; "
        "change the included file instead\n"
    )
    assert snapshot_tree(theatre) == before
    assert _history(theatre) == history_before


@pytest.mark.parametrize("config_name", CONFIG_NAMES)
@pytest.mark.parametrize(
    ("root", "files", "path", "value", "changed", "expected", "old", "new", "reads"),
    [
        pytest.param(
            "x: 1\nteam: {base}/team.yml  # shared team\n",
            {},
            "team.newkey",
            "v",
            None,
            "x: 1\nteam:  # shared team\n  $ref: {base}/team.yml\n  newkey: v\n",
            None,
            "v",
            {"team.newkey": "v\n", "team.a": "1\n", "team.b": "2\n"},
            id="new-key-converts-scalar-form",
        ),
        pytest.param(
            "x: 1\nteam:\n  $ref: '{base}/team.yml'\n  b: 5\n",
            {},
            "team.newkey",
            "v",
            None,
            "x: 1\nteam:\n  $ref: '{base}/team.yml'\n  b: 5\n  newkey: v\n",
            None,
            "v",
            {"team.newkey": "v\n", "team.a": "1\n", "team.b": "5\n"},
            id="new-key-in-ref-form",
        ),
        pytest.param(
            "x: 1\nteam:\n  $ref: {base}/team.yml\n  b: 5\n",
            {},
            "team.b",
            "6",
            None,
            "x: 1\nteam:\n  $ref: {base}/team.yml\n  b: 6\n",
            5,
            6,
            {"team.a": "1\n", "team.b": "6\n"},
            id="overridden-remote-value",
        ),
        pytest.param(
            "x: 1\nteam: file:team.yml\n",
            {".troupe/team.yml": "own: 1\nshared: {base}/team.yml\n"},
            "team.shared.newkey",
            "v",
            "team.yml",
            "own: 1\nshared:\n  $ref: {base}/team.yml\n  newkey: v\n",
            None,
            "v",
            {"team.shared.newkey": "v\n", "team.shared.a": "1\n", "team.own": "1\n"},
            id="new-key-below-remote-in-a-local-file-include",
        ),
    ],
)
def test_set_writes_local_overrides_below_remote_includes_with_events(
    make_theatre: MakeTheatre,
    remote_server: RemoteServer,
    config_name: str,
    root: str,
    files: dict[str, str],
    path: str,
    value: str,
    changed: str | None,
    expected: str,
    old: object,
    new: object,
    reads: dict[str, str],
) -> None:
    """Req 6.3, 6.4: a new key below a remote include becomes a local override (a scalar-form
    include converted to mapping form, its include string unchanged) and an overridden remote
    value changes in the override, also below a local ``file:`` include; the local file is
    exactly the expected bytes, every other file is unchanged, ``get`` reads the new value next
    to the remote ones, and the change is one ``set`` event with its ``--reason``."""
    remote_server.route("/team.yml", "a: 1\nb: 2\n")
    theatre_files: TheatreFiles = {name: _fill(body, remote_server) for name, body in files.items()}
    theatre = make_theatre(_fill(root, remote_server), config_name=config_name, files=theatre_files)
    target = theatre / ".troupe" / (config_name if changed is None else changed)
    history_before = _history(theatre)
    before = snapshot_tree(theatre)

    result = run_set(theatre, path, value, "--reason", "share the team")

    assert result.exit_code == ExitCode.OK, result.stderr
    assert (result.stdout, result.stderr) == (b"", "")
    assert target.read_text() == _fill(expected, remote_server)
    after = snapshot_tree(theatre)
    changed_key = target.relative_to(theatre).as_posix()
    assert {k: v for k, v in after.items() if k != changed_key} == {
        k: v for k, v in before.items() if k != changed_key
    }
    events = _history(theatre)
    assert events[: len(history_before)] == history_before
    added = [
        (event["op"], event["path"], event["old"], event["new"], event["reason"])
        for event in events[len(history_before) :]
    ]
    assert added == [("set", path, old, new, "share the team")]
    assert {read: _get(theatre, read) for read in reads} == reads
