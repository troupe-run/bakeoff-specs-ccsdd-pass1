"""Sets on configurations with remote content (config-remote-schema).

Remote content is read-only (task 3.3): a change written there is refused (exit 3, no file
changed) naming the local include line, while local values, overridden remote values and new keys
below a remote include are written in the local file. ``write_atomically`` never writes a remote
source (an internal backstop). Every CLI test runs the real console script against the local
``remote_server`` fixture.

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

from pathlib import Path

import pytest

from remote_support import RemoteServer
from set_support import run_config, run_set, snapshot_tree
from support import CLI_CONTEXT, MakeTheatre
from troupe.config.errors import ExitCode, WriteRefusedError
from troupe.config.model import LineEnding, SourceFile
from troupe.config.paths import ConfigPath
from troupe.config.theatre import open_theatre
from troupe.config.writeback.atomic import write_atomically
from troupe.config.writeback.remote_guard import REMOTE_READ_ONLY
from troupe.config.writeback.setter import set_value
from troupe.config.writeback.value import mark_include_strings, parse_value

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

    with open_theatre(theatre) as opened:
        value = mark_include_strings(
            parse_value(missing, path), path, opened.document.schema, include=True
        )
        with pytest.raises(WriteRefusedError) as caught:
            set_value(opened, path, value, CLI_CONTEXT)

    error = caught.value
    assert error.exit_code is ExitCode.WRITE_REFUSED
    assert error.file == theatre / ".troupe" / config_name
    assert error.path == "team"
    assert error.problem.startswith("written value cannot be read back: ")
    assert missing in error.problem
    assert remote_server.paths() == ["/s.yml", "/missing.yml"]
    assert snapshot_tree(theatre) == before
