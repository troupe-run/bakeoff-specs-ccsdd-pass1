"""Read-only safety and history state location, end to end through the real console script.

Covers product-owner decision E: the whole tree around a theatre (the theatre itself, an include
directory inside the theatre but outside ``.troupe/``, an include directory outside the theatre and
the working directory) is listed before and after every kind of command -- first ``show`` (import),
``history``, a second ``show``, usage errors, a path error (exit 5) and load failures (exit 4).
After each command the only new entry anywhere is ``.troupe/history.sqlite``, no entry is removed,
every user file (configuration, YAML includes, text includes with CRLF and no final newline) is
byte-identical and untouched (3.6, 7.6), and ``.troupe/`` holds no ``history.sqlite-wal`` /
``history.sqlite-shm`` because the store was closed (7.9). A configuration that fails to load
creates no history state (7.4); a corrupt history store exits 4 naming the store (7.8). Every
non-zero exit writes nothing to stdout (10.2).

The side-file check is the filesystem-visible part of 7.9 only: the event-sourcing application also
closes its connections when it is garbage-collected at interpreter exit, so an explicit close that
went missing is caught by the in-process tests in ``test_theatre.py``, not here.
"""

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

import pytest

from support import CliResult, RunCli, write_theatre
from troupe.config.constants import CONFIG_DIR_NAME, HISTORY_STORE_NAME

DIRECTORY = "<dir>"
"""Listing marker for a directory entry."""

STATE_FILE = f"theatre/{CONFIG_DIR_NAME}/{HISTORY_STORE_NAME}"
"""The only entry any command may add, relative to the listed root."""

SIDECARS = (f"{STATE_FILE}-wal", f"{STATE_FILE}-shm", f"{STATE_FILE}-journal")
"""SQLite side files that must never survive a command (the store is closed before exit)."""

CONFIG = """\
# root configuration; includes live in directories outside .troupe/
name: demo  # trailing comment
team: file:../shared/agents.yaml
external: file:../../external/settings.yml
local: file:local.yml
"""

AGENTS = """\
# shared agents, outside .troupe/
lead:
  name: 'alice'
  prompt: file:prompts/lead.txt
members:
  - bob
  - "carol"
"""

LEAD_PROMPT = b"You are the lead.\r\nBe brief.\r\n\r\nNo final newline"
"""Text include with CRLF line endings and no final newline."""

EXTERNAL = "retries: 3\nratio: 0.5\nnotes: file:notes.md\n"
NOTES = b"# notes\r\nkept verbatim\r\n"
LOCAL = "debug: false\n"


@dataclass(frozen=True)
class Entry:
    """One listed entry: a directory, or a file with its content hash and modification time."""

    digest: str
    mtime_ns: int


type Listing = dict[str, Entry]


def _list_tree(root: Path) -> Listing:
    """List every file and directory below ``root`` (relative POSIX paths), hashing every file."""
    listing: Listing = {}
    for directory, dirnames, filenames in os.walk(root):
        base = Path(directory)
        for name in dirnames:
            path = base / name
            listing[path.relative_to(root).as_posix()] = Entry(DIRECTORY, 0)
        for name in filenames:
            path = base / name
            data = path.read_bytes()
            listing[path.relative_to(root).as_posix()] = Entry(
                hashlib.sha256(data).hexdigest(), path.stat().st_mtime_ns
            )
    return listing


def _build(root: Path) -> Path:
    """Write ``root/theatre`` and an include directory outside the theatre, ``root/external``."""
    write_theatre(
        root / "external",
        files={"settings.yml": EXTERNAL, "notes.md": NOTES},
    )
    return write_theatre(
        root / "theatre",
        CONFIG,
        files={
            "shared/agents.yaml": AGENTS,
            "shared/prompts/lead.txt": LEAD_PROMPT,
            f"{CONFIG_DIR_NAME}/local.yml": LOCAL,
        },
    )


def _assert_only_state_changed(before: Listing, after: Listing) -> None:
    """Assert decision E: no entry removed, user files untouched, only the store may be added."""
    assert sorted(set(before) - set(after)) == []
    assert sorted(set(after) - set(before)) in ([], [STATE_FILE])
    assert [name for name in SIDECARS if name in after] == []
    for name, entry in before.items():
        if name != STATE_FILE:
            assert after[name] == entry, name


def _assert_failure(result: CliResult, exit_code: int) -> None:
    assert result.exit_code == exit_code, result.stderr
    assert result.stdout == b""
    assert result.stderr != ""


# ---- decision E: every kind of command on one theatre, in sequence (3.6, 7.6, 7.9, 10.2) ----


def test_every_command_kind_only_adds_the_store_and_leaves_user_files_untouched(
    tmp_path: Path, run_cli: RunCli
) -> None:
    theatre = _build(tmp_path)
    first = _list_tree(tmp_path)
    assert STATE_FILE not in first

    # First show: imports, creating the store.
    result = run_cli("config", "show", "team.lead.prompt", "--theatre", str(theatre))
    assert (result.exit_code, result.stderr) == (0, "")
    assert result.stdout == LEAD_PROMPT + b"\n"
    after_import = _list_tree(tmp_path)
    _assert_only_state_changed(first, after_import)
    assert STATE_FILE in after_import

    # history, a second show, usage errors and a path error.
    steps: list[tuple[tuple[str, ...], int]] = [
        (("history",), 0),
        (("show", "external.notes"), 0),
        (("show", "a..b"), 2),
        (("history", ".team"), 2),
        (("show", "team", "--bogus"), 2),
        (("show", "team.lead.missing"), 5),
        (("show", "team.members"), 5),
    ]
    for args, exit_code in steps:
        before = _list_tree(tmp_path)
        result = run_cli("config", *args, "--theatre", str(theatre))
        if exit_code == 0:
            assert (result.exit_code, result.stderr) == (0, ""), args
        else:
            _assert_failure(result, exit_code)
        _assert_only_state_changed(before, _list_tree(tmp_path))

    # Break an include (the edit happens before the listing), then every command fails to load.
    (theatre / "shared" / "agents.yaml").write_text("lead: file:prompts/missing.txt\n")
    for args in (("show", "name"), ("history",)):
        before = _list_tree(tmp_path)
        result = run_cli("config", *args, "--theatre", str(theatre))
        _assert_failure(result, 4)
        assert "missing.txt" in result.stderr
        _assert_only_state_changed(before, _list_tree(tmp_path))


def test_show_and_history_leave_every_file_byte_identical(tmp_path: Path, run_cli: RunCli) -> None:
    theatre = _build(tmp_path)
    before = _list_tree(tmp_path)

    for args in (("show", "team.lead.name"), ("history",), ("show", "local.debug"), ("history",)):
        result = run_cli("config", *args, "--theatre", str(theatre))
        assert (result.exit_code, result.stderr) == (0, ""), args

    after = _list_tree(tmp_path)
    assert sorted(set(after) - set(before)) == [STATE_FILE]
    assert {name: after[name] for name in before} == before
    assert (theatre / "shared" / "prompts" / "lead.txt").read_bytes() == LEAD_PROMPT
    assert (tmp_path / "external" / "notes.md").read_bytes() == NOTES
    assert (theatre / CONFIG_DIR_NAME / "troupe.yml").read_text() == CONFIG


# ---- decision E on a fresh theatre: each command kind as the very first command (7.4, 7.6) ----


@pytest.mark.parametrize(
    ("args", "exit_code", "creates_store"),
    [
        (("show", "name"), 0, True),
        (("history",), 0, True),
        (("show", "a..b"), 2, False),
        (("history", "a."), 2, False),
        (("show", "team.lead.missing"), 5, True),
    ],
    ids=["show", "history", "show-usage", "history-usage", "path-error"],
)
def test_first_command_of_each_kind_only_creates_the_store_under_troupe(
    tmp_path: Path,
    run_cli: RunCli,
    args: tuple[str, ...],
    exit_code: int,
    creates_store: bool,
) -> None:
    theatre = _build(tmp_path)
    before = _list_tree(tmp_path)

    result = run_cli("config", *args, "--theatre", str(theatre))

    if exit_code == 0:
        assert (result.exit_code, result.stderr) == (0, "")
    else:
        _assert_failure(result, exit_code)
    after = _list_tree(tmp_path)
    _assert_only_state_changed(before, after)
    assert (STATE_FILE in after) is creates_store


# ---- 7.4: a configuration that fails to load creates no history state ----


@pytest.mark.parametrize(
    "broken",
    [
        {"shared/agents.yaml": "lead: file:prompts/absent.txt\n"},
        {"shared/agents.yaml": "lead: [unclosed\n"},
        {"shared/agents.yaml": "lead: file:../.troupe/troupe.yml\n"},
    ],
    ids=["missing-target", "invalid-yaml", "cycle"],
)
@pytest.mark.parametrize("args", [("show", "name"), ("history",)], ids=["show", "history"])
def test_load_failure_on_fresh_theatre_creates_no_history_state(
    tmp_path: Path, run_cli: RunCli, broken: dict[str, str], args: tuple[str, ...]
) -> None:
    theatre = _build(tmp_path)
    write_theatre(theatre, files=broken)
    before = _list_tree(tmp_path)

    result = run_cli("config", *args, "--theatre", str(theatre))

    _assert_failure(result, 4)
    after = _list_tree(tmp_path)
    assert after == before
    assert list((theatre / CONFIG_DIR_NAME).glob(f"{HISTORY_STORE_NAME}*")) == []


# ---- 7.8: a corrupt history store exits 4 naming the store, and is left as found ----


@pytest.mark.parametrize("args", [("show", "name"), ("history",)], ids=["show", "history"])
def test_corrupt_store_exits_4_naming_the_store_without_touching_anything(
    tmp_path: Path, run_cli: RunCli, args: tuple[str, ...]
) -> None:
    theatre = _build(tmp_path)
    store = theatre / CONFIG_DIR_NAME / HISTORY_STORE_NAME
    store.write_bytes(b"this is certainly not an SQLite database " * 10)
    before = _list_tree(tmp_path)

    result = run_cli("config", *args, "--theatre", str(theatre))

    _assert_failure(result, 4)
    assert result.stderr.startswith(f"{store}: "), result.stderr
    assert _list_tree(tmp_path) == before
