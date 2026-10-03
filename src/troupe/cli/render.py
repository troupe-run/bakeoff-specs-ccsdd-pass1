"""Pure formatting of command output (6.1-6.6, 8.2, 8.6, 8.7; config-get-show 5.4).

``get`` (and ``show`` for a scalar without ``--sources``, config-get-show 2.1) prints one scalar
per :func:`format_show_output`; ``history`` prints one JSON line per record per
:func:`format_history_line`. Both return bytes so handlers can write them into the
binary output buffer without newline translation (a CRLF in a text include is kept).
:func:`source_label` names a loaded file relative to the theatre directory for ``--sources``.
"""

import json
import math
import os
from pathlib import Path

from troupe.config.history import HistoryRecord
from troupe.config.model import SourceFile
from troupe.config.scalars import ScalarValue


def format_show_output(value: ScalarValue) -> bytes:
    """Return the ``get`` output for ``value``: its text followed by a newline, as UTF-8.

    ``show`` prints a scalar the same way when ``--sources`` is not given (config-get-show 2.1).

    A string is printed exactly and gets a newline only when it does not already end with one;
    booleans print as ``true``/``false`` (checked before ``int``, of which ``bool`` is a subclass),
    ``None`` as ``null``, integers in decimal and floats in ``repr()`` form (``inf``, ``nan``).
    """
    if isinstance(value, str):
        text = value if value.endswith("\n") else value + "\n"
    elif isinstance(value, bool):
        text = "true\n" if value else "false\n"
    elif value is None:
        text = "null\n"
    elif isinstance(value, int):
        text = str(value) + "\n"
    else:
        text = repr(value) + "\n"
    return text.encode("utf-8")


def format_history_line(record: HistoryRecord) -> bytes:
    """Return one JSON Lines line for ``record`` with members seq, op, path, old, new, as UTF-8.

    Infinite and NaN values in ``old``/``new`` become the strings ``".inf"``, ``"-.inf"`` and
    ``".nan"``; the line is encoded with ``allow_nan=False`` so it is always strict JSON.
    """
    line = json.dumps(
        {
            "seq": record.seq,
            "op": record.op,
            "path": record.path,
            "old": _json_safe(record.old),
            "new": _json_safe(record.new),
        },
        allow_nan=False,
    )
    return (line + "\n").encode("utf-8")


def _json_safe(value: ScalarValue) -> ScalarValue:
    """Replace a non-finite float by its YAML 1.2 canonical string; return others unchanged."""
    if isinstance(value, float):
        if math.isnan(value):
            return ".nan"
        if math.isinf(value):
            return ".inf" if value > 0 else "-.inf"
    return value


def source_label(file: SourceFile, theatre_dir: Path) -> str:
    """Return ``file``'s path relative to ``theatre_dir`` with ``/`` separators (5.4).

    Lexical, like error messages: ``abspath`` normalises ``.`` and ``..`` without resolving
    symbolic links, so the label shows the path the user wrote. A file outside the theatre
    directory gets leading ``..`` components (e.g. ``../shared/prompt.md``), and only there.
    """
    relative = os.path.relpath(os.path.abspath(file.path), os.path.abspath(theatre_dir))
    return Path(relative).as_posix()
