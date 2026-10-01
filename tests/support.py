"""Test helpers: real temporary theatres and the real ``troupe`` console script.

Imported by ``conftest.py`` fixtures and directly by tests (``tests/`` is on the pytest
``pythonpath``).
"""

import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

TheatreFiles = Mapping[str, str | bytes]


@dataclass(frozen=True)
class CliResult:
    """Outcome of one real ``troupe`` invocation."""

    exit_code: int
    stdout: bytes
    stderr: str


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
