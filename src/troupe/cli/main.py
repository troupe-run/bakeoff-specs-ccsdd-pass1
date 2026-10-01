"""Entry point of the ``troupe`` console script: the ``troupe config`` command group.

``run`` parses the command line, dispatches to the subcommand's handler with an in-memory bytes
buffer and copies the buffer to stdout only when the handler succeeded. Every
:class:`~troupe.config.errors.TroupeError` (including usage errors raised by the parser) is written
to stderr as its formatted message and its exit code is returned; stdout stays empty (10.1-10.3).
``run`` never opens the theatre: each handler validates its own arguments first and then opens the
theatre in a ``with`` block, so usage errors create no state and the store is closed at the end
of every command (1.5, 7.4, 7.9). Unexpected exceptions are not caught (traceback, exit 1); stdout
is still empty because output is buffered.
"""

import argparse
import io
import sys
from collections.abc import Sequence
from typing import BinaryIO, Final, NoReturn, TextIO

from troupe.cli.render import format_history_line, format_show_output
from troupe.cli.set_command import add_set_subparser
from troupe.cli.shell import SubcommandRegistrar, TroupeArgumentParser, theatre_option_parent
from troupe.config.errors import TroupeError
from troupe.config.paths import ConfigPath
from troupe.config.theatre import list_history, open_theatre, show_value


def handle_show(args: argparse.Namespace, buffer: io.BytesIO) -> None:
    """Handler of ``troupe config show PATH``: print the scalar at PATH."""
    path = ConfigPath.parse(args.path)
    with open_theatre(args.theatre) as theatre:
        buffer.write(format_show_output(show_value(theatre, path)))


def handle_history(args: argparse.Namespace, buffer: io.BytesIO) -> None:
    """Handler of ``troupe config history [PATH]``: print one JSON line per history record."""
    prefix = None if args.path is None else ConfigPath.parse(args.path)
    with open_theatre(args.theatre) as theatre:
        for record in list_history(theatre, prefix):
            buffer.write(format_history_line(record))


def add_show_subparser(subparsers: "argparse._SubParsersAction[TroupeArgumentParser]") -> None:
    """Register ``show PATH``."""
    parser = subparsers.add_parser(
        "show",
        parents=[theatre_option_parent()],
        help="print the value at PATH",
        description="Print the scalar value at PATH as written in the configuration files.",
    )
    parser.add_argument("path", metavar="PATH", help="dotted path, e.g. agents.lead or items.0")
    parser.set_defaults(handler=handle_show)


def add_history_subparser(
    subparsers: "argparse._SubParsersAction[TroupeArgumentParser]",
) -> None:
    """Register ``history [PATH]``."""
    parser = subparsers.add_parser(
        "history",
        parents=[theatre_option_parent()],
        help="list the configuration history as JSON lines",
        description="List the recorded configuration changes, oldest first, one JSON line each.",
    )
    parser.add_argument(
        "path", metavar="PATH", nargs="?", default=None, help="only PATH and paths below it"
    )
    parser.set_defaults(handler=handle_history)


# ---- REGISTRATION POINT ----
# Each entry adds one `troupe config` subcommand. config-set-writeback extends this tuple with
# exactly one entry (its `set` registrar, imported from its own module); nothing else changes.
SUBCOMMAND_REGISTRARS: Final[tuple[SubcommandRegistrar, ...]] = (
    add_show_subparser,
    add_history_subparser,
    add_set_subparser,
)


def build_parser() -> TroupeArgumentParser:
    """Build ``troupe config {...}`` with one subparser per entry of SUBCOMMAND_REGISTRARS."""
    parser = TroupeArgumentParser(prog="troupe")
    groups = parser.add_subparsers(dest="group", metavar="COMMAND", required=True)
    config = groups.add_parser(
        "config",
        help="inspect a theatre's configuration",
        description="Inspect a theatre's configuration and its history.",
    )
    subcommands = config.add_subparsers(dest="subcommand", metavar="SUBCOMMAND", required=True)
    for register in SUBCOMMAND_REGISTRARS:
        register(subcommands)
    return parser


def run(argv: Sequence[str], stdout: BinaryIO, stderr: TextIO) -> int:
    """Run one command; return its exit code. stdout is written only on success."""
    buffer = io.BytesIO()
    try:
        args = build_parser().parse_args(argv)
        args.handler(args, buffer)
    except TroupeError as error:
        stderr.write(f"{error}\n")
        return int(error.exit_code)
    stdout.write(buffer.getvalue())
    stdout.flush()
    return 0


def main() -> NoReturn:
    """Console-script entry point."""
    sys.exit(run(sys.argv[1:], sys.stdout.buffer, sys.stderr))
