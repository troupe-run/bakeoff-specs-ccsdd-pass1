"""Writing include-like VALUE strings and the ``--include`` option (config-remote-schema).

``set`` marks VALUE before target resolution (task 3.5): without ``--include`` every include-like
string of VALUE is written tagged ``!literal`` unless the schema gives its own path
``format: uri``; with ``--include`` it is written plain and loads as an include. ``--include`` is a
``store_true`` flag under the same rules as ``--in-include`` and ``--expand-alias``. Every test
runs the real console script against real files and, for remote URLs, the local
``remote_server`` fixture.

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

import json
from pathlib import Path
from typing import Final

import pytest

from remote_support import RemoteServer
from set_support import assert_file_bytes, run_config, snapshot_tree
from support import CliResult, MakeTheatre
from troupe.config.constants import CONFIG_DIR_NAME, HISTORY_STORE_NAME
from troupe.config.errors import ExitCode

CONFIG_NAMES: Final = ["troupe.yml", "troupe.yaml"]

URI_SCHEMA: Final = json.dumps({"properties": {"hook": {"type": "string", "format": "uri"}}})
"""A schema giving ``hook`` the format ``uri``."""


def _set(theatre: Path, *args: str) -> CliResult:
    return run_config("set", *args, "--theatre", str(theatre), cwd=theatre)


def _get(theatre: Path, path: str) -> bytes:
    result = run_config("get", path, "--theatre", str(theatre), cwd=theatre)
    assert (result.exit_code, result.stderr) == (0, ""), result.stderr
    return result.stdout


def _history(theatre: Path) -> list[dict[str, object]]:
    result = run_config("history", "--theatre", str(theatre), cwd=theatre)
    assert (result.exit_code, result.stderr) == (0, ""), result.stderr
    return [json.loads(line) for line in result.stdout.decode("utf-8").splitlines()]


def _history_state(theatre: Path) -> dict[str, bytes]:
    directory = theatre / CONFIG_DIR_NAME
    return {p.name: p.read_bytes() for p in sorted(directory.glob(f"{HISTORY_STORE_NAME}*"))}


def _ok(result: CliResult) -> None:
    assert (result.exit_code, result.stdout, result.stderr) == (0, b"", ""), result.stderr


# ---- 8.1, 9.3: tagged without --include, an include with it ----


@pytest.mark.parametrize("config_name", CONFIG_NAMES)
@pytest.mark.parametrize(
    ("path", "value", "expected"),
    [
        pytest.param(
            "hook", "https://example.invalid/x", "hook: !literal https://example.invalid/x\n"
        ),
        pytest.param("notes", "file:notes.md", "notes: !literal file:notes.md\n"),
        pytest.param("b", "file:inc.yaml", "b: !literal file:inc.yaml\n"),
        pytest.param(
            "a", "{b: http://example.invalid/y}", "a:\n  b: !literal http://example.invalid/y\n"
        ),
    ],
)
def test_set_writes_an_include_like_string_tagged_literal(
    make_theatre: MakeTheatre, config_name: str, path: str, value: str, expected: str
) -> None:
    """Req 8.1, 10.2: without ``--include`` an include-like VALUE string (alone or nested in a
    flow mapping) is written ``!literal`` + one space + the string, read back as that string, and
    no include file is read (``inc.yaml`` and ``notes.md`` exist and stay untouched)."""
    theatre = make_theatre(
        "x: 1\n",
        config_name=config_name,
        files={"inc.yaml": "inner: 1\n", "notes.md": "notes\n"},
    )

    _ok(_set(theatre, path, value))

    assert_file_bytes(theatre / ".troupe" / config_name, "x: 1\n" + expected)
    assert (theatre / "inc.yaml").read_bytes() == b"inner: 1\n"


def test_set_literal_reads_back_as_the_string(make_theatre: MakeTheatre) -> None:
    """Req 8.1: the tagged string reads back as the string itself, not an include."""
    theatre = make_theatre("x: 1\n", files={".troupe/inc.yaml": "inner: 1\n"})

    _ok(_set(theatre, "b", "file:inc.yaml"))

    assert _get(theatre, "b") == b"file:inc.yaml\n"


@pytest.mark.parametrize("config_name", CONFIG_NAMES)
def test_set_include_writes_a_local_include_plain(
    make_theatre: MakeTheatre, config_name: str
) -> None:
    """Req 9.3, 9.6: ``set --include b file:inc.yaml`` writes the include string plain, so it
    loads the file, and records the set's events like any other set."""
    theatre = make_theatre(
        "x: 1\n", config_name=config_name, files={".troupe/inc.yaml": "inner: 1\n"}
    )

    _ok(_set(theatre, "--include", "b", "file:inc.yaml"))

    assert_file_bytes(theatre / ".troupe" / config_name, "x: 1\nb: file:inc.yaml\n")
    assert _get(theatre, "b.inner") == b"1\n"
    sets = [line for line in _history(theatre) if line["op"] == "set"]
    assert [(line["path"], line["new"]) for line in sets] == [("b.inner", 1)]


def test_set_include_writes_a_remote_include_fetched_on_read_back(
    make_theatre: MakeTheatre, remote_server: RemoteServer
) -> None:
    """Req 9.3, 9.6: ``set --include shared URL`` writes the URL plain; the read-back fetches it
    and ``get`` reads the remote content through it."""
    url = remote_server.route("/team.yml", "lead: alice\n")
    theatre = make_theatre("x: 1\n")

    _ok(_set(theatre, "--include", "shared", url))

    assert_file_bytes(theatre / ".troupe" / "troupe.yml", f"x: 1\nshared: {url}\n")
    assert remote_server.paths() == ["/team.yml"]
    assert _get(theatre, "shared.lead") == b"alice\n"


def test_set_include_of_an_unfetchable_remote_url_is_refused_naming_the_url(
    make_theatre: MakeTheatre, remote_server: RemoteServer
) -> None:
    """Req 9.4: ``set --include team URL`` with a URL that cannot be fetched exits 3, writes
    nothing to stdout, names PATH and the URL on stderr and changes no file."""
    missing = remote_server.url("/missing.yml")
    theatre = make_theatre("x: 1\n")
    before = snapshot_tree(theatre)

    result = _set(theatre, "--include", "team", missing)

    assert result.exit_code == ExitCode.WRITE_REFUSED, result.stderr
    assert result.stdout == b""
    assert result.stderr.startswith(f"{theatre / '.troupe' / 'troupe.yml'}: team: ")
    assert missing in result.stderr
    assert snapshot_tree(theatre) == before


def test_set_include_writes_a_ref_mapping_as_an_include(make_theatre: MakeTheatre) -> None:
    """Req 9.3, 8.6: with ``--include`` a ``$ref`` mapping VALUE is written plain and loads as a
    mapping-form include with its override."""
    theatre = make_theatre("x: 1\n", files={".troupe/inc.yaml": "a: 1\nb: 2\n"})

    _ok(_set(theatre, "--include", "s", "{$ref: file:inc.yaml, b: 3}"))

    assert_file_bytes(
        theatre / ".troupe" / "troupe.yml", "x: 1\ns:\n  $ref: file:inc.yaml\n  b: 3\n"
    )
    assert (_get(theatre, "s.a"), _get(theatre, "s.b")) == (b"1\n", b"3\n")


def test_set_of_a_ref_mapping_without_include_is_refused(make_theatre: MakeTheatre) -> None:
    """Req 8.6: without ``--include`` a ``$ref`` mapping VALUE exits 3 naming PATH and mentioning
    ``--include``, with no file changed."""
    theatre = make_theatre("x: 1\n", files={".troupe/inc.yaml": "a: 1\n"})
    before = snapshot_tree(theatre)

    result = _set(theatre, "s", "{$ref: file:inc.yaml}")

    assert (result.exit_code, result.stdout) == (ExitCode.WRITE_REFUSED, b""), result.stderr
    assert "s: " in result.stderr and "--include" in result.stderr
    assert snapshot_tree(theatre) == before


# ---- 8.2: schema format uri ----


def test_set_writes_a_schema_uri_string_plain(make_theatre: MakeTheatre) -> None:
    """Req 8.2: a VALUE string whose path the schema gives ``format: uri`` is written untagged
    and reads back as the string (no request is made)."""
    theatre = make_theatre("x: 1\n", files={".troupe/troupe.schema.json": URI_SCHEMA})

    _ok(_set(theatre, "hook", "https://example.invalid/x"))

    assert_file_bytes(theatre / ".troupe" / "troupe.yml", "x: 1\nhook: https://example.invalid/x\n")
    assert _get(theatre, "hook") == b"https://example.invalid/x\n"


# ---- 9.5: --include without include strings ----


def test_set_include_without_include_strings_is_an_ordinary_set(
    make_theatre: MakeTheatre,
) -> None:
    """Req 9.5: ``--include`` with a VALUE holding no include string sets it exactly as without
    the option."""
    with_option = make_theatre("x: 1\n", name="with")
    without = make_theatre("x: 1\n", name="without")

    _ok(_set(with_option, "--include", "x", "[a, {b: 2}]"))
    _ok(_set(without, "x", "[a, {b: 2}]"))

    root = Path(".troupe") / "troupe.yml"
    assert (with_option / root).read_bytes() == (without / root).read_bytes()
    assert (without / root).read_bytes() == b"x:\n  - a\n  - b: 2\n"


# ---- 9.1: positions and combinations ----


@pytest.mark.parametrize(
    "layout",
    [
        pytest.param(("--include", "P", "V"), id="before"),
        pytest.param(("P", "--include", "V"), id="between"),
        pytest.param(("P", "V", "--include"), id="after"),
        pytest.param(("--include", "--reason", "R", "P", "V"), id="with-reason"),
        pytest.param(("P", "--include", "V", "--in-include"), id="with-in-include"),
        pytest.param(("--expand-alias", "P", "V", "--include"), id="with-expand-alias"),
        pytest.param(
            ("--in-include", "P", "--include", "V", "--reason=R", "--expand-alias"), id="all"
        ),
        pytest.param(("P", "--include", "V", "--include"), id="twice"),
    ],
)
def test_include_anywhere_together_with_the_other_options(
    make_theatre: MakeTheatre, layout: tuple[str, ...]
) -> None:
    """Req 9.1, 9.6: ``--include`` takes no value, stands before, between or after PATH and VALUE
    (also twice) and combines with ``--reason``, ``--in-include`` and ``--expand-alias``; the
    include is written plain and the reason recorded."""
    theatre = make_theatre("x: 1\n", files={".troupe/inc.yaml": "inner: 1\n"})
    names = {"R": "why", "--reason=R": "--reason=why", "P": "b", "V": "file:inc.yaml"}

    _ok(_set(theatre, *(names.get(arg, arg) for arg in layout)))

    assert_file_bytes(theatre / ".troupe" / "troupe.yml", "x: 1\nb: file:inc.yaml\n")
    sets = [line for line in _history(theatre) if line["op"] == "set"]
    reason = "why" if "R" in layout or "--reason=R" in layout else None
    assert [(line["path"], line["new"], line["reason"]) for line in sets] == [
        ("b.inner", 1, reason)
    ]


# ---- 9.2: --include=TEXT ----


@pytest.mark.parametrize("attached", ["x", "", "true"])
def test_include_with_a_value_is_a_usage_error(make_theatre: MakeTheatre, attached: str) -> None:
    """Req 9.2: ``--include=TEXT`` exits 2 with empty stdout and a usage message on stderr,
    leaves every file byte-identical and creates no history state."""
    theatre = make_theatre("x: 1\n", files={".troupe/inc.yaml": "inner: 1\n"})
    before = snapshot_tree(theatre)

    result = _set(theatre, "b", "file:inc.yaml", f"--include={attached}")

    assert (result.exit_code, result.stdout) == (2, b"")
    assert result.stderr == (
        f"troupe config set: argument --include: ignored explicit argument {attached!r}\n"
    )
    assert snapshot_tree(theatre) == before
    assert _history_state(theatre) == {}


def test_set_help_mentions_the_include_option(make_theatre: MakeTheatre) -> None:
    """Req 9.1: ``set --help`` lists ``--include`` without a metavar."""
    theatre = make_theatre("x: 1\n")
    result = run_config("set", "--help", cwd=theatre)
    assert (result.exit_code, result.stderr) == (0, ""), result.stderr
    text = result.stdout.decode("utf-8")
    assert "--include " in text or "--include\n" in text
    assert "--include=" not in text and "--include TEXT" not in text
