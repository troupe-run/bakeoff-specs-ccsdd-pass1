"""``troupe config watch``: the readiness line, stopping on a signal and start-up failures.

Every test drives the real console script in a subprocess through ``watch_support.WatchProcess``;
signals are only ever sent to that child. The one in-process test calls ``run`` for a start-up
failure, which returns before any signal could matter, and checks that the previous SIGINT and
SIGTERM handlers of the pytest process are back afterwards.
"""

import io
import os
import signal
import time
from pathlib import Path

import pytest

from support import write_theatre
from troupe.cli.main import run
from troupe.config.constants import CONFIG_DIR_NAME, HISTORY_STORE_NAME
from watch_support import WatchProcess

CONFIG = "name: demo\nretries: 1\n"


def _theatre(tmp_path: Path) -> Path:
    return write_theatre(tmp_path / "theatre", CONFIG)


def _env_without_theatre() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if key != "TROUPE_THEATRE"}


# ---- 4.3, 4.5, 4.6: the readiness line and stopping ----


@pytest.mark.parametrize("given", ["theatre/", "./theatre"], ids=["trailing-slash", "dot-relative"])
def test_readiness_line_echoes_the_theatre_directory_as_given(tmp_path: Path, given: str) -> None:
    """Req 4.3, 4.5: stdout is exactly ``watching <dir>\\n`` with ``<dir>`` as typed."""
    _theatre(tmp_path)

    with WatchProcess(given, cwd=tmp_path) as watch:
        assert watch.wait_ready() == f"watching {given}\n".encode()
        assert watch.stop(signal.SIGINT) == 0

    assert watch.stdout == f"watching {given}\n".encode()


def test_readiness_line_names_the_current_directory_by_default(tmp_path: Path) -> None:
    """Req 4.1, 4.3: without ``--theatre`` the theatre is the current directory, printed as such."""
    theatre = _theatre(tmp_path)
    expected = f"watching {theatre.resolve()}\n".encode()

    with WatchProcess(None, cwd=theatre, theatre=theatre, env=_env_without_theatre()) as watch:
        assert watch.wait_ready() == expected
        assert watch.stop(signal.SIGTERM) == 0

    assert watch.stdout == expected


@pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGTERM], ids=["SIGINT", "SIGTERM"])
def test_signal_stops_watch_with_exit_0_and_no_traceback(
    tmp_path: Path, sig: signal.Signals
) -> None:
    """Req 4.5, 4.6, 2.7: a signal exits 0, no traceback; stdout holds only the one line."""
    theatre = _theatre(tmp_path)

    with WatchProcess(str(theatre), cwd=tmp_path) as watch:
        watch.wait_ready()
        assert watch.stop(sig) == 0

    assert watch.stdout == f"watching {theatre}\n".encode()
    assert "Traceback" not in watch.stderr
    assert watch.stderr == ""


def test_edit_after_the_line_is_recorded_within_2_seconds(tmp_path: Path) -> None:
    """Req 4.3, 4.4: an edit made right after the readiness line is recorded within 2 s."""
    theatre = _theatre(tmp_path)
    config = theatre / CONFIG_DIR_NAME / "troupe.yml"

    with WatchProcess(str(theatre), cwd=tmp_path) as watch:
        watch.wait_ready()
        since = time.monotonic()
        config.write_text("name: demo\nretries: 2\n", encoding="utf-8")
        events = watch.wait_for_history(
            lambda events: any(e["op"] == "set" and e["path"] == "retries" for e in events),
            since=since,
        )
        assert watch.stop(signal.SIGINT) == 0

    assert [e for e in events if e["op"] == "set"] == [
        {"seq": 3, "op": "set", "path": "retries", "old": 1, "new": 2}
    ]
    assert watch.stderr == ""


# ---- 4.7, 4.8: start-up failures print no line ----


def test_surplus_argument_is_a_usage_error_without_a_line(tmp_path: Path) -> None:
    """Req 4.7: a surplus argument exits 2 under the usage rules and prints no ``watching`` line."""
    theatre = _theatre(tmp_path)

    with WatchProcess(str(theatre), cwd=tmp_path, extra_args=["surplus"]) as watch:
        assert watch.wait() == 2

    assert watch.stdout == b""
    assert "surplus" in watch.stderr
    assert "Traceback" not in watch.stderr
    assert not (theatre / CONFIG_DIR_NAME / HISTORY_STORE_NAME).exists()


def test_missing_config_exits_4_naming_the_expected_file(tmp_path: Path) -> None:
    """Req 4.8: no configuration file exits 4, empty stdout, stderr naming ``troupe.yml``."""
    theatre = tmp_path / "theatre"
    theatre.mkdir()

    with WatchProcess(str(theatre), cwd=tmp_path) as watch:
        assert watch.wait() == 4

    assert watch.stdout == b""
    assert watch.stderr.startswith(f"{theatre / CONFIG_DIR_NAME / 'troupe.yml'}: "), watch.stderr
    assert "Traceback" not in watch.stderr


@pytest.mark.parametrize("kind", ["garbage-file", "directory"])
def test_unreadable_store_exits_4_naming_the_store(tmp_path: Path, kind: str) -> None:
    """Req 4.8: a history store that cannot be read exits 4, empty stdout, stderr naming it."""
    theatre = _theatre(tmp_path)
    store = theatre / CONFIG_DIR_NAME / HISTORY_STORE_NAME
    if kind == "directory":
        store.mkdir()
    else:
        store.write_bytes(b"this is certainly not an SQLite database " * 10)

    with WatchProcess(str(theatre), cwd=tmp_path) as watch:
        assert watch.wait() == 4

    assert watch.stdout == b""
    assert watch.stderr.startswith(f"{store}: "), watch.stderr
    assert "Traceback" not in watch.stderr


def test_start_up_failure_restores_the_previous_signal_handlers(tmp_path: Path) -> None:
    """Req 4.6, 4.8: after an exit-4 start-up in process, SIGINT/SIGTERM handlers are as before."""
    theatre = tmp_path / "theatre"
    theatre.mkdir()
    before = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    stdout = io.BytesIO()
    stderr = io.StringIO()

    code = run(["config", "watch", "--theatre", str(theatre)], stdout, stderr)

    assert code == 4
    assert stdout.getvalue() == b""
    assert str(theatre / CONFIG_DIR_NAME / "troupe.yml") in stderr.getvalue()
    assert {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)} == before
