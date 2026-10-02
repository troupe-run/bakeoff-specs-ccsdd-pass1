# Implementation Plan

**Conventions.** Every test written for this spec names in its docstring the requirement IDs it proves (for
example `"""Req 2.6: ..."""`), as in the step 1 specs. Tests use real theatres on disk and the real CLI
(`testing.md`). "Recorded configuration" and "reconcile" are used as defined in requirements.md. A timing
assertion that something *happens* polls `troupe config history` with a 2 s deadline measured from the edit. An
assertion that *nothing* happens waits 2.5 s and then checks. `history` never reconciles, so it is a safe
observer.

A watch test polls `history` no more often than every 100 ms, because each poll briefly takes the theatre lock.

**External prerequisites.** config-foundation and config-set-writeback are complete (all their tasks `[x]`). No
new runtime dependency is added.

- [x] 1. Foundation: shared comparison and constants
- [x] 1.1 Make typed value comparison and the watch constants available to core code
  - Move the typed scalar comparison (same type and value, floats by `repr`) from the write-back package into the core scalar-typing module, and keep it importable from its old location so `set` behaves identically
  - Add the watch poll interval (0.2 s) and the readiness line prefix as single-place constants
  - Done when the existing comparison tests pass unchanged against the new home, a new test imports the comparison from the core module and proves `1`, `1.0`, `True` and `"1"` are pairwise different and `nan` equals `nan`, no core module imports the write-back package, and `uv run pytest -n auto`, `uv run mypy --strict .` and ruff (lint and format) pass
  - _Requirements: 2.5_

- [ ] 2. Core: recorded configuration, lock and reconcile
- [x] 2.1 (P) Fold the history into the recorded configuration
  - Fold history records in `seq` order: an import creates a leaf; a set either creates a leaf (when its path is absent from the fold) or updates its value and keeps its creation `seq`; a remove deletes the leaf
  - Decide "absent" by the path's presence in the fold, never by `old` being null (a leaf can hold null)
  - Return the live leaves ordered by creation `seq`, oldest first
  - Done when unit tests on records read from a real store prove import → set → remove → set again gives the right live leaves, values and a creation `seq` that follows re-creation; that a set on a null-valued leaf is not a creation; and that `inf`, `nan`, `1.0` and `True` keep their types
  - _Boundary: RecordedConfiguration_
  - _Requirements: 2.1, 2.2, 2.3, 2.6_

- [ ] 2.2 (P) Provide the exclusive per-theatre lock
  - Take a blocking exclusive lock on the theatre's existing `.troupe/` directory itself, so no file is created; release on request and on process exit
  - Refuse a second acquire for the same directory within one process with a programming error rather than deadlocking; report an OS failure while locking as a load error naming the `.troupe/` directory
  - Done when tests prove a child process trying to take the lock waits until the parent releases it (observed through timestamps), the directory listing of `.troupe/` is identical before, during and after holding the lock, a nested acquire raises, release is idempotent, and a missing directory gives a load error naming it
  - _Boundary: TheatreLock_
  - _Requirements: 6.4, 7.1, 7.2_

- [ ] 2.3 Compute and record the reconcile changes
  - Diff the loaded files' leaves against the recorded configuration with the typed comparison:
    - a changed leaf gives a `set` (old = recorded value, new = file value);
    - a leaf missing from the record gives a `set` with old null;
    - a recorded leaf missing from the files gives a `remove` (old = recorded value)
  - Order the batch with `set` changes in the files' document order, then `remove` changes in creation-`seq` order; record it with the store's existing one-transaction append, and record nothing for an empty diff
  - Never read history into or write to user files; never import the write-back or CLI packages
  - Done when tests on real loaded theatres prove the exact change list and order for changed, created, removed and type-changed leaves; prove an empty list for formatting-only, comment-only, quoting-only, key-order-only and inline-versus-include rewrites, and for `{}`↔`[]`; prove one `set` for a text-include content change; prove a hand-added YAML include yields a `set` per contributed leaf and a removed include a `remove` per vanished leaf; and prove the recorded configuration equals the files' leaves after recording
  - _Depends: 1.1, 2.1_
  - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 3.1, 3.2, 3.3, 5.5_

- [ ] 3. Integration: reconcile first in every command except history
- [ ] 3.1 Open every theatre under the lock, reconcile by default, and keep history reconcile-free
  - Order the opening as: locate the config file, take the lock on its directory, load, open the store, import when never imported, otherwise reconcile unless the caller opted out
  - A theatre that was just imported records no reconcile events in the same command
  - On close, close the store and then release the lock; on any failure while opening, release what was acquired in reverse order and re-raise the original error; a load failure records nothing
  - Keep the configuration the command works on as the files read from disk (decision A); reconcile does not change it
  - Make `history` open its theatre with the reconcile opt-out in the same change, so no intermediate state has `history` reconciling; it still imports a never-imported theatre
  - In the same change, rewrite the step 1 tests that asserted "hand edits add no events" to the step 2 behaviour: `tests/test_e2e_history.py::test_hand_edits_add_no_events_while_show_reflects_them`, `tests/test_e2e_show.py::test_show_after_hand_edit_prints_new_value_and_records_no_event`, `tests/test_theatre.py::test_hand_edit_after_import_shows_in_show_but_adds_no_history`, `tests/test_set_history.py::test_old_value_is_the_hand_edited_value_not_the_imported_one` and `tests/writeback/test_setter.py::test_old_values_come_from_the_files_on_disk_not_the_history`
    - `show` after a hand edit now records one reconcile `set`
    - `set` after a hand edit now records the reconcile `set` before the set's own `set`, with `old` chained
    - `history` after a hand edit still records nothing
  - Done when:
    - tests prove reconcile runs only when the theatre was already imported and the opt-out is not used;
    - the opt-out records nothing;
    - a load failure records nothing and leaves the lock free (a second open succeeds immediately);
    - a store failure releases the lock;
    - reconcile events recorded before a `show`/`set` failure remain in the history;
    - a second process opening the same theatre waits until the first closes it;
    - opening the same theatre inside an open block of the same process raises instead of deadlocking;
    - `uv run pytest -n auto`, `uv run mypy --strict .` and ruff pass on the whole suite
  - _Depends: 2.2, 2.3_
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 6.1, 6.2, 6.3, 6.4, 7.3_

- [ ] 3.2 Align docstrings and the step 1 specs with step 2
  - Clarify the store-level "second import records nothing" test and the docstrings of the store, the history events, the leaf diff and the theatre module (set and remove events now also come from reconcile; import stays import-only; the leaf diff's `old` values still come from the files)
  - Add a note to config-foundation requirements 6.9 and 7.3, and to the config-set-writeback 15.1 example, that step 2 (this spec) supersedes them
  - In the roadmap, tick the two "Existing Spec Updates" entries and mark decision A's "hand edits add no event" as superseded by step 2 for every command except `history`
  - Done when a search of `src/`, `tests/` and `.kiro/` finds no text still claiming that commands other than `history` never record hand edits, and the full suite passes
  - _Requirements: 1.3, 1.7, 2.9_

- [ ] 3.3 Validate reconcile-first through the real CLI
  - Cover, through `troupe config` subprocesses on real theatres:
    - a hand edit then `history` (nothing recorded), then `show` (one `set`), then `history` (the event printed in the usual line format);
    - a hand edit then `set` (reconcile event first, then the set's events);
    - reconcile events kept after `show` exits 5 and after `set` exits 3;
    - a fresh theatre's first `show` (import only);
    - a usage error and a load error (nothing recorded);
    - a comment-only edit (nothing recorded);
    - a text-include edit (one `set`);
    - a hand-added and a hand-removed include (`set` and `remove` events);
    - both `troupe.yml` and `troupe.yaml` theatres
  - Done when:
    - these end-to-end tests pass;
    - the positive cases (show or set after a hand edit, the text include, the added and removed includes, events kept after exit 3/5) each fail when reconcile-first is disabled, checked once by temporarily opting every command out
  - _Depends: 3.1_
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 2.4, 2.8, 2.9, 3.1, 3.2, 3.3_

- [ ] 4. Watch
- [ ] 4.1 Build the watcher loop
  - The watch set is every file the last successful load read, plus both root file names
  - A snapshot holds each watched file's bytes read by path, or "missing"
  - A sync repeats "snapshot the watch set, then open the theatre (which reconciles), then recompute the watch set" until the watch set is stable, and then keeps that snapshot as the baseline
  - Start performs the first sync and lets any troupe error propagate
  - The loop, until stop is requested: sync when the snapshot differs from the baseline or a retry is pending; on a troupe error, report the message once per distinct message, set retry pending and record nothing; otherwise sleep one poll interval in steps that check for stop
  - Done when in-process tests on real theatres prove:
    - start reconciles offline edits;
    - an edit after start is recorded within 2 s;
    - a replace-by-rename and a `.yml`→`.yaml` rename are recorded;
    - a hand-added include is followed and its later edits are recorded, while a removed include's file is no longer looked at;
    - an unrelated file and a touch without a leaf change record nothing;
    - half-written YAML gives exactly one report, no events, and recovery within 2 s of the fix;
    - an unreadable history store gives a report, and the outstanding events are recorded once it is readable again;
    - stop returns within one poll interval of being requested;
    - no user file changes;
    - the full suite, mypy and ruff pass
  - _Depends: 3.1_
  - _Requirements: 3.4, 3.5, 4.2, 4.4, 4.9, 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7_

- [ ] 4.2 (P) Let subcommands stream output and see the theatre as typed
  - Make the `--theatre` option also keep the directory text as given (the argument as typed, else `$TROUPE_THEATRE` as set, else the current directory), without changing the parsed path the other commands use
  - Let a subcommand register a streaming handler that receives stdout and stderr and returns the exit code; troupe errors raised by it keep the shared exit-code and stderr conventions with empty stdout; the buffered path stays unchanged for `show`, `set` and `history`
  - Done when tests prove the raw text is kept for `./t/`, for `$TROUPE_THEATRE` and for the default; a test-only streaming subcommand can write to stdout and return its code; a troupe error raised from it yields its exit code, empty stdout and the formatted stderr message; and all existing CLI tests pass
  - _Boundary: CLI shell, CLI main_
  - _Depends: 3.1_ (only because both edit the CLI main module; independent of 4.1)
  - _Requirements: 4.1, 4.3_

- [ ] 4.3 Add the `watch` subcommand and the watch-process test helper
  - Register `watch` with the shared theatre option and no positional arguments, so a surplus argument is a usage error
  - Update `tests/test_cli_usage.py::test_registration_point_holds_show_and_history`, which pins the exact list of registered subcommands
  - Install SIGINT and SIGTERM handlers that only request a stop; start the watcher; unless a stop was already requested, print exactly `watching <dir>` with the directory as given and a newline, and flush; run the watcher, reporting each message on stderr; restore the previous signal handlers in a `finally`, so they are restored on every exit path, including a start-up failure; return 0 after a stop
  - Never write anything else to stdout
  - Add `tests/watch_support.py`: a helper that starts `troupe config watch` as a background subprocess, waits up to 10 s for the readiness line, waits for expected history events against a deadline (polling `history` no more often than every 100 ms, because each poll takes the theatre lock), sends a signal, and always terminates and kills the child in teardown, collecting its exit code, stdout and stderr
  - All signal tests run the command in a subprocess, never in the pytest process
  - Done when tests driving the real command through the helper prove:
    - the exact readiness line;
    - exit 0 with no traceback on SIGINT and on SIGTERM;
    - exit 2 and no line for a surplus argument;
    - exit 4 with empty stdout and a stderr message naming the expected config file when no config exists;
    - exit 4 with empty stdout and a stderr message naming the history store when the store cannot be read at start-up;
    - after an in-process exit-4 start-up call, the previous SIGINT/SIGTERM handlers are back (checked with `signal.getsignal`);
    - the full suite, mypy and ruff pass
  - _Depends: 4.1, 4.2_
  - _Requirements: 2.7, 4.1, 4.3, 4.5, 4.6, 4.7, 4.8_

- [ ] 5. Validation: watch end to end
- [ ] 5.1 Validate observation end to end
  - Using the watch-process helper from 4.3, cover end to end:
    - the readiness line appears before any edit, and an edit made immediately after it is recorded within 2 s;
    - edits to the root file, a YAML include, a nested include and a text include are each recorded within 2 s;
    - a save by writing a new file and renaming it over the old one, and a `.yml`→`.yaml` rename;
    - a hand-added include (its leaves appear as `set` events, and later edits to it are followed) and a hand-removed include (`remove` events, later edits to it ignored);
    - comment-only, formatting-only and touch-only edits, and edits to an unrelated file, record nothing within 2.5 s
  - Done when these tests pass reliably under `uv run pytest -n auto` (run the module three times in a row without failure)
  - _Depends: 4.3_
  - _Requirements: 2.4, 2.8, 3.1, 3.2, 3.3, 3.4, 3.5, 4.3, 4.4, 4.5, 5.1, 5.2, 5.6, 5.7_

- [ ] 5.2 Validate concurrency, failures, signals and clean-up end to end
  - Cover end to end:
    - a successful `set` while `watch` runs, with a hand edit made before it (the history holds the reconcile event, then exactly the set's own events, and nothing more after 2.5 s);
    - a refused `set` while `watch` runs (no event for the set);
    - `watch` racing repeated `show` commands on the same hand edit (recorded exactly once, `seq` consecutive, batches not interleaved);
    - invalid YAML while watching (a stderr message naming the file, no events, and recovery within 2 s of the fix);
    - SIGINT and SIGTERM (exit 0, no traceback, stdout exactly the one line);
    - after each stop, `.troupe/` holding only the configuration files and the history store file, and every user file byte-identical to what the test wrote
  - Done when these tests pass reliably under `uv run pytest -n auto` (run the module three times in a row without failure)
  - _Depends: 5.1_
  - _Requirements: 2.7, 4.5, 4.6, 4.9, 5.3, 6.1, 6.2, 6.3, 6.4, 7.1, 7.2, 7.3_

- [ ] 5.3 Run the full quality gate and check traceability
  - Run `uv run pytest -n auto`, `uv run mypy --strict .`, `uv run ruff check .` and `uv run ruff format --check .`
  - Check that every requirement ID of this spec is named by at least one test docstring, that every new test names at least one ID, and that no test still asserts the superseded step 1 behaviour
  - Mark config-file-sync complete in the roadmap's spec list
  - Done when all four commands pass with their output shown, and the traceability check lists no requirement without a test and no test without a requirement
  - _Requirements: 1.1, 2.1, 3.1, 4.1, 5.1, 6.1, 7.1_
