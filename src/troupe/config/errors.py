"""Error types, the exit-code mapping and the single message format.

Every failure the CLI reports is a :class:`TroupeError` subclass carrying the offending
``file``, the key ``path`` and the ``problem``. The exit code is a class attribute, so the CLI
never decides exit codes itself. Files are given as the path that was opened (theatre argument
joined with ``.troupe/...`` and include specs, not resolved) so users recognise them; for a
value written in an included file, the included file is named.
"""

from enum import IntEnum
from pathlib import Path
from typing import ClassVar


class ExitCode(IntEnum):
    """Process exit codes shared by every ``troupe config`` subcommand."""

    OK = 0
    USAGE = 2
    WRITE_REFUSED = 3
    LOAD = 4
    PATH = 5


def format_message(*, file: Path | None, path: str | None, problem: str) -> str:
    """Return ``<file>: <path>: <problem>``, omitting whichever of file and path is not set."""
    parts: list[str] = []
    if file is not None:
        parts.append(str(file))
    if path is not None:
        parts.append(path)
    parts.append(problem)
    return ": ".join(parts)


class TroupeError(Exception):
    """Base of every error the CLI reports; carries the file, the key path and the problem."""

    exit_code: ClassVar[ExitCode]

    def __init__(self, problem: str, *, file: Path | None = None, path: str | None = None) -> None:
        super().__init__(problem)
        self.problem = problem
        self.file = file
        self.path = path

    def __str__(self) -> str:
        return format_message(file=self.file, path=self.path, problem=self.problem)


class UsageError(TroupeError):
    """Malformed command line or PATH."""

    exit_code = ExitCode.USAGE


class WriteRefusedError(TroupeError):
    """A write was refused; defined here, raised only by config-set-writeback."""

    exit_code = ExitCode.WRITE_REFUSED


class ConfigLoadError(TroupeError):
    """The configuration or the history store cannot be located, read or loaded."""

    exit_code = ExitCode.LOAD


class PathError(TroupeError):
    """A PATH does not exist or names the wrong kind of node for the command."""

    exit_code = ExitCode.PATH


class PathNotFoundError(PathError):
    """The PATH does not exist; ``file`` names the file of the deepest existing node."""


class WrongNodeKindError(PathError):
    """The node at PATH exists but is the wrong kind for the command; ``file`` names its file."""
