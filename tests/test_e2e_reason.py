"""``troupe config set --reason TEXT`` end to end through the real ``troupe`` console script.

Proves, through real theatres on disk and real ``troupe config`` subprocesses, that the reason
given with ``set`` is printed by ``history`` exactly as given on every event of the set's own
change, with ``--reason TEXT`` before, between or after the positionals and as ``--reason=TEXT``,
including spaces, quotes, a line break, non-ASCII, the empty text and texts starting with ``-``
(6.1, 1.5); that it is ``null`` without the option (6.2, 1.5) and on the import and reconcile events
a reasoned ``set`` records before its own (6.3); that the last ``--reason`` counts (6.4); that
``--reason`` without TEXT exits 2 with empty stdout and a usage message, before any file is changed
or any history state is created (6.5); and that a reasoned ``set`` that changes nothing records
nothing and exits 0 with empty stdout (6.6).

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

import json
from pathlib import Path
from typing import Any, Final

import pytest

from support import LINEAGE_KEYS, STEP1_KEYS, CliResult, MakeTheatre, RunCli
from troupe.config.constants import CONFIG_DIR_NAME, HISTORY_STORE_NAME

type Line = dict[str, Any]
"""One ``history`` line parsed as JSON (values are JSON, hence ``Any``)."""

CONFIG: Final = """\
# the reason theatre
name: demo   # keep this comment
agents:
  lead: alice
  coder: 'bob'
"""

EDITED: Final = CONFIG.replace("lead: alice", "lead: zed")
"""``CONFIG`` with one leaf (``agents.lead``) changed by hand."""

PATH: Final = "agents"
VALUE: Final = "{lead: carol, coder: dave}"
"""A set that changes two leaves, so the reason must be on every event of one change."""


# ---- helpers ----


def _ok(result: CliResult) -> None:
    """A successful ``set``: exit 0, nothing on stdout or stderr."""
    assert (result.exit_code, result.stdout, result.stderr) == (0, b"", ""), result.stderr


def _lines(run_cli: RunCli, theatre: Path) -> list[Line]:
    """Every ``history`` line parsed in full, asserting the member order of each line."""
    result = run_cli("config", "history", "--theatre", str(theatre))
    assert (result.exit_code, result.stderr) == (0, ""), result.stderr
    lines: list[Line] = []
    for raw in result.stdout.decode("utf-8").splitlines():
        parsed = json.loads(raw)
        assert isinstance(parsed, dict), raw
        assert tuple(parsed) == STEP1_KEYS + LINEAGE_KEYS, raw
        lines.append(parsed)
    return lines


def _cli(lines: list[Line]) -> list[tuple[str, Any, Any]]:
    """``(path, new, reason)`` of every cli event, in order."""
    return [(line["path"], line["new"], line["reason"]) for line in lines if line["via"] == "cli"]


def _history_state(theatre: Path) -> dict[str, bytes]:
    """Every ``.troupe/history.sqlite*`` file by name and exact bytes."""
    return {
        path.name: path.read_bytes()
        for path in sorted((theatre / CONFIG_DIR_NAME).glob(f"{HISTORY_STORE_NAME}*"))
    }


def _snapshot(theatre: Path) -> dict[str, bytes | None]:
    """Every entry under ``theatre``: files by exact bytes, directories as ``None``."""
    return {
        path.relative_to(theatre).as_posix(): (path.read_bytes() if path.is_file() else None)
        for path in sorted(theatre.rglob("*"))
    }


# ---- 6.1, 1.5: wherever the option stands ----


@pytest.mark.parametrize(
    "layout",
    [
        pytest.param(("--reason", "{r}", PATH, VALUE, "--theatre", "{t}"), id="before"),
        pytest.param((PATH, "--reason", "{r}", VALUE, "--theatre", "{t}"), id="between"),
        pytest.param((PATH, VALUE, "--reason", "{r}", "--theatre", "{t}"), id="after"),
        pytest.param((PATH, VALUE, "--theatre", "{t}", "--reason={r}"), id="equals-after"),
    ],
)
def test_reason_in_every_position_is_on_every_event_of_the_change(
    layout: tuple[str, ...], make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    """Req 6.1, 1.5: ``--reason TEXT``/``--reason=TEXT`` before, between or after PATH and VALUE
    is printed as the ``reason`` of both events of the set's one change."""
    theatre = make_theatre(CONFIG)
    reason = "hand the lead to carol"
    args = [arg.replace("{r}", reason).replace("{t}", str(theatre)) for arg in layout]

    _ok(run_cli("config", "set", *args, cwd=theatre.parent))

    lines = _lines(run_cli, theatre)
    assert _cli(lines) == [
        ("agents.lead", "carol", reason),
        ("agents.coder", "dave", reason),
    ]
    assert len({line["change"] for line in lines if line["via"] == "cli"}) == 1


# ---- 6.1, 1.5: TEXT kept exactly ----


@pytest.mark.parametrize(
    ("reason_args", "reason"),
    [
        pytest.param(
            ("--reason", "two  spaces and\ta tab "), "two  spaces and\ta tab ", id="spaces"
        ),
        pytest.param(("--reason", 'it\'s "quoted" \\o/'), 'it\'s "quoted" \\o/', id="quotes"),
        pytest.param(("--reason", "line one\nline two"), "line one\nline two", id="newline"),
        pytest.param(
            ("--reason=ticket №42: Zoë → 東京 🎭",), "ticket №42: Zoë → 東京 🎭", id="unicode"
        ),
        pytest.param(("--reason", ""), "", id="empty"),
        pytest.param(("--reason", "-x"), "-x", id="single-dash"),
        pytest.param(("--reason=-h",), "-h", id="equals-help"),
    ],
)
def test_reason_text_is_kept_exactly(
    reason_args: tuple[str, ...], reason: str, make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    """Req 6.1, 1.5: TEXT with spaces, quotes, a line break, non-ASCII, empty, or starting with
    ``-`` (``--reason -x``; option-like texts as ``--reason=TEXT``, research Decision 6) is printed
    as ``reason`` exactly as given, as one JSON string on one history line."""
    theatre = make_theatre(CONFIG)

    _ok(run_cli("config", "set", "name", "x", *reason_args, "--theatre", str(theatre)))

    assert _cli(_lines(run_cli, theatre)) == [("name", "x", reason)]
    raw = run_cli("config", "history", "--theatre", str(theatre)).stdout.decode("utf-8")
    assert f'"reason": {json.dumps(reason)}, ' in raw.splitlines()[-1]


# ---- 6.2, 1.5: absent -> null ----


def test_without_reason_every_event_has_null_reason(
    make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    """Req 6.2, 1.5: a set without ``--reason`` prints ``"reason": null`` on each of its events."""
    theatre = make_theatre(CONFIG)

    _ok(run_cli("config", "set", PATH, VALUE, "--theatre", str(theatre)))

    assert _cli(_lines(run_cli, theatre)) == [
        ("agents.lead", "carol", None),
        ("agents.coder", "dave", None),
    ]
    raw = run_cli("config", "history", "--theatre", str(theatre)).stdout.decode("utf-8")
    assert all('"reason": null, ' in line for line in raw.splitlines())


# ---- 6.4: given twice -> last ----


def test_reason_given_twice_records_the_last(make_theatre: MakeTheatre, run_cli: RunCli) -> None:
    """Req 6.4: with ``--reason TEXT`` and later ``--reason=TEXT``, the event's ``reason`` is the
    last TEXT."""
    theatre = make_theatre(CONFIG)

    _ok(
        run_cli(
            "config",
            "set",
            "name",
            "--reason",
            "first",
            "x",
            "--reason=second",
            "--theatre",
            str(theatre),
        )
    )

    assert _cli(_lines(run_cli, theatre)) == [("name", "x", "second")]


# ---- 6.3: import and reconcile events of a reasoned set ----


def test_import_events_of_a_first_reasoned_set_have_null_reason(
    make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    """Req 6.3, 6.1: when the first command on a fresh theatre is ``set --reason``, the import
    events it records have ``reason`` null and only the set's own events carry the reason."""
    theatre = make_theatre(CONFIG)

    _ok(run_cli("config", "set", PATH, VALUE, "--reason", "why", "--theatre", str(theatre)))

    lines = _lines(run_cli, theatre)
    imports = [(line["path"], line["reason"]) for line in lines if line["via"] == "import"]
    assert imports == [("name", None), ("agents.lead", None), ("agents.coder", None)]
    assert {line["op"] for line in lines if line["via"] == "import"} == {"import"}
    assert _cli(lines) == [("agents.lead", "carol", "why"), ("agents.coder", "dave", "why")]


def test_reconcile_events_before_a_reasoned_set_have_null_reason(
    make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    """Req 6.3, 6.1: after a hand edit, ``set --reason`` first records the file event with
    ``reason`` null, then its own event with the reason, in separate changes."""
    theatre = make_theatre(CONFIG)
    _ok(run_cli("config", "set", "name", "first", "--theatre", str(theatre)))
    (theatre / CONFIG_DIR_NAME / "troupe.yml").write_text(
        EDITED.replace("name: demo", "name: first"), encoding="utf-8"
    )

    _ok(
        run_cli(
            "config", "set", "agents.coder", "dave", "--reason", "why", "--theatre", str(theatre)
        )
    )

    lines = _lines(run_cli, theatre)
    assert [(line["via"], line["path"], line["new"], line["reason"]) for line in lines[-3:]] == [
        ("cli", "name", "first", None),
        ("file", "agents.lead", "zed", None),
        ("cli", "agents.coder", "dave", "why"),
    ]
    assert lines[-2]["change"] != lines[-1]["change"]


# ---- 6.5: missing TEXT ----


def test_reason_without_text_exits_2_and_touches_nothing(
    make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    """Req 6.5: ``--reason`` without TEXT exits 2 with empty stdout and a usage message on stderr,
    leaves every file byte-identical and creates no history state on a fresh theatre."""
    theatre = make_theatre(CONFIG)
    before = _snapshot(theatre)

    result = run_cli("config", "set", "name", "x", "--theatre", str(theatre), "--reason")

    assert result.exit_code == 2, result
    assert result.stdout == b""
    assert result.stderr == "troupe config set: argument --reason: expected one argument\n"
    assert _snapshot(theatre) == before
    assert _history_state(theatre) == {}


# ---- 6.6: a reasoned set that changes nothing ----


def test_reasoned_set_that_changes_nothing_records_nothing(
    make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    """Req 6.6: on an imported theatre, ``set PATH VALUE --reason TEXT`` with VALUE equal to the
    current value (a mapping equal to the current one) exits 0 with empty stdout, records no event
    (history state bytes unchanged, same ``history`` output) and leaves the files byte-identical."""
    theatre = make_theatre(CONFIG)
    imported = run_cli("config", "history", "--theatre", str(theatre))
    assert imported.exit_code == 0, imported.stderr
    files = _snapshot(theatre)
    state = _history_state(theatre)
    assert state  # the theatre is imported, so the no-op set has a store to leave alone

    result = run_cli(
        "config",
        "set",
        PATH,
        "{lead: alice, coder: bob}",
        "--reason",
        "no-op",
        "--theatre",
        str(theatre),
    )

    _ok(result)
    assert _history_state(theatre) == state
    assert _snapshot(theatre) == files
    after = run_cli("config", "history", "--theatre", str(theatre))
    assert after.stdout == imported.stdout
    assert all(line["reason"] is None for line in _lines(run_cli, theatre))
