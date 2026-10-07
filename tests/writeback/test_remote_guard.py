"""Unit tests of the remote write guard (config-remote-schema task 3.3):
``troupe.config.writeback.remote_guard.remote_refusal`` and its use by the PatchPlanner and the
alias expander.

The directive lookup is proved on loaded documents: a scalar-form and a ``$ref`` include, quoted
include strings (kept exactly as written), remote in remote (the outermost directive written in a
local file is named) and a remote file included at several places (the one on PATH's walk is
named, else the first in document order).

The planner and expander tests load real theatres whose remote includes are served by the local
``remote_server`` fixture, resolve the set with the real TargetResolver and plan it: every edit
into remote content (a changed value, a removal across layers, an ``--in-include`` placement, a
text-include rewrite, an alias expansion) is refused naming the local include line, while an
overridden value and a new key below a remote include are planned in the local file.

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

from collections.abc import Mapping
from pathlib import Path

import pytest

from remote_support import RemoteServer
from troupe.config.errors import ExitCode, WriteRefusedError
from troupe.config.loader import load_config
from troupe.config.model import ConfigDocument, ConfigMapping, SourceFile
from troupe.config.paths import ConfigPath
from troupe.config.writeback import expand as expander
from troupe.config.writeback.aliases import AliasSite, alias_sites
from troupe.config.writeback.compare import plain_equal, root_plain
from troupe.config.writeback.planner import plan_set
from troupe.config.writeback.remote_guard import REMOTE_READ_ONLY, remote_refusal
from troupe.config.writeback.target import resolve_target
from troupe.config.writeback.value import parse_value

ROOT = "troupe.yml"


def _load(tmp_path: Path, files: Mapping[str, str]) -> ConfigDocument:
    for name, text in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_bytes(text.encode("utf-8"))
    return load_config(tmp_path / ROOT)


def _remote(document: ConfigDocument, url: str) -> SourceFile:
    return next(file for file in document.files if file.url == url)


def _message(file: Path, path: str, string: str, line: int) -> str:
    return f"{file}: {path}: " + REMOTE_READ_ONLY.format(string=string, line=line)


def _plan(
    tmp_path: Path, files: Mapping[str, str], path: str, value: str, *, in_include: bool = False
) -> dict[str, str]:
    """Plan a set; return each patched file's name and new text, proving that every patched file
    is local and that the patched texts read back as the intended configuration."""
    document = _load(tmp_path, files)
    config_path = ConfigPath.parse(path)
    request = resolve_target(
        document, config_path, parse_value(value, config_path), in_include=in_include
    )
    plan = plan_set(document, request)
    assert all(patch.file.url is None for patch in plan.patches)
    reloaded = load_config(
        tmp_path / ROOT, {patch.file.key: patch.new_text for patch in plan.patches}
    )
    assert plain_equal(root_plain(reloaded), plan.intended, ordered=True)
    return {patch.file.real_path.name: patch.new_text for patch in plan.patches}


def _refused(
    tmp_path: Path, files: Mapping[str, str], path: str, value: str, *, in_include: bool = False
) -> WriteRefusedError:
    document = _load(tmp_path, files)
    config_path = ConfigPath.parse(path)
    request = resolve_target(
        document, config_path, parse_value(value, config_path), in_include=in_include
    )
    with pytest.raises(WriteRefusedError) as caught:
        plan_set(document, request)
    assert caught.value.exit_code == ExitCode.WRITE_REFUSED
    return caught.value


# --- the directive lookup ----------------------------------------------------------------------


def test_scalar_form_include_is_named_with_its_file_text_and_line(
    tmp_path: Path, remote_server: RemoteServer
) -> None:
    """Req 6.1: the refusal names the local file holding the directive, PATH, the include string
    as written and its 1-based line."""
    url = remote_server.route("/team.yml", "x: 1\n")
    document = _load(tmp_path, {ROOT: f"a: 1\nteam: {url}\n"})

    error = remote_refusal(document, _remote(document, url), ConfigPath.parse("team.x"))

    assert error.exit_code == ExitCode.WRITE_REFUSED
    assert error.file == tmp_path / ROOT
    assert error.path == "team.x"
    assert str(error) == _message(tmp_path / ROOT, "team.x", url, 2)


@pytest.mark.parametrize(
    ("written", "quoted"),
    [
        pytest.param("{url}", "{url}", id="plain"),
        pytest.param('"{url}"', '"{url}"', id="double-quoted"),
        pytest.param("'{url}'", "'{url}'", id="single-quoted"),
    ],
)
def test_ref_form_include_is_named_exactly_as_written(
    tmp_path: Path, remote_server: RemoteServer, written: str, quoted: str
) -> None:
    """Req 6.1: a ``$ref`` directive is quoted from the source text, quotes included, with the
    line of the ``$ref`` value."""
    url = remote_server.route("/team.yml", "x: 1\n")
    root = f"# shared\nteam:\n  $ref: {written.format(url=url)}\n  y: 2\n"
    document = _load(tmp_path, {ROOT: root})

    error = remote_refusal(document, _remote(document, url), ConfigPath.parse("team.x"))

    assert str(error) == _message(tmp_path / ROOT, "team.x", quoted.format(url=url), 3)


def test_quoted_scalar_form_keeps_its_quotes(tmp_path: Path, remote_server: RemoteServer) -> None:
    """Req 6.1: a quoted scalar-form include string is named with its quotes."""
    url = remote_server.route("/team.yml", "x: 1\n")
    document = _load(tmp_path, {ROOT: f"team: '{url}'\n"})

    error = remote_refusal(document, _remote(document, url), ConfigPath.parse("team.x"))

    assert str(error) == _message(tmp_path / ROOT, "team.x", f"'{url}'", 1)


def test_remote_in_remote_names_the_outermost_local_directive(
    tmp_path: Path, remote_server: RemoteServer
) -> None:
    """Req 6.2: content reached through several remote includes is reported at the outermost
    directive written in a local file."""
    inner = remote_server.route("/inner.yml", "x: 1\n")
    outer = remote_server.route("/outer.yml", f"sub: {inner}\n")
    document = _load(tmp_path, {ROOT: f"a: 1\n\nteam: {outer}\n"})

    error = remote_refusal(document, _remote(document, inner), ConfigPath.parse("team.sub.x"))

    assert str(error) == _message(tmp_path / ROOT, "team.sub.x", outer, 3)


def test_remote_in_remote_through_a_chained_root(
    tmp_path: Path, remote_server: RemoteServer
) -> None:
    """Req 6.2: a remote file whose root is itself a remote ``$ref`` is reached through the
    outer directive."""
    inner = remote_server.route("/inner.yml", "x: 1\n")
    outer = remote_server.route("/outer.yml", f"$ref: {inner}\n")
    document = _load(tmp_path, {ROOT: f"team: {outer}\n"})

    error = remote_refusal(document, _remote(document, inner), ConfigPath.parse("team.x"))

    assert str(error) == _message(tmp_path / ROOT, "team.x", outer, 1)


@pytest.mark.parametrize(
    ("path", "line"),
    [
        pytest.param("b.x", 2, id="on-the-walk-second"),
        pytest.param("a.x", 1, id="on-the-walk-first"),
        pytest.param("c", 1, id="off-the-walk-first-in-document-order"),
    ],
)
def test_several_places_prefer_the_one_on_paths_walk(
    tmp_path: Path, remote_server: RemoteServer, path: str, line: int
) -> None:
    """Req 6.1, 6.2: a remote file included at several places is named at the place on PATH's
    walk, else at the first place in document order."""
    url = remote_server.route("/team.yml", "x: 1\n")
    document = _load(tmp_path, {ROOT: f"a: {url}\nb: {url}\nc: 3\n"})

    error = remote_refusal(document, _remote(document, url), ConfigPath.parse(path))

    assert str(error) == _message(tmp_path / ROOT, path, url, line)


def test_directive_in_a_local_included_file_is_named_there(
    tmp_path: Path, remote_server: RemoteServer
) -> None:
    """Req 6.1: a remote include written in a local included file names that file and line."""
    url = remote_server.route("/team.yml", "x: 1\n")
    document = _load(tmp_path, {ROOT: "a: file:inc.yml\n", "inc.yml": f"k: 1\nt: {url}\n"})

    error = remote_refusal(document, _remote(document, url), ConfigPath.parse("a.t.x"))

    assert str(error) == _message(tmp_path / "inc.yml", "a.t.x", url, 2)


# --- the planner -------------------------------------------------------------------------------


def test_changed_remote_value_is_refused(tmp_path: Path, remote_server: RemoteServer) -> None:
    """Req 6.1: changing a value written in remote content is refused naming the include line."""
    url = remote_server.route("/team.yml", "x: 1\nm:\n  p: 1\n")
    files = {ROOT: f"a: 1\nteam: {url}\n"}

    error = _refused(tmp_path, files, "team.x", "2")

    assert str(error) == _message(tmp_path / ROOT, "team.x", url, 2)


def test_collection_replacement_changing_remote_content_is_refused(
    tmp_path: Path, remote_server: RemoteServer
) -> None:
    """Req 6.1: a collection replacement that changes or removes part of remote content is
    refused."""
    url = remote_server.route("/team.yml", "x: 1\nm:\n  p: 1\n  q: 2\n")
    files = {ROOT: f"team: {url}\n"}

    error = _refused(tmp_path, files, "team.m", "{p: 1}")

    assert str(error) == _message(tmp_path / ROOT, "team.m", url, 1)


def test_removal_across_layers_from_remote_content_is_refused(
    tmp_path: Path, remote_server: RemoteServer
) -> None:
    """Req 6.1: removing a key that the remote side of a mapping-form include holds is refused,
    even when the override side changes too."""
    url = remote_server.route("/team.yml", "m:\n  a: 1\n  b: 2\n")
    files = {ROOT: f"team:\n  $ref: {url}\n  m:\n    a: 5\n"}

    error = _refused(tmp_path, files, "team.m", "{a: 6}")

    assert str(error) == _message(tmp_path / ROOT, "team.m", url, 2)


def test_in_include_placement_below_a_remote_include_is_refused(
    tmp_path: Path, remote_server: RemoteServer
) -> None:
    """Req 6.1: a new key that ``--in-include`` would put into remote content is refused."""
    url = remote_server.route("/team.yml", "x: 1\n")
    files = {ROOT: f"team: {url}\n"}

    error = _refused(tmp_path, files, "team.new", "1", in_include=True)

    assert str(error) == _message(tmp_path / ROOT, "team.new", url, 1)


def test_remote_text_include_rewrite_is_refused(
    tmp_path: Path, remote_server: RemoteServer
) -> None:
    """Req 6.1: a text include served remotely is never rewritten."""
    url = remote_server.route("/notes.txt", "hello\n")
    files = {ROOT: f"notes: {url}\n"}

    error = _refused(tmp_path, files, "notes", "bye")

    assert str(error) == _message(tmp_path / ROOT, "notes", url, 1)


def test_remote_in_remote_value_is_refused_at_the_local_line(
    tmp_path: Path, remote_server: RemoteServer
) -> None:
    """Req 6.1, 6.2: a value written in a remote file included by remote content is refused
    naming the local directive of the outer include."""
    inner = remote_server.route("/inner.yml", "x: 1\n")
    outer = remote_server.route("/outer.yml", f"sub: {inner}\n")
    files = {ROOT: f"team: {outer}\n"}

    error = _refused(tmp_path, files, "team.sub.x", "2")

    assert str(error) == _message(tmp_path / ROOT, "team.sub.x", outer, 1)


def test_remote_include_root_replacement_names_the_include_string(
    tmp_path: Path, remote_server: RemoteServer
) -> None:
    """Req 6.5: replacing the whole value of a remote include follows the include-root rule, and
    the message contains the include string and names the source by its URL."""
    url = remote_server.route("/team.yml", "x: 1\n")
    files = {ROOT: f"team: {url}\n"}

    error = _refused(tmp_path, files, "team", "5")

    assert error.file == url
    assert url in error.problem
    assert error.problem == (
        f"the whole value comes from the include {url}; change the included file instead"
    )


def test_overridden_remote_value_changes_in_the_override(
    tmp_path: Path, remote_server: RemoteServer
) -> None:
    """Req 6.3: a remote value overridden in a local file is changed in the override."""
    url = remote_server.route("/team.yml", "a: 1\nb: 2\n")
    files = {ROOT: f"team:\n  $ref: {url}\n  a: 5\n"}

    assert _plan(tmp_path, files, "team.a", "6") == {ROOT: f"team:\n  $ref: {url}\n  a: 6\n"}


def test_unchanged_remote_entries_do_not_block_a_change_of_the_override(
    tmp_path: Path, remote_server: RemoteServer
) -> None:
    """Req 6.1, 6.3: a mapping replacement that leaves the remote side unchanged is planned in
    the override only (nothing is refused that would not have been written)."""
    url = remote_server.route("/team.yml", "m:\n  a: 1\n  b: 2\n")
    files = {ROOT: f"team:\n  $ref: {url}\n  m:\n    a: 5\n"}

    assert _plan(tmp_path, files, "team.m", "{a: 6, b: 2}") == {
        ROOT: f"team:\n  $ref: {url}\n  m:\n    a: 6\n"
    }


def test_new_key_below_a_scalar_form_remote_include_converts_it(
    tmp_path: Path, remote_server: RemoteServer
) -> None:
    """Req 6.4: a new key below a scalar-form remote include becomes a local override, the
    include converted to mapping form with its include string unchanged."""
    url = remote_server.route("/team.yml", "x: 1\n")
    files = {ROOT: f"a: 1\nteam: {url}\n"}

    assert _plan(tmp_path, files, "team.new", "2") == {
        ROOT: f"a: 1\nteam:\n  $ref: {url}\n  new: 2\n"
    }


def test_new_key_below_a_ref_form_remote_include_is_an_override(
    tmp_path: Path, remote_server: RemoteServer
) -> None:
    """Req 6.4: a new key below a mapping-form remote include is written in its override."""
    url = remote_server.route("/team.yml", "x: 1\n")
    files = {ROOT: f"team: {{$ref: '{url}'}}\n"}

    assert _plan(tmp_path, files, "team.new", "2") == {ROOT: f"team: {{$ref: '{url}', new: 2}}\n"}


# --- the alias expander -------------------------------------------------------------------------


@pytest.mark.parametrize("expand", [False, True], ids=["refuse", "expand"])
def test_alias_guard_refuses_an_alias_in_remote_content(
    tmp_path: Path, remote_server: RemoteServer, expand: bool
) -> None:
    """Req 6.1: the alias guard refuses an alias written in remote content naming the local
    include line, with or without ``--expand-alias`` (expanding would write remote content)."""
    url = remote_server.route("/team.yml", "base: &b\n  k: 1\nuse: *b\n")
    document = _load(tmp_path, {ROOT: f"local: 1\nteam: {url}\n"})
    path = ConfigPath.parse("team.use.k")
    request = resolve_target(document, path, parse_value("2", path))

    with pytest.raises(WriteRefusedError) as caught:
        alias_sites(document, request, expand=expand)

    assert str(caught.value) == _message(tmp_path / ROOT, "team.use.k", url, 2)


def test_merge_key_guard_refuses_a_merged_value_in_remote_content(
    tmp_path: Path, remote_server: RemoteServer
) -> None:
    """Req 6.1: a value received from a merge key written in remote content is refused naming
    the local include line."""
    url = remote_server.route("/team.yml", "base: &b\n  k: 1\nm:\n  <<: *b\n  own: 2\n")
    document = _load(tmp_path, {ROOT: f"team: {url}\n"})
    path = ConfigPath.parse("team.m.k")
    request = resolve_target(document, path, parse_value("2", path))

    with pytest.raises(WriteRefusedError) as caught:
        alias_sites(document, request, expand=True)

    assert str(caught.value) == _message(tmp_path / ROOT, "team.m.k", url, 1)


def test_alias_expansion_in_remote_content_is_refused(
    tmp_path: Path, remote_server: RemoteServer
) -> None:
    """Req 6.1: the expander never expands an alias written in remote content."""
    url = remote_server.route("/team.yml", "base: &b\n  k: 1\nuse: *b\n")
    document = _load(tmp_path, {ROOT: f"local: 1\nteam: {url}\n"})
    team = document.root.get("team")
    assert team is not None and isinstance(team.value, ConfigMapping)
    use = team.value.get("use")
    assert use is not None
    site = AliasSite(
        place=use.value, parent=team.value, key="use", path=ConfigPath.parse("team.use")
    )

    with pytest.raises(WriteRefusedError) as caught:
        expander.expand_alias(document, site)

    assert str(caught.value) == _message(tmp_path / ROOT, "team.use", url, 2)
