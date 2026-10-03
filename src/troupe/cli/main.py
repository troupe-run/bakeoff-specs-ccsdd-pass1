"""Entry point of the ``troupe`` console script: the ``troupe config`` command group.

``run`` parses the command line, dispatches to the subcommand's handler with an in-memory bytes
buffer and copies the buffer to stdout only when the handler succeeded. A subcommand whose output
must stream (``watch``) sets a ``stream_handler`` instead: ``run`` passes it the real stdout and
stderr and returns its exit code (config-file-sync 4.3). Every
:class:`~troupe.config.errors.TroupeError` (including usage errors raised by the parser) is written
to stderr as its formatted message and its exit code is returned; stdout stays empty (10.1-10.3).
``run`` never opens the theatre: each handler validates its own arguments first and then opens the
theatre in a ``with`` block, so usage errors create no state and the store is closed at the end
of every command (1.5, 7.4, 7.9). Unexpected exceptions are not caught (traceback, exit 1); for
buffered handlers stdout is still empty because output is buffered.

Step 3 (config-get-show) adds ``get PATH`` and replaces step 1's scalar-only ``show PATH`` with
``show [PATH] [--sources]``, which prints values and whole sections as YAML; both are registered
first, in that order, from the read-command module :mod:`troupe.cli.get_show_command`
(config-get-show 1.1-1.5, 2.1-2.7).
"""

import argparse
import io
import sys
from collections.abc import Sequence
from typing import BinaryIO, Final, NoReturn, TextIO

from troupe.cli.get_show_command import add_get_subparser, add_show_subparser
from troupe.cli.render import format_history_line
from troupe.cli.set_command import add_set_subparser
from troupe.cli.shell import (
    StreamHandler,
    SubcommandRegistrar,
    TroupeArgumentParser,
    theatre_option_parent,
)
from troupe.cli.watch_command import add_watch_subparser
from troupe.config.errors import TroupeError
from troupe.config.paths import ConfigPath
from troupe.config.theatre import list_history, open_theatre


def handle_history(args: argparse.Namespace, buffer: io.BytesIO) -> None:
    """Handler of ``troupe config history [PATH]``: print one JSON line per history record.

    ``history`` never reconciles (config-file-sync 1.3): it prints what has been recorded, after
    importing a never-imported theatre.
    """
    prefix = None if args.path is None else ConfigPath.parse(args.path)
    with open_theatre(args.theatre, reconcile=False) as theatre:
        for record in list_history(theatre, prefix):
            buffer.write(format_history_line(record))


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
# config-file-sync appends its streaming `watch` registrar the same way.
# config-get-show (step 3) puts its `get` registrar first and replaces step 1's `show` with its
# own, both from its read-command module.
SUBCOMMAND_REGISTRARS: Final[tuple[SubcommandRegistrar, ...]] = (
    add_get_subparser,
    add_show_subparser,
    add_history_subparser,
    add_set_subparser,
    add_watch_subparser,
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
    """Run one command; return its exit code.

    A buffered ``handler``'s output reaches stdout only on success. A ``stream_handler`` writes to
    stdout and stderr itself and its return value is the exit code. For both, a ``TroupeError``
    (including a usage error from the parser) is written to stderr as its formatted message and its
    exit code returned; a streaming handler raising it before writing to stdout leaves stdout empty.
    """
    buffer = io.BytesIO()
    try:
        args = build_parser().parse_args(argv)
        stream_handler: StreamHandler | None = getattr(args, "stream_handler", None)
        if stream_handler is not None:
            return stream_handler(args, stdout, stderr)
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
