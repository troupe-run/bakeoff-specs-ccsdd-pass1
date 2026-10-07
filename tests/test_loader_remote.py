"""Loading remote includes (config-remote-schema; task 2.4).

Every test serves real HTTP from the local ``remote_server`` fixture and loads a real theatre
with ``load_config`` (or runs the real CLI); nothing reaches the internet and the fetcher is never
mocked. A remote include (``https://`` or ``http://``, in the scalar form or as the ``$ref`` of a
mapping-form include) is fetched once per load, decoded strictly as UTF-8 and read as YAML or text
by its URL path's suffix; it becomes a source with that URL (1.1, 1.2, 1.6). Remote YAML may hold
remote includes, not ``file:`` ones, and a URL cycle is refused (1.3-1.5). Every failure is a load
error naming the including file by its display name, the key path and the URL (2.1, 2.2), and the
CLI then exits 4 changing nothing (2.3).

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

import socket

import pytest

from remote_support import RemoteServer
from set_support import snapshot_tree
from support import MakeTheatre, RunCli
from troupe.config.errors import ConfigLoadError, ExitCode
from troupe.config.loader import load_config, load_include
from troupe.config.model import (
    ConfigDocument,
    ConfigMapping,
    ConfigNode,
    ConfigSequence,
    IncludeKind,
)

CONFIG_NAMES = ["troupe.yml", "troupe.yaml"]


def _load(
    make_theatre: MakeTheatre, config: str, files: dict[str, str] | None = None
) -> ConfigDocument:
    theatre = make_theatre(config, files=files)
    return load_config(theatre / ".troupe" / "troupe.yml")


def _refusal(
    make_theatre: MakeTheatre, config: str, files: dict[str, str] | None = None
) -> ConfigLoadError:
    with pytest.raises(ConfigLoadError) as caught:
        _load(make_theatre, config, files)
    return caught.value


def _plain(node: ConfigNode) -> object:
    if isinstance(node, ConfigMapping):
        return {entry.key: _plain(entry.value) for entry in node.entries}
    if isinstance(node, ConfigSequence):
        return [_plain(item) for item in node.items]
    return node.value


def _at(document: ConfigDocument, *keys: str) -> ConfigNode:
    node: ConfigNode = document.root
    for key in keys:
        assert isinstance(node, ConfigMapping)
        entry = node.get(key)
        assert entry is not None, key
        node = entry.value
    return node


def _unused_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


# --- 1.1, 1.2, 1.6: YAML and text, both forms -------------------------------------------------


def test_a_remote_yaml_include_is_fetched_and_becomes_a_source_with_its_url(
    make_theatre: MakeTheatre, remote_server: RemoteServer
) -> None:
    """Req 1.1, 1.2, 1.6: a scalar-form ``http://`` include of a ``.yml`` URL is fetched with one
    GET and embedded as YAML; the remote source carries the URL as identity and display name and
    is listed with the files; no file is created for it."""
    url = remote_server.route("/shared.yml", "a: 1\nb: [x, y]\n")
    theatre = make_theatre(f"shared: {url}\nlocal: 2\n")
    before = snapshot_tree(theatre)

    document = load_config(theatre / ".troupe" / "troupe.yml")

    assert _plain(document.root) == {"shared": {"a": 1, "b": ["x", "y"]}, "local": 2}
    shared = _at(document, "shared")
    assert shared.include is not None
    assert shared.include.kind is IncludeKind.YAML
    assert shared.include.string == url
    assert shared.include.spec == url
    remote = shared.include.target
    assert (remote.url, remote.key, remote.shown, remote.text) == (
        url,
        url,
        url,
        "a: 1\nb: [x, y]\n",
    )
    assert shared.origin.file is remote
    assert [file.url for file in document.files] == [None, url]
    assert remote_server.paths() == ["/shared.yml"]
    assert snapshot_tree(theatre) == before


@pytest.mark.parametrize(
    ("route", "query", "body", "expected"),
    [
        pytest.param("/motd.txt", "", "hello\r\nworld\n", "hello\r\nworld\n", id="text-crlf"),
        pytest.param("/conf", "", "a: 1\n", "a: 1\n", id="no-suffix-is-text"),
        pytest.param("/a.YML", "", "a: 1\n", "a: 1\n", id="upper-case-suffix-is-text"),
        pytest.param("/a.txt", "?f=x.yml#y.yaml", "a: 1\n", "a: 1\n", id="query-ignored"),
        pytest.param("/a.yaml", "?v=2#top", "a: 1\n", {"a": 1}, id="yaml-with-query"),
        pytest.param("/empty.yml", "", "", None, id="empty-yaml-is-null"),
        pytest.param("/empty.txt", "", "", "", id="empty-text"),
    ],
)
def test_remote_content_is_yaml_or_text_by_the_url_path_suffix(
    make_theatre: MakeTheatre,
    remote_server: RemoteServer,
    route: str,
    query: str,
    body: str,
    expected: object,
) -> None:
    """Req 1.2: YAML exactly when the URL path, without query and fragment, ends ``.yml`` or
    ``.yaml`` (case-sensitive); otherwise the exact text, never parsed; empty YAML is null."""
    url = remote_server.route(route, body) + query
    document = _load(make_theatre, f"v: '{url}'\n")

    node = _at(document, "v")
    assert _plain(node) == expected
    assert node.include is not None
    is_yaml = isinstance(expected, dict) or expected is None
    assert node.include.kind is (IncludeKind.YAML if is_yaml else IncludeKind.TEXT)
    assert node.include.target.url == url


def test_remote_yaml_keeps_its_anchors_aliases_and_merge_keys(
    make_theatre: MakeTheatre, remote_server: RemoteServer
) -> None:
    """Req 1.2: remote YAML is read under the rules of a local YAML include."""
    url = remote_server.route("/s.yml", "base: &b {x: 1}\ncopy: *b\nmerged:\n  <<: *b\n  y: 2\n")
    document = _load(make_theatre, f"s: {url}\n")

    assert _plain(_at(document, "s")) == {
        "base": {"x": 1},
        "copy": {"x": 1},
        "merged": {"y": 2, "x": 1},
    }


def test_a_remote_include_as_ref_merges_its_overrides(
    make_theatre: MakeTheatre, remote_server: RemoteServer
) -> None:
    """Req 1.1: the ``$ref`` of a mapping-form include may be a URL; overrides are merged as for a
    local include, and a bare ``$ref`` mapping loads as the scalar form."""
    url = remote_server.route("/s.yml", "a: 1\nnested: {x: 1, y: 2}\n")
    config = (
        f"over:\n  $ref: {url}\n  a: 9\n  nested: {{y: 3}}\n  new: n\nbare: {{$ref: '{url}'}}\n"
    )
    document = _load(make_theatre, config)

    assert _plain(_at(document, "over")) == {
        "a": 9,
        "nested": {"x": 1, "y": 3},
        "new": "n",
    }
    over = _at(document, "over")
    assert over.include is not None and over.include.form is not None
    assert over.include.string == url
    assert _plain(_at(document, "bare")) == {"a": 1, "nested": {"x": 1, "y": 2}}
    assert remote_server.paths() == ["/s.yml"]


def test_a_remote_include_in_a_local_included_file_is_fetched(
    make_theatre: MakeTheatre, remote_server: RemoteServer
) -> None:
    """Req 1.1: a remote include written in a local included file is fetched like one in the
    root file."""
    url = remote_server.route("/s.txt", "text")
    document = _load(make_theatre, "inc: file:inc.yml\n", {".troupe/inc.yml": f"t: {url}\n"})

    assert _plain(document.root) == {"inc": {"t": "text"}}


def test_a_remote_include_follows_redirects(
    make_theatre: MakeTheatre, remote_server: RemoteServer
) -> None:
    """Req 1.6: redirects are followed; the source keeps the URL as written."""
    remote_server.route("/real.yml", "a: 1\n")
    url = remote_server.redirect("/moved.yml", "/real.yml")
    document = _load(make_theatre, f"s: {url}\n")

    assert _plain(_at(document, "s")) == {"a": 1}
    assert document.files[1].url == url


def test_load_include_reads_a_remote_include_string(
    make_theatre: MakeTheatre, remote_server: RemoteServer
) -> None:
    """Req 1.1: the included-content helper used by writers takes a whole remote include
    string."""
    url = remote_server.route("/s.yml", "a: 1\n")
    document = _load(make_theatre, "x: 1\n")

    node = load_include(document.root_file, url, {})

    assert _plain(node) == {"a": 1}
    assert node.include is not None and node.include.target.url == url


# --- 1.3, 1.4, 1.5: remote in remote, file: in remote, cycles ------------------------------------


def test_remote_yaml_may_include_remote_content_at_any_depth(
    make_theatre: MakeTheatre, remote_server: RemoteServer
) -> None:
    """Req 1.3: remote includes inside remote content are fetched too, in both forms."""
    third = remote_server.route("/c.txt", "deep")
    second = remote_server.route("/b.yml", f"c: {third}\nd: 1\n")
    first = remote_server.route("/a.yml", f"b:\n  $ref: {second}\n  d: 2\n")
    document = _load(make_theatre, f"a: {first}\n")

    assert _plain(document.root) == {"a": {"b": {"c": "deep", "d": 2}}}
    assert [file.url for file in document.files] == [None, first, second, third]


def test_each_url_is_fetched_once_per_load_across_a_diamond(
    make_theatre: MakeTheatre, remote_server: RemoteServer
) -> None:
    """Req 1.6: a URL included at several places (here from the root and from two remote files)
    is fetched once and listed once."""
    shared = remote_server.route("/shared.yml", "v: 1\n")
    left = remote_server.route("/left.yml", f"s: {shared}\n")
    right = remote_server.route("/right.yml", f"s: {shared}\n")
    document = _load(make_theatre, f"l: {left}\nr: {right}\nd: {shared}\ne: {{$ref: '{shared}'}}\n")

    assert _plain(document.root) == {
        "l": {"s": {"v": 1}},
        "r": {"s": {"v": 1}},
        "d": {"v": 1},
        "e": {"v": 1},
    }
    assert sorted(remote_server.paths()) == ["/left.yml", "/right.yml", "/shared.yml"]
    assert [file.url for file in document.files] == [None, left, shared, right]


def test_no_request_is_made_when_the_overrides_hold_the_url(
    make_theatre: MakeTheatre, remote_server: RemoteServer
) -> None:
    """Req 10.3, 1.6: a text given in the overrides under the URL is used instead of fetching."""
    url = remote_server.url("/never-served.yml")
    theatre = make_theatre(f"s: {url}\n")

    document = load_config(theatre / ".troupe" / "troupe.yml", {url: "a: from-overrides\n"})

    assert _plain(document.root) == {"s": {"a": "from-overrides"}}
    assert document.files[1].url == url
    assert remote_server.paths() == []


def test_a_file_include_in_remote_content_is_refused_naming_the_url_and_key_path(
    make_theatre: MakeTheatre, remote_server: RemoteServer
) -> None:
    """Req 1.4: a ``file:`` include in remote YAML is a load error naming the URL of that
    content and the key path; the local file is never read."""
    url = remote_server.route("/s.yml", "a:\n  b: file:secret.yml\n")
    error = _refusal(make_theatre, f"s: {url}\n", {".troupe/secret.yml": "x: 1\n"})

    assert (error.file, error.path) == (url, "s.a.b")
    assert error.problem == "local includes are not allowed in remote content: file:secret.yml"


def test_a_file_ref_in_remote_content_is_refused(
    make_theatre: MakeTheatre, remote_server: RemoteServer
) -> None:
    """Req 1.4: the mapping form of a ``file:`` include in remote YAML is refused too."""
    url = remote_server.route("/s.yml", "a: {$ref: 'file:x.yml', b: 1}\n")
    error = _refusal(make_theatre, f"s: {url}\n")

    assert (error.file, error.path) == (url, "s.a")
    assert "local includes are not allowed in remote content" in error.problem


def test_a_url_cycle_is_refused_listing_the_cycle(
    make_theatre: MakeTheatre, remote_server: RemoteServer
) -> None:
    """Req 1.5: a remote YAML include leading back to a URL on its chain is refused, listing the
    cycle by URL."""
    a = remote_server.url("/a.yml")
    b = remote_server.route("/b.yml", f"back: {a}\n")
    remote_server.route("/a.yml", f"next: {b}\n")
    error = _refusal(make_theatre, f"start: {a}\n")

    assert error.problem == f"include cycle: {a} -> {b} -> {a}"
    assert error.file == a


def test_a_remote_text_include_of_its_own_url_is_not_a_cycle(
    make_theatre: MakeTheatre, remote_server: RemoteServer
) -> None:
    """Req 1.5: only YAML includes form cycles; a text URL included twice loads."""
    text = remote_server.route("/t.txt", "t")
    url = remote_server.route("/a.yml", f"t: {text}\n")
    document = _load(make_theatre, f"a: {url}\nt: {text}\n")

    assert _plain(document.root) == {"a": {"t": "t"}, "t": "t"}


def test_overrides_on_remote_text_or_a_non_mapping_are_refused(
    make_theatre: MakeTheatre, remote_server: RemoteServer
) -> None:
    """Req 1.1: overrides on a remote include follow the local rules, naming the URL."""
    text = remote_server.route("/t.txt", "t")
    error = _refusal(make_theatre, f"x: {{$ref: '{text}', a: 1}}\n")
    assert (error.path, error.problem) == (
        "x",
        f"overrides apply only to YAML includes; the URL path of {text} does not end in .yml/.yaml",
    )

    listing = remote_server.route("/l.yml", "- 1\n")
    error = _refusal(make_theatre, f"x: {{$ref: '{listing}', a: 1}}\n")
    assert error.problem == f"overrides need a mapping; the included file {listing} holds a list"


# --- 4.6: literals are never fetched ------------------------------------------------------------


def test_literal_remote_strings_are_never_fetched(
    make_theatre: MakeTheatre, remote_server: RemoteServer
) -> None:
    """Req 4.1, 4.6: a ``!literal`` URL stays a string and causes no request."""
    url = remote_server.route("/s.yml", "a: 1\n")
    document = _load(make_theatre, f"hook: !literal {url}\n")

    assert _plain(document.root) == {"hook": url}
    assert remote_server.paths() == []


# --- 2.1, 2.2, 2.3: failures ----------------------------------------------------------------------


def test_a_failed_fetch_names_the_including_file_key_path_url_and_reason(
    make_theatre: MakeTheatre, remote_server: RemoteServer
) -> None:
    """Req 2.1: a non-2xx answer is a load error naming the including file (as opened), the key
    path, the URL and the status."""
    url = remote_server.route("/gone.yml", "x", status=404)
    theatre = make_theatre("a:\n  b: file:inc.yml\n", files={".troupe/inc.yml": f"c: {url}\n"})

    with pytest.raises(ConfigLoadError) as caught:
        load_config(theatre / ".troupe" / "troupe.yml")

    assert str(caught.value) == (
        f"{theatre / '.troupe' / 'inc.yml'}: a.b.c: cannot fetch include {url}: HTTP 404 Not Found"
    )


def test_remote_content_that_is_not_utf8_is_a_fetch_failure(
    make_theatre: MakeTheatre, remote_server: RemoteServer
) -> None:
    """Req 2.1: a body that is not valid UTF-8 is a load error naming the URL and the offset."""
    url = remote_server.route("/bad.txt", b"ok\xff")
    error = _refusal(make_theatre, f"t: {url}\n")

    assert error.path == "t"
    assert error.problem == (
        f"cannot fetch include {url}: the content is not valid UTF-8 (invalid byte at offset 2)"
    )


def test_an_unreachable_server_is_a_fetch_failure(make_theatre: MakeTheatre) -> None:
    """Req 2.1: a server that cannot be reached is a load error giving the reason."""
    url = f"http://127.0.0.1:{_unused_port()}/s.yml"
    error = _refusal(make_theatre, f"s: {url}\n")

    assert error.problem == f"cannot fetch include {url}: connection refused"


def test_an_https_include_is_recognised_and_fetched(
    make_theatre: MakeTheatre, remote_server: RemoteServer
) -> None:
    """Req 1.1, 2.1: an ``https://`` string is an include too; the plain-HTTP test server cannot
    complete a TLS handshake, so the fetch fails naming the URL (proving it was attempted)."""
    url = f"https://127.0.0.1:{remote_server.port}/s.yml"
    error = _refusal(make_theatre, f"s: {url}\n")

    assert error.path == "s"
    assert error.problem.startswith(f"cannot fetch include {url}: ")


@pytest.mark.parametrize(
    ("body", "path", "problem"),
    [
        pytest.param("a: [1\n", None, "invalid YAML: ", id="syntax"),
        pytest.param("a: !tag x\n", "s.a", "explicit tag '!tag' is not supported", id="tag"),
        pytest.param("a: 1\na: 2\n", "s.a", "duplicate key in mapping", id="duplicate"),
    ],
)
def test_unloadable_remote_yaml_names_the_url(
    make_theatre: MakeTheatre,
    remote_server: RemoteServer,
    body: str,
    path: str | None,
    problem: str,
) -> None:
    """Req 2.2: remote YAML that cannot be loaded is a load error naming the URL as the file and
    the key path where known."""
    url = remote_server.route("/s.yml", body)
    error = _refusal(make_theatre, f"s: {url}\n")

    assert (error.file, error.path) == (url, path)
    assert error.problem.startswith(problem)


@pytest.mark.parametrize("config_name", CONFIG_NAMES)
@pytest.mark.parametrize("command", ["show", "get", "history"])
def test_failed_fetches_exit_4_through_the_cli_changing_nothing(
    make_theatre: MakeTheatre,
    run_cli: RunCli,
    remote_server: RemoteServer,
    config_name: str,
    command: str,
) -> None:
    """Req 2.1, 2.3: through the real CLI a failed fetch exits 4 with nothing on stdout, one
    stderr line naming the root file, the key path and the URL, and no file or history state
    created or changed."""
    url = remote_server.route("/s.yml", "x", status=500)
    theatre = make_theatre(f"local: 1\ns: {url}\n", config_name=config_name)
    before = sorted(str(path.relative_to(theatre)) for path in theatre.rglob("*"))
    args = ["config", command, "--theatre", str(theatre)]
    if command == "get":
        args.insert(2, "local")

    result = run_cli(*args)

    assert result.exit_code == ExitCode.LOAD, result.stderr
    assert result.stdout == b""
    config = theatre / ".troupe" / config_name
    assert result.stderr == (
        f"{config}: s: cannot fetch include {url}: HTTP 500 Internal Server Error\n"
    )
    assert sorted(str(path.relative_to(theatre)) for path in theatre.rglob("*")) == before


@pytest.mark.parametrize("config_name", CONFIG_NAMES)
def test_remote_content_is_shown_through_the_cli(
    make_theatre: MakeTheatre, run_cli: RunCli, remote_server: RemoteServer, config_name: str
) -> None:
    """Req 1.1, 1.2: through the real CLI, remote YAML and text values are read like local ones,
    and no file is created for them."""
    text = remote_server.route("/motd.txt", "hi\n")
    url = remote_server.route("/s.yml", f"a: 1\nm: {text}\n")
    theatre = make_theatre(f"s: {url}\n", config_name=config_name)
    before = snapshot_tree(theatre)

    assert run_cli("config", "get", "s.a", "--theatre", str(theatre)).stdout == b"1\n"
    result = run_cli("config", "get", "s.m", "--theatre", str(theatre))
    assert (result.exit_code, result.stdout, result.stderr) == (0, b"hi\n", "")
    assert snapshot_tree(theatre) == before
