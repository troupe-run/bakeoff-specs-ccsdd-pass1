# Requirements Document

## Project Description (Input)
### Problem
Developers edit `.troupe/` files directly, in their editor, while troupe is running. Today those edits never
reach the history: step 1 records an import once and then never reconciles ("importing is not reconciling").
So `history` silently misses every hand edit, and nothing notices edits as they happen.

### Current State
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

### Desired Outcome
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

### Contract: Sync with files (step 2, verbatim)
- `troupe config watch --theatre <dir>` runs in the foreground until SIGINT or SIGTERM (then exits 0). Once it
  is watching, it prints one line `watching <dir>` to stdout and flushes. The line is a promise: it is printed
  only after `watch` is able to observe changes. Within 2 seconds of any change to the config file or an
  included file made after that line appears, it reconciles.
- **Reconcile:** compare the files with the configuration and emit a `set`/`remove` event for each leaf that
  differs. A formatting-only or comment-only edit emits nothing.
- Every other command except `history` reconciles first, so edits made while nothing was running are picked
  up. `history` never reconciles: it reports what has been recorded.
- A `set` while `watch` runs emits exactly the `set`'s own events; its write-back produces no further events.
- An include added by a hand edit is followed; its leaves appear as `set` events.

See `brief.md` for the chosen approach, scope, boundaries and open choices.

## Requirements
<!-- Will be generated in /kiro-spec-requirements phase -->


