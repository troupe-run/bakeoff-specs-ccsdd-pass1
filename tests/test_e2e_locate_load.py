"""Locating and loading the configuration, end to end through the real ``troupe`` console script.

Covers locating the root file in ``.troupe/`` (2.1-2.4, with ``--theatre`` per 1.2) and loading
errors (3.2-3.5, 3.7, 3.9): every failure exits 4 with empty stdout (10.2) and a stderr message
``<file>: [<path>: ]<problem>`` naming the file and, where relevant, the key path (10.3). The user's
files are never modified (3.6 is proven in full by the read-only safety tests).
"""

from pathlib import Path

import pytest

from support import CliResult, MakeTheatre, RunCli

CONFIG_NAMES = ["troupe.yml", "troupe.yaml"]

# Both read commands share the locate/load conventions (10.5).
COMMANDS = [("show", "a"), ("history",)]


def _assert_load_failure(result: CliResult, file: Path, path: str | None = None) -> str:
    """Assert exit 4, empty stdout and a single stderr line naming ``file`` (and ``path``).

    Returns the problem text that follows the file (and path) prefix.
    """
    prefix = f"{file}: " if path is None else f"{file}: {path}: "
    assert result.exit_code == 4, result.stderr
    assert result.stdout == b""
    assert result.stderr.startswith(prefix), result.stderr
    assert result.stderr.endswith("\n")
    assert result.stderr.count("\n") == 1, result.stderr
    return result.stderr[len(prefix) :]


def _snapshot(root: Path) -> dict[str, bytes]:
    """Every regular file under ``root`` (relative path -> bytes)."""
    return {
        str(entry.relative_to(root)): entry.read_bytes()
        for entry in sorted(root.rglob("*"))
        if entry.is_file()
    }


# ---- 2.1, 1.2: exactly one of troupe.yml / troupe.yaml is used ----


@pytest.mark.parametrize("config_name", CONFIG_NAMES)
def test_show_reads_the_single_existing_config_file(
    make_theatre: MakeTheatre, run_cli: RunCli, config_name: str
) -> None:
    theatre = make_theatre(f"a: from {config_name}\n", config_name=config_name)

    result = run_cli("config", "show", "a", "--theatre", str(theatre))

    assert (result.exit_code, result.stdout, result.stderr) == (
        0,
        f"from {config_name}\n".encode(),
        "",
    )


@pytest.mark.parametrize("config_name", CONFIG_NAMES)
def test_show_without_theatre_option_reads_the_config_in_the_working_directory(
    make_theatre: MakeTheatre, run_cli: RunCli, config_name: str
) -> None:
    theatre = make_theatre(f"a: from {config_name}\n", config_name=config_name)

    result = run_cli("config", "show", "a", cwd=theatre)

    assert (result.exit_code, result.stdout, result.stderr) == (
        0,
        f"from {config_name}\n".encode(),
        "",
    )


# ---- 2.2: neither file exists ----


@pytest.mark.parametrize("command", COMMANDS)
def test_missing_config_file_exits_4_naming_the_expected_location(
    make_theatre: MakeTheatre, run_cli: RunCli, command: tuple[str, ...]
) -> None:
    theatre = make_theatre(files={".troupe/other.yml": "a: 1\n"})
    before = _snapshot(theatre)

    result = run_cli("config", *command, "--theatre", str(theatre))

    problem = _assert_load_failure(result, theatre / ".troupe" / "troupe.yml")
    assert "troupe.yml or troupe.yaml" in problem
    assert _snapshot(theatre) == before


def test_missing_config_file_in_working_directory_names_the_expected_location(
    make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    theatre = make_theatre()

    result = run_cli("config", "show", "a", cwd=theatre)

    problem = _assert_load_failure(result, theatre / ".troupe" / "troupe.yml")
    assert "troupe.yml or troupe.yaml" in problem


# ---- 2.3: both files exist ----


@pytest.mark.parametrize("command", COMMANDS)
def test_both_config_files_exit_4_naming_both_files(
    make_theatre: MakeTheatre, run_cli: RunCli, command: tuple[str, ...]
) -> None:
    theatre = make_theatre(
        "a: yml\n", config_name="troupe.yml", files={".troupe/troupe.yaml": "a: yaml\n"}
    )
    before = _snapshot(theatre)

    result = run_cli("config", *command, "--theatre", str(theatre))

    problem = _assert_load_failure(result, theatre / ".troupe" / "troupe.yml")
    assert str(theatre / ".troupe" / "troupe.yaml") in problem
    assert "troupe.yml" in problem
    assert _snapshot(theatre) == before


# ---- 2.4: the theatre directory is missing or not a directory ----


@pytest.mark.parametrize("command", COMMANDS)
def test_missing_theatre_directory_exits_4_naming_the_expected_location(
    run_cli: RunCli, tmp_path: Path, command: tuple[str, ...]
) -> None:
    theatre = tmp_path / "no-such-theatre"

    result = run_cli("config", *command, "--theatre", str(theatre))

    problem = _assert_load_failure(result, theatre / ".troupe" / "troupe.yml")
    assert "troupe.yml or troupe.yaml" in problem
    assert not theatre.exists()


def test_theatre_that_is_a_regular_file_exits_4_naming_the_expected_location(
    run_cli: RunCli, tmp_path: Path
) -> None:
    theatre = tmp_path / "plain-file"
    theatre.write_bytes(b"a: 1\n")

    result = run_cli("config", "show", "a", "--theatre", str(theatre))

    problem = _assert_load_failure(result, theatre / ".troupe" / "troupe.yml")
    assert "troupe.yml or troupe.yaml" in problem
    assert theatre.read_bytes() == b"a: 1\n"


# ---- 3.2: invalid YAML and non-UTF-8 bytes ----


@pytest.mark.parametrize("config_name", CONFIG_NAMES)
@pytest.mark.parametrize("command", COMMANDS)
def test_invalid_yaml_exits_4_naming_the_file(
    make_theatre: MakeTheatre, run_cli: RunCli, config_name: str, command: tuple[str, ...]
) -> None:
    content = b"# broken\na: [1, 2\nb: 3\n"
    theatre = make_theatre(content, config_name=config_name)

    result = run_cli("config", *command, "--theatre", str(theatre))

    problem = _assert_load_failure(result, theatre / ".troupe" / config_name)
    assert problem.startswith("invalid YAML")
    assert (theatre / ".troupe" / config_name).read_bytes() == content


@pytest.mark.parametrize("config_name", CONFIG_NAMES)
def test_non_utf8_bytes_exit_4_naming_the_file(
    make_theatre: MakeTheatre, run_cli: RunCli, config_name: str
) -> None:
    content = b"a: caf\xe9\nb: \xff\xfe\n"
    theatre = make_theatre(content, config_name=config_name)

    result = run_cli("config", "show", "a", "--theatre", str(theatre))

    problem = _assert_load_failure(result, theatre / ".troupe" / config_name)
    assert "UTF-8" in problem
    assert (theatre / ".troupe" / config_name).read_bytes() == content


# ---- 3.3: duplicate keys name the file and the key path ----


@pytest.mark.parametrize("config_name", CONFIG_NAMES)
@pytest.mark.parametrize(
    ("content", "key_path"),
    [
        ("a: 1\nb: 2\na: 3\n", "a"),
        ("agents:\n  lead: alice\n  # again\n  lead: bob\n", "agents.lead"),
        ("list:\n  - x: 1\n    x: 2\n", "list.0.x"),
    ],
)
def test_duplicate_key_exits_4_naming_the_file_and_key_path(
    make_theatre: MakeTheatre, run_cli: RunCli, config_name: str, content: str, key_path: str
) -> None:
    theatre = make_theatre(content, config_name=config_name)

    result = run_cli("config", "show", "a", "--theatre", str(theatre))

    problem = _assert_load_failure(result, theatre / ".troupe" / config_name, key_path)
    assert "duplicate key" in problem
    assert (theatre / ".troupe" / config_name).read_bytes() == content.encode()


# ---- 3.7: explicit tags name the file and the key path ----


@pytest.mark.parametrize("config_name", CONFIG_NAMES)
@pytest.mark.parametrize(
    ("content", "key_path", "tag"),
    [
        ("a: !!str 5\n", "a", "!!str"),
        ("agents:\n  lead: !custom x\n", "agents.lead", "!custom"),
        ("items:\n  - ok\n  - !!int 3\n", "items.1", "!!int"),
    ],
)
def test_explicit_tag_exits_4_naming_the_file_and_key_path(
    make_theatre: MakeTheatre,
    run_cli: RunCli,
    config_name: str,
    content: str,
    key_path: str,
    tag: str,
) -> None:
    theatre = make_theatre(content, config_name=config_name)

    result = run_cli("config", "show", "a", "--theatre", str(theatre))

    problem = _assert_load_failure(result, theatre / ".troupe" / config_name, key_path)
    assert tag in problem
    assert (theatre / ".troupe" / config_name).read_bytes() == content.encode()


# ---- 3.9: merge keys name the file and the key path ----
# Since config-aliases task 2.1 anchors load and since task 2.2 aliases load
# (tests/test_e2e_aliases.py). Until task 2.3 a merge key is refused.


@pytest.mark.parametrize("config_name", CONFIG_NAMES)
@pytest.mark.parametrize(
    ("content", "key_path"),
    [
        ("<<: {a: 1}\n", "<<"),
        ("agents:\n  <<: {lead: alice}\n", "agents.<<"),
    ],
)
def test_merge_key_exits_4_naming_the_file_and_key_path(
    make_theatre: MakeTheatre,
    run_cli: RunCli,
    config_name: str,
    content: str,
    key_path: str,
) -> None:
    theatre = make_theatre(content, config_name=config_name)

    result = run_cli("config", "show", "a", "--theatre", str(theatre))

    problem = _assert_load_failure(result, theatre / ".troupe" / config_name, key_path)
    assert "merge key" in problem
    assert (theatre / ".troupe" / config_name).read_bytes() == content.encode()


# ---- 3.4: the top-level node must be a mapping ----


@pytest.mark.parametrize("config_name", CONFIG_NAMES)
@pytest.mark.parametrize(
    ("content", "kind"),
    [
        ("- a\n- b\n", "list"),
        ("just a scalar\n", "scalar"),
        ("---\n", "scalar"),
        ("~\n", "scalar"),
    ],
)
@pytest.mark.parametrize("command", COMMANDS)
def test_non_mapping_root_exits_4_naming_the_file(
    make_theatre: MakeTheatre,
    run_cli: RunCli,
    config_name: str,
    content: str,
    kind: str,
    command: tuple[str, ...],
) -> None:
    theatre = make_theatre(content, config_name=config_name)

    result = run_cli("config", *command, "--theatre", str(theatre))

    problem = _assert_load_failure(result, theatre / ".troupe" / config_name)
    assert problem == f"the top-level node is a {kind}; expected a mapping\n"
    assert (theatre / ".troupe" / config_name).read_bytes() == content.encode()


# ---- 3.5: an empty or comment-only root file is the empty mapping ----


@pytest.mark.parametrize("config_name", CONFIG_NAMES)
@pytest.mark.parametrize("content", ["", "# only a comment\n\n# and another\n", "\n  \n"])
def test_empty_root_file_has_no_values_and_history_prints_nothing(
    make_theatre: MakeTheatre, run_cli: RunCli, config_name: str, content: str
) -> None:
    theatre = make_theatre(content, config_name=config_name)

    history = run_cli("config", "history", "--theatre", str(theatre))
    show = run_cli("config", "show", "a", "--theatre", str(theatre))

    assert (history.exit_code, history.stdout, history.stderr) == (0, b"", "")
    # An empty mapping has no key ``a``: a path error (5), not a load failure (4).
    assert show.exit_code == 5
    assert show.stdout == b""
    assert show.stderr.startswith(f"{theatre / '.troupe' / config_name}: a: ")
    assert (theatre / ".troupe" / config_name).read_bytes() == content.encode()
