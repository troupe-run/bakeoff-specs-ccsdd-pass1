"""AtomicWriter: the only writer of user files (atomic replace next to the target, restore).

A write goes to ``SourceFile.real_path`` (a symbolic link is written through and stays a link): the
UTF-8 bytes of the text (a BOM in the text is kept; no newline translation) are written to an
exclusively created hidden temporary file ``.<name>.troupe-tmp-<16 hex>`` in the target's directory
with the target's permission bits, flushed and synced, then renamed over the target. On any failure
the temporary file is removed and a :class:`WriteRefusedError` naming the file is raised; the
caller adds PATH. No extra file remains after any handled outcome (product-owner decision E).
"""

import contextlib
import os
import secrets
import stat
from pathlib import Path

from troupe.config.errors import WriteRefusedError
from troupe.config.model import SourceFile

_TEMP_INFIX = ".troupe-tmp-"
_CREATE_FLAGS = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_CLOEXEC", 0)
_MAX_NAME_ATTEMPTS = 16


def write_atomically(file: SourceFile, text: str) -> None:
    """Replace the content of ``file.real_path`` with ``text`` encoded as UTF-8, atomically.

    Raises :class:`WriteRefusedError` (``file=file.path``) when the file cannot be written; the
    target then keeps its previous content and no temporary file remains.
    """
    try:
        data = text.encode("utf-8")
    except UnicodeEncodeError as error:
        raise WriteRefusedError(
            f"cannot write file: text is not valid UTF-8 ({error.reason})", file=file.path
        ) from error
    target = file.real_path
    try:
        mode = stat.S_IMODE(os.stat(target).st_mode)
        temp, descriptor = _create_temp(target, mode)
    except OSError as error:
        raise WriteRefusedError(f"cannot write file: {_reason(error)}", file=file.path) from error
    replaced = False
    try:
        try:
            os.fchmod(descriptor, mode)  # O_CREAT's mode is reduced by the umask
            _write_all(descriptor, data)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temp, target)
        replaced = True
    except Exception as error:
        raise WriteRefusedError(f"cannot write file: {_reason(error)}", file=file.path) from error
    finally:
        if not replaced:
            with contextlib.suppress(OSError):
                os.unlink(temp)


def restore_file(file: SourceFile) -> None:
    """Write ``file``'s original text back to ``file.real_path`` (same rules as a write)."""
    write_atomically(file, file.text)


def _create_temp(target: Path, mode: int) -> tuple[Path, int]:
    """Exclusively create a hidden temporary file next to ``target``; return it and its fd."""
    for _attempt in range(_MAX_NAME_ATTEMPTS):
        temp = target.parent / f".{target.name}{_TEMP_INFIX}{secrets.token_hex(8)}"
        try:
            return temp, os.open(temp, _CREATE_FLAGS, mode)
        except FileExistsError:
            continue
    raise FileExistsError(f"no free temporary file name next to {target.name}")


def _write_all(descriptor: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(descriptor, view)
        view = view[written:]


def _reason(error: Exception) -> str:
    if isinstance(error, OSError) and error.strerror:
        return error.strerror
    return str(error) or type(error).__name__
