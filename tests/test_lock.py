"""The exclusive per-theatre lock on the ``.troupe/`` directory (6.4, 7.1, 7.2).

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from support import MakeTheatre
from troupe.config.constants import CONFIG_DIR_NAME
from troupe.config.errors import ConfigLoadError, ExitCode
from troupe.config.lock import TheatreLock

CONFIG = "name: demo\n"
CHILD_TIMEOUT_SECONDS = 60.0
HOLD_SECONDS = 0.5

# The child first proves the lock is busy with a non-blocking attempt, then announces that it
# is about to block, takes the lock through TheatreLock and reports the wall-clock time it got it.
CHILD_SCRIPT = textwrap.dedent(
    """
    import fcntl, os, sys, time
    from pathlib import Path
    from troupe.config.lock import TheatreLock

    config_dir = Path(sys.argv[1])
    fd = os.open(config_dir, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        print("free", flush=True)
    except BlockingIOError:
        print("busy", flush=True)
    finally:
        os.close(fd)
    print("waiting", flush=True)
    lock = TheatreLock.acquire(config_dir)
    print(f"acquired {time.time()!r}", flush=True)
    lock.release()
    """
)


@pytest.fixture
def config_dir(make_theatre: MakeTheatre) -> Path:
    return make_theatre(CONFIG) / CONFIG_DIR_NAME


def _listing(directory: Path) -> list[str]:
    return sorted(str(path.relative_to(directory)) for path in directory.rglob("*"))


def _read_line(process: subprocess.Popen[str]) -> str:
    assert process.stdout is not None
    line: str = process.stdout.readline()
    assert line, f"child exited early: {process.stderr.read() if process.stderr else ''}"
    return line.strip()


def test_child_process_waits_until_the_parent_releases(config_dir: Path) -> None:
    """Req 6.4: a second process blocks on the lock until the holder releases it."""
    lock = TheatreLock.acquire(config_dir)
    child = subprocess.Popen(
        [sys.executable, "-c", CHILD_SCRIPT, str(config_dir)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert _read_line(child) == "busy"
        assert _read_line(child) == "waiting"
        time.sleep(HOLD_SECONDS)
        released_at = time.time()
        lock.release()
        acquired = _read_line(child)
        child.wait(timeout=CHILD_TIMEOUT_SECONDS)
    finally:
        lock.release()
        if child.poll() is None:
            child.kill()
            child.wait()

    assert child.returncode == 0
    word, stamp = acquired.split()
    assert word == "acquired"
    assert float(stamp) >= released_at


def test_lock_is_released_when_the_holding_process_exits(config_dir: Path) -> None:
    """Req 6.4: a holder that exits without releasing does not leave the theatre locked."""
    script = "import sys; from pathlib import Path; from troupe.config.lock import TheatreLock;"
    script += " TheatreLock.acquire(Path(sys.argv[1]))"
    subprocess.run(
        [sys.executable, "-c", script, str(config_dir)],
        check=True,
        timeout=CHILD_TIMEOUT_SECONDS,
    )

    lock = TheatreLock.acquire(config_dir)
    lock.release()


def test_holding_the_lock_creates_no_file(config_dir: Path) -> None:
    """Req 7.1, 7.2: the lock leaves the ``.troupe/`` listing identical before, during and after."""
    before = _listing(config_dir)
    theatre_before = _listing(config_dir.parent)

    lock = TheatreLock.acquire(config_dir)
    during = _listing(config_dir)
    lock.release()
    after = _listing(config_dir)

    assert before == during == after
    assert _listing(config_dir.parent) == theatre_before


def test_nested_acquire_in_one_process_raises(config_dir: Path) -> None:
    """Req 6.4: a second acquire of a held directory is a programming error, not a deadlock."""
    lock = TheatreLock.acquire(config_dir)
    try:
        with pytest.raises(RuntimeError):
            TheatreLock.acquire(config_dir)
        # The same directory reached through another spelling is still the same lock.
        with pytest.raises(RuntimeError):
            TheatreLock.acquire(config_dir.parent / "." / CONFIG_DIR_NAME)
    finally:
        lock.release()


def test_nested_acquire_through_a_symlink_raises(config_dir: Path, tmp_path: Path) -> None:
    """Req 6.4: a symlinked path to a held directory is recognised as the same lock."""
    link = tmp_path / "link"
    link.symlink_to(config_dir, target_is_directory=True)
    lock = TheatreLock.acquire(config_dir)
    try:
        with pytest.raises(RuntimeError):
            TheatreLock.acquire(link)
    finally:
        lock.release()


def test_release_is_idempotent_and_the_lock_can_be_taken_again(config_dir: Path) -> None:
    """Req 6.4: releasing twice is harmless and a released lock can be acquired again."""
    lock = TheatreLock.acquire(config_dir)
    lock.release()
    lock.release()

    again = TheatreLock.acquire(config_dir)
    again.release()
    lock.release()  # releasing the old handle must not free the new holder's registration

    third = TheatreLock.acquire(config_dir)
    third.release()


def test_stale_release_does_not_unregister_a_new_holder(config_dir: Path) -> None:
    """Req 6.4: an already released handle cannot drop the nested-use guard of a newer holder."""
    old = TheatreLock.acquire(config_dir)
    old.release()
    current = TheatreLock.acquire(config_dir)
    try:
        old.release()
        with pytest.raises(RuntimeError):
            TheatreLock.acquire(config_dir)
    finally:
        current.release()


def test_locks_on_different_theatres_are_independent(make_theatre: MakeTheatre) -> None:
    """Req 6.4: the lock is per theatre; holding one does not refuse another."""
    first = TheatreLock.acquire(make_theatre(CONFIG, name="one") / CONFIG_DIR_NAME)
    second = TheatreLock.acquire(make_theatre(CONFIG, name="two") / CONFIG_DIR_NAME)
    second.release()
    first.release()


def test_missing_directory_gives_a_load_error_naming_it(tmp_path: Path) -> None:
    """Req 7.2: a missing ``.troupe/`` is a load error (exit 4) naming it; nothing is created."""
    missing = tmp_path / "theatre" / CONFIG_DIR_NAME

    with pytest.raises(ConfigLoadError) as caught:
        TheatreLock.acquire(missing)

    assert caught.value.file == missing
    assert caught.value.exit_code == ExitCode.LOAD
    assert str(missing) in str(caught.value)
    assert not missing.exists()
    assert not missing.parent.exists()


def test_failed_acquire_leaves_no_registration_or_open_fd(tmp_path: Path) -> None:
    """Req 7.2: a failed acquire neither registers the directory nor leaks a file descriptor."""
    missing = tmp_path / CONFIG_DIR_NAME
    fds_before = _open_fds()
    with pytest.raises(ConfigLoadError):
        TheatreLock.acquire(missing)
    assert _open_fds() == fds_before

    missing.mkdir()
    lock = TheatreLock.acquire(missing)
    lock.release()


def _open_fds() -> set[str]:
    for candidate in (Path("/proc/self/fd"), Path("/dev/fd")):
        if candidate.is_dir():
            return set(os.listdir(candidate))
    pytest.skip("no fd listing on this platform")
