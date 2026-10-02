"""The exclusive per-theatre lock (6.4, 7.1, 7.2).

Every command and every watch sync on a theatre holds this lock, so two reconciles, or a reconcile
and a ``set``, never record their events interleaved (6.4). The lock is a blocking exclusive
``flock`` on a read-only descriptor of the theatre's existing ``.troupe/`` directory itself, so no
lock file is created (7.1, 7.2); the kernel releases it when the holding process exits.

The lock is not reentrant: acquiring a directory that this process already holds raises
:class:`RuntimeError` (a programming error) instead of deadlocking.
"""

import fcntl
import os
from pathlib import Path
from typing import Self

from troupe.config.errors import ConfigLoadError

# Real paths of the ``.troupe/`` directories this process currently holds.
_held: set[Path] = set()


class TheatreLock:
    """A held exclusive lock on one theatre's ``.troupe/`` directory."""

    def __init__(self, config_dir: Path, real_path: Path, fd: int) -> None:
        self.config_dir = config_dir
        self._real_path = real_path
        self._fd: int | None = fd

    @classmethod
    def acquire(cls, config_dir: Path) -> Self:
        """Block until the lock on ``config_dir`` is held and return it.

        Raises :class:`RuntimeError` when this process already holds it, and
        :class:`ConfigLoadError` naming ``config_dir`` when it cannot be opened or locked.
        """
        real_path = config_dir.resolve()
        if real_path in _held:
            raise RuntimeError(f"{config_dir} is already locked by this process")
        try:
            fd = os.open(config_dir, os.O_RDONLY)
        except OSError as error:
            raise _lock_error(config_dir, error) from error
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
        except OSError as error:
            os.close(fd)
            raise _lock_error(config_dir, error) from error
        _held.add(real_path)
        return cls(config_dir, real_path, fd)

    def release(self) -> None:
        """Unlock and close the descriptor; releasing again does nothing."""
        fd, self._fd = self._fd, None
        if fd is None:
            return
        _held.discard(self._real_path)
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _lock_error(config_dir: Path, error: OSError) -> ConfigLoadError:
    reason = error.strerror or type(error).__name__
    return ConfigLoadError(f"cannot lock the theatre: {reason}", file=config_dir)
