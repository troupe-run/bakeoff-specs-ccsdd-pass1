"""CLI shell: the names every ``troupe config`` subcommand module needs (1.2-1.4, 10.5).

This is a leaf module: it imports only the standard library and :mod:`troupe.config.errors`, never
another ``troupe.cli`` module. Subcommand modules (here and in other specifications) import these
names from ``troupe.cli.shell``; ``troupe.cli.main`` imports the subcommand modules. The import
direction is therefore ``shell <- subcommand modules <- main`` and has no cycle.
"""

import argparse
import io
import os
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, BinaryIO, NoReturn, TextIO

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

type StreamHandler = Callable[[argparse.Namespace, BinaryIO, TextIO], int]
"""A streaming subcommand handler, for commands whose output cannot be buffered (e.g. ``watch``).

It receives the real stdout (bytes) and stderr (text), writes to them directly (flushing as it
needs) and returns the exit code. Like a :data:`Handler`, it validates its own arguments and opens
the theatre itself. A :class:`~troupe.config.errors.TroupeError` it raises before writing to
stdout gets the shared conventions: ``run`` writes the formatted message to stderr and returns the
error's exit code, so stdout stays empty. A streaming handler is registered on its subparser with
``subparser.set_defaults(stream_handler=...)``; ``run`` prefers it over the buffered path.
"""

type SubcommandRegistrar = Callable[[argparse._SubParsersAction[TroupeArgumentParser]], None]
"""Adds one subparser (with ``parents=[theatre_option_parent()]``) and sets its handler."""


class _TheatreAction(argparse.Action):
    """Store ``--theatre DIR`` as ``theatre`` (a :class:`Path`) and ``theatre_text`` (as typed)."""

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: str | Sequence[Any] | None,
        option_string: str | None = None,
    ) -> None:
        text = str(values)
        setattr(namespace, self.dest, Path(text))
        namespace.theatre_text = text


def theatre_option_parent() -> TroupeArgumentParser:
    """Return a parent parser adding ``--theatre DIR``.

    The default is ``$TROUPE_THEATRE`` when it is set and not empty, else the current directory;
    it is read when the parser is built. Besides ``theatre`` (a :class:`Path`, used by every
    handler), the option stores ``theatre_text``: the directory text as given, i.e. the argument
    as typed, else the ``$TROUPE_THEATRE`` value as set, else ``str(Path.cwd())`` (config-file-sync
    4.3: ``watch`` prints it unnormalised).
    """
    env_theatre = os.environ.get("TROUPE_THEATRE")
    default_text = env_theatre if env_theatre else str(Path.cwd())
    parent = TroupeArgumentParser(add_help=False)
    parent.set_defaults(theatre_text=default_text)
    parent.add_argument(
        "--theatre",
        metavar="DIR",
        action=_TheatreAction,
        default=Path(default_text),
        help=(
            "theatre directory holding .troupe/ "
            "(default: $TROUPE_THEATRE if set, else the current directory)"
        ),
    )
    return parent
