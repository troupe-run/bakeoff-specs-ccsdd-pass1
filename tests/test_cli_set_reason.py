"""``troupe config set --reason TEXT`` (config-lineage task 2.4).

Proves that the reason given with ``set`` is recorded verbatim on every event of the set's own
change, wherever the option stands among the arguments and in its ``--reason=TEXT`` form (6.1);
that it is ``null`` without the option (6.2) and on the reconcile events a reasoned ``set`` records
first (6.3); that the last of several ``--reason`` counts (6.4); and that ``--reason`` without TEXT
is a usage error that touches no file and creates no history state (6.5). The reason is read from
real ``history`` output.

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from set_support import assert_file_bytes, run_config, run_config_in_process, snapshot_tree
from support import CliResult, MakeTheatre, step1_members
from troupe.cli.actor import TROUPE_ACTOR_VARIABLE

CONFIG = "# reason fixture\nname: demo\nagents:\n  lead: alice  # keep\n  coder: bob\n"


@pytest.fixture(autouse=True)
def _declared_actor(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every command in this module runs as a declared actor (no developer shell leakage)."""
    monkeypatch.setenv(TROUPE_ACTOR_VARIABLE, "agent:planner")


def _config(theatre: Path) -> Path:
    return theatre / ".troupe" / "troupe.yml"


def _history_state(theatre: Path) -> list[str]:
    return sorted(p.name for p in (theatre / ".troupe").glob("history.sqlite*"))


def _assert_success(result: CliResult) -> None:
    assert (result.exit_code, result.stdout, result.stderr) == (0, b"", ""), result.stderr


def _history(theatre: Path) -> list[dict[str, Any]]:
    result = run_config_in_process("history", "--theatre", str(theatre))
    assert result.exit_code == 0, result.stderr
    lines = [json.loads(line) for line in result.stdout.decode("utf-8").splitlines()]
    for line in lines:
        step1_members(line)  # member order and no stray member
    return lines


def _cli_events(theatre: Path) -> list[dict[str, Any]]:
    return [line for line in _history(theatre) if line["via"] == "cli"]


def _layouts(theatre: Path, reason: str) -> list[tuple[str, ...]]:
    """``set agents {lead: carol, coder: dave}`` with ``--reason`` before, between, after, ``=``."""
    path, value, where = "agents", "{lead: carol, coder: dave}", f"--theatre={theatre}"
    return [
        ("set", "--reason", reason, path, value, where),
        ("set", path, "--reason", reason, value, where),
        ("set", path, value, "--reason", reason, where),
        ("set", path, value, where, "--reason", reason),
        ("set", f"--reason={reason}", path, value, where),
        ("set", path, f"--reason={reason}", value, where),
        ("set", path, value, f"--reason={reason}", where),
    ]


# ---- 6.1: the reason is recorded verbatim on every event of the set's own change ----


@pytest.mark.parametrize("position", range(7))
def test_reason_is_recorded_on_every_event_of_the_change_in_every_position(
    make_theatre: MakeTheatre, position: int
) -> None:
    """Req 6.1: `--reason TEXT`/`--reason=TEXT` before, between or after the positionals."""
    theatre = make_theatre(CONFIG)
    reason = "rotate leads: Q4 plan  (ticket #42) é"

    _assert_success(run_config_in_process(*_layouts(theatre, reason)[position]))

    events = _cli_events(theatre)
    assert [(e["path"], e["new"]) for e in events] == [
        ("agents.lead", "carol"),
        ("agents.coder", "dave"),
    ]
    assert [e["reason"] for e in events] == [reason, reason]
    assert len({e["change"] for e in events}) == 1
    assert {e["via"] for e in _history(theatre) if e["op"] == "import"} == {"import"}


def test_empty_reason_is_kept_as_empty_text(make_theatre: MakeTheatre) -> None:
    """Req 6.1: `--reason=` and `--reason ""` record the empty text, not null."""
    theatre = make_theatre(CONFIG)

    _assert_success(run_config_in_process("set", "name", "x", "--reason=", f"--theatre={theatre}"))
    _assert_success(
        run_config_in_process("set", "name", "y", "--reason", "", f"--theatre={theatre}")
    )

    assert [e["reason"] for e in _cli_events(theatre)] == ["", ""]


def test_reason_through_the_console_script(make_theatre: MakeTheatre) -> None:
    """Req 6.1: the real `troupe config set ... --reason TEXT` records TEXT on its events."""
    theatre = make_theatre(CONFIG)

    _assert_success(
        run_config(
            "set",
            "agents.lead",
            "carol",
            "--reason",
            "hand over",
            "--theatre",
            str(theatre),
            cwd=theatre,
        )
    )

    assert [(e["path"], e["reason"]) for e in _cli_events(theatre)] == [
        ("agents.lead", "hand over")
    ]


# ---- 6.2: without --reason the set's events have reason null ----


def test_without_reason_the_events_have_null_reason(make_theatre: MakeTheatre) -> None:
    """Req 6.2: a set without `--reason` records `reason` null on each of its events."""
    theatre = make_theatre(CONFIG)

    _assert_success(
        run_config_in_process("set", "agents", "{lead: carol, coder: dave}", f"--theatre={theatre}")
    )

    events = _cli_events(theatre)
    assert len(events) == 2
    assert [e["reason"] for e in events] == [None, None]


# ---- 6.3: import and reconcile events recorded by a reasoned set have reason null ----


def test_reconcile_events_before_a_reasoned_set_have_null_reason(
    make_theatre: MakeTheatre,
) -> None:
    """Req 6.3, 6.1: hand edit then `set --reason`: file events null, the set's events TEXT."""
    theatre = make_theatre(CONFIG)
    _assert_success(run_config_in_process("set", "name", "first", f"--theatre={theatre}"))
    _config(theatre).write_text(
        "# reason fixture\nname: first\nagents:\n  lead: zed  # keep\n  coder: bob\n",
        encoding="utf-8",
    )

    _assert_success(
        run_config_in_process(
            "set", "agents.coder", "dave", "--reason", "why", f"--theatre={theatre}"
        )
    )

    lines = _history(theatre)
    assert {line["reason"] for line in lines if line["via"] == "import"} == {None}
    tail = [(line["via"], line["path"], line["new"], line["reason"]) for line in lines[-2:]]
    assert tail == [
        ("file", "agents.lead", "zed", None),
        ("cli", "agents.coder", "dave", "why"),
    ]
    assert lines[-2]["change"] != lines[-1]["change"]


# ---- 6.4: the last --reason counts ----


def test_reason_given_twice_uses_the_last(make_theatre: MakeTheatre) -> None:
    """Req 6.4: `--reason A ... --reason=B` records B."""
    theatre = make_theatre(CONFIG)

    _assert_success(
        run_config_in_process(
            "set", "--reason", "first", "name", "x", "--reason=second", f"--theatre={theatre}"
        )
    )

    assert [e["reason"] for e in _cli_events(theatre)] == ["second"]


# ---- 6.1: VALUEs (and reasons) starting with "-" ----


@pytest.mark.parametrize(
    "args",
    [
        ("set", "name", "-x", "--reason", "neg"),
        ("set", "--reason", "neg", "name", "-x"),
        ("set", "name", "--reason=neg", "-x"),
    ],
)
def test_leading_dash_value_still_works_with_reason(
    make_theatre: MakeTheatre, args: tuple[str, ...]
) -> None:
    """Req 6.1: a VALUE starting with '-' is still a VALUE when `--reason` is present."""
    theatre = make_theatre("name: demo\n")

    _assert_success(run_config_in_process(*args, f"--theatre={theatre}"))

    assert_file_bytes(_config(theatre), "name: -x\n")
    assert [e["reason"] for e in _cli_events(theatre)] == ["neg"]


@pytest.mark.parametrize(
    ("reason_args", "reason"),
    [
        (("--reason=-h --theatre",), "-h --theatre"),
        (("--reason=-h",), "-h"),
        (("--reason=--theatre",), "--theatre"),
        (("--reason", "-x"), "-x"),
    ],
)
def test_reason_starting_with_a_dash_is_kept_exactly(
    make_theatre: MakeTheatre, reason_args: tuple[str, ...], reason: str
) -> None:
    """Req 6.1: a reason starting with '-' is given as `--reason=TEXT` (or `--reason -x`)."""
    theatre = make_theatre("name: demo\n")

    _assert_success(run_config_in_process("set", "name", "x", *reason_args, f"--theatre={theatre}"))

    assert [e["reason"] for e in _cli_events(theatre)] == [reason]


# ---- 6.5: --reason without TEXT is a usage error with no state ----


@pytest.mark.parametrize(
    "args",
    [
        pytest.param(("set", "name", "x", "--reason"), id="at-the-end"),
        pytest.param(("set", "name", "x", "--reason", "--theatre"), id="before-an-option"),
    ],
)
def test_reason_without_text_exits_2_with_no_state(
    make_theatre: MakeTheatre, args: tuple[str, ...]
) -> None:
    """Req 6.5: `set PATH VALUE --reason` (no TEXT): exit 2, no stdout, usage message, no state."""
    theatre = make_theatre(CONFIG)
    before = snapshot_tree(theatre)

    if args[-1] == "--reason":
        result = run_config(*args, cwd=theatre)  # theatre found from the working directory
    else:
        result = run_config(*args, str(theatre), cwd=theatre)

    assert result.exit_code == 2, result.stderr
    assert result.stdout == b""
    assert result.stderr == "troupe config set: argument --reason: expected one argument\n"
    assert snapshot_tree(theatre) == before
    assert _history_state(theatre) == []


def test_help_lists_reason_with_its_metavar(tmp_path: Path) -> None:
    """Req 6.1: `troupe config set --help` lists `--reason TEXT`."""
    result = run_config("set", "--help", cwd=tmp_path)

    assert result.exit_code == 0, result.stderr
    assert b"--reason TEXT" in result.stdout
