"""Test helpers: real temporary theatres and the real ``troupe`` console script.

Imported by ``conftest.py`` fixtures and directly by tests (``tests/`` is on the pytest
``pythonpath``).
"""

import subprocess
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol, TypedDict

from troupe.config.history import HistoryOp, HistoryRecord
from troupe.config.lineage import Actor, ChangeContext, Lineage, cli_context
from troupe.config.scalars import ScalarValue

TheatreFiles = Mapping[str, str | bytes]


@dataclass(frozen=True)
class CliResult:
    """Outcome of one real ``troupe`` invocation."""

    exit_code: int
    stdout: bytes
    stderr: str


@dataclass(frozen=True)
class Step1Record:
    """The five step-1 members of a history record (``seq``, ``op``, ``path``, ``old``, ``new``).

    Tests that pin step 1-3 facts compare records through :func:`step1`, because the lineage the
    store stamps (time, change id) differs on every run (config-lineage 4.3, 5.1).
    """

    seq: int
    op: HistoryOp
    path: str
    old: ScalarValue
    new: ScalarValue


def step1(records: Iterable[HistoryRecord]) -> tuple[Step1Record, ...]:
    """Project history records onto their five step-1 members, in order."""
    return tuple(
        Step1Record(seq=record.seq, op=record.op, path=record.path, old=record.old, new=record.new)
        for record in records
    )


class LineageFields(TypedDict):
    """The flat lineage keyword arguments of the history aggregate's command methods."""

    actor_kind: str
    actor_id: str | None
    via: str
    at: str
    reason: str | None
    change: str


FIXED_LINEAGE: Final = Lineage(
    actor=Actor(kind="human", id="alice"),
    via="cli",
    at="2026-10-04T09:05:07.123456Z",
    reason="a fixed reason",
    change="fixed-change",
)
"""A fixed lineage for tests that build events or records by hand."""

CLI_CONTEXT: Final[ChangeContext] = cli_context(FIXED_LINEAGE.actor, None)
"""A cli context for tests that record batches the way ``set`` does."""


def lineage_fields(lineage: Lineage = FIXED_LINEAGE) -> LineageFields:
    """The flat event fields of ``lineage`` (what the store passes to every event of a batch)."""
    return LineageFields(
        actor_kind=lineage.actor.kind,
        actor_id=lineage.actor.id,
        via=lineage.via,
        at=lineage.at,
        reason=lineage.reason,
        change=lineage.change,
    )


class MakeTheatre(Protocol):
    """Signature of the ``make_theatre`` fixture."""

    def __call__(
        self,
        config: str | bytes | None = None,
        *,
        config_name: str = "troupe.yml",
        files: TheatreFiles | None = None,
        name: str = "theatre",
    ) -> Path: ...


class RunCli(Protocol):
    """Signature of the ``run_cli`` fixture."""

    def __call__(self, *args: str, cwd: Path | None = None) -> CliResult: ...


def troupe_executable() -> Path:
    """Return the ``troupe`` console script installed next to the running interpreter."""
    script = Path(sys.executable).parent / "troupe"
    if not script.is_file():
        raise RuntimeError(f"{script}: troupe console script is not installed")
    return script


def write_theatre(
    root: Path,
    config: str | bytes | None = None,
    *,
    config_name: str = "troupe.yml",
    files: TheatreFiles | None = None,
) -> Path:
    """Write a real theatre under ``root`` and return the theatre directory.

    ``config`` becomes ``<root>/.troupe/<config_name>`` (``troupe.yml`` or ``troupe.yaml``);
    ``files`` maps paths relative to the theatre directory to their exact content (e.g. include
    files). ``str`` content is written as UTF-8 without newline translation; ``bytes`` verbatim.
    """
    root.mkdir(parents=True, exist_ok=True)
    entries: dict[str, str | bytes] = {}
    if config is not None:
        entries[f".troupe/{config_name}"] = config
    if files is not None:
        entries.update(files)
    for relative, content in entries.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        data = content.encode("utf-8") if isinstance(content, str) else content
        target.write_bytes(data)
    return root


def invoke_troupe(*args: str, cwd: Path) -> CliResult:
    """Run the real ``troupe`` console script with ``args`` in ``cwd``."""
    completed = subprocess.run(
        [str(troupe_executable()), *args],
        cwd=cwd,
        capture_output=True,
        check=False,
    )
    return CliResult(
        exit_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr.decode("utf-8"),
    )
