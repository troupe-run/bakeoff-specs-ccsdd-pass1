"""The watcher loop behind ``troupe config watch``.

config-file-sync 3.4, 3.5, 4.2, 4.4, 4.9, 5.1-5.7.

The watcher observes the theatre by polling byte snapshots, without any file-watching dependency:

- The **watch set** is the display path of every file the last successful load read (the root
  configuration file and every YAML or text include, scalar or mapping form, at any depth;
  config-include-overrides 3.5), plus both root candidates
  ``<theatre>/.troupe/troupe.yml`` and ``troupe.yaml``, so an added or renamed root is seen (5.2),
  plus the schema file ``<theatre>/.troupe/troupe.schema.json``, present or absent
  (config-remote-schema 3.4). A remote source (one with a URL) is never in the watch set: no URL
  is polled, and every reload fetches remote content again (config-remote-schema 3.4, 3.5).
  Files outside the watch set are never read (5.6).
- A **snapshot** maps each watched path to its bytes, read by path so that a replace-by-rename is
  seen (5.2), or to ``None`` when the file is missing or unreadable. A touch leaves the bytes
  unchanged and is therefore not a change (5.7).
- A **sync** repeats "snapshot the watch set, open the theatre (which imports or reconciles), then
  recompute the watch set from the loaded files" until the watch set is stable, and then sets the
  baseline. The baseline holds, for every file the load read, the bytes the load actually read, and
  for the other watched paths (such as the absent root candidate) the snapshot taken *before* the
  load. Any write made after the snapshot therefore differs from the baseline at the next tick, so
  nothing is missed (4.4, 5.1); and a transient state the load happened to read (a file truncated
  mid-save) is never mistaken for the file's settled content, so a file that returns to its earlier
  bytes is reconciled again (5.5, 5.7). A newly included file enlarges the watch set and forces
  one more round that puts it under the baseline (3.4); a removed include's file leaves the watch
  set and is never read again (3.5).
- The **loop** syncs whenever the snapshot differs from the baseline or a retry is pending. A
  :class:`TroupeError` (a load failure, 5.3, or a history store failure, 5.4) is reported once per
  distinct message and retried on every tick; nothing is recorded until a sync succeeds, because
  ``open_theatre`` records only after a successful load, in one transaction. Between ticks the loop
  sleeps in short steps that check the stop flag.

The watcher only reads user files; it never creates, modifies, renames or deletes one (4.9).
"""

import time
from collections.abc import Callable, Iterable
from pathlib import Path

from troupe.config.constants import (
    CONFIG_DIR_NAME,
    CONFIG_FILE_NAMES,
    SCHEMA_FILE_NAME,
    WATCH_POLL_SECONDS,
)
from troupe.config.errors import TroupeError
from troupe.config.model import ConfigDocument
from troupe.config.theatre import open_theatre

Snapshot = dict[Path, bytes | None]

_STOP_CHECK_SECONDS = 0.05
"""Longest single sleep between two checks of the stop flag."""


class Watcher:
    """Keep a theatre's history reconciled with its files until a stop is requested."""

    def __init__(
        self,
        directory: Path,
        *,
        report: Callable[[str], None],
        stop_requested: Callable[[], bool],
        poll_seconds: float = WATCH_POLL_SECONDS,
    ) -> None:
        self._directory = directory
        self._report = report
        self._stop_requested = stop_requested
        self._poll_seconds = poll_seconds
        self._root_candidates = frozenset(
            directory / CONFIG_DIR_NAME / name for name in CONFIG_FILE_NAMES
        )
        self._always_watched = self._root_candidates | {
            directory / CONFIG_DIR_NAME / SCHEMA_FILE_NAME
        }
        self.watch_set: frozenset[Path] = self._always_watched
        self.baseline: Snapshot = {}
        self.pending = False
        self.last_report: str | None = None

    def start(self) -> None:
        """Perform the first sync (import or reconcile); raise any :class:`TroupeError` (4.2, 4.8).

        Once this returns, any change to the watched files is observed by :meth:`run` (4.4).
        """
        self._sync()

    def run(self) -> None:
        """Sync on every change or pending retry until a stop is requested; never raise a
        :class:`TroupeError` (5.3, 5.4)."""
        while not self._stop_requested():
            if self.pending or self._snapshot(self.watch_set) != self.baseline:
                try:
                    self._sync()
                except TroupeError as error:
                    self._failed(str(error))
            self._sleep()

    def _sync(self) -> None:
        """Reconcile; the baseline is the bytes the load read, else the snapshot taken before it."""
        self.pending = True
        while True:
            snapshot = self._snapshot(self.watch_set)
            with open_theatre(self._directory) as theatre:
                document = theatre.document
            watch_set = self._watch_paths(document)
            if watch_set == self.watch_set:
                self.baseline = snapshot | _loaded_bytes(document)
                self.pending = False
                self.last_report = None
                return
            self.watch_set = watch_set
            if self._stop_requested():
                return  # still pending; the loop ends without another round

    def _failed(self, message: str) -> None:
        self.pending = True
        if message != self.last_report:
            self._report(message)
            self.last_report = message

    def _watch_paths(self, document: ConfigDocument) -> frozenset[Path]:
        local = frozenset(source.path for source in document.files if source.url is None)
        return local | self._always_watched

    def _sleep(self) -> None:
        deadline = time.monotonic() + self._poll_seconds
        while not self._stop_requested():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(remaining, _STOP_CHECK_SECONDS))

    @staticmethod
    def _snapshot(paths: Iterable[Path]) -> Snapshot:
        return {path: _read_bytes(path) for path in paths}


def _loaded_bytes(document: ConfigDocument) -> Snapshot:
    """The bytes each loaded local file, and the schema file, held when the load read it.

    The loader decodes strictly as UTF-8 without newline translation and keeps a BOM, so encoding
    the text gives back exactly the bytes read. Remote sources have no bytes on disk to compare.
    """
    loaded: Snapshot = {
        source.path: source.text.encode("utf-8") for source in document.files if source.url is None
    }
    if document.schema is not None:
        loaded[document.schema.path] = document.schema.text.encode("utf-8")
    return loaded


def _read_bytes(path: Path) -> bytes | None:
    """The file's bytes, or ``None`` when it is missing or cannot be read."""
    try:
        return path.read_bytes()
    except OSError:
        return None
