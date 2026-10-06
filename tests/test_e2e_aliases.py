"""Anchors, aliases and merge keys end to end through the real ``troupe`` console script
(config-aliases).

Task 2.1: a file with an anchor is read like any other file (the anchor-only row that
config-foundation refused with exit 4), and an anchor on a mapping key is still refused, with no
history state created. Task 2.2: aliases load (the alias row that config-foundation refused), and
an alias used as a key, an alias inside its own anchored node and an undefined alias are refused
with exit 4 and no history state created.

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

from pathlib import Path

import pytest

from support import CliResult, MakeTheatre, RunCli

CONFIG_NAMES = ["troupe.yml", "troupe.yaml"]


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
