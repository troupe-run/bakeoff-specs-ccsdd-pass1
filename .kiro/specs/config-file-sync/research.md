# Research & Gap Analysis: config-file-sync

## Gap Analysis (2026-10-02)

Requirements status at the time of analysis: generated, **not yet approved**. This analysis may still feed
revisions to them.

### 1. Current State

| Area | Asset | What it gives us | Notes |
|---|---|---|---|
| Opening a command | `config/theatre.py`: `open_theatre(directory) -> Theatre` (locate, then load, then `HistoryStore.open`, then `ensure_imported`). `Theatre` is a context manager that closes the store. | One place that every command passes through, so the natural hook for reconcile-first. | Used by `handle_show`, `handle_history` (`cli/main.py`) and `handle_set` (`cli/set_command.py`). `history` must opt out. |
| Loaded configuration | `load_config(config_file) -> ConfigDocument`. `document.files: tuple[SourceFile, ...]` holds the root, YAML includes and text includes, each with `path` (display) and `real_path`. | The exact watch set after each successful load. | A load failure raises `ConfigLoadError(file=...)`, which names the offending file. That covers a missing include target, a cycle, bad YAML and a missing or duplicated root. |
| Leaves | `resolve.flatten_leaves(root) -> tuple[Leaf, ...]` in document order, `Leaf.path: ConfigPath`, `Leaf.value: ScalarValue`. | The "files" side of reconcile. | Empty collections have no leaves (matches 2.4). |
| Typed comparison | `writeback/compare.scalar_equal`: same type and value, floats compared by `repr` (`nan == nan`, `0.0 != -0.0`). | Exactly the "differs" rule in the requirements. | It lives in `writeback/`. Reconcile in core would import from `writeback`, or the function moves to a shared module. |
| Leaf diff | `writeback/leaf_diff.diff_leaves(before: ConfigDocument, after: ConfigDocument)` gives `SetChange`s in after-order, then `RemoveChange`s in before-order. | The algorithm shape fits reconcile. | It takes two `ConfigDocument`s. Reconcile's "before" is the recorded configuration (a path to value map, not a document), and its remove order differs (2.6). It needs a sibling function or a generalisation over leaf sequences. |
| History store | `store.HistoryStore`: `open`, `is_imported`, `ensure_imported`, `record_changes(Sequence[LeafChange])` (one `save`, which is one SQLite transaction), `records()`, `close()`. | `record_changes` already records reconcile's `SetChange`/`RemoveChange` atomically (2.7). | Its docstring says "changes reported by one `set` invocation". Only the wording and traceability need to change. |
| Events | `history.ConfigHistory` with `LeafImported`, `LeafValueSet`, `LeafRemoved` and `to_history_record`. | Reconcile reuses `set`/`remove` with no new event types. | The aggregate holds no state, and its docstrings say set-only and "never reconciled". |
| Recorded configuration | **Missing.** | — | It can be derived by folding `records()`. Verified: typed values (`inf`, `nan`, `1.0`, `True`) round-trip through the store with the correct Python types, so the fold compares correctly with `scalar_equal`. |
| CLI | `cli/main.py` `SUBCOMMAND_REGISTRARS`, `run()` buffers stdout and writes it only on success; `cli/shell.py` `theatre_option_parent()` (`--theatre`, type `Path`, default `$TROUPE_THEATRE` or cwd). | Registration point for `watch`. | **Constraint**: the buffered handler model can't print the `watching` line and keep running. `watch` needs its own handler shape, or `run` needs a streaming escape. |
| Writes by `set` | `writeback/setter.set_value(theatre, path, value)`: resolve on `theatre.document`, plan, verify, diff, `write_patches` (atomic temp+rename, `.<name>.troupe-tmp-<16hex>`), then `record_changes`. | Unchanged. Once `open_theatre` has reconciled, `theatre.document` and the history agree, so `old` values stay correct. | Between `write_patches` and `record_changes` a watcher would see "new" file content. Without mutual exclusion that is a duplicate-event race (Requirement 6). |
| Concurrency | **Missing.** | — | Verified: two processes appending to the same aggregate cause the second `save` to fail with `IntegrityError (UNIQUE originator_id, originator_version)`. That surfaces as exit 4 and nothing is corrupted, but it breaks 6.3/6.4 and would make a `set` fail spuriously while `watch` runs. A lock is required. |
| Watching | **Missing.** No file-watching dependency; runtime deps are `eventsourcing` and `ruamel.yaml` only. | — | — |
| Signals | **Missing.** SIGTERM's default action kills the process (exit -15), and SIGINT raises `KeyboardInterrupt` (traceback, exit 1). | — | Both need handlers that lead to a clean exit 0 (4.6) with the store closed (7.3). |
| Test support | `tests/support.py` `invoke_troupe` (`subprocess.run` of the real script), `write_theatre`; `tests/conftest.py` `make_theatre`, `run_cli`. | Real-CLI test style to follow. | **Missing**: a helper for a background process (`Popen`, wait for the `watching` line with a timeout, poll `history` until the expected events appear or 2 s pass, send the signal, collect the exit code and stderr). |

Conventions to keep: modules hold one decision each (`constants.py`, `errors.py`). Errors carry the file and
path. `mypy --strict` and ruff apply. Docstrings cite requirement numbers. Tests are named after what they
prove. Commands open the theatre in a `with` block so the store is always closed.

### 2. Requirement-to-Asset Map

| Req | Need | Existing | Gap |
|---|---|---|---|
| 1.1–1.2, 1.6 | Reconcile in `show`/`set` before their own work; keep events on exit 3/5 | `open_theatre` | **Missing**: a reconcile step after `ensure_imported`, recorded before the handler's use case runs |
| 1.3 | `history` never reconciles | `open_theatre` | **Constraint**: an opt-out (a parameter, or a separate opener) |
| 1.4 | First command imports, with no reconcile events | `ensure_imported` returns `True` on import | Use that return value to skip reconcile (a reconcile after an import would find nothing anyway) |
| 1.5 | Usage or load errors record nothing | Handlers validate before opening, and loading happens before the store opens | Already true; needs tests only |
| 1.7 | Hand edit recorded before the `set`'s events | `set_value` records after writing | Holds if reconcile runs inside `open_theatre`. Needs the shared lock to cover the whole `set` |
| 2.1–2.5, 2.8 | Leaf diff, files against recorded | `flatten_leaves`, `scalar_equal`, `diff_leaves` (shape) | **Missing**: a recorded-configuration projection and a diff against it |
| 2.6 | `set`s in file order, then `remove`s ordered by the leaf's creation `seq` | Records carry `seq` and `old` | **Missing**: the fold must track each live leaf's creation `seq` (import, or a set that created a missing leaf) |
| 2.7 | Atomic | `record_changes` (one transaction) | Signal timing: see Research Needed R3 |
| 2.9 | Same `history` rendering | `format_history_line` | None |
| 3.1–3.3 | Includes added or removed by hand | The loader follows includes, and the diff covers leaves | None beyond reconcile |
| 3.4–3.5, 5.1–5.2, 5.6–5.7 | Watch the root and every included file at any depth; replace-by-rename; `.yml`/`.yaml` rename; watch set recomputed | `document.files` | **Missing**: the watch loop and change detection. **Unknown**: what to watch while the files cannot be loaded (R2) |
| 4.1 | Same theatre default | `theatre_option_parent()` | None |
| 4.2–4.4 | Import or reconcile, then the readiness line printed after observation is armed | — | **Missing**. With polling, "armed" means the baseline snapshot is taken before the startup reconcile and the line |
| 4.3 | `<dir>` as given | `--theatre` is `type=Path` | **Constraint/Unknown**: `str(Path(...))` normalises (`./t/` becomes `t`). For "as given" exactly, keep the raw string (R5) |
| 4.5 | Only the `watching` line on stdout | `run` buffers stdout | **Constraint**: `watch` must stream to stdout and keep everything else on stderr |
| 4.6 | SIGINT/SIGTERM give exit 0 with no traceback | — | **Missing** |
| 4.7–4.8 | Exit 2 or 4 at startup, with no line | `run` error mapping | Works if startup goes through the same `TroupeError` path before the line is printed |
| 4.9 | `watch` never writes user files | — | Holds by construction (reconcile only reads files) |
| 5.3 | Load errors while running: report, keep going, retry | `ConfigLoadError` messages | **Missing**: an error loop; de-duplicate repeated messages (R4) |
| 5.4 | Store errors while running: report, keep going, retry | `ConfigLoadError` from the store | **Missing**: the retry rule (retry on the next poll even without a file change, otherwise a lost edit would wait for the next edit) |
| 5.5 | Net difference allowed | Diff against the recorded state is naturally net | None |
| 6.1–6.4 | Exactly-once with concurrent `set`/`show`/`watch`; no interleaving | — | **Missing**: an inter-process lock held over reconcile+record (all commands) and over the whole `set` (reconcile, write, record) |
| 7.1–7.3 | No extra files; store closed | `HistoryStore.close` removes the WAL side files on the last close | **Constraint**: the lock must leave no file. If `watch` keeps a connection open, other commands' `-wal`/`-shm` files persist while it runs, so open the store per reconcile inside the lock |

Existing tests that encode the superseded step 1 behaviour and must change in the same change as the code
(collaboration.md):
- `tests/test_e2e_history.py::test_hand_edits_add_no_events_while_show_reflects_them`
- `tests/test_e2e_show.py::test_show_after_hand_edit_prints_new_value_and_records_no_event`
- `tests/test_theatre.py::test_hand_edit_after_import_shows_in_show_but_adds_no_history`
- `tests/test_set_history.py::test_old_value_is_the_hand_edited_value_not_the_imported_one`. The `set` event's
  `old` is still 5, but a reconcile event now comes before it.
- `tests/writeback/test_setter.py`: the same idea. Check whether it goes through `open_theatre`.
- Keep `tests/test_store.py::test_second_import_records_nothing_and_never_reconciles`. It is still true at
  store level (`ensure_imported` must stay a pure import), but its name and docstring may need clarifying.
- The `history` behaviour (`tests/test_e2e_history.py`) gains a test: a hand edit followed by `history` records
  nothing, and a following `show` records the reconcile.

### 3. Implementation Approach Options

#### Option A: Extend existing components
- `store.HistoryStore`: add `recorded_leaves()` (the fold) and reuse `record_changes`.
- `theatre.open_theatre(directory, *, reconcile=True)`: after `ensure_imported`, diff and record. `history`
  passes `reconcile=False`.
- `writeback/leaf_diff.py`: generalise `diff_leaves` to take leaf sequences, used by both `set` and reconcile.
- `cli/main.py`: add `handle_watch` with the loop inline. Lock and signals live in `theatre.py`/`main.py`.
- ✅ Few new files; reconcile-first is automatic for every command that already uses `open_theatre`.
- ❌ `main.py` and `theatre.py` take on long-running-process concerns (loop, signals, streaming stdout). Core
  would import from `writeback` (layering inversion). `diff_leaves` would carry two ordering rules.

#### Option B: New components
- New `config/recorded.py` (fold history records into ordered recorded leaves with creation `seq`), new
  `config/reconcile.py` (`reconcile(document, store) -> tuple[LeafChange, ...]`), new `config/lock.py`
  (inter-process theatre lock), new `config/watch.py` (watch set, snapshots, poll loop, error tolerance), new
  `cli/watch_command.py` (registrar, streaming handler, signals).
- `open_theatre` itself stays untouched; callers compose `lock`, `open`, then `reconcile`.
- ✅ Each concern is testable alone; it follows the "one decision, one place" rule.
- ❌ Every handler must remember to take the lock and reconcile, so it is easy to miss one (Requirement 1 says
  *every* command except `history`, so a future command could silently skip it).

#### Option C: Hybrid (likely best fit)
- **New**: `recorded.py` (projection), `reconcile.py` (diff and record), `lock.py` (theatre lock), `watch.py`
  (loop and observation), `cli/watch_command.py` (registrar and handler, with its own stdout and signal
  handling).
- **Extend**: `open_theatre` gains the lock and the reconcile step, with an explicit opt-out used only by
  `history`. Commands stay safe by default and `history` stays reconcile-free. The `Theatre` context manager
  releases the lock after closing the store. `scalar_equal` moves (or is re-exported) to a core module so
  `reconcile.py` does not depend on `writeback/`. `diff_leaves` stays as it is for `set`, and reconcile has
  its own small diff with the 2.6 ordering.
- `watch` reuses the same opener for each reconcile (lock, load, open store, reconcile, close), so there is one
  code path for "reconcile" across all commands.
- ✅ Safe default and clean seams; `set`'s pipeline is untouched apart from running inside the lock.
- ❌ The lock is held for the whole `with` block, so a slow command blocks `watch`. Acceptable, because
  configurations are small and the 2 s budget is per change.

### 4. Research Needed (carry to design)
- **R1 Lock primitive.** `fcntl.flock` on an fd of the `.troupe/` directory, which works on Linux and macOS
  (verified that directory flock succeeds on Linux) and creates no file, versus flock on `history.sqlite`. The
  latter interacts with SQLite's own locking, and the file does not exist before the first import. Decide on
  blocking with or without a timeout, and what happens when the lock cannot be taken (exit 4 naming `.troupe/`?).
  Check NFS caveats (out of scope?).
- **R2 Observation mechanism.** Options:
  1. Snapshot the bytes of the watched paths and reconcile on difference. The watch set is `document.files`
     display paths, plus both root candidates, plus the file named by the last load error. A missing file is
     a distinct state.
  2. Reconcile under the lock on every poll tick, which is simplest and catches everything, at the cost of
     store churn and repeated error messages.
  3. Native notifications (`watchfiles`/`watchdog`). Rejected at discovery, but confirm.

  Also decide the poll interval (around 200–500 ms against the 2 s budget, including lock wait and reconcile
  time) and whether to re-reconcile periodically to cover 5.4 retries. Which files are watched when the
  initial load succeeds but a later edit introduces an include whose target is missing: the error's `file`
  attribute must name the missing target, so check what the loader reports.
- **R3 Signal semantics.** Set a flag in the handlers and check it between ticks, or raise into the loop.
  Make sure a signal during `record_changes` cannot leave a half-written transaction. SQLite rolls back an
  uncommitted transaction, but `KeyboardInterrupt` inside eventsourcing's `save` must not escape as a
  traceback. Install the handlers before the `watching` line.
- **R4 Error reporting while running.** Use the step 1 message format (`format_message`). Report once per
  distinct error, not on every tick. Decide whether a recovery is reported (requirements say nothing, so
  report nothing).
- **R5 `<dir>` text.** `--theatre` is parsed as `Path`. "As given" exactly needs the raw argument string.
  When defaulted, print `$TROUPE_THEATRE` as set or the cwd. Confirm the PO intent: tests most likely pass an
  absolute path, which is identical either way.
- **R6 Removal order.** The fold must yield, for each live leaf, the `seq` of its creating event (an import,
  or a `set` whose leaf was absent before; `old` null alone is not enough because a leaf can exist with value
  null).
- **R7 Testing timing.** Test the 2 s bound by polling `history` with a deadline. Keep the tests robust under
  `pytest -n auto` load: use generous waits for "nothing happens" assertions (for example wait 2.5 s and
  assert no events). Add a background-process fixture that always kills the child.
- **R8 Store lifetime in `watch`.** Opening per reconcile (inside the lock) versus keeping the store open.
  Opening per reconcile satisfies 7.2/7.3 while other commands run and avoids stale application caches.

### 5. Effort and Risk
- **Effort: M (3–7 days).** The reconcile core is small and reuses existing pieces. Most of the work is in the
  `watch` process (observation, readiness, signals, error tolerance), the lock, and the reliable timing-based
  subprocess tests, plus updating the superseded step 1 tests.
- **Risk: Medium.** The ingredients are familiar (stdlib, the existing store), but cross-process concurrency,
  signal handling and timing tests are classic sources of flakiness. The verified `IntegrityError` on
  concurrent appends shows the lock is not optional.

### 6. Recommendations for Design
- Prefer **Option C**. New `recorded`, `reconcile`, `lock`, `watch` and `cli/watch_command` modules.
  `open_theatre` takes the lock and reconciles by default, with an explicit opt-out for `history` only.
- Decide R1 (lock), R2 (observation) and R3 (signals) first; they shape everything else.
- Keep `set`'s pipeline unchanged apart from running inside the lock that `open_theatre` takes.
- Record the requirement choices made where the contract is silent (5.3, 5.4, 5.5, 4.3, 2.6) as decision
  records here, in the collaboration.md format.

---

## Design Discovery & Decisions (2026-10-02)

### Summary
- **Feature**: `config-file-sync`
- **Discovery Scope**: Extension (light discovery). No new external dependency; integration-focused.
- **Key Findings**:
  - Concurrent appends from two processes fail with `IntegrityError`, so a theatre lock is required for 6.x
    and for the `set` write-back window.
  - The loader exposes every file read (`ConfigDocument.files`, display `path` + exact `text`), so the watch set
    comes for free after each successful load.
  - Typed values round-trip through the store, so a fold of `records()` gives a recorded configuration that
    compares correctly with `scalar_equal`.
- **Skills consulted**: kiro-spec-design rules (principles, light discovery, synthesis, review gate). No other
  domain skill applies (CLI and data, no UI).

### Decision: Reconcile runs inside `open_theatre` (opt-out for `history`)
- **Context**: 1.1–1.5 require every command except `history` to reconcile first; `watch` must reconcile the same
  way.
- **Alternatives**: (1) each handler calls reconcile explicitly; (2) a separate opener per mode;
  (3) a default-on flag in `open_theatre`.
- **Selected**: (3). `open_theatre(directory, *, reconcile=True)`; only `handle_history` passes `False`.
  `watch` syncs are `with open_theatre(dir)` blocks.
- **Rationale**: safe by default, so a new command cannot forget to reconcile, and there is one code path for
  every reconcile.
- **Revisit when**: a command appears that must neither import nor reconcile.

### Decision: Diff against the recorded configuration, not against previous file contents
- **Alternatives**: remember what troupe wrote (hashes) and ignore it; diff against the previous snapshot.
- **Selected**: fold the history into `path → (value, created_seq)` and diff the files' leaves against it.
- **Rationale**: idempotent. It picks up offline edits and naturally ignores a `set`'s write-back (6.1) once the
  set has recorded it. Repeated or overlapping reconciles are harmless (5.5, 6.3).

### Decision: Theatre lock = `fcntl.flock` on the `.troupe/` directory fd
- **Alternatives**: (1) a lock file `.troupe/lock`, which leaves an extra file and violates decision E (7.2);
  (2) flock on `history.sqlite`, which does not exist before the first import and mixes with SQLite's own
  locking; (3) rely on eventsourcing's version conflict and retry, which still lets `watch` record a `set`'s
  write-back between write and record.
- **Selected**: blocking exclusive flock on an `O_RDONLY` fd of the directory, held from after locate until
  the theatre is closed, and taken by every command including `history` (its import also writes).
- **Rationale**: no file is created, the lock is released by the kernel if a process dies, and it is supported
  on Linux and macOS (verified on Linux).
- **Trade-offs**: blocking with no timeout, so a hung troupe process blocks others; acceptable for small
  configurations. Not reentrant, so a nested acquire in one process raises instead of deadlocking.
- **Revisit when**: NFS-hosted theatres or Windows come into scope.

### Decision: Observe by polling byte snapshots (stdlib), 0.2 s tick
- **Alternatives**: watchfiles/watchdog (native notifications); reconcile unconditionally on every tick.
- **Selected**: snapshot `{path: bytes | None}` for the watch set (the display paths of `document.files` plus
  both root names); sync when the snapshot differs from the baseline or a retry is pending.
- **Rationale**: no dependency. Rename-safe, because files are read by path. Readiness is provable: the
  baseline is taken before the load it describes, so no change after the line is missed. A sync repeats until
  the watch set is stable, which places a newly added include under the baseline. 0.2 s plus a sync is well
  inside 2 s.
- **Trade-offs**: a few small file reads per tick; repeated loads while the files are broken (one per tick).

### Decision: Failures while watching are retried every tick and reported once per distinct message
- Covers 5.3 (load) and 5.4 (store) with one rule. Retrying every tick, rather than only on change, means a
  missing include target that appears, or a store that becomes readable, is picked up without needing to know
  which file to watch. Deduplicating the message keeps stderr readable. No events are recorded on failure, so a
  broken file is never read as "removed".

### Decision: Signals only set a flag
- **Alternatives**: let SIGINT raise `KeyboardInterrupt`, or raise from a SIGTERM handler.
- **Selected**: handlers set a flag; the loop checks it between ticks and while sleeping. A sync in progress
  completes (one transaction), the `with` block closes the store and releases the lock, and the handler returns 0.
- **Rationale**: 2.7 (never partial) and 4.6 (exit 0, no traceback) hold without reasoning about exceptions
  inside eventsourcing/SQLite code. Stop latency is at most one tick plus the current sync.

### Decision: `<dir>` is the raw `--theatre` text (4.3)
- `--theatre` is parsed into a `Path`, which normalises (`./t/` becomes `t`). A custom argparse action also
  stores `theatre_text`: the argument as typed, else `$TROUPE_THEATRE` as set, else `str(Path.cwd())`.

### Decision: `scalar_equal` moves to `troupe.config.scalars`
- Lets core reconcile compare values exactly as `set` does without importing `writeback` (dependency direction).
  `writeback/compare.py` re-imports it, so there is no behaviour change.

### Decision: Remove-event order by creation seq (2.6)
- The files no longer contain removed leaves, so "previous document order" (as `set` uses) is unavailable. The
  creation `seq` of the live leaf is the closest stable equivalent: imported leaves keep import (document)
  order, and leaves added later follow in the order they were added.

### Synthesis outcomes
- **Generalisation**: the step 1 "import" and the step 2 "reconcile" are both "make the history match the files
  at open". They stay separate operations (import is not reconcile for `history`), but they share one opener.
- **Build vs adopt**: adopt the stdlib (`fcntl`, `signal`, polling) over watchfiles/watchdog; reuse the existing
  store, events, loader and `scalar_equal`. Build only the projection, the diff, the lock and the loop.
- **Simplification**: no separate "self-write suppression" component, because the idempotent diff plus the lock
  covers it. One retry rule for load and store failures. No persisted projection (computed per sync).

### Risks & Mitigations
- Flaky timing tests under `pytest -n auto`: use deadline polling for positive checks, 2.5 s waits for negative
  ones, and a helper that always kills the child.
- A nested `open_theatre` in tests would deadlock: the lock raises on a nested acquire, and the existing tests
  open sequentially (checked).
- A lock held by a crashed process: impossible with flock, which the kernel releases on exit.
