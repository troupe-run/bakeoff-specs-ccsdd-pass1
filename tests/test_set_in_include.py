"""``troupe config set --in-include`` end to end through the real console script (task 4.3, CR-B).

With ``--in-include`` a new key is written by the step 1 rule: into the file holding the deepest
existing ancestor mapping of PATH, which for a merged mapping is its included side (followed through
nested includes to the innermost file), with no conversion of a scalar-form include (10.1-10.3).
Existing values, appends and replacements are written as without the option (10.4); below no
include the bytes are those of the set without the option (10.5). Each case runs the real
``troupe`` console script on a real theatre and asserts:

* exit 0 with empty stdout and stderr;
* the exact bytes of the one changed file and that every other file is byte-identical, with no file
  added or removed (10.1, 10.7);
* the history: the events the ``set`` added are exactly its ``set`` events, one per created leaf,
  with ``via`` ``cli``, the actor from ``TROUPE_ACTOR``, the ``--reason`` TEXT (or ``null``) and one
  change id; a reconciling ``show`` afterwards records nothing (10.7);
* for contrast, that the same command without the option writes an override instead.

The option takes no value and may stand before, between or after PATH and VALUE, together with
``--reason``; ``--in-include=TEXT`` is a usage error (exit 2, empty stdout, a usage message on
stderr) that changes no file and creates no history state (10.6).

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

import json
from pathlib import Path
from typing import Any, Final

import pytest

from set_support import TreeSnapshot, assert_file_bytes, run_config, snapshot_tree
from support import LINEAGE_KEYS, STEP1_KEYS, CliResult, MakeTheatre
from troupe.cli.actor import TROUPE_ACTOR_VARIABLE
from troupe.config.constants import CONFIG_DIR_NAME, HISTORY_STORE_NAME

ROOT: Final = ".troupe/troupe.yml"
CODER_FILE: Final = ".troupe/agents/coder.yaml"
ACTOR: Final = "agent:planner"
ACTOR_JSON: Final = {"kind": "agent", "id": "planner"}

CODER: Final = "# shared coder settings\nmodel: small\n\nmax_tokens: 100  # cap\n"
"""An included agent with a comment, a blank line and an end-of-line comment."""

type Line = dict[str, Any]
"""One ``history`` line parsed as JSON."""

SetEvent = tuple[str, str, object, object]
"""The members of one recorded event this module checks: ``(op, path, old, new)``."""


@pytest.fixture(autouse=True)
def _declared_actor(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every command in this module runs as a declared actor (no developer shell leakage)."""
    monkeypatch.setenv(TROUPE_ACTOR_VARIABLE, ACTOR)


# ---- helpers ----


def _ok(result: CliResult) -> None:
    assert (result.exit_code, result.stdout, result.stderr) == (0, b"", ""), result.stderr


def _history(theatre: Path) -> list[Line]:
    """Every ``troupe config history`` line, parsed in full (``history`` never reconciles)."""
    result = run_config("history", "--theatre", str(theatre), cwd=theatre)
    assert (result.exit_code, result.stderr) == (0, ""), result.stderr
    lines: list[Line] = []
    for raw in result.stdout.decode("utf-8").splitlines():
        parsed = json.loads(raw)
        assert isinstance(parsed, dict) and tuple(parsed) == STEP1_KEYS + LINEAGE_KEYS, raw
        lines.append(parsed)
    return lines


def _imported(theatre: Path) -> int:
    """Import the theatre (a ``show`` records every leaf); return the number of history lines."""
    result = run_config("show", "--theatre", str(theatre), cwd=theatre)
    assert (result.exit_code, result.stderr) == (0, ""), result.stderr
    lines = _history(theatre)
    assert lines and {line["op"] for line in lines} == {"import"}
    return len(lines)


def _assert_set_events(
    theatre: Path, start: int, expected: list[SetEvent], reason: str | None = None
) -> None:
    """Assert that the history lines after the first ``start`` are exactly ``expected``: ``set``
    events with cli lineage (actor, ``reason``), one change; a reconciling ``show`` records
    nothing afterwards."""
    added = _history(theatre)[start:]
    assert [(line["op"], line["path"], line["old"], line["new"]) for line in added] == expected
    assert all(line["via"] == "cli" for line in added), added
    assert all(line["actor"] == ACTOR_JSON for line in added), added
    assert all(line["reason"] == reason for line in added), added
    assert len({line["change"] for line in added}) == 1, added
    reconcile = run_config("show", "--theatre", str(theatre), cwd=theatre)
    assert (reconcile.exit_code, reconcile.stderr) == (0, ""), reconcile.stderr
    assert len(_history(theatre)) == start + len(expected)


def _assert_only_changed(theatre: Path, before: TreeSnapshot, relative: str, expected: str) -> None:
    """Assert that ``relative`` holds exactly ``expected`` and every other file is unchanged,
    with no file added or removed."""
    assert_file_bytes(theatre / relative, expected)
    after = snapshot_tree(theatre)
    assert set(after) == set(before)
    assert {k: v for k, v in after.items() if k != relative} == {
        k: v for k, v in before.items() if k != relative
    }


def _set(theatre: Path, *args: str) -> CliResult:
    return run_config("set", *args, "--theatre", str(theatre), cwd=theatre)


def _show(theatre: Path, path: str) -> bytes:
    result = run_config("show", path, "--theatre", str(theatre), cwd=theatre)
    assert (result.exit_code, result.stderr) == (0, ""), result.stderr
    return result.stdout


def _history_state(theatre: Path) -> list[str]:
    return sorted(p.name for p in (theatre / CONFIG_DIR_NAME).glob(f"{HISTORY_STORE_NAME}*"))


def _check(
    make_theatre: MakeTheatre,
    files: dict[str, str],
    args: tuple[str, ...],
    changed: str,
    expected: str,
    events: list[SetEvent],
    *,
    reason: str | None = None,
) -> Path:
    """Run ``set ARGS`` on a fresh imported theatre; assert the bytes and the events."""
    theatre = make_theatre(files=files)
    start = _imported(theatre)
    before = snapshot_tree(theatre)
    _ok(_set(theatre, *args))
    _assert_only_changed(theatre, before, changed, expected)
    _assert_set_events(theatre, start, events, reason)
    return theatre


# ---- 10.1: a scalar-form include gains the key in the included file ----

SCALAR_ROOT: Final = "# team\nagents:\n  coder: file:agents/coder.yaml  # c\n"


def test_scalar_form_include_gains_the_key_in_the_included_file(
    make_theatre: MakeTheatre,
) -> None:
    """Req 10.1, 10.7: ``set --in-include`` below a scalar-form include writes the key into the
    included file; the include is not converted and the including file is byte-identical."""
    _check(
        make_theatre,
        {ROOT: SCALAR_ROOT, CODER_FILE: CODER},
        ("--in-include", "agents.coder.temperature", "0.3"),
        CODER_FILE,
        CODER + "temperature: 0.3\n",
        [("set", "agents.coder.temperature", None, 0.3)],
    )


def test_scalar_form_include_without_the_option_is_converted(make_theatre: MakeTheatre) -> None:
    """Req 10.1 (contrast): the same command without ``--in-include`` writes an override in the
    including file (converted to mapping form) and leaves the included file unchanged."""
    _check(
        make_theatre,
        {ROOT: SCALAR_ROOT, CODER_FILE: CODER},
        ("agents.coder.temperature", "0.3"),
        ROOT,
        "# team\nagents:\n  coder:  # c\n    $ref: file:agents/coder.yaml\n    temperature: 0.3\n",
        [("set", "agents.coder.temperature", None, 0.3)],
    )


def test_missing_intermediate_mappings_go_into_the_included_file(
    make_theatre: MakeTheatre,
) -> None:
    """Req 10.1, 10.7: missing intermediate mappings are created in the included file, with one
    ``set`` event per created leaf."""
    _check(
        make_theatre,
        {ROOT: SCALAR_ROOT, CODER_FILE: CODER},
        ("agents.coder.params", "{t: 1, k: [a]}", "--in-include"),
        CODER_FILE,
        CODER + "params:\n  t: 1\n  k:\n    - a\n",
        [
            ("set", "agents.coder.params.t", None, 1),
            ("set", "agents.coder.params.k.0", None, "a"),
        ],
    )


# ---- 10.2: a merged mapping gains the key on its included side ----

MERGED_ROOT: Final = (
    "agents:\n  coder:\n    $ref: file:agents/coder.yaml\n    model: big\n    extra: 1\n"
)


def test_merged_mapping_gains_the_key_on_its_included_side(make_theatre: MakeTheatre) -> None:
    """Req 10.1, 10.2, 10.7: below a merged mapping the key goes into the included file, and
    ``show`` lists it after the included keys and before the override-only key."""
    theatre = _check(
        make_theatre,
        {ROOT: MERGED_ROOT, CODER_FILE: CODER},
        ("agents.coder.temp", "--in-include", "0.5"),
        CODER_FILE,
        CODER + "temp: 0.5\n",
        [("set", "agents.coder.temp", None, 0.5)],
    )
    assert _show(theatre, "agents.coder") == b"model: big\nmax_tokens: 100\ntemp: 0.5\nextra: 1\n"


def test_merged_mapping_without_the_option_gains_an_override(make_theatre: MakeTheatre) -> None:
    """Req 10.2 (contrast): without ``--in-include`` the key is an override in the including
    file, listed after the override-only key."""
    theatre = _check(
        make_theatre,
        {ROOT: MERGED_ROOT, CODER_FILE: CODER},
        ("agents.coder.temp", "0.5"),
        ROOT,
        MERGED_ROOT + "    temp: 0.5\n",
        [("set", "agents.coder.temp", None, 0.5)],
    )
    assert _show(theatre, "agents.coder") == b"model: big\nmax_tokens: 100\nextra: 1\ntemp: 0.5\n"


# ---- 10.1: nested includes and override-only ancestors ----

NESTED: Final = {
    ROOT: "agent:\n  $ref: file:mid.yaml\n  a: 1\n",
    ".troupe/mid.yaml": "$ref: file:inner.yaml\nb: 2\n",
    ".troupe/inner.yaml": "c: 3\n",
}


def test_nested_includes_write_in_the_innermost_file(make_theatre: MakeTheatre) -> None:
    """Req 10.1, 10.2, 10.7: through nested mapping-form includes the key goes into the innermost
    file and is listed before the override-only keys of every level."""
    theatre = _check(
        make_theatre,
        NESTED,
        ("--in-include", "agent.d", "4"),
        ".troupe/inner.yaml",
        "c: 3\nd: 4\n",
        [("set", "agent.d", None, 4)],
    )
    assert _show(theatre, "agent") == b"c: 3\nd: 4\nb: 2\na: 1\n"


def test_nested_includes_without_the_option_write_in_the_outermost_file(
    make_theatre: MakeTheatre,
) -> None:
    """Req 10.1 (contrast): without ``--in-include`` the key is an override in the root file."""
    _check(
        make_theatre,
        NESTED,
        ("agent.d", "4"),
        ROOT,
        "agent:\n  $ref: file:mid.yaml\n  a: 1\n  d: 4\n",
        [("set", "agent.d", None, 4)],
    )


def test_ancestor_written_only_in_an_override_writes_in_the_including_file(
    make_theatre: MakeTheatre,
) -> None:
    """Req 10.1, 10.7: an ancestor written only in an override gains the key in the including
    file; the included file is byte-identical."""
    root = "agents:\n  coder:\n    $ref: file:agents/coder.yaml\n    extra:\n      x: 1\n"
    _check(
        make_theatre,
        {ROOT: root, CODER_FILE: CODER},
        ("agents.coder.extra.y", "2", "--in-include"),
        ROOT,
        root + "      y: 2\n",
        [("set", "agents.coder.extra.y", None, 2)],
    )


# ---- 10.3: a mapping VALUE's new keys ----

PARAMS_ROOT: Final = "coder:\n  $ref: file:coder.yaml\n  params:\n    u: 3\n"
PARAMS_CODER: Final = "model: y\nparams:\n  t: 1\n  v: 2\n"


def test_mapping_value_new_keys_go_to_the_included_file(make_theatre: MakeTheatre) -> None:
    """Req 10.3, 10.2, 10.7: a mapping VALUE's new keys go into the included side of the merged
    mapping, listed before its override-only key; one event per created leaf."""
    theatre = _check(
        make_theatre,
        {ROOT: PARAMS_ROOT, ".troupe/coder.yaml": PARAMS_CODER},
        ("--in-include", "coder.params", "{t: 1, v: 2, u: 3, k: 4, m: 5}"),
        ".troupe/coder.yaml",
        PARAMS_CODER + "  k: 4\n  m: 5\n",
        [("set", "coder.params.k", None, 4), ("set", "coder.params.m", None, 5)],
    )
    assert _show(theatre, "coder.params") == b"t: 1\nv: 2\nk: 4\nm: 5\nu: 3\n"


def test_mapping_value_without_the_option_writes_overrides(make_theatre: MakeTheatre) -> None:
    """Req 10.3 (contrast): without ``--in-include`` the new keys are overrides."""
    _check(
        make_theatre,
        {ROOT: PARAMS_ROOT, ".troupe/coder.yaml": PARAMS_CODER},
        ("coder.params", "{t: 1, v: 2, u: 3, k: 4, m: 5}"),
        ROOT,
        PARAMS_ROOT + "    k: 4\n    m: 5\n",
        [("set", "coder.params.k", None, 4), ("set", "coder.params.m", None, 5)],
    )


# ---- 10.4: existing values, appends, replacements ----

EXISTING_ROOT: Final = "coder:\n  $ref: file:coder.yaml\n  model: z\n  extra: 1\n"
EXISTING_CODER: Final = "model: y\ntools: 1\nlist:\n  - a\n"


@pytest.mark.parametrize(
    ("path", "value", "changed", "expected", "event"),
    [
        pytest.param(
            "coder.model",
            "w",
            ROOT,
            "coder:\n  $ref: file:coder.yaml\n  model: w\n  extra: 1\n",
            ("set", "coder.model", "z", "w"),
            id="overridden",
        ),
        pytest.param(
            "coder.tools",
            "2",
            ".troupe/coder.yaml",
            "model: y\ntools: 2\nlist:\n  - a\n",
            ("set", "coder.tools", 1, 2),
            id="included",
        ),
        pytest.param(
            "coder.list.1",
            "b",
            ".troupe/coder.yaml",
            "model: y\ntools: 1\nlist:\n  - a\n  - b\n",
            ("set", "coder.list.1", None, "b"),
            id="append",
        ),
        pytest.param(
            "coder.extra",
            "2",
            ROOT,
            "coder:\n  $ref: file:coder.yaml\n  model: z\n  extra: 2\n",
            ("set", "coder.extra", 1, 2),
            id="override-only",
        ),
    ],
)
@pytest.mark.parametrize("with_option", [True, False], ids=["in-include", "plain"])
def test_existing_values_and_appends_do_not_depend_on_the_option(
    make_theatre: MakeTheatre,
    path: str,
    value: str,
    changed: str,
    expected: str,
    event: SetEvent,
    with_option: bool,
) -> None:
    """Req 10.4, 10.7: an existing value changes where it is written and an append goes to the
    list's file, with the same bytes and events with and without ``--in-include``."""
    option = ("--in-include",) if with_option else ()
    _check(
        make_theatre,
        {ROOT: EXISTING_ROOT, ".troupe/coder.yaml": EXISTING_CODER},
        (path, value, *option),
        changed,
        expected,
        [event],
    )


# ---- 10.5: no include involved ----

PLAIN_ROOT: Final = "# plain\na:\n  x: 1  # c\nb: 2\n"


@pytest.mark.parametrize("with_option", [True, False], ids=["in-include", "plain"])
def test_no_include_gives_the_same_bytes_as_without_the_option(
    make_theatre: MakeTheatre, with_option: bool
) -> None:
    """Req 10.5, 10.7: below no include, ``--in-include`` writes exactly what ``set`` writes
    without it."""
    option = ("--in-include",) if with_option else ()
    _check(
        make_theatre,
        {ROOT: PLAIN_ROOT},
        (*option, "a.p.q", "1"),
        ROOT,
        "# plain\na:\n  x: 1  # c\n  p:\n    q: 1\nb: 2\n",
        [("set", "a.p.q", None, 1)],
    )


# ---- 10.6, 10.7: option position and --reason ----


@pytest.mark.parametrize(
    "layout",
    [
        pytest.param(("--in-include", "--reason", "R", "P", "V"), id="before"),
        pytest.param(("P", "--in-include", "V", "--reason=R"), id="between"),
        pytest.param(("P", "V", "--reason", "R", "--in-include"), id="after"),
        pytest.param(("--reason=R", "P", "--in-include", "V"), id="reason-first"),
        pytest.param(("P", "V", "--in-include", "--in-include", "--reason", "R"), id="twice"),
    ],
)
def test_option_anywhere_together_with_reason(
    make_theatre: MakeTheatre, layout: tuple[str, ...]
) -> None:
    """Req 10.6, 10.7: ``--in-include`` takes no value and works before, between or after PATH and
    VALUE (also given twice), together with ``--reason``; the reason is on the set's event."""
    reason = "extend the shared coder"
    names = {
        "R": reason,
        "--reason=R": f"--reason={reason}",
        "P": "agents.coder.temperature",
        "V": "0.3",
    }
    args = tuple(names.get(arg, arg) for arg in layout)
    _check(
        make_theatre,
        {ROOT: SCALAR_ROOT, CODER_FILE: CODER},
        args,
        CODER_FILE,
        CODER + "temperature: 0.3\n",
        [("set", "agents.coder.temperature", None, 0.3)],
        reason=reason,
    )


@pytest.mark.parametrize("attached", ["x", "", "true"])
def test_in_include_with_a_value_is_a_usage_error(make_theatre: MakeTheatre, attached: str) -> None:
    """Req 10.6: ``--in-include=TEXT`` exits 2 with empty stdout and a usage message on stderr,
    leaves every file byte-identical and creates no history state on a fresh theatre."""
    theatre = make_theatre(files={ROOT: SCALAR_ROOT, CODER_FILE: CODER})
    before = snapshot_tree(theatre)

    result = _set(theatre, "agents.coder.temperature", "0.3", f"--in-include={attached}")

    assert result.exit_code == 2, result
    assert result.stdout == b""
    assert result.stderr == (
        f"troupe config set: argument --in-include: ignored explicit argument {attached!r}\n"
    )
    assert snapshot_tree(theatre) == before
    assert _history_state(theatre) == []


def test_in_include_with_a_value_on_an_imported_theatre_changes_no_history(
    make_theatre: MakeTheatre,
) -> None:
    """Req 10.6: on an imported theatre, ``--in-include=TEXT`` leaves the history state bytes and
    every file unchanged."""
    theatre = make_theatre(files={ROOT: SCALAR_ROOT, CODER_FILE: CODER})
    _imported(theatre)
    state = {
        name: (theatre / CONFIG_DIR_NAME / name).read_bytes() for name in _history_state(theatre)
    }
    before = snapshot_tree(theatre)

    result = _set(theatre, "--in-include=yes", "agents.coder.temperature", "0.3")

    assert (result.exit_code, result.stdout) == (2, b"")
    assert "--in-include" in result.stderr
    assert snapshot_tree(theatre) == before
    assert {
        name: (theatre / CONFIG_DIR_NAME / name).read_bytes() for name in _history_state(theatre)
    } == state


def test_set_help_mentions_the_option(make_theatre: MakeTheatre) -> None:
    """Req 10.6: ``set --help`` lists ``--in-include`` (without a metavar: it takes no value)."""
    theatre = make_theatre(files={ROOT: SCALAR_ROOT})
    result = run_config("set", "--help", cwd=theatre)
    assert (result.exit_code, result.stderr) == (0, ""), result.stderr
    text = result.stdout.decode("utf-8")
    assert "--in-include " in text or "--in-include\n" in text
    assert "--in-include=" not in text and "--in-include TEXT" not in text
