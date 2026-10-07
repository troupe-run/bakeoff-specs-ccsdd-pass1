"""Writes below includes, end to end through the real `troupe config set` (tasks 4.1, 4.2).

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

Task 4.2 adds, with the same checks: lists (6.1-6.3, including a list-item include converted in
the list's file, 5.3), existing values changed where they are written (7.1, 7.2), collection
replacements across layers with removals, pruning and ``{}`` (7.3, 7.4), the refusal to replace a
mapping-form include root (7.5), a bare text include (7.6) and ``$ref`` in PATH and VALUE (8.1,
8.2). Refusals assert exit 3, empty stdout, a stderr line naming the file and PATH, no file
changed and no event (9.2, 9.3).

Task 4.4 pins how 7.5 judges "differs": as for a scalar-form include (rule 4), so a mapping
equal to the merged value is refused.

Task 5.1 (choice 16) pins the include's own string (or a `$ref` VALUE with other overrides) over an
include with overrides as refused, and exactly the written overrides as a no-op.

Task 5.2 (choice 17) pins a new key below a middle file included at several places as written once
in that file, visible at every place (in merged order where a place has overrides of its own),
with one `set` event per new leaf path.

Task 6.1 (choice 18, Requirement 11) pins every other write into a file included at two places as
written once and visible at every place it is not shadowed by an outer override: an existing value
(also one overridden at one place), `--in-include`, a new key in a list item, a list append, a
removal in a collection replacement and a text include shared by two places, with one event per
changed leaf path.

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from set_support import (
    TreeSnapshot,
    assert_file_bytes,
    run_config,
    run_set,
    set_args,
    snapshot_tree,
)
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


# =================================================================================================
# Task 4.2: lists, existing values, collection replacement and `$ref` (6.x, 7.x, 8.x, 9.x)
# =================================================================================================


def _assert_refused(theatre: Path, result: CliResult, file: Path, path: str, before: int) -> str:
    """Assert exit 3, empty stdout, one stderr line ``<file>: <path>: <problem>`` and no history
    event after the first ``before`` lines; return the problem text. Callers also compare the
    tree snapshot (no file changed, added or removed)."""
    prefix = f"{file}: {path}: "
    assert result.exit_code == 3, result.stderr
    assert result.stdout == b""
    assert result.stderr.startswith(prefix), result.stderr
    assert result.stderr.endswith("\n") and result.stderr.count("\n") == 1, result.stderr
    assert len(_history(theatre)) == before
    return result.stderr[len(prefix) : -1]


# ---- 6.1: appending to a list stays in the file holding the list ----

TOOLS_CODER = "model: small\ntools:  # list\n  - read\n  - write\n"

OVERRIDE_ROOT = """\
agents:
  coder:  # c
    $ref: file:agents/coder.yaml
    model: big  # mine
  lead: x
"""


def test_append_to_an_included_list_stays_in_the_included_file(make_theatre: MakeTheatre) -> None:
    """Req 6.1, 9.1, 9.3: `set agents.coder.tools.2 grep`, the list written in coder.yaml below a
    mapping-form include with an override, appends the item in coder.yaml; troupe.yml (holding
    the include and its override) is unchanged; one `set` event."""
    theatre = make_theatre(OVERRIDE_ROOT, files={CODER_FILE: TOOLS_CODER})
    start = _imported(theatre)
    before = snapshot_tree(theatre)

    _assert_success(run_set(theatre, "agents.coder.tools.2", "grep"))

    _assert_only_changed(theatre, before, CODER_FILE, TOOLS_CODER + "  - grep\n")
    _assert_set_events(theatre, start, [("set", "agents.coder.tools.2", None, "grep", "cli")])
    assert _show(theatre, "agents.coder.tools") == b"- read\n- write\n- grep\n"


def test_append_to_an_overriding_list_stays_in_the_override(make_theatre: MakeTheatre) -> None:
    """Req 6.1, 9.1, 9.3: a list written as an override (it replaces the included list) gains the
    item in the override, in its flow style; coder.yaml is unchanged."""
    root = "agents:\n  coder:\n    $ref: file:agents/coder.yaml\n    tools: [read]\n"
    theatre = make_theatre(root, files={CODER_FILE: TOOLS_CODER})
    start = _imported(theatre)
    before = snapshot_tree(theatre)

    _assert_success(run_set(theatre, "agents.coder.tools.1", "grep"))

    _assert_only_changed(theatre, before, ROOT, root.replace("[read]", "[read, grep]"))
    _assert_set_events(theatre, start, [("set", "agents.coder.tools.1", None, "grep", "cli")])


# ---- 6.2: a new key in a list item of an included list ----

STEPS_CODER = "model: small\nsteps:\n  - name: a  # first\n    run: x\n  - name: b\n"


def test_new_key_in_an_item_of_an_included_list_goes_to_the_included_file(
    make_theatre: MakeTheatre,
) -> None:
    """Req 6.2, 9.1, 9.3: `agents.coder.steps.0.retries`, whose deepest ancestor is an item of a
    list written in coder.yaml, is added to that item in coder.yaml (no conversion of the
    `coder` include in troupe.yml); one `set` event."""
    theatre = make_theatre(CONTRACT_ROOT, files={CODER_FILE: STEPS_CODER})
    start = _imported(theatre)
    before = snapshot_tree(theatre)

    _assert_success(run_set(theatre, "agents.coder.steps.0.retries", "3"))

    _assert_only_changed(
        theatre, before, CODER_FILE, STEPS_CODER.replace("run: x\n", "run: x\n    retries: 3\n")
    )
    _assert_set_events(theatre, start, [("set", "agents.coder.steps.0.retries", None, 3, "cli")])


# ---- 6.3, 5.3: a list-item include is converted in the list's file ----


def test_list_item_include_is_converted_compactly_in_the_lists_file(
    make_theatre: MakeTheatre,
) -> None:
    """Req 6.3, 5.3, 5.5, 9.1, 9.3: a block list item `- file:agents/coder.yaml` without a
    comment gaining a key becomes the compact `- $ref: file:agents/coder.yaml` / `  temperature:
    0.3` in troupe.yml; coder.yaml is unchanged; one `set` event and none for the conversion."""
    root = "name: demo\nagents:\n  - file:agents/coder.yaml\n  - lead\n"
    theatre = make_theatre(root, files={CODER_FILE: CODER})
    start = _imported(theatre)
    before = snapshot_tree(theatre)

    _assert_success(run_set(theatre, "agents.0.temperature", "0.3"))

    _assert_only_changed(
        theatre,
        before,
        ROOT,
        "name: demo\nagents:\n  - $ref: file:agents/coder.yaml\n    temperature: 0.3\n  - lead\n",
    )
    _assert_set_events(theatre, start, [("set", "agents.0.temperature", None, 0.3, "cli")])
    assert _show(theatre, "agents.0") == b"model: small\nmax_tokens: 100\ntemperature: 0.3\n"


def test_list_item_include_with_a_comment_starts_the_mapping_on_the_next_line(
    make_theatre: MakeTheatre,
) -> None:
    """Req 6.3, 5.2, 5.3, 5.5, 9.1, 9.3: a block list item `- file:agents/coder.yaml  # c` keeps
    `-` with the whitespace and comment (`-  # c`) and the `$ref` line and override follow on the
    next lines at the item's content column; coder.yaml is unchanged; one `set` event."""
    root = "name: demo\nagents:\n  - file:agents/coder.yaml  # c\n  - lead\n"
    theatre = make_theatre(root, files={CODER_FILE: CODER})
    start = _imported(theatre)
    before = snapshot_tree(theatre)

    _assert_success(run_set(theatre, "agents.0.temperature", "0.3"))

    _assert_only_changed(
        theatre,
        before,
        ROOT,
        "name: demo\nagents:\n"
        "  -  # c\n    $ref: file:agents/coder.yaml\n    temperature: 0.3\n"
        "  - lead\n",
    )
    _assert_set_events(theatre, start, [("set", "agents.0.temperature", None, 0.3, "cli")])


# ---- 7.1, 7.2: existing values change where they are written ----


def test_overridden_value_changes_in_the_override(make_theatre: MakeTheatre) -> None:
    """Req 7.1, 9.1, 9.3: `agents.coder.model` (overridden: `big` over `small`) changes in
    troupe.yml, keeping its end-of-line comment's column; coder.yaml is unchanged."""
    theatre = make_theatre(OVERRIDE_ROOT, files={CODER_FILE: CODER})
    start = _imported(theatre)
    before = snapshot_tree(theatre)

    _assert_success(run_set(theatre, "agents.coder.model", "huge"))

    _assert_only_changed(
        theatre, before, ROOT, OVERRIDE_ROOT.replace("model: big  # mine", "model: huge # mine")
    )
    _assert_set_events(theatre, start, [("set", "agents.coder.model", "big", "huge", "cli")])


def test_value_that_is_not_overridden_changes_in_the_included_file(
    make_theatre: MakeTheatre,
) -> None:
    """Req 7.2, 9.1, 9.3: `agents.coder.max_tokens` (only in coder.yaml) changes in coder.yaml,
    its comment, blank line and end-of-line comment kept; troupe.yml is unchanged."""
    theatre = make_theatre(OVERRIDE_ROOT, files={CODER_FILE: CODER})
    start = _imported(theatre)
    before = snapshot_tree(theatre)

    _assert_success(run_set(theatre, "agents.coder.max_tokens", "200"))

    _assert_only_changed(
        theatre, before, CODER_FILE, CODER.replace("max_tokens: 100", "max_tokens: 200")
    )
    _assert_set_events(theatre, start, [("set", "agents.coder.max_tokens", 100, 200, "cli")])


# ---- 7.3, 7.4: collection replacement across layers ----

PARAMS_CODER = "model: small\nparams:  # tuning\n  top_k: 40\n  seed: 7\nmax_tokens: 100\n"

PARAMS_ROOT = """\
agents:
  coder:
    $ref: file:agents/coder.yaml
    params:
      seed: 9  # mine
      temp: 0.3
    model: big
"""


def _assert_two_changed(theatre: Path, before: TreeSnapshot, expected: dict[str, str]) -> None:
    """Assert that exactly the files of ``expected`` hold their texts and every other file is
    unchanged, with no file added or removed."""
    for relative, text in expected.items():
        assert_file_bytes(theatre / relative, text)
    after = snapshot_tree(theatre)
    assert set(after) == set(before)
    assert {k: v for k, v in after.items() if k not in expected} == {
        k: v for k, v in before.items() if k not in expected
    }


def test_collection_replacement_changes_removes_and_adds_across_layers(
    make_theatre: MakeTheatre,
) -> None:
    """Req 7.3, 7.1, 7.2, 7.4, 4.1, 9.1, 9.3: `set agents.coder.params {top_k: 50, seed: 10,
    top_p: 0.9}` over the merged `params` changes `top_k` in coder.yaml (not overridden), `seed`
    in the override (coder.yaml's `seed: 7` untouched), removes the override-only `temp` from
    troupe.yml and adds `top_p` as an override; one change with one event per leaf."""
    theatre = make_theatre(PARAMS_ROOT, files={CODER_FILE: PARAMS_CODER})
    start = _imported(theatre)
    before = snapshot_tree(theatre)

    _assert_success(run_set(theatre, "agents.coder.params", "{top_k: 50, seed: 10, top_p: 0.9}"))

    _assert_two_changed(
        theatre,
        before,
        {
            CODER_FILE: PARAMS_CODER.replace("top_k: 40", "top_k: 50"),
            ROOT: PARAMS_ROOT.replace(
                "      seed: 9  # mine\n      temp: 0.3\n",
                "      seed: 10 # mine\n      top_p: 0.9\n",
            ),
        },
    )
    _assert_set_events(
        theatre,
        start,
        [
            ("set", "agents.coder.params.top_k", 40, 50, "cli"),
            ("set", "agents.coder.params.seed", 9, 10, "cli"),
            ("set", "agents.coder.params.top_p", None, 0.9, "cli"),
            ("remove", "agents.coder.params.temp", 0.3, None, "cli"),
        ],
    )
    assert _show(theatre, "agents.coder.params") == b"top_k: 50\nseed: 10\ntop_p: 0.9\n"


def test_key_removed_by_a_replacement_leaves_both_layers(make_theatre: MakeTheatre) -> None:
    """Req 7.4, 7.3, 9.1, 9.3: a replacement dropping `seed`, written in coder.yaml and in the
    override, removes it from both files; the other override entry stays."""
    theatre = make_theatre(PARAMS_ROOT, files={CODER_FILE: PARAMS_CODER})
    start = _imported(theatre)
    before = snapshot_tree(theatre)

    _assert_success(run_set(theatre, "agents.coder.params", "{top_k: 40, temp: 0.3}"))

    _assert_two_changed(
        theatre,
        before,
        {
            CODER_FILE: PARAMS_CODER.replace("  seed: 7\n", ""),
            ROOT: PARAMS_ROOT.replace("      seed: 9  # mine\n", ""),
        },
    )
    _assert_set_events(theatre, start, [("remove", "agents.coder.params.seed", 9, None, "cli")])
    assert _show(theatre, "agents.coder.params") == b"top_k: 40\ntemp: 0.3\n"


def test_override_emptied_by_a_replacement_is_pruned(make_theatre: MakeTheatre) -> None:
    """Req 7.4, 7.3, 9.1, 9.3 (research Decision 4): when every entry of the nested `params`
    override is removed, its `params:` entry is removed instead of being left null; `seed` also
    leaves coder.yaml."""
    theatre = make_theatre(PARAMS_ROOT, files={CODER_FILE: PARAMS_CODER})
    start = _imported(theatre)
    before = snapshot_tree(theatre)

    _assert_success(run_set(theatre, "agents.coder.params", "{top_k: 40}"))

    _assert_two_changed(
        theatre,
        before,
        {
            CODER_FILE: PARAMS_CODER.replace("  seed: 7\n", ""),
            ROOT: "agents:\n  coder:\n    $ref: file:agents/coder.yaml\n    model: big\n",
        },
    )
    _assert_set_events(
        theatre,
        start,
        [
            ("remove", "agents.coder.params.seed", 9, None, "cli"),
            ("remove", "agents.coder.params.temp", 0.3, None, "cli"),
        ],
    )
    assert _show(theatre, "agents.coder.params") == b"top_k: 40\n"


def test_mapping_losing_every_key_becomes_an_empty_flow_mapping(make_theatre: MakeTheatre) -> None:
    """Req 7.3, 7.4, 9.1, 9.3 (research Decision 9): `set agents.coder.params {}` prunes the
    override and writes `params: {}` in coder.yaml (never a null `params:`), the comment one space
    after the longer value (config-set-writeback 12.2); every removed leaf is recorded."""
    theatre = make_theatre(PARAMS_ROOT, files={CODER_FILE: PARAMS_CODER})
    start = _imported(theatre)
    before = snapshot_tree(theatre)

    _assert_success(run_set(theatre, "agents.coder.params", "{}"))

    _assert_two_changed(
        theatre,
        before,
        {
            CODER_FILE: "model: small\nparams: {} # tuning\nmax_tokens: 100\n",
            ROOT: "agents:\n  coder:\n    $ref: file:agents/coder.yaml\n    model: big\n",
        },
    )
    _assert_set_events(
        theatre,
        start,
        [
            ("remove", "agents.coder.params.top_k", 40, None, "cli"),
            ("remove", "agents.coder.params.seed", 9, None, "cli"),
            ("remove", "agents.coder.params.temp", 0.3, None, "cli"),
        ],
    )
    assert _show(theatre, "agents.coder.params") == b"{}\n"


# ---- 7.5: a mapping-form include root is never replaced ----


@pytest.mark.parametrize(
    "value",
    ["{model: big, params: {top_k: 40, seed: 9, temp: 0.3}}", "none", "[a]"],
)
def test_replacing_a_mapping_form_include_root_is_refused(
    make_theatre: MakeTheatre, value: str
) -> None:
    """Req 7.5, 9.1: setting `agents.coder`, a mapping-form include with overrides, to a value
    differing from its merged value exits 3 with empty stdout and a stderr line naming PATH and
    the included file; no file changes and nothing is recorded."""
    theatre = make_theatre(PARAMS_ROOT, files={CODER_FILE: PARAMS_CODER})
    start = _imported(theatre)
    before = snapshot_tree(theatre)

    result = run_set(theatre, "agents.coder", value)

    problem = _assert_refused(theatre, result, theatre / CODER_FILE, "agents.coder", start)
    assert "file:agents/coder.yaml" in problem
    assert snapshot_tree(theatre) == before


def test_mapping_equal_to_the_merged_value_is_refused_like_a_scalar_form_include(
    make_theatre: MakeTheatre,
) -> None:
    """Req 7.5, 9.1: "differs" is judged as for a scalar-form include (rule 4), so a mapping VALUE
    equal to the merged value of `agents.coder` (same keys, values and order) still differs from
    the include and exits 3 naming PATH and the included file; no file changes, nothing is
    recorded."""
    theatre = make_theatre(PARAMS_ROOT, files={CODER_FILE: PARAMS_CODER})
    merged = "{model: big, params: {top_k: 40, seed: 9, temp: 0.3}, max_tokens: 100}"
    assert _show(theatre, "agents.coder") == (
        b"model: big\nparams:\n  top_k: 40\n  seed: 9\n  temp: 0.3\nmax_tokens: 100\n"
    )
    start = _imported(theatre)
    before = snapshot_tree(theatre)

    result = run_set(theatre, "agents.coder", merged)

    problem = _assert_refused(theatre, result, theatre / CODER_FILE, "agents.coder", start)
    assert "file:agents/coder.yaml" in problem
    assert snapshot_tree(theatre) == before


# ---- 7.5, choice 16: an include's own string over its overrides ----

PARAMS_WRITTEN = "{$ref: file:agents/coder.yaml, params: {seed: 9, temp: 0.3}, model: big}"
"""VALUE naming the include of `agents.coder` in PARAMS_ROOT with exactly its written overrides."""


@pytest.mark.parametrize(
    "value",
    [
        "file:agents/coder.yaml",
        "{$ref: file:agents/coder.yaml}",
        "{$ref: file:agents/coder.yaml, model: big}",
        "{$ref: file:agents/coder.yaml, params: {seed: 9}, model: big}",
        "{$ref: file:agents/coder.yaml, params: {seed: 9, temp: 0.3}, model: big, max_tokens: 100}",
        "{$ref: file:agents/coder.yaml, params: {seed: 9, temp: 0.3}, model: small}",
    ],
    ids=["own-string", "bare-ref", "fewer", "fewer-nested", "more", "other"],
)
def test_own_include_string_over_an_include_with_overrides_is_refused(
    make_theatre: MakeTheatre, value: str
) -> None:
    """Req 7.5, 8.2, 9.1: over `agents.coder`, a mapping-form include with overrides, the include's
    own string, `{$ref: <same>}`, or a `$ref` VALUE with fewer, more or other overrides than are
    written exits 3 with empty stdout and a stderr line naming PATH and the included file; no
    file changes and nothing is recorded (choice 16). The VALUE is set with ``--include``
    (config-remote-schema 9.3), so its include strings are includes."""
    theatre = make_theatre(PARAMS_ROOT, files={CODER_FILE: PARAMS_CODER})
    start = _imported(theatre)
    before = snapshot_tree(theatre)

    result = run_set(theatre, "agents.coder", value, "--include")

    problem = _assert_refused(theatre, result, theatre / CODER_FILE, "agents.coder", start)
    assert "file:agents/coder.yaml" in problem
    assert snapshot_tree(theatre) == before


@pytest.mark.parametrize(
    "value",
    [
        PARAMS_WRITTEN,
        "{model: big, $ref: file:agents/coder.yaml, params: {temp: 0.3, seed: 9}}",
    ],
    ids=["as-written", "reordered"],
)
def test_ref_value_with_exactly_the_written_overrides_is_a_no_op(
    make_theatre: MakeTheatre, value: str
) -> None:
    """Req 7.5, 8.2, 9.1: a `$ref` VALUE naming the same include with exactly the written overrides
    (in any key order), set with ``--include``, equals `agents.coder`: exit 0, empty output, no
    file changes and nothing is recorded."""
    theatre = make_theatre(PARAMS_ROOT, files={CODER_FILE: PARAMS_CODER})
    start = _imported(theatre)
    before = snapshot_tree(theatre)

    _assert_success(run_set(theatre, "agents.coder", value, "--include"))

    assert snapshot_tree(theatre) == before
    assert len(_history(theatre)) == start


@pytest.mark.parametrize(
    "root",
    [
        "agents:\n  coder: file:agents/coder.yaml\n",
        "agents:\n  coder:\n    $ref: file:agents/coder.yaml\n",
        "agents:\n  coder: {$ref: file:agents/coder.yaml}\n",
    ],
    ids=["scalar-form", "bare-block", "bare-flow"],
)
@pytest.mark.parametrize("value", ["file:agents/coder.yaml", "{$ref: file:agents/coder.yaml}"])
def test_own_include_string_over_an_include_without_overrides_is_a_no_op(
    make_theatre: MakeTheatre, root: str, value: str
) -> None:
    """Req 7.5, 8.2 (config-set-writeback 2.8): over a scalar-form or bare mapping-form include,
    the include's own string (or `{$ref: <same>}`) set with ``--include`` is a no-op: exit 0,
    empty output, no file changes and nothing is recorded."""
    theatre = make_theatre(root, files={CODER_FILE: CODER})
    start = _imported(theatre)
    before = snapshot_tree(theatre)

    _assert_success(run_set(theatre, "agents.coder", value, "--include"))

    assert snapshot_tree(theatre) == before
    assert len(_history(theatre)) == start


# ---- 7.6: a bare text include in mapping form ----


@pytest.mark.parametrize(
    "root",
    [
        "agents:\n  coder:\n    $ref: file:prompt.md  # bare\n  lead: x\n",
        "agents:\n  coder: {$ref: file:prompt.md}\n  lead: x\n",
    ],
    ids=["block", "flow"],
)
def test_bare_text_include_in_mapping_form_replaces_the_target_content(
    make_theatre: MakeTheatre, root: str
) -> None:
    """Req 7.6, 9.1, 9.3: `set agents.coder "You review code."` over a bare `$ref` text include
    replaces prompt.md's content with VALUE exactly; troupe.yml is unchanged; one `set`."""
    theatre = make_theatre(root, files={".troupe/prompt.md": "You write code.\n"})
    start = _imported(theatre)
    before = snapshot_tree(theatre)

    _assert_success(run_set(theatre, "agents.coder", "You review code."))

    _assert_only_changed(theatre, before, ".troupe/prompt.md", "You review code.")
    _assert_set_events(
        theatre,
        start,
        [("set", "agents.coder", "You write code.\n", "You review code.", "cli")],
    )


# ---- 8.1: `$ref` in PATH ----


@pytest.mark.parametrize("path", ["agents.coder.$ref", "agents.helper.$ref", "$ref"])
def test_ref_segment_in_path_is_refused(make_theatre: MakeTheatre, path: str) -> None:
    """Req 8.1, 9.1: a PATH with a `$ref` segment (an existing include's, a new one's, the root's)
    exits 3 with empty stdout and a stderr line naming PATH and saying `$ref` is reserved for
    includes; no file changes and nothing is recorded."""
    theatre = make_theatre(OVERRIDE_ROOT, files={CODER_FILE: CODER})
    start = _imported(theatre)
    before = snapshot_tree(theatre)

    result = run_set(theatre, path, "file:agents/coder.yaml", "--include")

    problem = _assert_refused(theatre, result, theatre / ROOT, path, start)
    assert "`$ref` is reserved for includes" in problem
    assert snapshot_tree(theatre) == before


# ---- 8.2: `$ref` in VALUE ----

PLAIN_ROOT = "name: demo\nagents:\n  lead: x\n"


def test_ref_value_round_trips_as_an_include(make_theatre: MakeTheatre) -> None:
    """Req 8.2, 9.1, 9.2, 9.3: `set --include agents.coder {$ref: file:agents/coder.yaml,
    temperature: 0.3}` writes the mapping as written; it reads back as the include with its
    override (`show` gives the merged content), records one `set` per merged leaf, and a second
    identical set is a no-op."""
    theatre = make_theatre(PLAIN_ROOT, files={CODER_FILE: CODER})
    start = _imported(theatre)
    before = snapshot_tree(theatre)
    value = "{$ref: file:agents/coder.yaml, temperature: 0.3}"

    _assert_success(run_set(theatre, "agents.coder", value, "--include"))

    _assert_only_changed(
        theatre,
        before,
        ROOT,
        PLAIN_ROOT + "  coder:\n    $ref: file:agents/coder.yaml\n    temperature: 0.3\n",
    )
    _assert_set_events(
        theatre,
        start,
        [
            ("set", "agents.coder.model", None, "small", "cli"),
            ("set", "agents.coder.max_tokens", None, 100, "cli"),
            ("set", "agents.coder.temperature", None, 0.3, "cli"),
        ],
    )
    assert _show(theatre, "agents.coder") == b"model: small\nmax_tokens: 100\ntemperature: 0.3\n"
    get = run_config("get", "agents.coder.model", "--theatre", str(theatre), cwd=theatre)
    assert (get.exit_code, get.stdout, get.stderr) == (0, b"small\n", "")

    again = snapshot_tree(theatre)
    count = len(_history(theatre))
    _assert_success(run_set(theatre, "agents.coder", value, "--include"))
    assert snapshot_tree(theatre) == again
    assert len(_history(theatre)) == count


@pytest.mark.parametrize(
    ("value", "named"),
    [
        ("{$ref: file:agents/missing.yaml, temperature: 0.3}", ".troupe/agents/missing.yaml"),
        ("{$ref: file:prompt.md, temperature: 0.3}", ".troupe/prompt.md"),
        ("{$ref: 7, temperature: 0.3}", ROOT),
    ],
    ids=["missing-target", "text-target-with-overrides", "not-an-include-string"],
)
def test_ref_value_that_does_not_read_back_as_an_include_is_refused(
    make_theatre: MakeTheatre, value: str, named: str
) -> None:
    """Req 8.2, 9.2, 9.3: a `$ref` VALUE that does not load as the include it describes (missing
    target, overrides on a text target, a `$ref` that is not an include string), set with
    ``--include``, exits 3 with empty stdout, a stderr line naming PATH and the include target or
    file; no file changes, no file is left behind and nothing is recorded."""
    theatre = make_theatre(
        PLAIN_ROOT, files={CODER_FILE: CODER, ".troupe/prompt.md": "You write code.\n"}
    )
    start = _imported(theatre)
    before = snapshot_tree(theatre)

    result = run_set(theatre, "agents.coder", value, "--include")

    problem = _assert_refused(theatre, result, theatre / ROOT, "agents.coder", start)
    assert str(theatre / named) in problem
    assert snapshot_tree(theatre) == before


# ---- 4.4, 7.3: new keys sharing a placement are merged (validate-impl remediation) ----

SIBLINGS_CODER = "params:\n  a:\n    x: 1\n  b:\n    y: 2\n"
SIBLINGS_ADDED = "    params:\n      a:\n        n1: 1\n      b:\n        n2: 2\n"


@pytest.mark.parametrize(
    "root",
    [
        "agents:\n  coder: file:agents/coder.yaml\n",
        "agents:\n  coder:\n    $ref: file:agents/coder.yaml\n    model: z\n",
    ],
    ids=["scalar-form", "ref-mapping"],
)
def test_new_keys_below_two_included_siblings_are_written_as_one_override(
    make_theatre: MakeTheatre, root: str
) -> None:
    """Req 4.4, 7.3, 4.7, 5.1, 9.1, 9.3: `set agents.coder.params {a: {x: 1, n1: 1}, b: {y: 2,
    n2: 2}}`, where `a` and `b` come from coder.yaml only, writes one `params` override holding
    both new keys (the scalar-form include converted once, or one entry in the `$ref` mapping);
    coder.yaml is unchanged; two `set` events in one change."""
    theatre = make_theatre(root, files={CODER_FILE: SIBLINGS_CODER})
    start = _imported(theatre)
    before = snapshot_tree(theatre)

    result = run_set(theatre, "agents.coder.params", "{a: {x: 1, n1: 1}, b: {y: 2, n2: 2}}")

    _assert_success(result)
    converted = "agents:\n  coder:\n    $ref: file:agents/coder.yaml\n"
    expected = (converted if root.endswith("coder.yaml\n") else root) + SIBLINGS_ADDED
    _assert_only_changed(theatre, before, ROOT, expected)
    _assert_set_events(
        theatre,
        start,
        [
            ("set", "agents.coder.params.a.n1", None, 1, "cli"),
            ("set", "agents.coder.params.b.n2", None, 2, "cli"),
        ],
    )
    assert _show(theatre, "agents.coder.params") == b"a:\n  x: 1\n  n1: 1\nb:\n  y: 2\n  n2: 2\n"


# ---- choice 5: an override on a file whose root is itself a `$ref` mapping ----

CHAINED_ROOT = "c:\n  $ref: file:a.yaml\n  x: 1\n"
CHAINED_MIDDLE_FILE = ".troupe/a.yaml"
CHAINED_INNER_FILE = ".troupe/b.yaml"


def test_key_from_a_chained_include_is_written_in_the_file_holding_its_reference(
    make_theatre: MakeTheatre,
) -> None:
    """Req 4.2, 4.4, 4.7, 9.1, 9.3 (choice 5): troupe.yml overrides a.yaml, whose root is the bare
    `$ref: file:b.yaml`; `set c.sub.n 2`, where `c.sub` comes from b.yaml only, writes the
    override into a.yaml (the file holding the reference to b.yaml), not into troupe.yml;
    troupe.yml and b.yaml are unchanged; one `set` event."""
    theatre = make_theatre(
        CHAINED_ROOT,
        files={CHAINED_MIDDLE_FILE: "$ref: file:b.yaml\n", CHAINED_INNER_FILE: "sub:\n  m: 1\n"},
    )
    start = _imported(theatre)
    before = snapshot_tree(theatre)

    _assert_success(run_set(theatre, "c.sub.n", "2"))

    _assert_only_changed(theatre, before, CHAINED_MIDDLE_FILE, "$ref: file:b.yaml\nsub:\n  n: 2\n")
    _assert_set_events(theatre, start, [("set", "c.sub.n", None, 2, "cli")])
    assert _show(theatre, "c") == b"sub:\n  m: 1\n  n: 2\nx: 1\n"


CHAINED_RESULT = "$ref: file:b.yaml\nsub:\n  n: 2\n"


@pytest.mark.parametrize(
    ("outer", "middle"),
    [
        ("c:\n  $ref: file:a.yaml\n", "$ref: file:b.yaml\n"),
        ("c: file:a.yaml\n", "$ref: file:b.yaml\n"),
        (CHAINED_ROOT, "file:b.yaml\n"),
        ("c:\n  $ref: file:a.yaml\n", "file:b.yaml\n"),
        ("c: file:a.yaml\n", "file:b.yaml\n"),
    ],
    ids=[
        "bare-outer",
        "scalar-outer",
        "scalar-middle-root",
        "bare-outer-scalar-middle-root",
        "scalar-outer-scalar-middle-root",
    ],
)
def test_key_from_a_chained_include_is_written_in_the_middle_file_whatever_the_forms(
    make_theatre: MakeTheatre, outer: str, middle: str
) -> None:
    """Req 4.2, 4.4, 4.7, 5.1, 5.5, 9.1, 9.3 (choice 5): whatever the form of troupe.yml's include
    of a.yaml, and whether a.yaml's root is the bare `$ref: file:b.yaml` or the scalar
    `file:b.yaml` (converted to mapping form at column 0), `set c.sub.n 2` writes the override
    into a.yaml, which becomes `$ref: file:b.yaml` / `sub:` / `n: 2`; troupe.yml and b.yaml are
    unchanged; one `set` event; the merged `c.sub` keeps `m` and gains `n`."""
    theatre = make_theatre(
        outer, files={CHAINED_MIDDLE_FILE: middle, CHAINED_INNER_FILE: "sub:\n  m: 1\n"}
    )
    start = _imported(theatre)
    before = snapshot_tree(theatre)

    _assert_success(run_set(theatre, "c.sub.n", "2"))

    _assert_only_changed(theatre, before, CHAINED_MIDDLE_FILE, CHAINED_RESULT)
    _assert_set_events(theatre, start, [("set", "c.sub.n", None, 2, "cli")])
    assert _show(theatre, "c.sub") == b"m: 1\nn: 2\n"


def test_key_below_an_override_lacking_it_is_written_where_its_content_comes_from(
    make_theatre: MakeTheatre,
) -> None:
    """Req 4.2, 4.4, 4.7, 9.1, 9.3 (choice 5): troupe.yml overrides `c.sub` with `k: 1`, but
    `c.sub.deep` comes from b.yaml, referenced by a.yaml's root `$ref`; `set c.sub.deep.n 2`
    writes `sub:` / `deep:` / `n: 2` into a.yaml, not into troupe.yml's `sub` override."""
    theatre = make_theatre(
        "c:\n  $ref: file:a.yaml\n  sub:\n    k: 1\n",
        files={
            CHAINED_MIDDLE_FILE: "$ref: file:b.yaml\n",
            CHAINED_INNER_FILE: "sub:\n  deep:\n    m: 1\n",
        },
    )
    start = _imported(theatre)
    before = snapshot_tree(theatre)

    _assert_success(run_set(theatre, "c.sub.deep.n", "2"))

    _assert_only_changed(
        theatre, before, CHAINED_MIDDLE_FILE, "$ref: file:b.yaml\nsub:\n  deep:\n    n: 2\n"
    )
    _assert_set_events(theatre, start, [("set", "c.sub.deep.n", None, 2, "cli")])
    assert _show(theatre, "c.sub") == b"deep:\n  m: 1\n  n: 2\nk: 1\n"


def test_key_below_a_scalar_form_root_include_converts_the_root_file(
    make_theatre: MakeTheatre,
) -> None:
    """Req 4.1, 4.7, 5.1, 5.5, 9.1, 9.3: a root file that is the scalar `file:base.yaml` gets the
    new key as an override: it becomes `$ref: file:base.yaml` / `extra: 2` at column 0, and
    base.yaml is unchanged; one `set` event."""
    theatre = make_theatre("file:base.yaml\n", files={".troupe/base.yaml": "a: 1\n"})
    start = _imported(theatre)
    before = snapshot_tree(theatre)

    _assert_success(run_set(theatre, "extra", "2"))

    _assert_only_changed(theatre, before, ROOT, "$ref: file:base.yaml\nextra: 2\n")
    _assert_set_events(theatre, start, [("set", "extra", None, 2, "cli")])
    shown = run_config("show", "--theatre", str(theatre), cwd=theatre)
    assert (shown.exit_code, shown.stdout, shown.stderr) == (0, b"a: 1\nextra: 2\n", "")


# ---- 4.8 (choice 17): an including file that is itself included at several places ----

SHARED_ROOT = "a: file:mid.yaml\nb: file:mid.yaml\n"
SHARED_MIDDLE = ".troupe/mid.yaml"
SHARED_LEAF = ".troupe/leaf.yaml"


@pytest.mark.parametrize("middle", ["$ref: file:leaf.yaml\n", "file:leaf.yaml\n"])
def test_key_from_a_middle_file_shared_by_two_includes_is_written_there_once(
    make_theatre: MakeTheatre, middle: str
) -> None:
    """Req 4.8, 4.2, 4.7, 9.1, 9.2 (choices 5, 17): mid.yaml, which holds the reference to
    leaf.yaml (bare `$ref` or a scalar root), is included as `a` and `b`; the override is written
    once in mid.yaml, leaf.yaml and the root are unchanged, `show` gives the key under `a.sub` and
    `b.sub`, and one `set` event is recorded per new leaf path."""
    theatre = make_theatre(
        SHARED_ROOT, files={SHARED_MIDDLE: middle, SHARED_LEAF: "sub:\n  m: 1\n"}
    )
    start = _imported(theatre)
    before = snapshot_tree(theatre)

    _assert_success(run_set(theatre, "a.sub.n", "1"))

    _assert_only_changed(theatre, before, SHARED_MIDDLE, "$ref: file:leaf.yaml\nsub:\n  n: 1\n")
    _assert_set_events(
        theatre, start, [("set", "a.sub.n", None, 1, "cli"), ("set", "b.sub.n", None, 1, "cli")]
    )
    shown = run_config("show", "--theatre", str(theatre), cwd=theatre)
    assert (shown.exit_code, shown.stdout, shown.stderr) == (
        0,
        b"a:\n  sub:\n    m: 1\n    n: 1\nb:\n  sub:\n    m: 1\n    n: 1\n",
        "",
    )


def test_nested_include_of_a_shared_file_is_converted_there_once(make_theatre: MakeTheatre) -> None:
    """Req 4.8, 4.2, 4.7, 5.1, 9.1 (choice 17): mid.yaml holds `coder: file:leaf.yaml` and is
    included as `a` and `b`; the include is converted once in mid.yaml, and both places show the
    new key, with one `set` event each."""
    theatre = make_theatre(
        SHARED_ROOT, files={SHARED_MIDDLE: "coder: file:leaf.yaml  # c\n", SHARED_LEAF: "m: 1\n"}
    )
    start = _imported(theatre)
    before = snapshot_tree(theatre)

    _assert_success(run_set(theatre, "b.coder.t", "0.3"))

    _assert_only_changed(
        theatre, before, SHARED_MIDDLE, "coder:  # c\n  $ref: file:leaf.yaml\n  t: 0.3\n"
    )
    _assert_set_events(
        theatre,
        start,
        [("set", "a.coder.t", None, 0.3, "cli"), ("set", "b.coder.t", None, 0.3, "cli")],
    )
    assert _show(theatre, "a.coder") == b"m: 1\nt: 0.3\n"
    assert _show(theatre, "b.coder") == b"m: 1\nt: 0.3\n"


def test_key_from_a_shared_file_keeps_merged_order_where_it_is_overridden(
    make_theatre: MakeTheatre,
) -> None:
    """Req 4.8, 1.3, 4.7, 9.2 (choice 17): `b` includes the shared mid.yaml with overrides of its
    own; the key written in mid.yaml comes, at `b`, after the included keys and before `b`'s
    override-only keys; troupe.yml is unchanged."""
    root = "a: file:mid.yaml\nb:\n  $ref: file:mid.yaml\n  sub:\n    x: 2\n  y: 3\n"
    theatre = make_theatre(
        root, files={SHARED_MIDDLE: "$ref: file:leaf.yaml\n", SHARED_LEAF: "sub:\n  m: 1\n"}
    )
    start = _imported(theatre)
    before = snapshot_tree(theatre)

    _assert_success(run_set(theatre, "a.sub.n", "1"))

    _assert_only_changed(theatre, before, SHARED_MIDDLE, "$ref: file:leaf.yaml\nsub:\n  n: 1\n")
    _assert_set_events(
        theatre, start, [("set", "a.sub.n", None, 1, "cli"), ("set", "b.sub.n", None, 1, "cli")]
    )
    assert _show(theatre, "a") == b"sub:\n  m: 1\n  n: 1\n"
    assert _show(theatre, "b") == b"sub:\n  m: 1\n  n: 1\n  x: 2\ny: 3\n"


# ---- 11.1-11.3 (choice 18): every write into a file included at more than one place ----


def test_existing_value_in_a_shared_middle_file_is_changed_at_both_places(
    make_theatre: MakeTheatre,
) -> None:
    """Req 11.1, 11.2, 11.3, 7.2: changing an existing value written in the shared mid.yaml
    writes it there once (rule 1); `show` gives the new value under `a.sub` and `b.sub`, and one
    `set` event is recorded per changed leaf path."""
    middle = "$ref: file:leaf.yaml\nsub:\n  n: 1  # n\n"
    theatre = make_theatre(
        SHARED_ROOT, files={SHARED_MIDDLE: middle, SHARED_LEAF: "sub:\n  m: 1\n"}
    )
    start = _imported(theatre)
    before = snapshot_tree(theatre)

    _assert_success(run_set(theatre, "a.sub.n", "2"))

    _assert_only_changed(
        theatre, before, SHARED_MIDDLE, "$ref: file:leaf.yaml\nsub:\n  n: 2  # n\n"
    )
    _assert_set_events(
        theatre, start, [("set", "a.sub.n", 1, 2, "cli"), ("set", "b.sub.n", 1, 2, "cli")]
    )
    assert _show(theatre, "a.sub") == b"m: 1\nn: 2\n"
    assert _show(theatre, "b.sub") == b"m: 1\nn: 2\n"


def test_existing_value_shadowed_at_one_place_changes_only_the_other(
    make_theatre: MakeTheatre,
) -> None:
    """Req 11.1, 11.2, 11.3, 7.2: `k` written in the shared agent.yaml is overridden at `b`; the
    set writes agent.yaml once, `a.k` changes, `b.k` keeps its override and records nothing."""
    root = "a: file:agent.yaml\nb:\n  $ref: file:agent.yaml\n  k: 9  # mine\n"
    theatre = make_theatre(root, files={".troupe/agent.yaml": "# shared\nk: 1\nj: 2\n"})
    start = _imported(theatre)
    before = snapshot_tree(theatre)

    _assert_success(run_set(theatre, "a.k", "5"))

    _assert_only_changed(theatre, before, ".troupe/agent.yaml", "# shared\nk: 5\nj: 2\n")
    _assert_set_events(theatre, start, [("set", "a.k", 1, 5, "cli")])
    assert _show(theatre, "a") == b"k: 5\nj: 2\n"
    assert _show(theatre, "b") == b"k: 9\nj: 2\n"


def test_in_include_into_a_file_shared_through_a_middle_file_is_written_once(
    make_theatre: MakeTheatre,
) -> None:
    """Req 11.1, 11.2, 11.3, 10.1: `--in-include` writes into leaf.yaml, which both places reach
    through mid.yaml, once; both places show the key and one `set` event each is recorded."""
    theatre = make_theatre(
        SHARED_ROOT,
        files={SHARED_MIDDLE: "$ref: file:leaf.yaml\n", SHARED_LEAF: "sub:\n  m: 1\n"},
    )
    start = _imported(theatre)
    before = snapshot_tree(theatre)

    result = run_config(*set_args(theatre, "a.sub.n", "1"), "--in-include", cwd=theatre)

    _assert_success(result)
    _assert_only_changed(theatre, before, SHARED_LEAF, "sub:\n  m: 1\n  n: 1\n")
    _assert_set_events(
        theatre, start, [("set", "a.sub.n", None, 1, "cli"), ("set", "b.sub.n", None, 1, "cli")]
    )
    assert _show(theatre, "b.sub") == b"m: 1\nn: 1\n"


SHARED_ITEMS = "items:  # list\n  - k: 1\n"


def test_new_key_in_a_list_item_of_a_shared_file_is_written_once(
    make_theatre: MakeTheatre,
) -> None:
    """Req 11.1, 11.2, 11.3, 6.2: a new key in a list item of the shared mid.yaml goes into the
    item (step 1 rule) once; both places show it, one `set` event each."""
    theatre = make_theatre(SHARED_ROOT, files={SHARED_MIDDLE: SHARED_ITEMS})
    start = _imported(theatre)
    before = snapshot_tree(theatre)

    _assert_success(run_set(theatre, "a.items.0.j", "2"))

    _assert_only_changed(theatre, before, SHARED_MIDDLE, "items:  # list\n  - k: 1\n    j: 2\n")
    _assert_set_events(
        theatre,
        start,
        [("set", "a.items.0.j", None, 2, "cli"), ("set", "b.items.0.j", None, 2, "cli")],
    )
    assert _show(theatre, "b.items") == b"- k: 1\n  j: 2\n"


def test_list_append_in_a_shared_file_is_written_once(make_theatre: MakeTheatre) -> None:
    """Req 11.1, 11.2, 11.3, 6.1: appending to a list of the shared mid.yaml writes the item
    there once; both places show it, one `set` event per new leaf path."""
    theatre = make_theatre(SHARED_ROOT, files={SHARED_MIDDLE: SHARED_ITEMS})
    start = _imported(theatre)
    before = snapshot_tree(theatre)

    _assert_success(run_set(theatre, "b.items.1", "{k: 2}"))

    _assert_only_changed(theatre, before, SHARED_MIDDLE, "items:  # list\n  - k: 1\n  - k: 2\n")
    _assert_set_events(
        theatre,
        start,
        [("set", "a.items.1.k", None, 2, "cli"), ("set", "b.items.1.k", None, 2, "cli")],
    )
    assert _show(theatre, "a.items") == b"- k: 1\n- k: 2\n"


def test_removal_in_a_collection_replacement_of_a_shared_file_is_written_once(
    make_theatre: MakeTheatre,
) -> None:
    """Req 11.1, 11.2, 11.3, 7.4: a mapping VALUE that drops `n` from a mapping written in the
    shared mid.yaml deletes it there once; both places lose it, one `remove` event each."""
    theatre = make_theatre(SHARED_ROOT, files={SHARED_MIDDLE: "sub:\n  m: 1\n  n: 2  # n\n"})
    start = _imported(theatre)
    before = snapshot_tree(theatre)

    _assert_success(run_set(theatre, "a.sub", "{m: 1}"))

    _assert_only_changed(theatre, before, SHARED_MIDDLE, "sub:\n  m: 1\n")
    _assert_set_events(
        theatre,
        start,
        [("remove", "a.sub.n", 2, None, "cli"), ("remove", "b.sub.n", 2, None, "cli")],
    )
    assert _show(theatre, "b.sub") == b"m: 1\n"


def test_text_include_shared_by_two_places_is_replaced_once(make_theatre: MakeTheatre) -> None:
    """Req 11.1, 11.2, 11.3 (choice 18): `a.p` and `b.p` both include prompt.md; setting `a.p`
    rewrites prompt.md once (troupe.yml unchanged), both places show the new text and one `set`
    event is recorded per place."""
    root = "a:\n  p: file:prompt.md\nb:\n  p: file:prompt.md  # same\n"
    theatre = make_theatre(root, files={".troupe/prompt.md": "You write code.\n"})
    start = _imported(theatre)
    before = snapshot_tree(theatre)

    _assert_success(run_set(theatre, "a.p", "You review code."))

    _assert_only_changed(theatre, before, ".troupe/prompt.md", "You review code.")
    _assert_set_events(
        theatre,
        start,
        [
            ("set", "a.p", "You write code.\n", "You review code.", "cli"),
            ("set", "b.p", "You write code.\n", "You review code.", "cli"),
        ],
    )
    assert _show(theatre, "b.p") == _show(theatre, "a.p")


def test_new_include_in_a_shared_file_merges_with_another_places_override(
    make_theatre: MakeTheatre,
) -> None:
    """Req 11.1, 11.2, 11.3, 1.2, 1.7: `set --include x.p file:t.yaml` writes the new include
    once in the shared s.yaml; at `y`, which overrides `p` with `{w: 9}`, t.yaml's content is
    deep-merged with that override; the events are the new leaves at both places and the removed
    `x.p`."""
    root = "x: file:s.yaml\ny:\n  $ref: file:s.yaml\n  p:\n    w: 9\n"
    theatre = make_theatre(
        root, files={".troupe/s.yaml": "p: 1\nq: 1\n", ".troupe/t.yaml": "v: 1\n"}
    )
    start = _imported(theatre)
    before = snapshot_tree(theatre)

    _assert_success(run_set(theatre, "x.p", "file:t.yaml", "--include"))

    _assert_only_changed(theatre, before, ".troupe/s.yaml", "p: file:t.yaml\nq: 1\n")
    _assert_set_events(
        theatre,
        start,
        [
            ("set", "x.p.v", None, 1, "cli"),
            ("set", "y.p.v", None, 1, "cli"),
            ("remove", "x.p", 1, None, "cli"),
        ],
    )
    assert _show(theatre, "x.p") == b"v: 1\n"
    assert _show(theatre, "y.p") == b"v: 1\nw: 9\n"


# ---- 11.4, 11.5 (choice 18): one VALUE asking for one written location at two places ----

TEXT_TWICE_ROOT = "top:\n  p: file:prompt.md\n  q: file:prompt.md  # same\n"
PROMPT = ".troupe/prompt.md"


def test_one_value_giving_both_places_of_a_text_file_the_same_text_writes_it_once(
    make_theatre: MakeTheatre,
) -> None:
    """Req 11.4, 11.3: `top.p` and `top.q` both include prompt.md; `set top` with the same text
    at both rewrites prompt.md once (troupe.yml unchanged) and records one `set` event per
    place."""
    theatre = make_theatre(TEXT_TWICE_ROOT, files={PROMPT: "You write code.\n"})
    start = _imported(theatre)
    before = snapshot_tree(theatre)

    _assert_success(run_set(theatre, "top", "{p: You review code., q: You review code.}"))

    _assert_only_changed(theatre, before, PROMPT, "You review code.")
    _assert_set_events(
        theatre,
        start,
        [
            ("set", "top.p", "You write code.\n", "You review code.", "cli"),
            ("set", "top.q", "You write code.\n", "You review code.", "cli"),
        ],
    )
    assert _show(theatre, "top.q") == _show(theatre, "top.p")


def test_one_value_giving_both_places_of_a_text_file_different_texts_is_refused(
    make_theatre: MakeTheatre,
) -> None:
    """Req 11.5: `set top` giving `top.p` and `top.q` (both prompt.md) different texts exits 3
    with empty stdout and a stderr line naming prompt.md and PATH, saying two places would need
    different values; no file changes and nothing is recorded."""
    theatre = make_theatre(TEXT_TWICE_ROOT, files={PROMPT: "You write code.\n"})
    start = _imported(theatre)
    before = snapshot_tree(theatre)

    result = run_set(theatre, "top", "{p: one, q: two}")

    problem = _assert_refused(theatre, result, theatre / PROMPT, "top", start)
    assert "two places that include this file would need different values" in problem
    assert snapshot_tree(theatre) == before


@pytest.mark.parametrize(
    "value",
    [
        "{a: {sub: {m: 1, n: 1}}, b: {sub: {m: 1, n: 1}}}",
        "{a: {sub: {m: 1, n: 1}}, b: {sub: {m: 1, n: 2}}}",
        "{a: {sub: {m: 2}}, b: {sub: {m: 2}}}",
        "{a: {sub: {m: 2}}, b: {sub: {m: 3}}}",
    ],
)
def test_one_value_over_two_includes_of_a_shared_yaml_file_is_refused_at_the_include_root(
    make_theatre: MakeTheatre, value: str
) -> None:
    """Req 11.4, 11.5 (shapes refused earlier): a VALUE reaching mid.yaml at `top.a` and `top.b`
    must change both include roots, which rule 4 / 5.8 refuses first: exit 3, empty stdout,
    stderr naming mid.yaml and `top.a`, no change and no event, agreeing or not."""
    root = "top:\n  a: file:mid.yaml\n  b: file:mid.yaml\n"
    theatre = make_theatre(
        root, files={SHARED_MIDDLE: "$ref: file:leaf.yaml\n", SHARED_LEAF: "sub:\n  m: 1\n"}
    )
    start = _imported(theatre)
    before = snapshot_tree(theatre)

    result = run_set(theatre, "top", value)

    problem = _assert_refused(theatre, result, theatre / SHARED_MIDDLE, "top.a", start)
    assert "the whole value comes from the include file:mid.yaml" in problem
    assert snapshot_tree(theatre) == before
