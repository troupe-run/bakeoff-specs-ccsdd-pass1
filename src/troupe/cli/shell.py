"""CLI shell: the names every ``troupe config`` subcommand module needs (1.2-1.4, 10.5).

This is a leaf module: it imports only the standard library and :mod:`troupe.config.errors`, never
another ``troupe.cli`` module. Subcommand modules (here and in other specifications) import these
names from ``troupe.cli.shell``; ``troupe.cli.main`` imports the subcommand modules. The import
direction is therefore ``shell <- subcommand modules <- main`` and has no cycle.
"""

import argparse
import io
import os
from collections.abc import Callable
from pathlib import Path
from typing import NoReturn

from troupe.config.errors import UsageError


class TroupeArgumentParser(argparse.ArgumentParser):
    """An argument parser whose errors raise :class:`UsageError` (exit 2) instead of exiting."""

    def error(self, message: str) -> NoReturn:
        raise UsageError(f"{self.prog}: {message}")


type Handler = Callable[[argparse.Namespace, io.BytesIO], None]
"""A subcommand handler.

It validates its own arguments (e.g. ``ConfigPath.parse``), then enters
``with open_theatre(args.theatre) as theatre:`` itself, runs its use case inside the block (so the
history store is closed at the end, 7.9) and writes its complete output as UTF-8 bytes into the
buffer. ``run`` copies the buffer to stdout only when the handler returns normally. A handler is
registered on its subparser with ``subparser.set_defaults(handler=...)``.
"""

type SubcommandRegistrar = Callable[[argparse._SubParsersAction[TroupeArgumentParser]], None]
"""Adds one subparser (with ``parents=[theatre_option_parent()]``) and sets its handler."""


def theatre_option_parent() -> TroupeArgumentParser:
    """Return a parent parser adding ``--theatre DIR``.

    The default is ``$TROUPE_THEATRE`` when it is set and not empty, else the current directory.
    """
    env_theatre = os.environ.get("TROUPE_THEATRE")
    parent = TroupeArgumentParser(add_help=False)
    parent.add_argument(
        "--theatre",
        metavar="DIR",
        type=Path,
        default=Path(env_theatre) if env_theatre else Path.cwd(),
        help=(
            "theatre directory holding .troupe/ "
            "(default: $TROUPE_THEATRE if set, else the current directory)"
        ),
    )
    return parent
