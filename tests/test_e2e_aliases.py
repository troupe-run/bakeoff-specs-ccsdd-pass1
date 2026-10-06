"""Anchors, aliases and merge keys end to end through the real ``troupe`` console script
(config-aliases).

Task 2.1: a file with an anchor is read like any other file (the anchor-only row that
config-foundation refused with exit 4), and an anchor on a mapping key is still refused, with no
history state created. Task 2.2: aliases load (the alias row that config-foundation refused), and
an alias used as a key, an alias inside its own anchored node and an undefined alias are refused
with exit 4 and no history state created. Task 2.3: merge keys load (the merge-key rows
config-foundation refused), and a bad merge value or a merge key among ``$ref`` overrides is
refused with exit 4 and no history state created. Task 2.4: ``get``, ``show``, ``show --sources``,
import, reconcile and ``watch`` read the expanded configuration.

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

import os
import signal
import time
from pathlib import Path
from typing import Any

import pytest

from set_support import snapshot_tree
from support import CliResult, MakeTheatre, RunCli, invoke_troupe, step1_members, write_theatre
from watch_support import WatchProcess

CONFIG_NAMES = ["troupe.yml", "troupe.yaml"]

type Event = tuple[int, str, str, Any, Any]
"""One history line as ``(seq, op, path, old, new)``."""


def _get(run_cli: RunCli, theatre: Path, path: str) -> CliResult:
    return run_cli("config", "get", path, "--theatre", str(theatre))


def _no_history_state(theatre: Path) -> bool:
    return not list((theatre / ".troupe").glob("history.sqlite*"))


@pytest.mark.parametrize("config_name", CONFIG_NAMES)
def test_get_reads_an_anchored_scalar(
    make_theatre: MakeTheatre, run_cli: RunCli, config_name: str
) -> None:
    """Req 1.1, 1.6: ``a: &x 1`` loads; ``get a`` prints the anchored value, without the anchor,
    and the file is unchanged."""
    content = "a: &x 1\n"
    theatre = make_theatre(content, config_name=config_name)

    result = _get(run_cli, theatre, "a")

    assert (result.exit_code, result.stdout, result.stderr) == (0, b"1\n", "")
    assert (theatre / ".troupe" / config_name).read_bytes() == content.encode()


@pytest.mark.parametrize("config_name", CONFIG_NAMES)
def test_get_reads_values_inside_anchored_collections(
    make_theatre: MakeTheatre, run_cli: RunCli, config_name: str
) -> None:
    """Req 1.1, 1.5: values inside an anchored block mapping, an anchored list and an anchored
    flow mapping are read at their paths."""
    theatre = make_theatre(
        "defaults: &d\n  model: opus\nlist: &l\n  - one\nflow: &f {k: 'v'}\n",
        config_name=config_name,
    )

    results = [_get(run_cli, theatre, path) for path in ("defaults.model", "list.0", "flow.k")]

    assert [(r.exit_code, r.stdout, r.stderr) for r in results] == [
        (0, b"opus\n", ""),
        (0, b"one\n", ""),
        (0, b"v\n", ""),
    ]


@pytest.mark.parametrize("config_name", CONFIG_NAMES)
def test_anchor_on_a_mapping_key_exits_4_naming_the_file_and_key_path(
    make_theatre: MakeTheatre, run_cli: RunCli, config_name: str
) -> None:
    """Req 1.4, 1.7: an anchor on a mapping key exits 4 with empty stdout, one stderr line naming
    the file and the key path, no file changed and no history state created."""
    content = "a:\n  &k b: 1\n"
    theatre = make_theatre(content, config_name=config_name)
    config = theatre / ".troupe" / config_name

    result = _get(run_cli, theatre, "a.b")

    assert (result.exit_code, result.stdout) == (4, b"")
    assert result.stderr == f"{config}: a.b: anchor on a mapping key is not supported\n"
    assert config.read_bytes() == content.encode()
    assert _no_history_state(theatre)


@pytest.mark.parametrize("config_name", CONFIG_NAMES)
def test_get_reads_an_alias_of_an_anchored_scalar(
    make_theatre: MakeTheatre, run_cli: RunCli, config_name: str
) -> None:
    """Req 1.1, 1.6: ``a: &x 1`` / ``b: *x`` loads; ``get b`` prints the anchored value, and the
    file is unchanged."""
    content = "a: &x 1\nb: *x\n"
    theatre = make_theatre(content, config_name=config_name)

    results = [_get(run_cli, theatre, path) for path in ("a", "b")]

    assert [(r.exit_code, r.stdout, r.stderr) for r in results] == [(0, b"1\n", "")] * 2
    assert (theatre / ".troupe" / config_name).read_bytes() == content.encode()


def test_get_and_show_read_the_expanded_configuration(
    make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    """Req 1.1, 1.2, 1.5: values reached through aliases (nested, in flow style, in an included
    file, with a reused name) are read at their paths, and ``show`` prints the expanded values
    without anchors or aliases."""
    theatre = make_theatre(
        "defaults: &d\n  model: opus\n  tools: [read, write]\n"
        "coder:\n  settings: *d\nreviewer: {settings: *d}\n"
        "n: &n 1\nfirst: *n\nn2: &n 2\nsecond: *n\nteam: file:team.yml\n",
        files={".troupe/team.yml": "lead: &l alice\nmembers:\n  - *l\n"},
    )

    results = [
        _get(run_cli, theatre, path)
        for path in (
            "coder.settings.model",
            "reviewer.settings.tools.1",
            "first",
            "second",
            "team.members.0",
        )
    ]
    shown = run_cli("config", "show", "coder", "--theatre", str(theatre))

    assert [(r.exit_code, r.stdout, r.stderr) for r in results] == [
        (0, b"opus\n", ""),
        (0, b"write\n", ""),
        (0, b"1\n", ""),
        (0, b"2\n", ""),
        (0, b"alice\n", ""),
    ]
    assert (shown.exit_code, shown.stderr) == (0, "")
    assert shown.stdout == b"settings:\n  model: opus\n  tools:\n    - read\n    - write\n"


def test_alias_of_an_include_reads_the_included_values(
    make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    """Req 1.5: an alias of an include string, of a ``$ref`` mapping with overrides, and of an
    anchored node holding an include each read the included values."""
    theatre = make_theatre(
        "i: &i file:inc.yml\nj: *i\nr: &r\n  $ref: file:inc.yml\n  k: w\ns: *r\n"
        "d: &d\n  sub: file:inc.yml\ne: *d\n",
        files={".troupe/inc.yml": "k: v\n"},
    )

    results = [_get(run_cli, theatre, path) for path in ("j.k", "s.k", "e.sub.k")]

    assert [(r.exit_code, r.stdout, r.stderr) for r in results] == [
        (0, b"v\n", ""),
        (0, b"w\n", ""),
        (0, b"v\n", ""),
    ]


# (case id, root file, included files, file named in the error, the rest of the stderr line)
LOAD_REFUSALS: list[tuple[str, str, dict[str, str], str, str]] = [
    (
        "undefined-alias",
        "a: 1\nb: *x\n",
        {},
        "troupe.yml",
        "invalid YAML: found undefined alias 'x' (line 2, column 4)",
    ),
    (
        "alias-defined-in-another-file",
        "inc: file:inc.yml\nb: *x\n",
        {".troupe/inc.yml": "a: &x 1\n"},
        "troupe.yml",
        "invalid YAML: found undefined alias 'x' (line 2, column 4)",
    ),
    (
        "recursive-alias",
        "a: 1\nd: &d\n  m: *d\n",
        {},
        "troupe.yml",
        "d.m: alias '*d' refers to its own anchored node",
    ),
    (
        "recursive-alias-in-an-included-file",
        "a: 1\ninc: file:inc.yml\n",
        {".troupe/inc.yml": "l: &l\n  - *l\n"},
        "inc.yml",
        "inc.l.0: alias '*l' refers to its own anchored node",
    ),
    (
        "alias-as-key",
        "a: &x k\nm:\n  *x : 1\n",
        {},
        "troupe.yml",
        "m.k: alias '*x' used as a mapping key is not supported",
    ),
]


@pytest.mark.parametrize(
    ("content", "files", "named", "message"),
    [case[1:] for case in LOAD_REFUSALS],
    ids=[case[0] for case in LOAD_REFUSALS],
)
def test_refused_alias_exits_4_with_no_history_state(
    make_theatre: MakeTheatre,
    run_cli: RunCli,
    content: str,
    files: dict[str, str],
    named: str,
    message: str,
) -> None:
    """Req 1.3, 1.4, 1.7: an undefined alias (also one defined only in another file), an alias
    inside its own anchored node and an alias used as a key exit 4 with empty stdout and one
    stderr line naming the file and the alias or key path; no file is changed and no history
    state is created."""
    theatre = make_theatre(content, files=files)
    before = {path: path.read_bytes() for path in theatre.rglob("*") if path.is_file()}

    result = _get(run_cli, theatre, "a")

    assert (result.exit_code, result.stdout) == (4, b"")
    assert result.stderr == f"{theatre / '.troupe' / named}: {message}\n"
    assert {path: path.read_bytes() for path in theatre.rglob("*") if path.is_file()} == before
    assert _no_history_state(theatre)


# ---- Task 2.3: merge keys load (the merge-key rows config-foundation refused) ----


@pytest.mark.parametrize("config_name", CONFIG_NAMES)
@pytest.mark.parametrize(
    ("content", "path", "expected"),
    [
        ("<<: {a: 1}\n", "a", b"1\n"),
        ("agents:\n  <<: {lead: alice}\n", "agents.lead", b"alice\n"),
    ],
)
def test_get_reads_a_value_merged_by_a_merge_key(
    make_theatre: MakeTheatre,
    run_cli: RunCli,
    config_name: str,
    content: str,
    path: str,
    expected: bytes,
) -> None:
    """Req 2.1, 1.6: an inline merge at the root and in a nested mapping loads; ``get`` prints
    the merged value and the file is unchanged."""
    theatre = make_theatre(content, config_name=config_name)

    result = _get(run_cli, theatre, path)

    assert (result.exit_code, result.stdout, result.stderr) == (0, expected, "")
    assert (theatre / ".troupe" / config_name).read_bytes() == content.encode()


def test_show_prints_the_merged_view_without_merge_keys(
    make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    """Req 2.1, 2.2, 2.4: ``show`` prints a mapping with merge keys as its merged view: own keys
    in written order, the merged keys it does not write itself where ``<<`` is written (earlier
    sources first), and no ``<<`` key."""
    theatre = make_theatre(
        "a: &a {x: 1, y: 1}\nb: &b {y: 2, z: 2}\nm:\n  first: 0\n  <<: [*a, *b]\n  x: 9\n"
    )

    result = run_cli("config", "show", "m", "--theatre", str(theatre))

    assert (result.exit_code, result.stderr) == (0, "")
    assert result.stdout == b"first: 0\ny: 1\nz: 2\nx: 9\n"


# (case id, root file, included files, file named in the error, the rest of the stderr line)
MERGE_LOAD_REFUSALS: list[tuple[str, str, dict[str, str], str, str]] = [
    (
        "scalar-value",
        "a: 1\nm:\n  <<: 5\n",
        {},
        "troupe.yml",
        "m.<<: merge key '<<' needs a mapping, an alias of a mapping or a list of those; "
        "found a scalar",
    ),
    (
        "list-with-a-scalar",
        "a: 1\nd: &d {k: 1}\nm:\n  <<: [*d, x]\n",
        {},
        "troupe.yml",
        "m.<<: merge key '<<' needs a mapping, an alias of a mapping or a list of those; "
        "item 1 is a scalar",
    ),
    (
        "include-as-merge-source",
        "a: 1\nm:\n  <<: file:base.yml\n",
        {".troupe/base.yml": "k: 1\n"},
        "troupe.yml",
        "m.<<: merge key '<<' needs a mapping, an alias of a mapping or a list of those; "
        "found an include",
    ),
    (
        "among-ref-overrides",
        "a: 1\nx:\n  $ref: file:inc.yml\n  <<: {k: 2}\n",
        {".troupe/inc.yml": "k: 1\n"},
        "troupe.yml",
        "x.<<: merge key '<<' is not supported among the overrides of a mapping-form include",
    ),
]


@pytest.mark.parametrize(
    ("content", "files", "named", "message"),
    [case[1:] for case in MERGE_LOAD_REFUSALS],
    ids=[case[0] for case in MERGE_LOAD_REFUSALS],
)
def test_refused_merge_key_exits_4_with_no_history_state(
    make_theatre: MakeTheatre,
    run_cli: RunCli,
    content: str,
    files: dict[str, str],
    named: str,
    message: str,
) -> None:
    """Req 2.3, 1.7: a bad merge value (also an include) and a merge key among ``$ref`` overrides
    exit 4 with empty
    stdout and one stderr line naming the file and the key path of the merge key; no file is
    changed and no history state is created."""
    theatre = make_theatre(content, files=files)
    before = {path: path.read_bytes() for path in theatre.rglob("*") if path.is_file()}

    result = _get(run_cli, theatre, "a")

    assert (result.exit_code, result.stdout) == (4, b"")
    assert result.stderr == f"{theatre / '.troupe' / named}: {message}\n"
    assert {path: path.read_bytes() for path in theatre.rglob("*") if path.is_file()} == before
    assert _no_history_state(theatre)


# ---- Task 2.4: every command reads the expanded configuration ----

READER_ROOT = """\
# shared defaults
defaults: &d
  model: opus
  retries: 3
coder:
  settings: *d
reviewer:
  <<: *d
  retries: 5
team: file:team.yml
"""
"""A root file with an anchored mapping, an alias of it and a merge key with an own override,
and an include whose file holds its own anchor, alias and merge key."""

READER_TEAM = "base: &b\n  role: dev\nlead:\n  <<: *b\n  name: alice\nmembers:\n  - *b\n"

READER_SHOW = (
    b"defaults:\n  model: opus\n  retries: 3\n"
    b"coder:\n  settings:\n    model: opus\n    retries: 3\n"
    b"reviewer:\n  model: opus\n  retries: 5\n"
    b"team:\n  base:\n    role: dev\n  lead:\n    role: dev\n    name: alice\n"
    b"  members:\n    - role: dev\n"
)
"""The expanded configuration of the reader theatre, as ``show`` prints it."""

READER_IMPORTS: list[Event] = [
    (1, "import", "defaults.model", None, "opus"),
    (2, "import", "defaults.retries", None, 3),
    (3, "import", "coder.settings.model", None, "opus"),
    (4, "import", "coder.settings.retries", None, 3),
    (5, "import", "reviewer.model", None, "opus"),
    (6, "import", "reviewer.retries", None, 5),
    (7, "import", "team.base.role", None, "dev"),
    (8, "import", "team.lead.role", None, "dev"),
    (9, "import", "team.lead.name", None, "alice"),
    (10, "import", "team.members.0.role", None, "dev"),
]
"""The import of the reader theatre: one leaf at every anchor, alias and merged path."""


def _reader_theatre(make_theatre: MakeTheatre, config_name: str = "troupe.yml") -> Path:
    return make_theatre(
        READER_ROOT, config_name=config_name, files={".troupe/team.yml": READER_TEAM}
    )


def _events(theatre: Path) -> list[Event]:
    """Every recorded event as ``(seq, op, path, old, new)`` (``history`` never reconciles)."""
    result = invoke_troupe("config", "history", "--theatre", str(theatre), cwd=theatre)
    assert (result.exit_code, result.stderr) == (0, ""), result.stderr
    rows = [step1_members(line) for line in result.stdout.decode("utf-8").splitlines()]
    return [(row["seq"], row["op"], row["path"], row["old"], row["new"]) for row in rows]


@pytest.mark.parametrize("config_name", CONFIG_NAMES)
def test_get_and_show_print_values_reached_through_aliases_and_merge_keys(
    make_theatre: MakeTheatre, run_cli: RunCli, config_name: str
) -> None:
    """Req 3.1: ``get`` prints values at an anchor, at an alias place and reached through a merge
    key (also in an included file); ``show`` prints the whole expanded configuration with no
    anchor, alias or merge key, and no file is changed."""
    theatre = _reader_theatre(make_theatre, config_name)
    files = snapshot_tree(theatre)

    results = [
        _get(run_cli, theatre, path)
        for path in (
            "defaults.model",
            "coder.settings.retries",
            "reviewer.model",
            "reviewer.retries",
            "team.lead.role",
            "team.members.0.role",
        )
    ]
    shown = run_cli("config", "show", "--theatre", str(theatre))
    shown_merged = run_cli("config", "show", "reviewer", "--theatre", str(theatre))

    assert [(r.exit_code, r.stdout, r.stderr) for r in results] == [
        (0, b"opus\n", ""),
        (0, b"3\n", ""),
        (0, b"opus\n", ""),
        (0, b"5\n", ""),
        (0, b"dev\n", ""),
        (0, b"dev\n", ""),
    ]
    assert (shown.exit_code, shown.stdout, shown.stderr) == (0, READER_SHOW, "")
    assert not any(marker in shown.stdout for marker in (b"&", b"*", b"<<"))
    assert (shown_merged.exit_code, shown_merged.stdout, shown_merged.stderr) == (
        0,
        b"model: opus\nretries: 5\n",
        "",
    )
    assert snapshot_tree(theatre) == files


def test_show_sources_names_the_file_of_the_anchored_node(
    make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    """Req 3.1, 3.2: ``show --sources`` labels every scalar reached through an alias or a merge
    key with the file holding its anchored node: the root file for the root's aliases and merge
    key, and the included file for the included file's own aliases and merge key."""
    theatre = _reader_theatre(make_theatre)
    root = b" # from .troupe/troupe.yml"
    team = b" # from .troupe/team.yml"

    result = run_cli("config", "show", "--sources", "--theatre", str(theatre))

    assert (result.exit_code, result.stderr) == (0, "")
    assert result.stdout == (
        b"defaults:\n  model: opus" + root + b"\n  retries: 3" + root + b"\n"
        b"coder:\n  settings:\n    model: opus" + root + b"\n    retries: 3" + root + b"\n"
        b"reviewer:\n  model: opus" + root + b"\n  retries: 5" + root + b"\n"
        b"team:\n  base:\n    role: dev" + team + b"\n"
        b"  lead:\n    role: dev" + team + b"\n    name: alice" + team + b"\n"
        b"  members:\n    - role: dev" + team + b"\n"
    )


def test_import_records_a_leaf_at_every_alias_and_merged_path(
    make_theatre: MakeTheatre,
) -> None:
    """Req 3.3: the import made by the first command records one ``import`` event for every
    leaf of the expanded configuration, at the anchor, at each alias place and at each path
    reached through a merge key, in the root file and in an included file."""
    theatre = _reader_theatre(make_theatre)

    assert _events(theatre) == READER_IMPORTS


@pytest.mark.parametrize("config_name", CONFIG_NAMES)
def test_hand_edit_at_an_anchor_is_reconciled_at_every_path_it_changes(
    make_theatre: MakeTheatre, run_cli: RunCli, config_name: str
) -> None:
    """Req 3.3: a hand edit at an anchor (in the root file and in an included file) is
    reconciled by the next command as one ``set`` event for every leaf path it changes: at the
    anchor, at the alias places and at the merged paths, but not at a path whose own key
    overrides the merged value; the edited files are not changed by the command."""
    theatre = _reader_theatre(make_theatre, config_name)
    assert _events(theatre) == READER_IMPORTS
    (theatre / ".troupe" / config_name).write_text(
        READER_ROOT.replace("model: opus", "model: sonnet").replace("retries: 3", "retries: 4")
    )
    (theatre / ".troupe" / "team.yml").write_text(READER_TEAM.replace("role: dev", "role: ops"))
    files = snapshot_tree(theatre)

    result = _get(run_cli, theatre, "reviewer.model")

    assert (result.exit_code, result.stdout, result.stderr) == (0, b"sonnet\n", "")
    assert snapshot_tree(theatre) == files
    assert _events(theatre) == [
        *READER_IMPORTS,
        (11, "set", "defaults.model", "opus", "sonnet"),
        (12, "set", "defaults.retries", 3, 4),
        (13, "set", "coder.settings.model", "opus", "sonnet"),
        (14, "set", "coder.settings.retries", 3, 4),
        (15, "set", "reviewer.model", "opus", "sonnet"),
        (16, "set", "team.base.role", "dev", "ops"),
        (17, "set", "team.lead.role", "dev", "ops"),
        (18, "set", "team.members.0.role", "dev", "ops"),
    ]


@pytest.mark.parametrize(
    "expanded",
    [
        pytest.param(
            READER_ROOT.replace(
                "  settings: *d\n", "  settings:\n    model: opus\n    retries: 3\n"
            ),
            id="alias-to-copy",
        ),
        pytest.param(
            READER_ROOT.replace("  <<: *d\n", "  model: opus\n"),
            id="merge-key-to-copy",
        ),
    ],
)
def test_hand_expansion_into_an_equal_copy_records_nothing(
    make_theatre: MakeTheatre, run_cli: RunCli, expanded: str
) -> None:
    """Req 3.4: replacing an alias (or a merge key) by an equal copy of its anchored node, and
    then putting the alias back, leaves the expanded configuration unchanged, so the commands
    after each hand edit record no event."""
    theatre = _reader_theatre(make_theatre)
    assert _events(theatre) == READER_IMPORTS
    config = theatre / ".troupe" / "troupe.yml"

    config.write_text(expanded)
    after_expansion = run_cli("config", "show", "--theatre", str(theatre))
    events_after_expansion = _events(theatre)
    config.write_text(READER_ROOT)
    after_restore = run_cli("config", "show", "--theatre", str(theatre))

    assert (after_expansion.exit_code, after_expansion.stdout, after_expansion.stderr) == (
        0,
        READER_SHOW,
        "",
    )
    assert events_after_expansion == READER_IMPORTS
    assert (after_restore.exit_code, after_restore.stdout, after_restore.stderr) == (
        0,
        READER_SHOW,
        "",
    )
    assert _events(theatre) == READER_IMPORTS


def test_watch_reconciles_an_edit_at_an_anchor(tmp_path: Path) -> None:
    """Req 3.5, 3.3: while ``watch`` runs, an edit at an anchor is recorded within 2 s as one
    ``set`` at the anchor, at the alias place and at the merged path; ``watch`` then stops
    cleanly (exit 0, only its readiness line on stdout, empty stderr)."""
    theatre = write_theatre(
        tmp_path / "theatre", READER_ROOT, files={".troupe/team.yml": READER_TEAM}
    )
    config = theatre / ".troupe" / "troupe.yml"
    imports = [
        {"seq": seq, "op": op, "path": path, "old": old, "new": new}
        for seq, op, path, old, new in READER_IMPORTS
    ]
    expected = [
        *imports,
        {"seq": 11, "op": "set", "path": "defaults.model", "old": "opus", "new": "sonnet"},
        {"seq": 12, "op": "set", "path": "coder.settings.model", "old": "opus", "new": "sonnet"},
        {"seq": 13, "op": "set", "path": "reviewer.model", "old": "opus", "new": "sonnet"},
    ]

    with WatchProcess(str(theatre), cwd=tmp_path) as watch:
        assert watch.wait_ready() == f"watching {theatre}\n".encode()
        assert watch.history() == imports
        since = time.monotonic()
        temporary = config.with_name(".troupe.yml.edit-tmp")
        temporary.write_text(READER_ROOT.replace("model: opus", "model: sonnet"))
        os.replace(temporary, config)
        events = watch.wait_for_history(lambda events: len(events) >= len(expected), since=since)
        assert events == expected
        assert watch.stop(signal.SIGTERM) == 0
        assert watch.stdout == f"watching {theatre}\n".encode()
        assert watch.stderr == ""
