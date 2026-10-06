"""``troupe config set`` refusals for aliases, merge keys and anchors, end to end (config-aliases).

Every refusal runs the real ``troupe`` console script on a real theatre and checks the whole
contract of a refusal: exit 3, empty stdout, one stderr line naming the file, PATH and the alias
or anchor (and ``--expand-alias`` for a set reached through an alias, Requirement 5), every file
under the theatre unchanged byte for byte, and no history event beyond the import that the first
command ran (Requirements 4.6, 4.7, 5.1-5.4, 9.2).

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

from pathlib import Path

import pytest

from set_support import assert_file_bytes, run_config, run_set, snapshot_tree
from support import CliResult, MakeTheatre

ISSUE_2 = "defaults: &d\n  model: opus\ncoder:\n  settings: *d\nreviewer:\n  settings: *d\n"
"""The reporter's file of tracker issue #2."""

ALIAS_PROBLEM = (
    "value is shared through alias '*{name}' (anchor '&{name}'); setting it here would change "
    "every place that uses the anchor; use --expand-alias to give this place its own copy"
)

MERGE_PROBLEM = (
    "value comes from merge key '<<' at {key_path}; merge keys are never written, "
    "and --expand-alias cannot expand them"
)

ROOT_FILE = ".troupe/troupe.yml"


def _history(theatre: Path) -> bytes:
    result = run_config("history", "--theatre", str(theatre), cwd=theatre)
    assert (result.exit_code, result.stderr) == (0, ""), result.stderr
    return result.stdout


def _assert_refused(result: CliResult, file: Path, path: str, problem: str) -> None:
    assert (result.exit_code, result.stdout) == (3, b""), result.stderr
    assert result.stderr == f"{file}: {path}: {problem}\n", result.stderr


def _refused_without_change(
    theatre: Path, path: str, value: str, named_file: str, problem: str
) -> None:
    """Run ``set PATH VALUE`` after an import; assert the refusal, no file and no event changed."""
    history = _history(theatre)
    before = snapshot_tree(theatre)

    result = run_set(theatre, path, value)

    _assert_refused(result, theatre / named_file, path, problem)
    assert snapshot_tree(theatre) == before
    assert _history(theatre) == history


# (case id, PATH, VALUE)
ISSUE_2_REFUSALS: list[tuple[str, str, str]] = [
    ("issue-2", "coder.settings.model", "sonnet"),
    ("the-alias-own-key", "coder.settings", "{model: x}"),
    ("no-op-through-the-alias", "coder.settings.model", "opus"),
    ("new-key-below-the-alias", "coder.settings.temperature", "0.3"),
]


@pytest.mark.parametrize(
    ("path", "value"),
    [case[1:] for case in ISSUE_2_REFUSALS],
    ids=[case[0] for case in ISSUE_2_REFUSALS],
)
def test_set_reached_through_an_alias_is_refused(
    make_theatre: MakeTheatre, path: str, value: str
) -> None:
    """Req 5.1, 5.3, 5.4: a set whose PATH (or the deepest existing ancestor of a new key) is
    reached through an alias, including the alias's own key and a VALUE equal to the current
    value, exits 3 naming the file, PATH, `*d` and `--expand-alias`, with no file and no history
    changed."""
    theatre = make_theatre(ISSUE_2)
    _refused_without_change(theatre, path, value, ROOT_FILE, ALIAS_PROBLEM.format(name="d"))
    assert_file_bytes(theatre / ROOT_FILE, ISSUE_2)


def test_replacement_changing_a_value_through_an_alias_below_path_is_refused(
    make_theatre: MakeTheatre,
) -> None:
    """Req 5.2, 5.4: a collection replacement that would change a value reached through an alias
    below PATH exits 3 naming the file, PATH, `*d` and `--expand-alias`, with no file and no
    history changed."""
    theatre = make_theatre(ISSUE_2)
    _refused_without_change(
        theatre, "coder", "{settings: {model: x}}", ROOT_FILE, ALIAS_PROBLEM.format(name="d")
    )


def test_set_through_an_alias_in_an_included_file_names_the_included_file(
    make_theatre: MakeTheatre,
) -> None:
    """Req 5.1, 5.4: an alias written in an included file is named with that file, not the root
    file; no file and no history changed."""
    theatre = make_theatre(
        "a: 1\ninc: file:inc.yml\n",
        files={".troupe/inc.yml": "d: &d\n  m: 1\nu: *d\n"},
    )
    _refused_without_change(
        theatre, "inc.u.m", "2", ".troupe/inc.yml", ALIAS_PROBLEM.format(name="d")
    )


@pytest.mark.parametrize(
    ("path", "value"),
    [("m.model", "x"), ("m.model", "opus"), ("m", "{own: 1}")],
    ids=["merged-value", "no-op-at-a-merged-value", "replacement-dropping-merged-values"],
)
def test_set_of_a_value_received_from_a_merge_key_is_refused(
    make_theatre: MakeTheatre, path: str, value: str
) -> None:
    """Req 9.2: a set that would change a value a mapping receives from its merge key exits 3
    naming the file, PATH and the merge key, says merge keys are never written (and that
    `--expand-alias` cannot expand them), with no file and no history changed."""
    theatre = make_theatre("d: &d\n  model: opus\n  temperature: 0.1\nm:\n  <<: *d\n  own: 1\n")
    _refused_without_change(theatre, path, value, ROOT_FILE, MERGE_PROBLEM.format(key_path="m.<<"))


def test_removing_an_anchor_still_used_by_an_alias_is_refused(make_theatre: MakeTheatre) -> None:
    """Req 4.6: a collection replacement that would remove an anchored node still used by an
    alias elsewhere in the file exits 3 naming the file, PATH and `&d`, with no file and no
    history changed."""
    theatre = make_theatre(
        "shared:\n  defaults: &d  # anchored\n    model: opus\n  other: 1\ncoder: *d\n"
    )
    _refused_without_change(
        theatre,
        "shared",
        "{other: 1}",
        ROOT_FILE,
        "anchor '&d' is still used by alias '*d' at coder",
    )


@pytest.mark.parametrize(
    "config",
    ["i: &i file:inc.yml\n", "i: &i file:inc.yml\nx: *i\n"],
    ids=["anchored-include", "anchored-include-with-alias"],
)
def test_converting_an_anchored_include_is_refused(make_theatre: MakeTheatre, config: str) -> None:
    """Req 4.7: a new key at an anchored scalar-form include, which would convert it to mapping
    form, exits 3 naming the file, PATH and `&i`, with no file and no history changed."""
    theatre = make_theatre(config, files={".troupe/inc.yml": "k: v\n"})
    _refused_without_change(
        theatre,
        "i.extra",
        "1",
        ROOT_FILE,
        "the include at anchor '&i' cannot be converted to mapping form",
    )


def test_set_at_the_anchor_is_no_longer_blocked(make_theatre: MakeTheatre) -> None:
    """Req 5.1: the guard refuses only sets reached through an alias; a set at the anchor itself
    of the issue #2 file proceeds (exit 0) and writes at the anchor."""
    theatre = make_theatre(ISSUE_2)

    result = run_set(theatre, "defaults.model", "sonnet")

    assert (result.exit_code, result.stdout, result.stderr) == (0, b"", "")
    assert_file_bytes(theatre / ROOT_FILE, ISSUE_2.replace("opus", "sonnet"))
