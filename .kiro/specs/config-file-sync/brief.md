# Brief: config-file-sync

## Problem
Developers edit `.troupe/` files directly, in their editor, while troupe is running. Today those edits never
reach the history: step 1 records an import once and then never reconciles ("importing is not reconciling").
So `history` silently misses every hand edit, and nothing notices edits as they happen.

## Current State
- config-foundation: `open_theatre` loads the files (`load_config` → `ConfigDocument`, whose `files` lists the
  root, YAML includes and text includes), opens `HistoryStore` (`<theatre>/.troupe/history.sqlite`), and imports
  once (`ensure_imported`). The `ConfigHistory` aggregate holds no derived leaf state; there is no projection of
  "the configuration as recorded". `flatten_leaves` gives leaves in document order; `scalar_equal` compares typed
  values.
- config-set-writeback: `set_value` diffs before/after documents with `diff_leaves` (sets in after-order, then
  removes in before-order), writes files atomically (`write_atomically`: hidden `.<name>.troupe-tmp-<hex>` temp
  file next to the target, then `os.replace`), then records events via `HistoryStore.record_changes`.
- CLI: subcommands registered through `SUBCOMMAND_REGISTRARS` in `cli/main.py`; handlers write into a buffer
  that is flushed only on success. This does not fit a long-running foreground command.
- No locking between processes. No file-watching dependency (runtime deps: eventsourcing, ruamel.yaml).
- Gap: no reconcile, no `watch`, nothing that stops a `set`'s write-back being seen as a new change.

## Desired Outcome
The step 2 contract section "Sync with files" holds:
- `troupe config watch --theatre <dir>` runs in the foreground until SIGINT or SIGTERM, then exits 0. Once it can
  observe changes, it prints exactly `watching <dir>\n` to stdout and flushes. Any change made after that
  line, to the config file or to an included file, is reconciled within 2 seconds.
- **Reconcile** compares the files with the configuration as recorded. It emits one `set` or `remove` event for
  each leaf that differs. An edit that only changes formatting or comments emits nothing.
- Every other command except `history` (`show`, `set`, `watch` at startup) reconciles first, so edits made while
  nothing was running are picked up. `history` never reconciles. It still imports a theatre that has never been
  imported (step 1), because importing is not reconciling.
- A `set` while `watch` runs emits exactly the `set`'s own events. Its write-back produces no further events.
- An include added by a hand edit is followed: its leaves appear as `set` events, and its files join the watch set.

## Approach
Chosen: **diff against the recorded configuration, poll with the standard library, and serialise with a lock.**
- *Recorded configuration*: a projection that folds the history (import/set assign `path → new`, remove deletes)
  into ordered leaves. Reconcile = load the files, `flatten_leaves`, diff with the recorded leaves using the same
  typed comparison as `set`, and record the differences in one transaction. Because the comparison is with what
  was recorded, not with the previous file contents, reconcile is idempotent. A `set` records its own changes, so
  a later reconcile of its write-back finds nothing. That is how write-back avoids producing events, without any
  "ignore my own write" bookkeeping.
- *Mutual exclusion*: one exclusive inter-process lock covers each critical section. For `set` that is
  reconcile, write and record; for `watch` it is each reconcile. This stops `watch` from reconciling between a
  `set`'s file write and its event record, which would duplicate events. The lock must not leave any extra file
  behind (PO decision E). Candidates are `fcntl.flock` on the `.troupe/` directory, or on the existing history
  store file. Directory `flock` works on Linux and macOS.
- *Observation*: poll the watched files every few hundred ms, comparing content (bytes, or existence plus a
  hash), not just mtime. That copes with editors that save by renaming, coarse mtime granularity and
  same-second edits. The watch set is `{f.real_path for f in document.files}` plus the config file candidates
  (`troupe.yml` and `troupe.yaml`), recomputed after every successful load so new includes are followed. The
  baseline snapshot is taken *before* `watching <dir>` is printed, which keeps the promise in that line.
  Changes to troupe's own temp files and to `history.sqlite*` are ignored.

Rejected alternatives:
- *watchfiles/watchdog (native OS notifications)*: lower latency, but adds a dependency. It also needs
  directory-level watches for includes anywhere, and FSEvents coalescing and readiness timing make the
  "printed only after able to observe" promise harder to guarantee. 2 s is easy to meet with polling, and
  configurations are small (no performance targets).
- *Suppress self-writes by remembering what troupe wrote (hash or mtime of written content)*: fragile under
  races and when the user edits the same file right after. Diffing against the recorded configuration makes it
  unnecessary.
- *Diff against the previous file snapshot instead of the history*: misses edits made while nothing ran, and
  double-counts a `set`'s write-back.

## Scope
- **In**: a projection of recorded leaves; reconcile (diff, event order, one transaction); reconcile-first in
  `show`, `set` and `watch` startup; `history` stays reconcile-free; the `watch` subcommand (argument parsing,
  readiness line, polling loop, include following, SIGINT/SIGTERM → exit 0, store opened and closed per
  reconcile); the inter-process lock shared by `set` and `watch`; updating the step-1 tests that assert "hand
  edits add no events".
- **Out**: changing `set`'s rendering and write-back rules; new event types (reconcile reuses `set`/`remove`);
  printing events from `watch` (the contract asks only for the `watching` line); Windows; anchors/aliases
  (step 6); performance tuning.

## Boundary Candidates
- Recorded-configuration projection (history → ordered leaves): store/history layer.
- Reconcile use case (files vs recorded → `LeafChange`s → record), reusable by every command.
- Command opening policy: `open_theatre` gets a reconcile step that `history` opts out of.
- Inter-process lock around reconcile and set.
- Watch loop: watch-set computation, polling and change detection, readiness, signal handling, tolerance of
  load errors.
- CLI `watch` subcommand: streaming stdout (not the buffered handler model).

## Out of Boundary
- How a `set` value is rendered into files (config-set-writeback).
- Loading, includes, path semantics and error/exit-code conventions (config-foundation; reused unchanged).
- `history` output format.

## Upstream / Downstream
- **Upstream**: config-foundation (`open_theatre`, `load_config`, `ConfigDocument.files`, `flatten_leaves`,
  `HistoryStore`, `ConfigHistory` events, errors); config-set-writeback (`diff_leaves` / `scalar_equal`
  comparison semantics, `set_value`, `write_atomically` temp-file naming).
- **Downstream**: later steps that change authority or add commands. Each new command must decide whether it
  reconciles first.

## Existing Spec Touchpoints
- **Extends**: config-foundation. 6.9 ("record no history event for the hand edit") and 7.3 ("shall not
  record events for differences between the files and the history") are superseded for every command except
  `history`. Decision A ("files are the truth": show/set read the files) still holds. config-set-writeback:
  15.1 stays true as worded, but its example changes. After a hand edit `a: 1 → 5`, `set a 7` is now preceded
  by a reconcile event `set a 1→5`, then `set a 5→7`. Tests to update include
  `tests/test_e2e_history.py` (the "importing is not reconciling" hand-edit test),
  `tests/test_set_history.py::test_old_value_is_the_hand_edited_value_not_the_imported_one` and
  `tests/writeback/test_setter.py` (same idea). Also check `tests/test_store.py` (second import records nothing,
  which still holds) and the `tests/test_e2e_show.py` docstring.
- **Adjacent**: config-set-writeback (`set` runs inside the same lock, after reconcile).

## Constraints
- Python 3.12+, uv, `eventsourcing`, stdlib only for watching unless the design justifies a dependency.
  `mypy --strict`, ruff, real-file and real-CLI tests. `watch` tests use `subprocess.Popen`, wait for the
  `watching` line, edit files, poll `history` (which never reconciles) until events appear within 2 s, then send
  SIGINT/SIGTERM and assert exit 0.
- State only under `<theatre>/.troupe/`; no extra file may remain after any command (PO decision E). Store
  closed at command end. For `watch`, open the store per reconcile so it is not held for the process lifetime.
- Each reconcile is recorded atomically (one save): all of its events or none, with consecutive `seq`. Proposed
  order, matching `set`: `set` events in the files' document order, then `remove` events in recorded order.
- Open choices to settle and record in requirements/research (conservative defaults proposed):
  1. *Unloadable files during `watch`* (half-saved YAML, missing include, config file deleted): record nothing,
     report on stderr naming the file, keep watching, and reconcile once the files load again. Never treat
     "cannot load" as "all leaves removed". At startup, and for other commands, the step-1 load errors (exit 4)
     apply unchanged.
  2. *`<dir>` in the `watching` line*: the theatre directory as given by `--theatre` (or the
     `TROUPE_THEATRE`/cwd default).
  3. *`watch` on a never-imported theatre*: import first. That is the step-1 rule for any first command. Reconcile
     after an import finds nothing.
  4. *A signal during a reconcile*: finish or abandon the single transaction (never partial), close the store,
     exit 0.
  5. *`watch` stdout*: only the `watching` line. Diagnostics go to stderr.
  6. *A reconcile failure inside `set`/`show`* (store unreadable): exit 4 naming the store, as in step 1.
