"""Fetching remote sources over real HTTP (config-remote-schema).

Every test runs against the local ``remote_server`` fixture; nothing reaches the internet and the
fetcher is never mocked.

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from remote_support import NO_PROXY_HOSTS, RemoteServer
from troupe.config.constants import REMOTE_FETCH_TIMEOUT_SECONDS
from troupe.config.fetch import FetchError, fetch_remote


def _reason(url: str, *, timeout: float = REMOTE_FETCH_TIMEOUT_SECONDS) -> str:
    with pytest.raises(FetchError) as caught:
        fetch_remote(url, timeout=timeout)
    assert str(caught.value) == caught.value.reason
    return caught.value.reason


def test_fetch_returns_the_exact_body_bytes_of_a_200(remote_server: RemoteServer) -> None:
    """Req 1.6: a 200 answer gives its body bytes unchanged (decoding is the loader's job)."""
    body = b"a: 1\r\nb: caf\xc3\xa9\n\xff"
    url = remote_server.route("/shared.yml", body, headers={"Content-Type": "text/yaml"})

    assert fetch_remote(url) == body


def test_fetch_accepts_any_2xx_status(remote_server: RemoteServer) -> None:
    """Req 2.1: only a final status outside 2xx fails; 203 is a success."""
    url = remote_server.route("/x.txt", b"text", status=203)

    assert fetch_remote(url) == b"text"


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        pytest.param(404, "HTTP 404 Not Found", id="404"),
        pytest.param(500, "HTTP 500 Internal Server Error", id="500"),
    ],
)
def test_fetch_refuses_a_non_2xx_status_with_its_code_and_phrase(
    remote_server: RemoteServer, status: int, reason: str
) -> None:
    """Req 2.1: a final status outside 2xx is a fetch error giving the status as its reason."""
    url = remote_server.route("/missing.yml", b"body is ignored", status=status)

    assert _reason(url) == reason


def test_fetch_refuses_an_unknown_path_as_404(remote_server: RemoteServer) -> None:
    """Req 2.1: the fixture answers 404 for a path it does not serve."""
    assert _reason(remote_server.url("/nowhere.yml")) == "HTTP 404 Not Found"


def test_fetch_follows_a_301_to_the_target_body(remote_server: RemoteServer) -> None:
    """Req 1.6: redirects are followed; the body is the final target's."""
    remote_server.route("/new.yml", b"moved: true\n")
    url = remote_server.redirect("/old.yml", "/new.yml", status=301)

    assert fetch_remote(url) == b"moved: true\n"
    assert remote_server.paths() == ["/old.yml", "/new.yml"]


def test_fetch_refuses_a_redirect_that_ends_in_404(remote_server: RemoteServer) -> None:
    """Req 2.1: the status that counts is the one after redirects."""
    url = remote_server.redirect("/old.yml", "/gone.yml", status=302)

    assert _reason(url) == "HTTP 404 Not Found"


def test_fetch_refuses_a_closed_port_as_connection_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Req 2.1: a server that cannot be reached is a fetch error with a short reason."""
    monkeypatch.setenv("NO_PROXY", NO_PROXY_HOSTS)
    monkeypatch.setenv("no_proxy", NO_PROXY_HOSTS)
    with RemoteServer() as server:
        url = server.url("/shared.yml")

    assert _reason(url) == "connection refused"


def test_fetch_gives_up_after_the_timeout(remote_server: RemoteServer) -> None:
    """Req 1.6, 2.1: a server that does not answer within the timeout is a fetch error."""
    url = remote_server.route("/slow.yml", b"late: true\n", delay=5.0)

    started = time.monotonic()
    reason = _reason(url, timeout=0.3)
    elapsed = time.monotonic() - started

    assert reason == "timed out"
    assert elapsed < 3.0


def test_fetch_default_timeout_is_the_step_7_constant() -> None:
    """Req 1.6: the fetcher waits at most 10 seconds unless told otherwise."""
    defaults = fetch_remote.__kwdefaults__

    assert defaults == {"timeout": REMOTE_FETCH_TIMEOUT_SECONDS}
    assert REMOTE_FETCH_TIMEOUT_SECONDS == 10


def test_fetch_sends_exactly_one_plain_get_per_call(remote_server: RemoteServer) -> None:
    """Req 1.6, 10.4: one call is one GET, with no credentials or cookies."""
    url = remote_server.route("/shared.yml", b"a: 1\n")

    fetch_remote(url)
    assert remote_server.paths() == ["/shared.yml"]
    fetch_remote(url)

    assert [request.method for request in remote_server.requests] == ["GET", "GET"]
    for request in remote_server.requests:
        names = {name.lower() for name in request.headers}
        assert names.isdisjoint({"authorization", "cookie", "proxy-authorization"})


def test_fetch_sends_the_query_and_never_the_fragment(remote_server: RemoteServer) -> None:
    """Req 1.6: the URL is requested as written; a fragment stays on the client."""
    url = remote_server.route("/shared.yml", b"a: 1\n")

    fetch_remote(f"{url}?v=2#part")

    assert remote_server.paths() == ["/shared.yml?v=2"]


def test_fetch_writes_nothing_to_disk(
    remote_server: RemoteServer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Req 1.6, 10.4: fetching creates no file."""
    monkeypatch.chdir(tmp_path)
    url = remote_server.route("/shared.yml", b"a: 1\n")

    fetch_remote(url)

    assert list(tmp_path.iterdir()) == []


def test_remote_server_fixture_bypasses_proxies_for_subprocesses(
    remote_server: RemoteServer,
) -> None:
    """Req 10.4: the fixture sets NO_PROXY so the CLI subprocess reaches 127.0.0.1 directly."""
    assert os.environ["NO_PROXY"] == NO_PROXY_HOSTS
    assert urllib.request.proxy_bypass(f"127.0.0.1:{remote_server.port}")
    assert remote_server.base_url.startswith("http://127.0.0.1:")


def test_remote_server_answers_a_separate_process(remote_server: RemoteServer) -> None:
    """Req 1.6: a subprocess (as the CLI e2e tests run) fetches from the fixture and is logged."""
    url = remote_server.route("/shared.yml", b"from: server\n")
    fetch = "sys.stdout.buffer.write(f.fetch_remote(sys.argv[1]))"
    script = f"import sys, troupe.config.fetch as f; {fetch}"

    completed = subprocess.run(
        [sys.executable, "-c", script, url], capture_output=True, check=False, timeout=30
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == b"from: server\n"
    assert remote_server.paths() == ["/shared.yml"]
