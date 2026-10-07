"""Test support for config-set-writeback: ``troupe config`` runners, byte checks, tree snapshots.

Builds on the foundation helpers in ``support.py`` (real theatres, the real console script) and
leaves them unchanged. ``tests/`` is on the pytest ``pythonpath``, so ``tests/writeback/`` unit
tests ``import set_support`` directly.

Traceability convention: every test written for config-set-writeback names the requirement IDs it
proves at the start of its docstring, for example ``Req 12.1, 12.2: ...``.
"""

import contextlib
import io
import os
from pathlib import Path

from support import CliResult, invoke_troupe
from troupe.cli import main as cli_main
from troupe.config.constants import CONFIG_DIR_NAME, HISTORY_STORE_NAME
from troupe.config.model import ConfigDocument
from troupe.config.paths import ConfigPath
from troupe.config.writeback.value import NewValue, mark_include_strings, parse_value

HISTORY_STATE_PREFIX = f"{CONFIG_DIR_NAME}/{HISTORY_STORE_NAME}"
"""Relative paths starting with this (the store and its SQLite side files) are not snapshotted."""

TreeSnapshot = dict[str, bytes]
"""Relative POSIX path of every file under a root mapped to its exact bytes."""


def run_config(*args: str, cwd: Path) -> CliResult:
    """Run the real console script as ``troupe config ARGS...`` in ``cwd`` (one argv per arg)."""
    return invoke_troupe("config", *args, cwd=cwd)


def run_config_in_process(*args: str, cwd: Path | None = None) -> CliResult:
    """Call the foundation CLI ``run`` with ``config ARGS...`` in this process.

    stdout and stderr are captured; ``cwd`` (if given) is the working directory during the call.
    Running in process lets tests monkeypatch the OS or SQLite boundary.
    """
    stdout = io.BytesIO()
    stderr = io.StringIO()
    with contextlib.chdir(cwd) if cwd is not None else contextlib.nullcontext():
        code = cli_main.run(["config", *args], stdout, stderr)
    return CliResult(exit_code=code, stdout=stdout.getvalue(), stderr=stderr.getvalue())


def set_args(theatre: Path, path: str, value: str, *options: str) -> tuple[str, ...]:
    """Return the ``troupe config`` arguments of ``set PATH VALUE [OPTIONS...] --theatre DIR``.

    VALUE is one argument whatever it contains (spaces, quotes, line breaks, a leading ``-``).
    """
    return ("set", path, value, *options, "--theatre", str(theatre))


def run_set(theatre: Path, path: str, value: str, *options: str) -> CliResult:
    """Run ``troupe config set PATH VALUE [OPTIONS...] --theatre DIR`` through the real console
    script (e.g. ``run_set(theatre, "b", "file:inc.yaml", "--include")``)."""
    return run_config(*set_args(theatre, path, value, *options), cwd=theatre)


def marked_value(
    document: ConfigDocument, text: str, path: ConfigPath, *, include: bool = False
) -> NewValue:
    """Parse VALUE ``text`` and mark its include strings as ``set`` does before target
    resolution: with ``document``'s schema, and as includes only with ``include``
    (``set --include``; without it they are ``!literal`` strings)."""
    return mark_include_strings(parse_value(text, path), path, document.schema, include=include)


def assert_file_bytes(path: Path, expected: str | bytes) -> None:
    """Assert that ``path`` holds exactly ``expected`` (``str`` is compared as UTF-8 bytes)."""
    want = expected.encode("utf-8") if isinstance(expected, str) else expected
    actual = path.read_bytes()
    assert actual == want, f"{path}: bytes differ\n  actual:   {actual!r}\n  expected: {want!r}"


def snapshot_tree(root: Path) -> TreeSnapshot:
    """Return every file under ``root`` (relative POSIX path -> bytes), sorted by path.

    ``.troupe/history.sqlite*`` is left out, so two snapshots are equal exactly when no user file
    was changed, added or removed.
    """
    snapshot: TreeSnapshot = {}
    for directory, _subdirectories, names in os.walk(root):
        for name in names:
            file = Path(directory) / name
            relative = file.relative_to(root).as_posix()
            if not relative.startswith(HISTORY_STATE_PREFIX):
                snapshot[relative] = file.read_bytes()
    return dict(sorted(snapshot.items()))
