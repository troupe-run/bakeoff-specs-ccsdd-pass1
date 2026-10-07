"""Sets on configurations with remote content (config-remote-schema).

Task 2.4 pins the temporary refusal that keeps every write away from remote content until the
remote write guard exists (task 3.3): ``set`` refuses (exit 3, no file changed) any configuration
that has a remote source, and ``write_atomically`` never writes a remote source. Every CLI test
runs the real console script against the local ``remote_server`` fixture.

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

from pathlib import Path

import pytest

from remote_support import RemoteServer
from set_support import run_set, snapshot_tree
from support import MakeTheatre
from troupe.config.errors import ExitCode, WriteRefusedError
from troupe.config.model import LineEnding, SourceFile
from troupe.config.writeback.atomic import write_atomically

CONFIG_NAMES = ["troupe.yml", "troupe.yaml"]


@pytest.mark.parametrize("config_name", CONFIG_NAMES)
@pytest.mark.parametrize(
    ("path", "value"),
    [
        pytest.param("local", "2", id="local-value"),
        pytest.param("s.a", "9", id="remote-value"),
        pytest.param("s.new", "1", id="new-key-below-remote"),
        pytest.param("local", "1", id="no-op"),
    ],
)
def test_set_refuses_every_configuration_with_a_remote_source(
    make_theatre: MakeTheatre,
    remote_server: RemoteServer,
    config_name: str,
    path: str,
    value: str,
) -> None:
    """Req 6.1, 10.1: until the remote write guard exists, a set on a configuration holding a
    remote include exits 3 with nothing on stdout, stderr naming the root file, PATH and the URL,
    and no user file changed."""
    url = remote_server.route("/s.yml", "a: 1\n")
    theatre = make_theatre(f"local: 1\ns: {url}\n", config_name=config_name)
    before = snapshot_tree(theatre)

    result = run_set(theatre, path, value)

    assert result.exit_code == ExitCode.WRITE_REFUSED, result.stderr
    assert result.stdout == b""
    assert result.stderr == (
        f"{theatre / '.troupe' / config_name}: {path}: the configuration has a remote include "
        f"({url}); set does not change configurations with remote includes yet\n"
    )
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
