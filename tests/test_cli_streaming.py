"""The CLI plumbing ``watch`` relies on: the raw ``--theatre`` text and streaming handlers.

Covers the directory text as given (config-file-sync 4.1, 4.3) and the streaming dispatch path of
``run`` with the shared exit-code and stderr conventions (4.7, 4.8 via config-foundation 10.1-10.3,
10.5). A test-only ``probe`` subcommand replaces the registration point; production code has no
test hooks.
"""

import argparse
import io
from pathlib import Path
from typing import BinaryIO, TextIO

import pytest

import troupe.cli.main as cli_main
from troupe.cli import shell
from troupe.cli.shell import StreamHandler
from troupe.config.errors import PathNotFoundError


def _parse_theatre(*argv: str) -> argparse.Namespace:
    """Parse ``argv`` with a parser built now (so the current ``$TROUPE_THEATRE`` applies)."""
    parser = shell.TroupeArgumentParser(prog="probe", parents=[shell.theatre_option_parent()])
    return parser.parse_args(list(argv))


# ---- 4.1, 4.3: the theatre directory as given ----


def test_theatre_text_keeps_the_option_as_typed(monkeypatch: pytest.MonkeyPatch) -> None:
    """4.3: ``--theatre ./t/`` keeps the text ``./t/``; the parsed path is unchanged (4.1)."""
    monkeypatch.delenv("TROUPE_THEATRE", raising=False)

    args = _parse_theatre("--theatre", "./t/")

    assert args.theatre_text == "./t/"
    assert args.theatre == Path("t")
    assert isinstance(args.theatre, Path)


def test_theatre_text_keeps_the_env_value_as_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """4.1, 4.3: the ``$TROUPE_THEATRE`` default keeps its text as set; the path is unchanged."""
    monkeypatch.setenv("TROUPE_THEATRE", "./env-t/")

    args = _parse_theatre()

    assert args.theatre_text == "./env-t/"
    assert args.theatre == Path("env-t")


def test_theatre_option_wins_over_the_env_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """4.1, 4.3: an explicit ``--theatre`` wins over ``$TROUPE_THEATRE`` for both attributes."""
    monkeypatch.setenv("TROUPE_THEATRE", "/from/env")

    args = _parse_theatre("--theatre", "other/")

    assert (args.theatre_text, args.theatre) == ("other/", Path("other"))


@pytest.mark.parametrize("env", [None, ""], ids=["unset", "empty"])
def test_theatre_text_defaults_to_the_current_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, env: str | None
) -> None:
    """4.1, 4.3: without ``--theatre`` and a non-empty ``$TROUPE_THEATRE``, the text is the cwd."""
    if env is None:
        monkeypatch.delenv("TROUPE_THEATRE", raising=False)
    else:
        monkeypatch.setenv("TROUPE_THEATRE", env)
    monkeypatch.chdir(tmp_path)

    args = _parse_theatre()

    assert args.theatre_text == str(Path.cwd())
    assert args.theatre == Path.cwd()


def test_registered_subcommands_get_the_theatre_text(monkeypatch: pytest.MonkeyPatch) -> None:
    """4.1, 4.3: the real parser gives every subcommand both attributes."""
    monkeypatch.delenv("TROUPE_THEATRE", raising=False)

    args = cli_main.build_parser().parse_args(["config", "show", "a", "--theatre", "./t/"])

    assert (args.theatre_text, args.theatre) == ("./t/", Path("t"))
    assert args.handler is cli_main.handle_show


# ---- 4.7, 4.8 (config-foundation 10.1-10.3, 10.5): streaming handlers in run() ----


def _register_stream_handler(monkeypatch: pytest.MonkeyPatch, handler: StreamHandler) -> None:
    """Replace the registration point by one ``probe`` subcommand streaming through ``handler``."""

    def add_probe(subparsers: "argparse._SubParsersAction[shell.TroupeArgumentParser]") -> None:
        parser = subparsers.add_parser("probe", parents=[shell.theatre_option_parent()])
        parser.set_defaults(stream_handler=handler)

    monkeypatch.setattr(cli_main, "SUBCOMMAND_REGISTRARS", (add_probe,))


def test_streaming_handler_writes_to_stdout_and_returns_its_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """4.3: a streaming handler writes straight to stdout/stderr and its code is the exit code."""
    seen: list[tuple[BinaryIO, TextIO]] = []

    def probe(args: argparse.Namespace, stdout: BinaryIO, stderr: TextIO) -> int:
        seen.append((stdout, stderr))
        stdout.write(f"watching {args.theatre_text}\n".encode())
        stderr.write("note\n")
        return 7

    _register_stream_handler(monkeypatch, probe)
    stdout, stderr = io.BytesIO(), io.StringIO()

    code = cli_main.run(["config", "probe", "--theatre", "./t/"], stdout, stderr)

    assert (code, stdout.getvalue(), stderr.getvalue()) == (7, b"watching ./t/\n", "note\n")
    assert seen == [(stdout, stderr)]


def test_streaming_handler_troupe_error_keeps_the_conventions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """4.8 (config-foundation 10.2, 10.3, 10.5): exit code, empty stdout, formatted stderr."""

    def probe(args: argparse.Namespace, stdout: BinaryIO, stderr: TextIO) -> int:
        raise PathNotFoundError("does not exist", file=Path("f.yml"), path="x.y")

    _register_stream_handler(monkeypatch, probe)
    stdout, stderr = io.BytesIO(), io.StringIO()

    code = cli_main.run(["config", "probe"], stdout, stderr)

    assert code == 5
    assert stdout.getvalue() == b""
    assert stderr.getvalue() == "f.yml: x.y: does not exist\n"


def test_streaming_subcommand_usage_error_exits_2(monkeypatch: pytest.MonkeyPatch) -> None:
    """4.7: a malformed command line never reaches the streaming handler (exit 2, empty stdout)."""
    calls: list[argparse.Namespace] = []

    def probe(args: argparse.Namespace, stdout: BinaryIO, stderr: TextIO) -> int:
        calls.append(args)
        return 0

    _register_stream_handler(monkeypatch, probe)
    stdout, stderr = io.BytesIO(), io.StringIO()

    code = cli_main.run(["config", "probe", "surplus"], stdout, stderr)

    assert code == 2
    assert stdout.getvalue() == b""
    assert "surplus" in stderr.getvalue()
    assert calls == []
