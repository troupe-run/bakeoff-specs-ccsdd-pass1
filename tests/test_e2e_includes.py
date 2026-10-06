"""``file:`` includes, end to end through the real ``troupe`` console script.

Covers YAML includes embedded in place (4.1, 4.2, 5.4), nested includes resolved relative to the
including file's directory (4.4), text includes printed byte for byte (4.3, 6.6), missing targets
and cycles (4.5, 4.6), load errors inside included files (4.7) and path errors naming the included
file that holds the value (10.4). Every failure exits non-zero with empty stdout (10.2).
"""

from pathlib import Path

import pytest

from support import CliResult, MakeTheatre, RunCli, step1_members

CONFIG_NAMES = ["troupe.yml", "troupe.yaml"]
COMMANDS = [("show", "a"), ("history",)]


def _show(run_cli: RunCli, theatre: Path, path: str) -> CliResult:
    return run_cli("config", "show", path, "--theatre", str(theatre))


def _assert_shows(result: CliResult, expected: bytes) -> None:
    assert (result.exit_code, result.stdout, result.stderr) == (0, expected, "")


def _assert_failure(result: CliResult, code: int, file: Path, path: str | None = None) -> str:
    """Assert ``code``, empty stdout and one stderr line ``<file>: [<path>: ]<problem>``.

    Returns the problem text.
    """
    prefix = f"{file}: " if path is None else f"{file}: {path}: "
    assert result.exit_code == code, result.stderr
    assert result.stdout == b""
    assert result.stderr.startswith(prefix), result.stderr
    assert result.stderr.endswith("\n")
    assert result.stderr.count("\n") == 1, result.stderr
    return result.stderr[len(prefix) :]


def _no_history_state(theatre: Path) -> bool:
    return not list((theatre / ".troupe").glob("history.sqlite*"))


# ---- 4.1, 4.2, 5.4: YAML includes are embedded as if written in place ----


@pytest.mark.parametrize("config_name", CONFIG_NAMES)
@pytest.mark.parametrize("suffix", [".yml", ".yaml"])
def test_yaml_include_values_are_addressable_in_place(
    make_theatre: MakeTheatre, run_cli: RunCli, config_name: str, suffix: str
) -> None:
    theatre = make_theatre(
        f"# root\nagents: file:agents{suffix}\nname: troupe\n",
        config_name=config_name,
        files={f".troupe/agents{suffix}": "lead:\n  model: opus\n  retries: 3\nsolo: false\n"},
    )

    _assert_shows(_show(run_cli, theatre, "agents.lead.model"), b"opus\n")
    _assert_shows(_show(run_cli, theatre, "agents.lead.retries"), b"3\n")
    _assert_shows(_show(run_cli, theatre, "agents.solo"), b"false\n")
    _assert_shows(_show(run_cli, theatre, "name"), b"troupe\n")


@pytest.mark.parametrize("directive", ["'file:list.yml'", '"file:list.yml"', "file:list.yml"])
def test_include_directive_in_any_scalar_style_embeds_a_list_target(
    make_theatre: MakeTheatre, run_cli: RunCli, directive: str
) -> None:
    theatre = make_theatre(
        f"items: {directive}\n", files={".troupe/list.yml": "- first\n- {k: second}\n"}
    )

    _assert_shows(_show(run_cli, theatre, "items.0"), b"first\n")
    _assert_shows(_show(run_cli, theatre, "items.1.k"), b"second\n")


def test_include_inside_a_list_item_is_embedded(make_theatre: MakeTheatre, run_cli: RunCli) -> None:
    theatre = make_theatre(
        "steps:\n  - plain\n  - file:step.yml\n", files={".troupe/step.yml": "run: build\n"}
    )

    _assert_shows(_show(run_cli, theatre, "steps.0"), b"plain\n")
    _assert_shows(_show(run_cli, theatre, "steps.1.run"), b"build\n")


def test_empty_yaml_include_is_null(make_theatre: MakeTheatre, run_cli: RunCli) -> None:
    theatre = make_theatre("a: file:empty.yml\n", files={".troupe/empty.yml": ""})

    _assert_shows(_show(run_cli, theatre, "a"), b"null\n")


# ---- 4.4: nested includes resolve relative to the including file, to any depth ----


def test_nested_includes_resolve_relative_to_each_including_file(
    make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    # root (.troupe/) -> ../shared/team.yml -> roles/lead.yaml (shared/roles/) -> ../../notes.md
    theatre = make_theatre(
        "team: file:../shared/team.yml\n",
        files={
            "shared/team.yml": "lead: file:roles/lead.yaml\n",
            "shared/roles/lead.yaml": "name: alice\nnotes: file:../../notes.md\n",
            "notes.md": "line one\nline two\n",
            # Decoys at the paths a wrong base directory would pick.
            ".troupe/roles/lead.yaml": "name: wrong-base\n",
            "shared/notes.md": "wrong base\n",
        },
    )

    _assert_shows(_show(run_cli, theatre, "team.lead.name"), b"alice\n")
    _assert_shows(_show(run_cli, theatre, "team.lead.notes"), b"line one\nline two\n")


def test_diamond_include_of_the_same_file_twice_is_not_a_cycle(
    make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    theatre = make_theatre(
        "a: file:common.yml\nb: file:common.yml\n", files={".troupe/common.yml": "x: 1\n"}
    )

    _assert_shows(_show(run_cli, theatre, "a.x"), b"1\n")
    _assert_shows(_show(run_cli, theatre, "b.x"), b"1\n")


# ---- 4.3, 6.6, 6.1: text includes are the target's exact text ----


@pytest.mark.parametrize(
    ("name", "content", "expected"),
    [
        ("prompt.txt", b"hello\r\nworld\r\n", b"hello\r\nworld\r\n"),
        ("prompt.txt", b"hello\r\nworld", b"hello\r\nworld\n"),
        ("prompt.md", b"no final newline", b"no final newline\n"),
        ("prompt.md", b"ends with newline\n", b"ends with newline\n"),
        ("prompt.md", b"  lead\n\n\ttrail  \n\n", b"  lead\n\n\ttrail  \n\n"),
        ("prompt.json", b'{"a": 1}', b'{"a": 1}\n'),
        ("prompt", "café ☃\r\n".encode(), "café ☃\r\n".encode()),
        ("empty.txt", b"", b"\n"),
    ],
)
def test_text_include_is_printed_byte_for_byte(
    make_theatre: MakeTheatre, run_cli: RunCli, name: str, content: bytes, expected: bytes
) -> None:
    theatre = make_theatre("p: file:" + name + "\n", files={f".troupe/{name}": content})

    _assert_shows(_show(run_cli, theatre, "p"), expected)


@pytest.mark.parametrize("name", ["data.txt", "data.YML", "data.yml.bak", "data.Yaml"])
def test_non_yaml_suffix_target_is_text_never_parsed(
    make_theatre: MakeTheatre, run_cli: RunCli, name: str
) -> None:
    content = b"key: value\nother: file:missing.yml\n"
    theatre = make_theatre(f"t: file:{name}\n", files={f".troupe/{name}": content})

    _assert_shows(_show(run_cli, theatre, "t"), content)


def test_text_include_from_crlf_yaml_include_in_another_directory(
    make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    theatre = make_theatre(
        b"agent: file:agents/coder.yml\r\n",
        files={
            ".troupe/agents/coder.yml": b"prompt: file:prompts/coder.txt\r\n",
            ".troupe/agents/prompts/coder.txt": b"You write code.\r\nCarefully.",
        },
    )

    _assert_shows(_show(run_cli, theatre, "agent.prompt"), b"You write code.\r\nCarefully.\n")


# ---- 7.2 via includes: history imports leaves through includes ----


def test_history_imports_leaves_through_yaml_and_text_includes(
    make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    theatre = make_theatre(
        "first: 0\nteam: file:team/team.yml\nprompt: file:prompt.txt\n",
        files={
            ".troupe/team/team.yml": "lead: alice\nflags: file:flags.yaml\n",
            ".troupe/team/flags.yaml": "- true\n- null\n",
            ".troupe/prompt.txt": "Be brief.\r\nBe kind.",
        },
    )

    result = run_cli("config", "history", "--theatre", str(theatre))

    assert result.exit_code == 0, result.stderr
    assert result.stderr == ""
    records = [step1_members(line) for line in result.stdout.decode().splitlines()]
    assert records == [
        {"seq": 1, "op": "import", "path": "first", "old": None, "new": 0},
        {"seq": 2, "op": "import", "path": "team.lead", "old": None, "new": "alice"},
        {"seq": 3, "op": "import", "path": "team.flags.0", "old": None, "new": True},
        {"seq": 4, "op": "import", "path": "team.flags.1", "old": None, "new": None},
        {"seq": 5, "op": "import", "path": "prompt", "old": None, "new": "Be brief.\r\nBe kind."},
    ]


# ---- 4.5: a missing include target names the target and the including file ----


@pytest.mark.parametrize("config_name", CONFIG_NAMES)
@pytest.mark.parametrize("target", ["missing.yml", "missing.txt", "sub"])
@pytest.mark.parametrize("command", COMMANDS)
def test_missing_include_target_exits_4_naming_target_and_including_file(
    make_theatre: MakeTheatre,
    run_cli: RunCli,
    config_name: str,
    target: str,
    command: tuple[str, ...],
) -> None:
    # ``sub`` exists but is a directory: not a readable regular file.
    theatre = make_theatre(
        f"a: 1\nb:\n  c: file:{target}\n",
        config_name=config_name,
        files={".troupe/sub/keep.txt": "x"},
    )

    result = run_cli("config", *command, "--theatre", str(theatre))

    problem = _assert_failure(result, 4, theatre / ".troupe" / config_name, "b.c")
    assert str(theatre / ".troupe" / target) in problem
    assert _no_history_state(theatre)


def test_missing_nested_include_target_names_the_included_including_file(
    make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    theatre = make_theatre(
        "a: file:sub/inner.yml\n", files={".troupe/sub/inner.yml": "b: file:gone.txt\n"}
    )

    result = _show(run_cli, theatre, "a")

    problem = _assert_failure(result, 4, theatre / ".troupe" / "sub" / "inner.yml", "a.b")
    assert str(theatre / ".troupe" / "sub" / "gone.txt") in problem
    assert _no_history_state(theatre)


# ---- 4.6: include cycles name the files forming the cycle ----


@pytest.mark.parametrize("command", COMMANDS)
def test_self_include_cycle_exits_4_naming_the_file(
    make_theatre: MakeTheatre, run_cli: RunCli, command: tuple[str, ...]
) -> None:
    theatre = make_theatre(
        "a: file:loop.yml\n", files={".troupe/loop.yml": "again: file:loop.yml\n"}
    )
    loop = theatre / ".troupe" / "loop.yml"

    result = run_cli("config", *command, "--theatre", str(theatre))

    problem = _assert_failure(result, 4, loop)
    assert "cycle" in problem
    assert problem.count(str(loop)) == 2, problem
    assert _no_history_state(theatre)


def test_include_cycle_back_to_the_root_names_every_file_in_the_cycle(
    make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    theatre = make_theatre(
        "a: file:one/x.yml\n",
        files={
            ".troupe/one/x.yml": "b: file:../two/y.yaml\n",
            ".troupe/two/y.yaml": "c: file:../troupe.yml\n",
        },
    )
    root = theatre / ".troupe" / "troupe.yml"

    result = _show(run_cli, theatre, "a")

    assert result.exit_code == 4, result.stderr
    assert result.stdout == b""
    assert "cycle" in result.stderr
    assert str(root) in result.stderr
    assert str(theatre / ".troupe" / "one" / "x.yml") in result.stderr
    assert str(theatre / ".troupe" / "one" / ".." / "two" / "y.yaml") in result.stderr
    assert _no_history_state(theatre)


# ---- 4.7: load errors inside an included file name the included file ----


@pytest.mark.parametrize(
    ("content", "path", "marker"),
    [
        ("x: 1\nx: 2\n", "inc.x", "duplicate key"),
        ("x: !!str 5\n", "inc.x", "!!str"),
        ("x:\n  &a y: 1\n", "inc.x.y", "anchor on a mapping key"),
        ("x: [1, 2\n", None, "invalid YAML"),
    ],
)
def test_load_error_inside_included_file_names_the_included_file(
    make_theatre: MakeTheatre, run_cli: RunCli, content: str, path: str | None, marker: str
) -> None:
    theatre = make_theatre(
        "ok: 1\ninc: file:conf/inc.yml\n", files={".troupe/conf/inc.yml": content}
    )

    result = _show(run_cli, theatre, "ok")

    problem = _assert_failure(result, 4, theatre / ".troupe" / "conf" / "inc.yml", path)
    assert marker in problem
    assert _no_history_state(theatre)


def test_non_utf8_text_include_names_the_text_file(
    make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    theatre = make_theatre("t: file:bad.txt\n", files={".troupe/bad.txt": b"caf\xe9\n"})

    result = _show(run_cli, theatre, "t")

    problem = _assert_failure(result, 4, theatre / ".troupe" / "bad.txt")
    assert "UTF-8" in problem


# ---- 10.4, 5.5, 6.8; config-get-show 1.2: path errors inside included files name the included file


@pytest.mark.parametrize(
    ("path", "file"),
    [
        ("team.lead.missing", "team/lead.yml"),
        ("team.lead.tags.5", "team/lead.yml"),
        ("team.lead.tags.x", "team/lead.yml"),
        ("team.lead.name.deeper", "team/lead.yml"),
        ("team.other", "team/team.yml"),
        ("team.lead.bio.line", "team/bio.txt"),
    ],
)
def test_path_not_found_inside_included_file_names_the_included_file(
    make_theatre: MakeTheatre, run_cli: RunCli, path: str, file: str
) -> None:
    theatre = make_theatre(
        "team: file:team/team.yml\n",
        files={
            ".troupe/team/team.yml": "lead: file:lead.yml\n",
            ".troupe/team/lead.yml": "name: alice\ntags: [a]\nbio: file:bio.txt\n",
            ".troupe/team/bio.txt": "Hello.\n",
        },
    )

    result = _show(run_cli, theatre, path)

    problem = _assert_failure(result, 5, theatre / ".troupe" / file, path)
    assert "not found" in problem


@pytest.mark.parametrize(("path", "file"), [("team", "team.yml"), ("team.tags", "team.yml")])
def test_get_of_included_collection_names_the_included_file(
    make_theatre: MakeTheatre, run_cli: RunCli, path: str, file: str
) -> None:
    """Req 10.4, config-get-show 1.2: `get` of a collection reached through an include exits 5
    naming the included file (step 3's `show` prints the collection instead)."""
    theatre = make_theatre(
        "team: file:team.yml\n", files={".troupe/team.yml": "tags:\n  - a\n  - b\n"}
    )

    result = run_cli("config", "get", path, "--theatre", str(theatre))

    _assert_failure(result, 5, theatre / ".troupe" / file, path)
    assert _show(run_cli, theatre, path).exit_code == 0
