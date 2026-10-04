"""``troupe config show [PATH]`` end to end through the real ``troupe`` console script (task 4.2).

The comprehensive proof of ``show``'s output and command behaviour: a scalar prints the same bytes
as ``get`` (2.1); a mapping section mixing a YAML include (in a subdirectory, itself holding a text
include) and a text include, a list and a list of mappings print exact block YAML with every include
in place and text includes as literal blocks (2.2, 3.4, 4.4); no PATH prints the whole
configuration, and an empty or comment-only root file prints ``{}`` (2.3, 3.5); a missing PATH
exits 5 (2.4); command-line and PATH syntax problems exit 2 (2.5). Command behaviour: the theatre
is selected and the configuration located as by every other subcommand, including ``troupe.yaml``
and ``TROUPE_THEATRE`` (6.1); an imported theatre is reconciled first, also when the command then
exits 5 (6.2), and the printed value is the file's (6.4); a never-imported theatre is imported with
no reconcile events (6.3); no user file changes and the only new entry anywhere is the history
store (6.5); a missing configuration file exits 4 creating no history state (6.6); every non-zero
exit has an empty stdout (6.7); the next command succeeds right after (6.8). The read-back
property (4.1) is proved separately in task 4.3's module.

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

import os
from pathlib import Path

import pytest

from set_support import run_set, snapshot_tree
from support import CliResult, MakeTheatre, RunCli, step1_members
from troupe.config.constants import CONFIG_DIR_NAME, HISTORY_STORE_NAME

CONFIG_NAMES = ["troupe.yml", "troupe.yaml"]

STORE = f"{CONFIG_DIR_NAME}/{HISTORY_STORE_NAME}"
"""The history store, relative to the theatre: the only entry `show` and `get` may add."""


def _show(run_cli: RunCli, theatre: Path, *args: str) -> CliResult:
    return run_cli("config", "show", *args, "--theatre", str(theatre))


def _get(run_cli: RunCli, theatre: Path, *args: str) -> CliResult:
    return run_cli("config", "get", *args, "--theatre", str(theatre))


def _assert_prints(result: CliResult, expected: bytes) -> None:
    assert (result.exit_code, result.stderr) == (0, ""), result.stderr
    assert result.stdout == expected, result.stdout.decode("utf-8", "replace")


def _assert_failure(result: CliResult, exit_code: int) -> None:
    """Assert ``exit_code``, an empty stdout (6.7) and a one-line stderr message."""
    assert result.exit_code == exit_code, result.stderr
    assert result.stdout == b""
    assert result.stderr.endswith("\n") and result.stderr.strip(), result.stderr
    assert result.stderr.count("\n") == 1, result.stderr


def _history_lines(run_cli: RunCli, theatre: Path) -> list[dict[str, object]]:
    """Every event printed by ``history`` (which never reconciles), asserting success, projected
    onto its step-1 members."""
    result = run_cli("config", "history", "--theatre", str(theatre))
    assert (result.exit_code, result.stderr) == (0, ""), result.stderr
    return [step1_members(line) for line in result.stdout.decode("utf-8").splitlines()]


def _ops(run_cli: RunCli, theatre: Path) -> list[str]:
    return [str(event["op"]) for event in _history_lines(run_cli, theatre)]


def _entries(root: Path) -> list[str]:
    """Every file and directory below ``root``, sorted, as relative POSIX paths (dirs end in /)."""
    found: list[str] = []
    for directory, dirnames, filenames in os.walk(root):
        base = Path(directory)
        found += [f"{(base / name).relative_to(root).as_posix()}/" for name in dirnames]
        found += [(base / name).relative_to(root).as_posix() for name in filenames]
    return sorted(found)


def _no_history_state(theatre: Path) -> bool:
    return not list((theatre / CONFIG_DIR_NAME).glob(f"{HISTORY_STORE_NAME}*"))


# ---- the fixture theatre used by most tests ----

ROOT = """\
# root configuration: comments, quoting and flow style are not carried into the output
name: "demo"
agents:
  lead: file:agents/lead.yml
  motto: file:motto.txt
  ratio: .inf
crew:
  - ada
  - name: bob
    age: 0x10
  - [x, 'y']
empty_map: {}
empty_list: []
"""

FILES: dict[str, str | bytes] = {
    ".troupe/agents/lead.yml": (
        "name: alice  # comment\nprompt: file:prompt.md\nflags: {debug: True, level: ~}\n"
    ),
    ".troupe/agents/prompt.md": b"  Be kind.\n\nBe brief.\n\n",
    ".troupe/motto.txt": b"go\r\nfast",
}

AGENTS_YAML = (
    b"lead:\n"
    b"  name: alice\n"
    b"  prompt: |2+\n"
    b"      Be kind.\n"
    b"\n"
    b"    Be brief.\n"
    b"\n"
    b"  flags:\n"
    b"    debug: true\n"
    b"    level: null\n"
    b"motto: |-\n"
    b"  go\r\n"
    b"  fast\n"
    b"ratio: .inf\n"
)
"""`agents`: the YAML include in place (its text include a `|2+` block: leading spaces, two final
newlines), the CRLF text include a `|-` literal whose content lines keep the `\\r`."""

CREW_YAML = b"- ada\n- name: bob\n  age: 16\n- - x\n  - y\n"


WHOLE_YAML = (
    b"name: demo\n"
    b"agents:\n"
    b"  lead:\n"
    b"    name: alice\n"
    b"    prompt: |2+\n"
    b"        Be kind.\n"
    b"\n"
    b"      Be brief.\n"
    b"\n"
    b"    flags:\n"
    b"      debug: true\n"
    b"      level: null\n"
    b"  motto: |-\n"
    b"    go\r\n"
    b"    fast\n"
    b"  ratio: .inf\n"
    b"crew:\n"
    b"  - ada\n"
    b"  - name: bob\n"
    b"    age: 16\n"
    b"  - - x\n"
    b"    - y\n"
    b"empty_map: {}\n"
    b"empty_list: []\n"
)


def _theatre(make_theatre: MakeTheatre, config_name: str = "troupe.yml") -> Path:
    return make_theatre(ROOT, config_name=config_name, files=FILES)


# ---- 2.1: a scalar prints the same bytes as get ----

SCALAR_CASES = [
    ("string-no-final-newline", "v: hello world\n", b"hello world\n"),
    ("string-final-newline", 'v: "line\\n"\n', b"line\n"),
    ("string-looks-like-bool", "v: 'true'\n", b"true\n"),
    ("bool", "v: false\n", b"false\n"),
    ("null", "v: ~\n", b"null\n"),
    ("int", "v: 0o17\n", b"15\n"),
    ("float", "v: 1e3\n", b"1000.0\n"),
    ("float-inf", "v: .inf\n", b"inf\n"),
    ("literal-keep", "v: |+\n  one\n\n", b"one\n\n"),
]


@pytest.mark.parametrize(
    ("content", "expected"),
    [(c, e) for _, c, e in SCALAR_CASES],
    ids=[i for i, _, _ in SCALAR_CASES],
)
def test_show_scalar_prints_the_same_bytes_as_get(
    make_theatre: MakeTheatre, run_cli: RunCli, content: str, expected: bytes
) -> None:
    """Req 2.1: for each scalar kind `show PATH` prints exactly `get PATH`'s bytes and exits 0."""
    theatre = make_theatre(content)

    show = _show(run_cli, theatre, "v")

    _assert_prints(show, expected)
    assert show == _get(run_cli, theatre, "v")


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("agents.motto", b"go\r\nfast\n"),
        ("agents.lead.prompt", b"  Be kind.\n\nBe brief.\n\n"),
        ("agents.lead.flags.debug", b"true\n"),
        ("crew.1.age", b"16\n"),
    ],
)
def test_show_text_include_and_included_scalars_print_the_same_bytes_as_get(
    make_theatre: MakeTheatre, run_cli: RunCli, path: str, expected: bytes
) -> None:
    """Req 2.1, 4.4: a text include (CRLF kept, newline added only when missing) and scalars inside
    a YAML include print as `get` prints them, not as literal blocks."""
    theatre = _theatre(make_theatre)

    show = _show(run_cli, theatre, path)

    _assert_prints(show, expected)
    assert show == _get(run_cli, theatre, path)


# ---- 2.2, 3.4, 4.4: mappings and lists as block YAML with includes in place ----


def test_show_mapping_section_with_yaml_and_text_includes_prints_exact_bytes(
    make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    """Req 2.2, 3.4, 4.4: a mapping mixing a YAML include in a subdirectory (itself holding a text
    include) and a CRLF text include prints the included content in place, no `file:` string, text
    includes as literal blocks with the chomping and indentation indicators they need."""
    theatre = _theatre(make_theatre)

    result = _show(run_cli, theatre, "agents")

    _assert_prints(result, AGENTS_YAML)
    assert b"file:" not in result.stdout


def test_show_yaml_include_mapping_prints_its_content_at_column_0(
    make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    """Req 2.2, 3.4, 4.4: the YAML include itself prints as a mapping at column 0, its text include
    as a `|2+` block indented two spaces more than its key."""
    theatre = _theatre(make_theatre)

    _assert_prints(
        _show(run_cli, theatre, "agents.lead"),
        b"name: alice\nprompt: |2+\n    Be kind.\n\n  Be brief.\n\nflags:\n  debug: true\n"
        b"  level: null\n",
    )


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        pytest.param("crew", CREW_YAML, id="list-with-mapping-and-list-items"),
        pytest.param("crew.2", b"- x\n- y\n", id="list-in-list"),
        pytest.param("crew.1", b"name: bob\nage: 16\n", id="mapping-in-list"),
    ],
)
def test_show_list_and_list_of_mappings_print_exact_bytes(
    make_theatre: MakeTheatre, run_cli: RunCli, path: str, expected: bytes
) -> None:
    """Req 2.2: a list prints its items at column 0, a mapping item starting on its dash line
    with its later entries aligned under the first, a nested list likewise."""
    theatre = _theatre(make_theatre)

    _assert_prints(_show(run_cli, theatre, path), expected)


def test_show_list_of_mappings_from_yaml_include_prints_exact_bytes(
    make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    """Req 2.2, 3.4, 4.4: a list of mappings whose items come from YAML includes, one holding a
    text include, prints block YAML with the included content in place."""
    theatre = make_theatre(
        "agents:\n  - file:a.yml\n  - name: b\n    role: helper\n",
        files={".troupe/a.yml": "name: a\nnote: file:a.txt\n", ".troupe/a.txt": "hi\n"},
    )

    _assert_prints(
        _show(run_cli, theatre, "agents"),
        b"- name: a\n  note: |\n    hi\n- name: b\n  role: helper\n",
    )


@pytest.mark.parametrize(("path", "expected"), [("empty_map", b"{}\n"), ("empty_list", b"[]\n")])
def test_show_empty_mapping_or_list_section_prints_flow_empty(
    make_theatre: MakeTheatre, run_cli: RunCli, path: str, expected: bytes
) -> None:
    """Req 2.2, 3.5: an empty mapping section prints `{}`, an empty list section `[]`."""
    theatre = _theatre(make_theatre)

    _assert_prints(_show(run_cli, theatre, path), expected)


# ---- 2.3, 3.5: no PATH prints the whole configuration ----


@pytest.mark.parametrize("config_name", CONFIG_NAMES)
def test_show_without_path_prints_the_whole_configuration_exactly(
    make_theatre: MakeTheatre, run_cli: RunCli, config_name: str
) -> None:
    """Req 2.3, 3.4, 3.5, 4.4, 6.1: `show` without PATH prints the top-level mapping as block YAML
    with every include in place and empty sections as `{}`/`[]`, from either root file name."""
    theatre = _theatre(make_theatre, config_name)

    _assert_prints(_show(run_cli, theatre), WHOLE_YAML)


@pytest.mark.parametrize(
    "content",
    [
        pytest.param("", id="empty-file"),
        pytest.param("# only a comment\n\n# and another\n", id="comment-only"),
    ],
)
def test_show_without_path_on_configuration_with_no_entries_prints_empty_mapping(
    make_theatre: MakeTheatre, run_cli: RunCli, content: str
) -> None:
    """Req 2.3, 3.5: an empty or comment-only root file makes `show` print `{}` and exit 0."""
    theatre = make_theatre(content)

    _assert_prints(_show(run_cli, theatre), b"{}\n")


# ---- 2.4, 6.7: a missing PATH exits 5 naming the deepest existing file ----


@pytest.mark.parametrize(
    ("path", "file"),
    [
        ("missing", "troupe.yml"),
        ("agents.missing", "troupe.yml"),
        ("crew.9", "troupe.yml"),
        ("agents.lead.missing", "agents/lead.yml"),
        ("agents.lead.flags.missing", "agents/lead.yml"),
        ("agents.lead.prompt.line", "agents/prompt.md"),
        ("agents.motto.x", "motto.txt"),
    ],
)
def test_show_missing_path_exits_5_naming_the_deepest_existing_file_and_path(
    make_theatre: MakeTheatre, run_cli: RunCli, path: str, file: str
) -> None:
    """Req 2.4, 6.7: a missing PATH exits 5, stdout empty, one stderr line naming the file that
    holds the deepest existing part of PATH (an included file when it lies there) and PATH."""
    theatre = _theatre(make_theatre)

    result = _show(run_cli, theatre, path)

    _assert_failure(result, 5)
    assert result.stderr.startswith(f"{theatre / CONFIG_DIR_NAME / file}: {path}: not found")


# ---- 2.5, 6.7: command-line problems exit 2 ----


@pytest.mark.parametrize(
    ("args", "named"),
    [
        pytest.param(("name", "crew"), "unrecognized arguments: crew", id="surplus-path"),
        pytest.param(("name", "--bogus"), "unrecognized arguments: --bogus", id="unknown-option"),
        pytest.param(("--bogus",), "unrecognized arguments: --bogus", id="unknown-option-no-path"),
        pytest.param(("a.",), "malformed PATH 'a.'", id="trailing-empty-segment"),
        pytest.param(("a..b",), "malformed PATH 'a..b'", id="doubled-dot"),
        pytest.param(("",), "malformed PATH ''", id="empty-path"),
    ],
)
def test_show_command_line_problem_exits_2_with_empty_stdout(
    make_theatre: MakeTheatre, run_cli: RunCli, args: tuple[str, ...], named: str
) -> None:
    """Req 2.5, 6.7: a surplus PATH, an unknown option or a malformed PATH exits 2 with an empty
    stdout and a stderr message naming the problem (the PATH when it is malformed)."""
    theatre = _theatre(make_theatre)

    result = _show(run_cli, theatre, *args)

    _assert_failure(result, 2)
    assert named in result.stderr, result.stderr
    assert _no_history_state(theatre)


# ---- 6.2, 6.4: reconcile first, also when the command exits 5 ----


@pytest.mark.parametrize("config_name", CONFIG_NAMES)
def test_hand_edit_before_show_is_reconciled_and_the_file_value_printed(
    make_theatre: MakeTheatre, run_cli: RunCli, config_name: str
) -> None:
    """Req 6.2, 6.4: after import, a hand edit (root file and a YAML include) is recorded as `set`
    events by `show` (seen through `history`, which never reconciles) and `show` prints the values
    as now in the files, not as recorded in the history."""
    theatre = _theatre(make_theatre, config_name)
    assert set(_ops(run_cli, theatre)) == {"import"}
    (theatre / CONFIG_DIR_NAME / config_name).write_text(ROOT.replace('"demo"', "edited"))
    (theatre / CONFIG_DIR_NAME / "agents" / "lead.yml").write_text(
        "name: carol\nprompt: file:prompt.md\nflags: {debug: True, level: ~}\n"
    )
    assert "set" not in _ops(run_cli, theatre)

    _assert_prints(
        _show(run_cli, theatre), WHOLE_YAML.replace(b"demo", b"edited").replace(b"alice", b"carol")
    )

    sets = [
        (event["path"], event["old"], event["new"])
        for event in _history_lines(run_cli, theatre)
        if event["op"] == "set"
    ]
    assert sorted(sets, key=str) == sorted(
        [("name", "demo", "edited"), ("agents.lead.name", "alice", "carol")], key=str
    )


@pytest.mark.parametrize("command", ["show", "get"])
def test_hand_edit_is_reconciled_even_when_the_path_is_missing(
    make_theatre: MakeTheatre, run_cli: RunCli, command: str
) -> None:
    """Req 6.2, 6.7: `show` (and `get`) of a missing PATH exits 5 with an empty stdout, yet the
    reconcile `set` event recorded before resolving PATH is kept."""
    theatre = make_theatre("a: 1\nb: x\n")
    assert _ops(run_cli, theatre) == ["import", "import"]
    (theatre / CONFIG_DIR_NAME / "troupe.yml").write_text("a: 2\nb: x\n")

    result = run_cli("config", command, "missing", "--theatre", str(theatre))

    _assert_failure(result, 5)
    assert _history_lines(run_cli, theatre)[2:] == [
        {"seq": 3, "op": "set", "path": "a", "old": 1, "new": 2}
    ]


def test_hand_edited_value_is_printed_by_show_of_that_scalar(
    make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    """Req 6.2, 6.4: `show` of the edited scalar prints the file's new value, not the recorded
    one, and records the reconcile `set`."""
    theatre = make_theatre("a: 1\n")
    _ops(run_cli, theatre)
    (theatre / CONFIG_DIR_NAME / "troupe.yml").write_text("a: 2\n")

    _assert_prints(_show(run_cli, theatre, "a"), b"2\n")
    assert _ops(run_cli, theatre) == ["import", "set"]


# ---- 6.3: a never-imported theatre is imported, with no reconcile events ----


@pytest.mark.parametrize(
    "args",
    [(), ("agents",), ("name",), ("missing",)],
    ids=["whole", "mapping", "scalar", "missing"],
)
def test_show_on_never_imported_theatre_imports_with_no_reconcile_events(
    make_theatre: MakeTheatre, run_cli: RunCli, args: tuple[str, ...]
) -> None:
    """Req 6.3: the first `show` on a theatre imports every leaf and records no `set` events, also
    when it then exits 5."""
    theatre = _theatre(make_theatre)
    assert _no_history_state(theatre)

    result = _show(run_cli, theatre, *args)

    assert result.exit_code == (5 if args == ("missing",) else 0), result.stderr
    events = _history_lines(run_cli, theatre)
    assert [event["op"] for event in events] == ["import"] * len(events)
    assert [event["path"] for event in events] == [
        "name",
        "agents.lead.name",
        "agents.lead.prompt",
        "agents.lead.flags.debug",
        "agents.lead.flags.level",
        "agents.motto",
        "agents.ratio",
        "crew.0",
        "crew.1.name",
        "crew.1.age",
        "crew.2.0",
        "crew.2.1",
    ]


# ---- 6.5: no user file changes, nothing left behind but the history store ----


def test_show_and_get_leave_files_byte_identical_and_add_only_the_store(
    tmp_path: Path, make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    """Req 6.5, 6.8: across `show`, `show PATH`, `show --sources`, `get` and a failing `show`, every
    configuration and included file stays byte-identical and the only new entry anywhere under
    the working directory is `.troupe/history.sqlite` (no lock, temp or SQLite side files)."""
    theatre = _theatre(make_theatre)
    files_before = snapshot_tree(theatre)
    entries_before = _entries(tmp_path)

    for args, exit_code in [
        ((), 0),
        (("agents",), 0),
        (("--sources",), 0),
        (("agents.lead", "--sources"), 0),
        (("missing",), 5),
    ]:
        result = _show(run_cli, theatre, *args)
        assert (result.exit_code, result.stderr == "") == (exit_code, exit_code == 0), args
    _assert_prints(_get(run_cli, theatre, "agents.motto"), b"go\r\nfast\n")

    assert snapshot_tree(theatre) == files_before
    assert _entries(tmp_path) == sorted([*entries_before, f"theatre/{STORE}"])


# ---- 6.6, 6.7: a missing configuration file exits 4 creating no history state ----


@pytest.mark.parametrize("args", [(), ("name",)], ids=["whole", "path"])
@pytest.mark.parametrize(
    "with_config_dir", [False, True], ids=["no-troupe-dir", "empty-troupe-dir"]
)
def test_show_without_configuration_file_exits_4_and_creates_no_state(
    tmp_path: Path, run_cli: RunCli, args: tuple[str, ...], with_config_dir: bool
) -> None:
    """Req 6.6, 6.7: no `troupe.yml`/`troupe.yaml` exits 4 with an empty stdout, a stderr message
    naming the missing file, and no history state or any other file created."""
    theatre = tmp_path / "theatre"
    (theatre / CONFIG_DIR_NAME if with_config_dir else theatre).mkdir(parents=True)
    before = _entries(tmp_path)

    result = _show(run_cli, theatre, *args)

    _assert_failure(result, 4)
    assert "no configuration file" in result.stderr, result.stderr
    assert _entries(tmp_path) == before


def test_show_with_unloadable_configuration_exits_4_and_records_no_events(
    make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    """Req 6.6, 6.7: after import, a configuration that no longer loads (a missing include) makes
    `show` exit 4 with an empty stdout and record no events."""
    theatre = make_theatre("a: 1\n")
    imported = _history_lines(run_cli, theatre)
    (theatre / CONFIG_DIR_NAME / "troupe.yml").write_text("a: file:absent.txt\n")

    result = _show(run_cli, theatre)

    _assert_failure(result, 4)
    assert "absent.txt" in result.stderr
    (theatre / CONFIG_DIR_NAME / "troupe.yml").write_text("a: 1\n")
    assert _history_lines(run_cli, theatre) == imported


# ---- 6.1: the theatre is selected as by every other subcommand ----


def test_show_honours_troupe_theatre_and_the_option_wins(
    make_theatre: MakeTheatre, run_cli: RunCli, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Req 6.1: without `--theatre`, `show` uses `TROUPE_THEATRE` (from any working directory);
    `--theatre` overrides it; with neither, the working directory is the theatre."""
    theatre = make_theatre("name: demo\nlist: [1]\n", config_name="troupe.yaml")
    other = make_theatre("name: other\n", name="other")
    monkeypatch.setenv("TROUPE_THEATRE", str(theatre))

    _assert_prints(run_cli("config", "show", cwd=tmp_path), b"name: demo\nlist:\n  - 1\n")
    _assert_prints(run_cli("config", "show", "name", "--theatre", str(other)), b"other\n")
    monkeypatch.delenv("TROUPE_THEATRE")
    _assert_prints(run_cli("config", "show", cwd=other), b"name: other\n")


# ---- 6.8: the store and lock are released, so the next command succeeds ----


def test_history_then_set_succeed_right_after_show(
    make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    """Req 6.8: right after `show` (successful and failing), `history` and then `set` succeed,
    so the history store and the lock were released."""
    theatre = make_theatre("a: 1\nb: x\n")

    _assert_prints(_show(run_cli, theatre), b"a: 1\nb: x\n")
    assert _ops(run_cli, theatre) == ["import", "import"]
    _assert_failure(_show(run_cli, theatre, "missing"), 5)
    _assert_prints(run_set(theatre, "a", "2"), b"")
    _assert_prints(_show(run_cli, theatre), b"a: 2\nb: x\n")
    assert _ops(run_cli, theatre) == ["import", "import", "set"]
