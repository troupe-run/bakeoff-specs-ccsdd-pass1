"""New keys below includes, end to end through the real `troupe config set` (task 4.1).

A new key whose deepest existing ancestor comes from an included file is written as an override in
the file holding that include's reference (4.1-4.6); a scalar-form include is first converted to
mapping form (5.1-5.5). Each case runs the real ``troupe`` console script on a real theatre and
asserts:

* exit 0 with empty stdout and stderr;
* the exact bytes of the one changed file, and that every other file (the included files above
  all) is byte-identical, with no file added or removed (5.5, 9.1, 9.3);
* the history: the events the ``set`` added are exactly its ``set`` events, one per created leaf,
  with ``via`` ``cli`` and one change id (4.7); a following ``show`` (which reconciles) records
  nothing, so the include's conversion to mapping form is no event of its own (4.7);
* the merged value read back by ``show``, which keeps every included key (4.4).

Every case fails if the new key is written into the included file instead (the step 1 rule): the
included file would change and the including file would not.

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

import json
from pathlib import Path
from typing import Any

from set_support import TreeSnapshot, assert_file_bytes, run_config, run_set, snapshot_tree
from support import LINEAGE_KEYS, STEP1_KEYS, CliResult, MakeTheatre

ROOT = ".troupe/troupe.yml"
CODER_FILE = ".troupe/agents/coder.yaml"

CODER = "# shared coder settings\nmodel: small\n\nmax_tokens: 100  # cap\n"
"""An included agent with a comment, a blank line and an end-of-line comment (never changed)."""

type Line = dict[str, Any]
"""One ``history`` line parsed as JSON."""

SetEvent = tuple[str, str, object, object, str]
"""The members of one recorded event this module checks: ``(op, path, old, new, via)``."""


def _assert_success(result: CliResult) -> None:
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
    """Import the theatre (a ``show`` records every leaf) and return the number of history lines."""
    result = run_config("show", "--theatre", str(theatre), cwd=theatre)
    assert (result.exit_code, result.stderr) == (0, ""), result.stderr
    lines = _history(theatre)
    assert lines and {line["op"] for line in lines} == {"import"}
    return len(lines)


def _assert_set_events(theatre: Path, start: int, expected: list[SetEvent]) -> None:
    """Assert that the history lines after the first ``start`` are exactly ``expected``, one
    change, and that a reconciling ``show`` afterwards records nothing (no conversion event)."""
    added = _history(theatre)[start:]
    assert [
        (line["op"], line["path"], line["old"], line["new"], line["via"]) for line in added
    ] == expected
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


def _show(theatre: Path, path: str) -> bytes:
    result = run_config("show", path, "--theatre", str(theatre), cwd=theatre)
    assert (result.exit_code, result.stderr) == (0, ""), result.stderr
    return result.stdout


# ---- 4.1, 5.1, 5.2: the contract example ----

CONTRACT_ROOT = """\
# team settings
name: demo
agents:
  coder: file:agents/coder.yaml  # c
  lead:
    model: big
"""

CONTRACT_RESULT = """\
# team settings
name: demo
agents:
  coder:  # c
    $ref: file:agents/coder.yaml
    temperature: 0.3
  lead:
    model: big
"""


def test_contract_example_converts_the_include_and_records_one_set(
    make_theatre: MakeTheatre,
) -> None:
    """Req 4.1, 4.7, 5.1, 5.2, 5.5, 9.1, 9.3: `coder: file:agents/coder.yaml  # c` with
    `set agents.coder.temperature 0.3` becomes exactly `coder:  # c` / `$ref: ...` /
    `temperature: 0.3` one step deeper; coder.yaml is byte-identical; exactly one `set` event
    with cli lineage and none for the conversion."""
    theatre = make_theatre(CONTRACT_ROOT, files={CODER_FILE: CODER})
    start = _imported(theatre)
    before = snapshot_tree(theatre)

    _assert_success(run_set(theatre, "agents.coder.temperature", "0.3"))

    _assert_only_changed(theatre, before, ROOT, CONTRACT_RESULT)
    _assert_set_events(theatre, start, [("set", "agents.coder.temperature", None, 0.3, "cli")])
    assert _show(theatre, "agents.coder") == b"model: small\nmax_tokens: 100\ntemperature: 0.3\n"


def test_contract_example_in_a_troupe_yaml_theatre(make_theatre: MakeTheatre) -> None:
    """Req 4.1, 4.7, 5.1, 5.2, 5.5, 9.1: the contract example in a theatre whose root file is
    `troupe.yaml` converts the include in `troupe.yaml`; coder.yaml is unchanged."""
    theatre = make_theatre(CONTRACT_ROOT, config_name="troupe.yaml", files={CODER_FILE: CODER})
    start = _imported(theatre)
    before = snapshot_tree(theatre)

    _assert_success(run_set(theatre, "agents.coder.temperature", "0.3"))

    _assert_only_changed(theatre, before, ".troupe/troupe.yaml", CONTRACT_RESULT)
    _assert_set_events(theatre, start, [("set", "agents.coder.temperature", None, 0.3, "cli")])


# ---- 4.3, 4.5: a second new key goes after the first override ----


def test_second_new_key_goes_after_the_first_override(make_theatre: MakeTheatre) -> None:
    """Req 4.3, 4.5, 4.7, 9.1, 9.3: after the first override, a second new key goes into the same
    `$ref` mapping after it (not into coder.yaml); each set records its own `set` event."""
    theatre = make_theatre(CONTRACT_ROOT, files={CODER_FILE: CODER})
    _assert_success(run_set(theatre, "agents.coder.temperature", "0.3"))
    start = len(_history(theatre))
    before = snapshot_tree(theatre)

    _assert_success(run_set(theatre, "agents.coder.top_p", "0.9"))

    _assert_only_changed(
        theatre,
        before,
        ROOT,
        CONTRACT_RESULT.replace("temperature: 0.3\n", "temperature: 0.3\n    top_p: 0.9\n"),
    )
    _assert_set_events(theatre, start, [("set", "agents.coder.top_p", None, 0.9, "cli")])


EXISTING_OVERRIDE_ROOT = """\
agents:
  coder:
    $ref: file:agents/coder.yaml
    'model': big  # mine
    # end of the overrides

  lead: x
"""


def test_new_key_follows_an_existing_override_with_its_trailing_comment_and_style(
    make_theatre: MakeTheatre,
) -> None:
    """Req 4.3, 4.5, 4.7, 9.1: a new key below an include that already has an override goes into
    that override, after its last entry and the comment trailing it, quoted like its sibling."""
    theatre = make_theatre(EXISTING_OVERRIDE_ROOT, files={CODER_FILE: CODER})
    start = _imported(theatre)
    before = snapshot_tree(theatre)

    _assert_success(run_set(theatre, "agents.coder.temperature", "0.3"))

    _assert_only_changed(
        theatre,
        before,
        ROOT,
        EXISTING_OVERRIDE_ROOT.replace(
            "# end of the overrides\n", "# end of the overrides\n    'temperature': 0.3\n"
        ),
    )
    _assert_set_events(theatre, start, [("set", "agents.coder.temperature", None, 0.3, "cli")])


# ---- 4.1, 4.4: missing intermediate keys, keys below included nested mappings ----


def test_missing_intermediate_keys_are_created_in_the_override(make_theatre: MakeTheatre) -> None:
    """Req 4.1, 4.7, 5.1, 9.1: `agents.coder.limits.daily.tokens` (no `limits` anywhere) converts
    the include and creates `limits:` / `daily:` below `$ref`; one `set` for the one leaf."""
    theatre = make_theatre(CONTRACT_ROOT, files={CODER_FILE: CODER})
    start = _imported(theatre)
    before = snapshot_tree(theatre)

    _assert_success(run_set(theatre, "agents.coder.limits.daily.tokens", "500"))

    _assert_only_changed(
        theatre,
        before,
        ROOT,
        CONTRACT_RESULT.replace(
            "temperature: 0.3\n", "limits:\n      daily:\n        tokens: 500\n"
        ),
    )
    _assert_set_events(
        theatre, start, [("set", "agents.coder.limits.daily.tokens", None, 500, "cli")]
    )


def test_mapping_value_creates_one_set_event_per_leaf(make_theatre: MakeTheatre) -> None:
    """Req 4.1, 4.7, 5.1, 9.1: a new key whose VALUE is a mapping is written as one override
    holding it, and records one `set` per created leaf (in VALUE order) in one change."""
    theatre = make_theatre(CONTRACT_ROOT, files={CODER_FILE: CODER})
    start = _imported(theatre)
    before = snapshot_tree(theatre)

    _assert_success(run_set(theatre, "agents.coder.limits", "{daily: 500, burst: 50}"))

    _assert_only_changed(
        theatre,
        before,
        ROOT,
        CONTRACT_RESULT.replace(
            "temperature: 0.3\n", "limits:\n      daily: 500\n      burst: 50\n"
        ),
    )
    _assert_set_events(
        theatre,
        start,
        [
            ("set", "agents.coder.limits.daily", None, 500, "cli"),
            ("set", "agents.coder.limits.burst", None, 50, "cli"),
        ],
    )


NESTED_CODER = "model: small\nparams:  # tuning\n  top_k: 40\n  seed: 7\n"


def test_key_below_an_included_nested_mapping_creates_it_in_the_override(
    make_theatre: MakeTheatre,
) -> None:
    """Req 4.1, 4.4, 4.7, 5.1, 9.1: `agents.coder.params.top_p`, where `params` exists only in
    coder.yaml, creates `params:` / `top_p: 0.9` in the override; the merged `params` keeps every
    included key and gains the new one; coder.yaml is unchanged."""
    theatre = make_theatre(CONTRACT_ROOT, files={CODER_FILE: NESTED_CODER})
    start = _imported(theatre)
    before = snapshot_tree(theatre)

    _assert_success(run_set(theatre, "agents.coder.params.top_p", "0.9"))

    _assert_only_changed(
        theatre,
        before,
        ROOT,
        CONTRACT_RESULT.replace("temperature: 0.3\n", "params:\n      top_p: 0.9\n"),
    )
    _assert_set_events(theatre, start, [("set", "agents.coder.params.top_p", None, 0.9, "cli")])
    assert _show(theatre, "agents.coder.params") == b"top_k: 40\nseed: 7\ntop_p: 0.9\n"


# ---- 4.2: nested includes write in the middle file ----

NESTED_ROOT = "name: demo\nteam: file:teams/core.yaml  # the core team\n"
MIDDLE_FILE = ".troupe/teams/core.yaml"
MIDDLE = "# core team\nlead: ann\ncoder: file:coder.yaml\n"
INNER_FILE = ".troupe/teams/coder.yaml"


def test_nested_includes_write_the_override_in_the_middle_file(make_theatre: MakeTheatre) -> None:
    """Req 4.2, 4.7, 5.1, 5.5, 9.1: below `team` (included by troupe.yml) and `team.coder`
    (included by teams/core.yaml), the new key converts the nearest include, in teams/core.yaml,
    the file holding its reference; troupe.yml and teams/coder.yaml are unchanged."""
    theatre = make_theatre(NESTED_ROOT, files={MIDDLE_FILE: MIDDLE, INNER_FILE: CODER})
    start = _imported(theatre)
    before = snapshot_tree(theatre)

    _assert_success(run_set(theatre, "team.coder.temperature", "0.3"))

    _assert_only_changed(
        theatre,
        before,
        MIDDLE_FILE,
        "# core team\nlead: ann\ncoder:\n  $ref: file:coder.yaml\n  temperature: 0.3\n",
    )
    _assert_set_events(theatre, start, [("set", "team.coder.temperature", None, 0.3, "cli")])


# ---- 4.5: a bare `$ref` mapping gains an override ----

BARE_ROOT = """\
agents:
  coder:
    $ref: "file:agents/coder.yaml"  # shared
  lead: x
"""


def test_bare_ref_mapping_gains_an_override(make_theatre: MakeTheatre) -> None:
    """Req 4.1, 4.5, 4.7, 9.1: a bare block `$ref` mapping gains the new key after its `$ref`
    entry, the `$ref` line kept as written; coder.yaml is unchanged."""
    theatre = make_theatre(BARE_ROOT, files={CODER_FILE: CODER})
    start = _imported(theatre)
    before = snapshot_tree(theatre)

    _assert_success(run_set(theatre, "agents.coder.temperature", "0.3"))

    _assert_only_changed(
        theatre,
        before,
        ROOT,
        BARE_ROOT.replace("# shared\n", "# shared\n    temperature: 0.3\n"),
    )
    _assert_set_events(theatre, start, [("set", "agents.coder.temperature", None, 0.3, "cli")])


def test_bare_flow_ref_mapping_gains_an_override(make_theatre: MakeTheatre) -> None:
    """Req 4.1, 4.5, 4.7, 9.1: a bare flow `{$ref: ...}` mapping gains the new key as a flow
    entry after `$ref`; the comment the longer value now reaches is one space after it
    (config-set-writeback 12.2); coder.yaml is unchanged."""
    root = "agents:\n  coder: {$ref: file:agents/coder.yaml}  # flow\n"
    theatre = make_theatre(root, files={CODER_FILE: CODER})
    start = _imported(theatre)
    before = snapshot_tree(theatre)

    _assert_success(run_set(theatre, "agents.coder.temperature", "0.3"))

    _assert_only_changed(
        theatre,
        before,
        ROOT,
        "agents:\n  coder: {$ref: file:agents/coder.yaml, temperature: 0.3} # flow\n",
    )
    _assert_set_events(theatre, start, [("set", "agents.coder.temperature", None, 0.3, "cli")])


# ---- 5.4: a flow include ----


def test_flow_include_becomes_a_flow_ref_mapping(make_theatre: MakeTheatre) -> None:
    """Req 4.1, 4.7, 5.4, 5.5, 9.1: a scalar-form include inside a flow mapping becomes
    `{$ref: <string>, k: v}` in place; the rest of the line is kept and its comment, now reached by
    the longer value, is one space after it (config-set-writeback 12.2); coder.yaml is unchanged."""
    root = "name: demo\nagents: {coder: file:agents/coder.yaml, lead: x}  # flow\n"
    theatre = make_theatre(root, files={CODER_FILE: CODER})
    start = _imported(theatre)
    before = snapshot_tree(theatre)

    _assert_success(run_set(theatre, "agents.coder.temperature", "0.3"))

    _assert_only_changed(
        theatre,
        before,
        ROOT,
        "name: demo\n"
        "agents: {coder: {$ref: file:agents/coder.yaml, temperature: 0.3}, lead: x} # flow\n",
    )
    _assert_set_events(theatre, start, [("set", "agents.coder.temperature", None, 0.3, "cli")])


# ---- 4.6: no include involved ----


def test_new_key_beside_an_include_in_a_root_mapping_stays_in_the_root_file(
    make_theatre: MakeTheatre,
) -> None:
    """Req 4.6, 4.7, 9.1: a new key whose deepest existing ancestor is written in troupe.yml (the
    `agents` mapping holding an include) is added there as before; coder.yaml is unchanged."""
    theatre = make_theatre(CONTRACT_ROOT, files={CODER_FILE: CODER})
    start = _imported(theatre)
    before = snapshot_tree(theatre)

    _assert_success(run_set(theatre, "agents.helper.model", "tiny"))

    _assert_only_changed(theatre, before, ROOT, CONTRACT_ROOT + "  helper:\n    model: tiny\n")
    _assert_set_events(theatre, start, [("set", "agents.helper.model", None, "tiny", "cli")])
