"""CLI ``troupe config watch``: registration and streaming handler (config-file-sync 2.7, 4.1-4.8).

Imports only :mod:`troupe.cli.shell` from the CLI package (never ``troupe.cli.main``, which imports
this module).

Signals only request a stop: the SIGINT and SIGTERM handlers set a flag and never raise, so a
reconcile in progress always finishes its single transaction (2.7), the theatre is closed and the
lock released, and the command exits 0 without a traceback (4.6). The previous handlers are
restored in a ``finally`` on every exit path, including a start-up failure.
"""

import argparse
import signal
from types import FrameType
from typing import BinaryIO, TextIO

from troupe.cli.shell import (
    StreamHandler,
    SubcommandRegistrar,
    TroupeArgumentParser,
    theatre_option_parent,
)
from troupe.config.constants import WATCHING_LINE_PREFIX
from troupe.config.watch import Watcher

_STOP_SIGNALS = (signal.SIGINT, signal.SIGTERM)


def handle_watch(args: argparse.Namespace, stdout: BinaryIO, stderr: TextIO) -> int:
    """Handler of ``troupe config watch``: sync, print ``watching <dir>``, follow until stopped.

    A :class:`~troupe.config.errors.TroupeError` from the first sync propagates to ``run`` before
    anything is written to stdout (4.8). The readiness line is the only thing ever written to
    stdout (4.3, 4.5); every message while watching goes to stderr. Returns 0 after a stop (4.6).
    """
    stop = False

    def request_stop(signum: int, frame: FrameType | None) -> None:
        nonlocal stop
        stop = True

    def report(message: str) -> None:
        stderr.write(f"{message}\n")
        stderr.flush()

    previous = {sig: signal.getsignal(sig) for sig in _STOP_SIGNALS}
    try:
        for sig in _STOP_SIGNALS:
            signal.signal(sig, request_stop)
        watcher = Watcher(args.theatre, report=report, stop_requested=lambda: stop)
        watcher.start()
        if not stop:
            stdout.write(f"{WATCHING_LINE_PREFIX}{args.theatre_text}\n".encode())
            stdout.flush()
        watcher.run()
        return 0
    finally:
        for sig, handler in previous.items():
            if handler is not None:  # None: installed outside Python, cannot be reinstalled
                signal.signal(sig, handler)


def add_watch_subparser(subparsers: "argparse._SubParsersAction[TroupeArgumentParser]") -> None:
    """Register ``watch`` with the shared ``--theatre`` option and no positional argument (4.7)."""
    parser = subparsers.add_parser(
        "watch",
        parents=[theatre_option_parent()],
        help="record hand edits to the configuration files as they happen",
        description=(
            "Import or reconcile the theatre, print 'watching DIR' once every later change is "
            "certain to be observed, then record each edit to the configuration file and its "
            "includes until stopped with SIGINT (Ctrl-C) or SIGTERM."
        ),
    )
    parser.set_defaults(stream_handler=handle_watch)


# Static conformance to the shell's protocols (checked by ``mypy --strict``).
_REGISTRAR: SubcommandRegistrar = add_watch_subparser
_HANDLER: StreamHandler = handle_watch
