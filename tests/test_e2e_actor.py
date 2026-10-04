"""``TROUPE_ACTOR`` end to end through the real ``troupe`` console script.

Proves, through real theatres on disk and real ``troupe config`` subprocesses, that a declared
``human:<id>``/``agent:<id>`` is the actor of ``set``'s cli events exactly as given, including an id
containing ``:`` and a non-ASCII id (2.1, 1.2); that an unset variable gives a human with the login
name from the environment (2.2, 1.2); and that every invalid value makes every subcommand (``get``,
``show``, ``set``, ``history``, ``watch``) exit 2 with empty stdout and a message naming
``TROUPE_ACTOR``, quoting the value and stating the accepted forms (2.3), before any file is read
or changed and without creating or changing any history state: no ``history.sqlite*`` on a fresh
theatre, and no reconcile of a pending hand edit on an imported one (2.4).

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

import json
import subprocess
from pathlib import Path
from typing import Any, Final

import pytest

from support import (
    LINEAGE_KEYS,
    STEP1_KEYS,
    CliResult,
    MakeTheatre,
    RunCli,
    troupe_executable,
)
from troupe.config.constants import CONFIG_DIR_NAME, HISTORY_STORE_NAME

type Line = dict[str, Any]
"""One ``history`` line parsed as JSON (values are JSON, hence ``Any``)."""

CONFIG: Final = """\
# the demo theatre
name: demo   # keep this comment
agents:
  lead:
    model: 'opus'
    prompt: file:prompts/lead.md

  helper: {model: haiku}
"""

FILES: Final = {".troupe/prompts/lead.md": "Lead the troupe.\n\n- be kind\n"}

EDITED: Final = CONFIG.replace("name: demo", "name: renamed")
"""``CONFIG`` with one leaf (``name``) changed by hand."""

INVALID_VALUES: Final = ["", "human", "human:", "agent:   ", "Human:x", "robot:x", ":x"]
"""Each invalid form of Req 2.3: empty, no ``:``, empty id, blank id, upper-case kind, other kind,
empty kind."""

MATRIX_VALUES: Final = ["", "Human:x", "agent:   "]
"""A few invalid values run against every subcommand (the rest run against ``set`` only)."""

SUBCOMMANDS: Final = {
    "get": ("get", "name"),
    "show": ("show",),
    "set": ("set", "name", "changed"),
    "history": ("history",),
    "watch": ("watch",),
}

LOGIN_VARIABLES: Final = ("LOGNAME", "USER", "LNAME", "USERNAME")
"""The variables ``getpass.getuser`` consults, in its order."""

COMMAND_TIMEOUT_SECONDS: Final = 30.0
"""Bound for one invalid-actor command; ``watch`` must exit at once instead of watching."""


# ---- helpers ----


def _ok(result: CliResult) -> CliResult:
    assert (result.exit_code, result.stderr) == (0, ""), result.stderr
    return result


def _expected_stderr(value: str) -> str:
    return f"TROUPE_ACTOR: invalid value {value!r}: expected human:<id> or agent:<id>\n"


def _lines(run_cli: RunCli, theatre: Path) -> list[Line]:
    """Every ``history`` line parsed in full, asserting member order and the actor members."""
    stdout = _ok(run_cli("config", "history", "--theatre", str(theatre))).stdout
    lines: list[Line] = []
    for raw in stdout.decode("utf-8").splitlines():
        parsed = json.loads(raw)
        assert isinstance(parsed, dict), raw
        assert tuple(parsed) == STEP1_KEYS + LINEAGE_KEYS, raw
        assert isinstance(parsed["actor"], dict) and tuple(parsed["actor"]) == ("kind", "id"), raw
        lines.append(parsed)
    return lines


def _snapshot(theatre: Path) -> dict[str, bytes | None]:
    """Every entry under ``theatre``: files by exact bytes, directories as ``None``."""
    return {
        path.relative_to(theatre).as_posix(): (path.read_bytes() if path.is_file() else None)
        for path in sorted(theatre.rglob("*"))
    }


def _history_state(theatre: Path) -> list[str]:
    return sorted(path.name for path in (theatre / CONFIG_DIR_NAME).glob(f"{HISTORY_STORE_NAME}*"))


def _invoke_bounded(*args: str, cwd: Path) -> CliResult:
    """Run the real ``troupe`` console script, failing if it does not exit within the bound."""
    try:
        completed = subprocess.run(
            [str(troupe_executable()), *args],
            cwd=cwd,
            capture_output=True,
            check=False,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as error:
        raise AssertionError(f"troupe {' '.join(args)} did not exit: {error}") from error
    return CliResult(
        exit_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr.decode("utf-8"),
    )


def _assert_refused(theatre: Path, subcommand: str, value: str) -> None:
    """Req 2.3: ``subcommand`` exits 2, stdout empty, stderr names the variable, value and forms."""
    result = _invoke_bounded(
        "config", *SUBCOMMANDS[subcommand], "--theatre", str(theatre), cwd=theatre.parent
    )

    assert result.exit_code == 2, result
    assert result.stdout == b""
    assert result.stderr == _expected_stderr(value)
    assert "TROUPE_ACTOR" in result.stderr
    assert repr(value) in result.stderr
    assert "human:<id> or agent:<id>" in result.stderr


# ---- 2.1, 1.2: the declared actor on set's events ----


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("human:alice", {"kind": "human", "id": "alice"}),
        ("agent:planner", {"kind": "agent", "id": "planner"}),
        ("agent:planner:v2", {"kind": "agent", "id": "planner:v2"}),
        ("human:élodie ü", {"kind": "human", "id": "élodie ü"}),
    ],
)
def test_declared_actor_is_the_actor_of_set_events_exactly_as_given(
    value: str,
    expected: dict[str, str],
    make_theatre: MakeTheatre,
    run_cli: RunCli,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Req 2.1, 1.2: with ``TROUPE_ACTOR`` ``human:<id>``/``agent:<id>``, every event of ``set``
    (a mapping changing two leaves) has that kind and as id every character after the first ``:``
    (a further ``:`` and non-ASCII kept), printed as an object of exactly ``kind`` and ``id``."""
    theatre = make_theatre(CONFIG, files=FILES)
    monkeypatch.setenv("TROUPE_ACTOR", value)

    _ok(
        run_cli(
            "config",
            "set",
            "agents.helper",
            "{model: sonnet, retries: 2}",
            "--theatre",
            str(theatre),
        )
    )
    lines = _lines(run_cli, theatre)

    assert [
        (line["op"], line["path"], line["new"], line["actor"])
        for line in lines
        if line["via"] == "cli"
    ] == [
        ("set", "agents.helper.model", "sonnet", expected),
        ("set", "agents.helper.retries", 2, expected),
    ]


# ---- 2.2, 1.2: the default actor ----


def test_unset_actor_is_a_human_with_the_login_name(
    make_theatre: MakeTheatre, run_cli: RunCli, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Req 2.2, 1.2: with ``TROUPE_ACTOR`` unset, ``set``'s event has actor kind ``human`` and the
    login name from the environment as id."""
    theatre = make_theatre(CONFIG, files=FILES)
    monkeypatch.delenv("TROUPE_ACTOR", raising=False)
    for name in LOGIN_VARIABLES:
        monkeypatch.setenv(name, "tester")

    _ok(run_cli("config", "set", "name", "changed", "--theatre", str(theatre)))
    lines = _lines(run_cli, theatre)

    assert [
        (line["path"], line["new"], line["actor"]) for line in lines if line["via"] == "cli"
    ] == [("name", "changed", {"kind": "human", "id": "tester"})]


# ---- 2.3, 2.4: invalid values refused before any state ----


@pytest.mark.parametrize("subcommand", list(SUBCOMMANDS))
@pytest.mark.parametrize("value", MATRIX_VALUES)
def test_every_subcommand_refuses_an_invalid_actor_on_a_fresh_theatre(
    subcommand: str, value: str, make_theatre: MakeTheatre, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Req 2.3, 2.4: every subcommand with an invalid ``TROUPE_ACTOR`` exits 2 (``watch`` at once)
    with empty stdout and the message; files stay byte-identical and no history store is created."""
    theatre = make_theatre(CONFIG, files=FILES)
    before = _snapshot(theatre)
    monkeypatch.setenv("TROUPE_ACTOR", value)

    _assert_refused(theatre, subcommand, value)

    assert _history_state(theatre) == []
    assert _snapshot(theatre) == before


@pytest.mark.parametrize("value", INVALID_VALUES)
def test_set_refuses_each_invalid_form_on_a_fresh_theatre(
    value: str, make_theatre: MakeTheatre, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Req 2.3, 2.4: each invalid form (empty, no ``:``, empty or blank id, upper-case or other
    kind, empty kind) makes ``set`` exit 2 with the message; nothing written, no history store."""
    theatre = make_theatre(CONFIG, files=FILES)
    before = _snapshot(theatre)
    monkeypatch.setenv("TROUPE_ACTOR", value)

    _assert_refused(theatre, "set", value)

    assert _history_state(theatre) == []
    assert _snapshot(theatre) == before


@pytest.mark.parametrize("subcommand", list(SUBCOMMANDS))
def test_invalid_actor_leaves_an_imported_theatre_and_its_pending_edit_untouched(
    subcommand: str, make_theatre: MakeTheatre, run_cli: RunCli, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Req 2.3, 2.4: on an imported theatre with a hand edit pending, an invalid ``TROUPE_ACTOR``
    makes every subcommand exit 2 without changing any file or the history store: the history reads
    the same afterwards and the edit is still reconciled by the next valid command."""
    theatre = make_theatre(CONFIG, files=FILES)
    imported = _lines(run_cli, theatre)
    (theatre / CONFIG_DIR_NAME / "troupe.yml").write_text(EDITED, encoding="utf-8")
    before = _snapshot(theatre)
    assert _history_state(theatre) != []

    monkeypatch.setenv("TROUPE_ACTOR", "Human:x")
    _assert_refused(theatre, subcommand, "Human:x")
    monkeypatch.delenv("TROUPE_ACTOR")

    assert _snapshot(theatre) == before
    assert _lines(run_cli, theatre) == imported
    assert _ok(run_cli("config", "get", "name", "--theatre", str(theatre))).stdout == b"renamed\n"
    edits = _lines(run_cli, theatre)[len(imported) :]
    assert [(line["op"], line["path"], line["new"], line["via"]) for line in edits] == [
        ("set", "name", "renamed", "file")
    ]
