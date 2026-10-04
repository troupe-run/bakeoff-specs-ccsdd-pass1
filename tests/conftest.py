"""Shared test support: real temporary theatres and the real ``troupe`` console script.

``tests/`` is on the pytest ``pythonpath`` (see ``pyproject.toml``), so helper modules placed
here are importable from test subdirectories such as ``tests/writeback/``.
"""

from pathlib import Path

import pytest

from support import CliResult, MakeTheatre, RunCli, TheatreFiles, invoke_troupe, write_theatre


@pytest.fixture(autouse=True)
def _no_troupe_actor(monkeypatch: pytest.MonkeyPatch) -> None:
    """Autouse: remove ``TROUPE_ACTOR``; no test depends on the developer's shell (lineage 2.2).

    Tests that need it set it with ``monkeypatch.setenv``; subprocesses inherit the test's
    environment.
    """
    monkeypatch.delenv("TROUPE_ACTOR", raising=False)


@pytest.fixture
def make_theatre(tmp_path: Path) -> MakeTheatre:
    """Fixture: build a real theatre inside ``tmp_path`` (see ``write_theatre``)."""

    def _make(
        config: str | bytes | None = None,
        *,
        config_name: str = "troupe.yml",
        files: TheatreFiles | None = None,
        name: str = "theatre",
    ) -> Path:
        return write_theatre(tmp_path / name, config, config_name=config_name, files=files)

    return _make


@pytest.fixture
def run_cli(tmp_path: Path) -> RunCli:
    """Fixture: run the real ``troupe`` console script (cwd defaults to ``tmp_path``)."""

    def _run(*args: str, cwd: Path | None = None) -> CliResult:
        return invoke_troupe(*args, cwd=tmp_path if cwd is None else cwd)

    return _run
