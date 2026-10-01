"""Smoke tests of the set-test support module ``set_support``.

Traceability convention of config-set-writeback: every test names the requirement IDs it proves
at the start of its docstring (for example ``Req 12.1, 12.2: ...``). The helpers proved here back
the byte-preservation checks of 13.1 ("unchanged", "no extra file") and the error-reporting checks
of 16.1 (exit code, empty stdout, message on stderr) in every later set test.
"""

from pathlib import Path

import pytest

import set_support
from support import MakeTheatre

CONFIG = "# demo theatre\nname: demo  # trailing\nteam: file:agents.yml\n"
AGENTS = "lead: alice\n"


def _fixture_theatre(make_theatre: MakeTheatre) -> Path:
    return make_theatre(CONFIG, files={".troupe/agents.yml": AGENTS, "notes.txt": b"a\r\nb"})


def test_snapshot_lists_every_file_with_its_exact_bytes(make_theatre: MakeTheatre) -> None:
    """Req 13.1: a snapshot maps each relative path to the file's exact bytes."""
    theatre = _fixture_theatre(make_theatre)

    assert set_support.snapshot_tree(theatre) == {
        ".troupe/agents.yml": AGENTS.encode(),
        ".troupe/troupe.yml": CONFIG.encode(),
        "notes.txt": b"a\r\nb",
    }


def test_snapshot_of_an_untouched_theatre_equals_itself(make_theatre: MakeTheatre) -> None:
    """Req 13.1: a read-only command leaves the snapshot equal (history state is ignored)."""
    theatre = _fixture_theatre(make_theatre)
    before = set_support.snapshot_tree(theatre)

    result = set_support.run_config("show", "name", "--theatre", str(theatre), cwd=theatre)

    assert result.exit_code == 0
    assert (theatre / ".troupe" / "history.sqlite").is_file()
    assert set_support.snapshot_tree(theatre) == before


def test_snapshot_ignores_only_history_state(make_theatre: MakeTheatre) -> None:
    """Req 13.1: history.sqlite* is ignored, any other added or changed file is seen."""
    theatre = _fixture_theatre(make_theatre)
    before = set_support.snapshot_tree(theatre)
    (theatre / ".troupe" / "history.sqlite-wal").write_bytes(b"x")
    assert set_support.snapshot_tree(theatre) == before

    (theatre / ".troupe" / "troupe.yml.tmp").write_bytes(b"")
    assert set_support.snapshot_tree(theatre) != before
    (theatre / ".troupe" / "troupe.yml.tmp").unlink()

    (theatre / "sub").mkdir()
    (theatre / "sub" / "history.sqlite").write_bytes(b"")
    assert set_support.snapshot_tree(theatre) != before
    (theatre / "sub" / "history.sqlite").unlink()

    (theatre / ".troupe" / "agents.yml").write_bytes(b"lead: alicf\n")
    assert set_support.snapshot_tree(theatre) != before


def test_assert_file_bytes_compares_exact_bytes(tmp_path: Path) -> None:
    """Req 13.1: the byte assertion accepts equal content and rejects any other byte."""
    target = tmp_path / "f.yml"
    target.write_bytes("é: 1\r\n".encode())

    set_support.assert_file_bytes(target, "é: 1\r\n")
    set_support.assert_file_bytes(target, "é: 1\r\n".encode())
    with pytest.raises(AssertionError):
        set_support.assert_file_bytes(target, "é: 1\n")
    with pytest.raises(AssertionError):
        set_support.assert_file_bytes(target, b"\xef\xbb\xbf" + "é: 1\r\n".encode())


def test_set_args_pass_value_as_one_argv_element(tmp_path: Path) -> None:
    """Req 16.1: the set convenience runs `troupe config set PATH VALUE --theatre DIR`."""
    value = '-x "a b"\nc'

    assert set_support.set_args(tmp_path, "a.b", value) == (
        "set",
        "a.b",
        value,
        "--theatre",
        str(tmp_path),
    )


def test_subprocess_runner_reports_usage_error(make_theatre: MakeTheatre) -> None:
    """Req 16.1: exit 2, empty stdout and a usage message on stderr for an unknown command."""
    theatre = _fixture_theatre(make_theatre)

    result = set_support.run_config("nosuchcommand", cwd=theatre)

    assert result.exit_code == 2
    assert result.stdout == b""
    assert result.stderr.startswith("troupe config: argument SUBCOMMAND: ")
    assert "invalid choice: 'nosuchcommand'" in result.stderr


def test_in_process_runner_reports_usage_error(make_theatre: MakeTheatre) -> None:
    """Req 16.1: the in-process runner captures exit 2, empty stdout and the usage message."""
    theatre = _fixture_theatre(make_theatre)

    result = set_support.run_config_in_process("nosuchcommand", cwd=theatre)

    assert result.exit_code == 2
    assert result.stdout == b""
    assert result.stderr.startswith("troupe config: argument SUBCOMMAND: ")
    assert "invalid choice: 'nosuchcommand'" in result.stderr


def test_in_process_runner_captures_stdout_and_uses_cwd(make_theatre: MakeTheatre) -> None:
    """Req 16.1: the in-process runner returns stdout and runs in the given directory."""
    theatre = _fixture_theatre(make_theatre)

    result = set_support.run_config_in_process("show", "team.lead", cwd=theatre)

    assert (result.exit_code, result.stdout, result.stderr) == (0, b"alice\n", "")
    assert Path.cwd() != theatre
