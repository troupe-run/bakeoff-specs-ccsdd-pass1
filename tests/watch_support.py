"""Test support for ``troupe config watch``: the real command as a background subprocess.

:class:`WatchProcess` starts ``troupe config watch --theatre DIR`` with the real console script
(``support.troupe_executable``), reads its stdout and stderr on background threads (so a full pipe
never blocks the child and a wait never blocks the test), waits for the readiness line, polls
``troupe config history`` (which never reconciles, so it is a safe observer) against a deadline,
sends signals, and always terminates and kills the child when it is closed. Signals are only ever
sent to the child: no test installs a signal handler in the pytest process for this.
"""

import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from types import TracebackType
from typing import IO, Any, Self

from support import invoke_troupe, step1_members, troupe_executable

READY_TIMEOUT_SECONDS = 10.0
"""Longest wait for the ``watching`` line after the process starts."""

HISTORY_POLL_SECONDS = 0.1
"""Shortest interval between two ``history`` polls (each poll briefly takes the theatre lock)."""

EXIT_TIMEOUT_SECONDS = 10.0
"""Longest wait for the child to exit after a signal or in teardown before it is killed."""

HistoryEvent = dict[str, Any]
"""The step-1 members of one ``troupe config history`` line (:func:`support.step1_members`)."""


class _PipeReader:
    """Collect everything a child writes to one pipe, on a daemon thread."""

    def __init__(self, pipe: IO[bytes]) -> None:
        self._pipe = pipe
        self._data = bytearray()
        self._closed = False
        self._changed = threading.Condition()
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()

    def _pump(self) -> None:
        try:
            while chunk := os.read(self._pipe.fileno(), 4096):
                with self._changed:
                    self._data.extend(chunk)
                    self._changed.notify_all()
        finally:
            with self._changed:
                self._closed = True
                self._changed.notify_all()

    def wait_for(self, predicate: Callable[[bytes, bool], bool], timeout: float) -> bytes:
        """Wait until ``predicate(data, closed)`` holds or ``timeout`` passes; return the data."""
        with self._changed:
            self._changed.wait_for(lambda: predicate(bytes(self._data), self._closed), timeout)
            return bytes(self._data)

    def finish(self, timeout: float) -> bytes:
        """Wait for end of file (the child exited), then return everything read."""
        self._thread.join(timeout)
        with self._changed:
            return bytes(self._data)


class WatchProcess:
    """``troupe config watch`` running in the background, for use as a context manager.

    ``theatre_arg`` is passed to ``--theatre`` exactly as given (``None`` omits the option);
    ``theatre`` is the theatre directory used for ``history`` polls (defaults to ``cwd /
    theatre_arg``); ``env`` replaces the child's environment when given. Closing the process
    (``close`` or leaving the ``with`` block) terminates and, if needed, kills the child, then
    records ``exit_code``, ``stdout`` and ``stderr``.
    """

    def __init__(
        self,
        theatre_arg: str | None,
        *,
        cwd: Path,
        theatre: Path | None = None,
        extra_args: Sequence[str] = (),
        env: Mapping[str, str] | None = None,
    ) -> None:
        if theatre is None:
            if theatre_arg is None:
                raise ValueError("theatre is required when theatre_arg is None")
            theatre = cwd / theatre_arg
        self.theatre = theatre
        self.cwd = cwd
        option = () if theatre_arg is None else ("--theatre", theatre_arg)
        self.argv = [str(troupe_executable()), "config", "watch", *option, *extra_args]
        self._process = subprocess.Popen(
            self.argv, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        assert self._process.stdout is not None and self._process.stderr is not None
        self._stdout = _PipeReader(self._process.stdout)
        self._stderr = _PipeReader(self._process.stderr)
        self.exit_code: int | None = None
        self.stdout = b""
        self.stderr = ""
        self._last_poll = float("-inf")

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    @property
    def pid(self) -> int:
        """The child's process ID."""
        return self._process.pid

    def wait_ready(self, timeout: float = READY_TIMEOUT_SECONDS) -> bytes:
        """Wait for the first stdout line and return it (with its newline).

        Fails the test (``AssertionError``) with the child's stderr when no line arrives in time
        or the child exits first.
        """
        data = self._stdout.wait_for(lambda data, closed: b"\n" in data or closed, timeout)
        if b"\n" not in data:
            stderr = self._stderr.wait_for(lambda _data, closed: closed, 0.5)
            raise AssertionError(
                f"no readiness line within {timeout} s (stdout {data!r}, "
                f"exit {self._process.poll()}, stderr {stderr.decode('utf-8', 'replace')!r})"
            )
        return data[: data.index(b"\n") + 1]

    def history(self) -> list[HistoryEvent]:
        """Run ``troupe config history`` on the theatre and return its lines' step-1 members.

        Each line is projected by :func:`support.step1_members` (lineage differs on every run).

        Never polls more often than every ``HISTORY_POLL_SECONDS``.
        """
        pause = self._last_poll + HISTORY_POLL_SECONDS - time.monotonic()
        if pause > 0:
            time.sleep(pause)
        self._last_poll = time.monotonic()
        result = invoke_troupe("config", "history", "--theatre", str(self.theatre), cwd=self.cwd)
        assert result.exit_code == 0, result.stderr
        return [step1_members(line) for line in result.stdout.decode("utf-8").splitlines()]

    def wait_for_history(
        self,
        predicate: Callable[[list[HistoryEvent]], bool],
        *,
        since: float,
        within: float = 2.0,
    ) -> list[HistoryEvent]:
        """Poll ``history`` until ``predicate`` holds; return the events that satisfied it.

        The deadline is ``since + within`` on the :func:`time.monotonic` clock (``since`` is
        typically taken just before the edit). Fails the test with the last events seen when the
        deadline passes; a poll that starts before the deadline still counts.
        """
        deadline = since + within
        while True:
            started = time.monotonic()
            events = self.history()
            if predicate(events):
                return events
            if started >= deadline or time.monotonic() >= deadline:
                raise AssertionError(
                    f"history did not match within {within} s; last events: {events!r}; "
                    f"watch stderr so far: {self.stderr_so_far()!r}"
                )

    def stderr_so_far(self) -> str:
        """What the child has written to stderr up to now."""
        return self._stderr.wait_for(lambda _data, _closed: True, 0).decode("utf-8", "replace")

    def send_signal(self, sig: signal.Signals) -> None:
        """Send ``sig`` to the child."""
        self._process.send_signal(sig)

    def wait(self, timeout: float = EXIT_TIMEOUT_SECONDS) -> int:
        """Wait for the child to exit on its own and collect its output; return its exit code.

        Fails the test when the child does not exit in time (``close`` then kills it).
        """
        try:
            self._process.wait(timeout)
        except subprocess.TimeoutExpired as error:
            raise AssertionError(f"watch did not exit within {timeout} s") from error
        return self._collect()

    def stop(self, sig: signal.Signals = signal.SIGINT) -> int:
        """Send ``sig`` and wait for the exit code."""
        self.send_signal(sig)
        return self.wait()

    def close(self) -> None:
        """Terminate the child (killing it if it does not exit in time) and collect its output."""
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(EXIT_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait()
        self._collect()

    def _collect(self) -> int:
        code = self._process.returncode
        assert code is not None
        self.exit_code = code
        self.stdout = self._stdout.finish(EXIT_TIMEOUT_SECONDS)
        self.stderr = self._stderr.finish(EXIT_TIMEOUT_SECONDS).decode("utf-8", "replace")
        for pipe in (self._process.stdout, self._process.stderr):
            if pipe is not None:
                pipe.close()
        return code
