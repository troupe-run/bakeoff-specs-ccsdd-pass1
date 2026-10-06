"""``troupe config set`` refusals for anchors, aliases and merge keys (config-aliases).

Temporary (tasks.md, "Safety window"): from task 2.1 the loader accepts anchors, but ``set`` only
learns to write them in task 4.1. Until then ``set`` refuses (exit 3, no file changed) any
configuration that contains an anchor, an alias or a merge key. Task 4.1 removes this refusal
together with the tests marked "temporary" below.

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

from pathlib import Path

import pytest

from set_support import run_config, run_set, snapshot_tree
from support import CliResult, MakeTheatre

# (case id, root file, included files, PATH, VALUE, file named in the refusal, construct named)
TEMPORARY_REFUSALS: list[tuple[str, str, dict[str, str], str, str, str, str]] = [
    ("at-the-anchor", "a: &x 1\nb: 2\n", {}, "a", "5", ".troupe/troupe.yml", "anchor '&x'"),
    ("elsewhere", "a: &x 1\nb: 2\n", {}, "b", "3", ".troupe/troupe.yml", "anchor '&x'"),
    ("new-key", "m: &m\n  k: v\n", {}, "n", "1", ".troupe/troupe.yml", "anchor '&m'"),
    ("no-op", "a: &x 1\nb: 2\n", {}, "b", "2", ".troupe/troupe.yml", "anchor '&x'"),
    ("anchored-root", "&r\na: 1\n", {}, "a", "2", ".troupe/troupe.yml", "anchor '&r'"),
    (
        "in-an-included-file",
        "a: 1\ninc: file:inc.yml\n",
        {".troupe/inc.yml": "k: &k v\n"},
        "a",
        "2",
        ".troupe/inc.yml",
        "anchor '&k'",
    ),
    (
        "on-an-include-string",
        "a: 1\ninc: &i file:inc.yml\n",
        {".troupe/inc.yml": "k: v\n"},
        "inc.k",
        "w",
        ".troupe/troupe.yml",
        "anchor '&i'",
    ),
]


def _history(theatre: Path) -> bytes:
    result = run_config("history", "--theatre", str(theatre), cwd=theatre)
    assert (result.exit_code, result.stderr) == (0, ""), result.stderr
    return result.stdout


def _assert_refused(result: CliResult, file: Path, path: str, construct: str) -> None:
    prefix = f"{file}: {path}: "
    assert (result.exit_code, result.stdout) == (3, b""), result.stderr
    assert result.stderr.startswith(prefix), result.stderr
    assert result.stderr.count("\n") == 1, result.stderr
    problem = result.stderr[len(prefix) :]
    assert construct in problem, problem
    assert "anchors, aliases or merge keys" in problem, problem


@pytest.mark.parametrize(
    ("config", "files", "path", "value", "named_file", "construct"),
    [case[1:] for case in TEMPORARY_REFUSALS],
    ids=[case[0] for case in TEMPORARY_REFUSALS],
)
def test_temporary_set_refuses_a_configuration_with_an_anchor(
    make_theatre: MakeTheatre,
    config: str,
    files: dict[str, str],
    path: str,
    value: str,
    named_file: str,
    construct: str,
) -> None:
    """Req 1.6: temporary (removed by task 4.1) - while ``set`` cannot write anchors yet, a set on
    a configuration that contains an anchor anywhere exits 3 naming the file holding it and PATH,
    and changes no file and no history."""
    theatre = make_theatre(config, files=files)
    history = _history(theatre)
    before = snapshot_tree(theatre)

    result = run_set(theatre, path, value)

    _assert_refused(result, theatre / named_file, path, construct)
    assert snapshot_tree(theatre) == before
    assert _history(theatre) == history


def test_set_without_anchors_still_writes(make_theatre: MakeTheatre) -> None:
    """Req 1.6: the temporary refusal applies only to configurations with anchors, aliases or
    merge keys; a configuration without them is written as before."""
    theatre = make_theatre("a: 1\nb: 2\n")

    result = run_set(theatre, "a", "5")

    assert (result.exit_code, result.stdout, result.stderr) == (0, b"", "")
    assert (theatre / ".troupe" / "troupe.yml").read_bytes() == b"a: 5\nb: 2\n"
