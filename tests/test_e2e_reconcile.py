"""Reconcile-first, end to end through the real ``troupe config`` console script.

Every command except ``history`` reconciles an imported theatre with its files before doing its
own work (config-file-sync 1.1); ``history`` never reconciles (1.3), so it is the safe observer
used here. Each test builds a real theatre on disk, hand-edits its files between commands, and
asserts the exact stdout, stderr and exit code of each command and the exact ``history`` lines.
Every case runs on both ``troupe.yml`` and ``troupe.yaml`` theatres.

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from set_support import run_config, run_set, snapshot_tree
from support import CliResult, MakeTheatre

type Event = tuple[int, str, str, Any, Any]
"""One history line as ``(seq, op, path, old, new)``."""


@pytest.fixture(params=["troupe.yml", "troupe.yaml"])
def config_name(request: pytest.FixtureRequest) -> str:
    """The root config file name of the theatre (both lookups are covered)."""
    name: str = request.param
    return name


def _root(theatre: Path, config_name: str) -> Path:
    return theatre / ".troupe" / config_name


def _history_bytes(theatre: Path) -> bytes:
    """The exact ``troupe config history`` stdout (asserting success and an empty stderr)."""
    result = run_config("history", "--theatre", str(theatre), cwd=theatre)
    assert (result.exit_code, result.stderr) == (0, ""), result.stderr
    return result.stdout


def _events(theatre: Path) -> list[Event]:
    """Every recorded event, parsed from the ``history`` lines."""
    rows = [json.loads(line) for line in _history_bytes(theatre).decode("utf-8").splitlines()]
    return [(row["seq"], row["op"], row["path"], row["old"], row["new"]) for row in rows]


def _show(theatre: Path, *args: str) -> CliResult:
    return run_config("show", *args, "--theatre", str(theatre), cwd=theatre)


def _assert_ok(result: CliResult, stdout: bytes) -> None:
    assert (result.exit_code, result.stdout, result.stderr) == (0, stdout, ""), result.stderr


# ---- 1.1, 1.3, 1.6, 2.1, 2.9: show reconciles a hand edit, history does not --------------------


def test_hand_edit_is_recorded_by_show_but_not_by_history_and_printed_in_the_usual_format(
    make_theatre: MakeTheatre, config_name: str
) -> None:
    """Req 1.1, 1.3, 1.6, 2.1, 2.9: after a hand edit, `history` records nothing; the next `show`
    prints the edited value, leaves every file untouched and records one `set` (old = imported,
    new = edited), which `history` then prints in the usual JSON line format."""
    theatre = make_theatre("a: 1\nb: x\n", config_name=config_name)
    imported = _history_bytes(theatre)
    assert imported == (
        b'{"seq": 1, "op": "import", "path": "a", "old": null, "new": 1}\n'
        b'{"seq": 2, "op": "import", "path": "b", "old": null, "new": "x"}\n'
    )
    _root(theatre, config_name).write_text("a: 2\nb: x\n")

    assert _history_bytes(theatre) == imported

    files = snapshot_tree(theatre)
    _assert_ok(_show(theatre, "a"), b"2\n")
    assert snapshot_tree(theatre) == files
    assert _history_bytes(theatre) == imported + (
        b'{"seq": 3, "op": "set", "path": "a", "old": 1, "new": 2}\n'
    )


def test_history_path_filter_applies_to_reconcile_events(
    make_theatre: MakeTheatre, config_name: str
) -> None:
    """Req 2.9: `history PATH` filters reconcile events like every other event."""
    theatre = make_theatre("a: 1\nb: x\n", config_name=config_name)
    _history_bytes(theatre)
    _root(theatre, config_name).write_text("a: 2\nb: y\n")
    _assert_ok(_show(theatre, "b"), b"y\n")

    result = run_config("history", "b", "--theatre", str(theatre), cwd=theatre)

    _assert_ok(
        result,
        b'{"seq": 2, "op": "import", "path": "b", "old": null, "new": "x"}\n'
        b'{"seq": 4, "op": "set", "path": "b", "old": "x", "new": "y"}\n',
    )


# ---- 1.1, 1.7: set reconciles first, so its own event's old is the hand-edited value -------------


def test_set_after_hand_edit_records_reconcile_set_before_its_own_set_with_old_chained(
    make_theatre: MakeTheatre, config_name: str
) -> None:
    """Req 1.1, 1.7, 2.1: a hand edit then `set` of the same leaf records the reconcile `set`
    (1 -> 2) first and then the set's own `set` (2 -> 3)."""
    theatre = make_theatre("a: 1\nb: x\n", config_name=config_name)
    _history_bytes(theatre)
    _root(theatre, config_name).write_text("a: 2\nb: x\n")

    result = run_set(theatre, "a", "3")

    _assert_ok(result, b"")
    assert _root(theatre, config_name).read_text() == "a: 3\nb: x\n"
    assert _events(theatre) == [
        (1, "import", "a", None, 1),
        (2, "import", "b", None, "x"),
        (3, "set", "a", 1, 2),
        (4, "set", "a", 2, 3),
    ]


# ---- 1.2: reconcile events survive a failing show (5) or a refused set (3) -----------------------


def test_reconcile_events_are_kept_when_show_exits_5(
    make_theatre: MakeTheatre, config_name: str
) -> None:
    """Req 1.1, 1.2: `show` of a missing PATH after a hand edit exits 5, and the reconcile `set`
    it recorded before resolving PATH stays in the history."""
    theatre = make_theatre("a: 1\n", config_name=config_name)
    _history_bytes(theatre)
    _root(theatre, config_name).write_text("a: 2\n")

    result = _show(theatre, "nope")

    assert (result.exit_code, result.stdout) == (5, b""), result.stderr
    assert result.stderr.startswith(f"{_root(theatre, config_name)}: nope: "), result.stderr
    assert _events(theatre) == [(1, "import", "a", None, 1), (2, "set", "a", 1, 2)]


def test_reconcile_events_are_kept_when_set_exits_3(
    make_theatre: MakeTheatre, config_name: str
) -> None:
    """Req 1.1, 1.2: a `set` refused with exit 3 (replacing a value that wholly comes from an
    include) after a hand edit changes no file, and the reconcile `set` stays in the history."""
    root = "a: 1\nagents:\n  coder: file:agents/coder.yaml\n"
    theatre = make_theatre(
        root, config_name=config_name, files={".troupe/agents/coder.yaml": "model: small\n"}
    )
    _history_bytes(theatre)
    edited = root.replace("a: 1", "a: 2")
    _root(theatre, config_name).write_text(edited)
    files = snapshot_tree(theatre)

    result = run_set(theatre, "agents.coder", "file:agents/other.yaml")

    assert (result.exit_code, result.stdout) == (3, b""), result.stderr
    assert "the whole value comes from the include file:agents/coder.yaml" in result.stderr
    assert snapshot_tree(theatre) == files
    assert _events(theatre) == [
        (1, "import", "a", None, 1),
        (2, "import", "agents.coder.model", None, "small"),
        (3, "set", "a", 1, 2),
    ]


# ---- 1.4: a never-imported theatre is imported, with no reconcile events -------------------------


def test_first_show_on_fresh_theatre_records_imports_only(
    make_theatre: MakeTheatre, config_name: str
) -> None:
    """Req 1.4: the first `show` on a never-imported theatre imports it and records no reconcile
    event in the same command."""
    theatre = make_theatre("a: 1\nb:\n  c: true\n", config_name=config_name)

    _assert_ok(_show(theatre, "b.c"), b"true\n")

    assert _events(theatre) == [
        (1, "import", "a", None, 1),
        (2, "import", "b.c", None, True),
    ]


# ---- 1.5: a usage error or a load error records nothing ----------------------------------------


@pytest.mark.parametrize(
    "args",
    [
        pytest.param(("show", "a", "--bogus"), id="unknown-option"),
        pytest.param(("show",), id="missing-path"),
        pytest.param(("set", "a"), id="set-missing-value"),
    ],
)
def test_usage_error_records_nothing(
    make_theatre: MakeTheatre, config_name: str, args: tuple[str, ...]
) -> None:
    """Req 1.5: a malformed command line exits 2 and does not reconcile a pending hand edit,
    which the next `show` still records."""
    theatre = make_theatre("a: 1\n", config_name=config_name)
    imported = _history_bytes(theatre)
    _root(theatre, config_name).write_text("a: 2\n")

    result = run_config(*args, "--theatre", str(theatre), cwd=theatre)

    assert (result.exit_code, result.stdout) == (2, b""), result.stderr
    assert _history_bytes(theatre) == imported
    _assert_ok(_show(theatre, "a"), b"2\n")
    assert _events(theatre)[-1] == (2, "set", "a", 1, 2)


def test_load_error_in_root_file_records_nothing(
    make_theatre: MakeTheatre, config_name: str
) -> None:
    """Req 1.5: `show` on an invalid root file exits 4 naming it and records nothing; after the
    file is fixed with a new value, `history` still lists only the import."""
    theatre = make_theatre("a: 1\n", config_name=config_name)
    imported = _history_bytes(theatre)
    _root(theatre, config_name).write_text("a: [\n")

    result = _show(theatre, "a")

    assert (result.exit_code, result.stdout) == (4, b""), result.stderr
    assert result.stderr.startswith(f"{_root(theatre, config_name)}: invalid YAML"), result.stderr
    _root(theatre, config_name).write_text("a: 2\n")
    assert _history_bytes(theatre) == imported


def test_load_error_in_include_does_not_reconcile_a_valid_root_edit(
    make_theatre: MakeTheatre, config_name: str
) -> None:
    """Req 1.5: with a valid hand edit in the root file and invalid YAML in an included file, `set`
    exits 4 naming the included file and records nothing; once the include is restored, `history`
    still lists only the imports."""
    theatre = make_theatre(
        "a: 1\nteam: file:team.yml\n",
        config_name=config_name,
        files={".troupe/team.yml": "lead: alice\n"},
    )
    imported = _history_bytes(theatre)
    _root(theatre, config_name).write_text("a: 2\nteam: file:team.yml\n")
    (theatre / ".troupe/team.yml").write_text("lead: [\n")

    result = run_set(theatre, "a", "3")

    assert (result.exit_code, result.stdout) == (4, b""), result.stderr
    assert result.stderr.startswith(f"{theatre / '.troupe/team.yml'}: "), result.stderr
    (theatre / ".troupe/team.yml").write_text("lead: alice\n")
    assert _history_bytes(theatre) == imported


# ---- 2.4: an edit that changes no leaf records nothing -----------------------------------------


def test_comment_and_formatting_only_edit_records_nothing(
    make_theatre: MakeTheatre, config_name: str
) -> None:
    """Req 2.4: an edit that only adds comments and blank lines, changes quoting style and key
    order records no event when `show` reconciles."""
    theatre = make_theatre("a: 1\nb: x\n", config_name=config_name)
    imported = _history_bytes(theatre)
    _root(theatre, config_name).write_text("# a comment\nb: 'x'  # quoted now\n\na: 1\n")

    _assert_ok(_show(theatre, "b"), b"x\n")

    assert _history_bytes(theatre) == imported


# ---- 2.8: a text-include edit records one set on the including leaf ------------------------------


def test_text_include_edit_records_one_set_with_old_and_new_text(
    make_theatre: MakeTheatre, config_name: str
) -> None:
    """Req 2.8: changing a text-included file records one `set` on the leaf holding the include,
    with the previous text as `old` and the new text as `new`."""
    theatre = make_theatre(
        "p: file:prompt.txt\n",
        config_name=config_name,
        files={".troupe/prompt.txt": "Be brief.\n"},
    )
    _history_bytes(theatre)
    (theatre / ".troupe/prompt.txt").write_text("Be kind.\nAlways.\n")

    _assert_ok(_show(theatre, "p"), b"Be kind.\nAlways.\n")

    assert _events(theatre) == [
        (1, "import", "p", None, "Be brief.\n"),
        (2, "set", "p", "Be brief.\n", "Be kind.\nAlways.\n"),
    ]


# ---- 3.1, 3.2, 3.3: includes added or removed by hand -------------------------------------------


def test_hand_added_yaml_include_records_a_set_per_contributed_leaf(
    make_theatre: MakeTheatre, config_name: str
) -> None:
    """Req 3.1, 2.2, 2.6: a hand-added YAML include records one `set` (old null) per leaf it
    contributes, in document order."""
    theatre = make_theatre(
        "a: 1\n", config_name=config_name, files={".troupe/team.yml": "lead: alice\nsize: 2\n"}
    )
    _history_bytes(theatre)
    _root(theatre, config_name).write_text("a: 1\nteam: file:team.yml\n")

    _assert_ok(_show(theatre, "team.size"), b"2\n")

    assert _events(theatre) == [
        (1, "import", "a", None, 1),
        (2, "set", "team.lead", None, "alice"),
        (3, "set", "team.size", None, 2),
    ]


def test_hand_added_text_include_records_one_set_with_the_exact_text(
    make_theatre: MakeTheatre, config_name: str
) -> None:
    """Req 3.2: a hand-added include of a non-YAML file records one `set` for the including leaf
    with the file's exact text as `new`."""
    theatre = make_theatre(
        "a: 1\n", config_name=config_name, files={".troupe/prompt.md": "Line one\r\nLine two"}
    )
    _history_bytes(theatre)
    _root(theatre, config_name).write_text("a: 1\np: file:prompt.md\n")

    _assert_ok(_show(theatre, "a"), b"1\n")

    assert _events(theatre) == [
        (1, "import", "a", None, 1),
        (2, "set", "p", None, "Line one\r\nLine two"),
    ]


def test_hand_removed_include_records_a_remove_per_contributed_leaf(
    make_theatre: MakeTheatre, config_name: str
) -> None:
    """Req 3.3, 2.3, 2.6: removing an include by hand records one `remove` per leaf it contributed
    (old = recorded value, new null), oldest-created first."""
    theatre = make_theatre(
        "a: 1\nteam: file:team.yml\n",
        config_name=config_name,
        files={".troupe/team.yml": "lead: alice\nsize: 2\n"},
    )
    _history_bytes(theatre)
    _root(theatre, config_name).write_text("a: 1\n")

    _assert_ok(_show(theatre, "a"), b"1\n")

    assert _events(theatre) == [
        (1, "import", "a", None, 1),
        (2, "import", "team.lead", None, "alice"),
        (3, "import", "team.size", None, 2),
        (4, "remove", "team.lead", "alice", None),
        (5, "remove", "team.size", 2, None),
    ]
