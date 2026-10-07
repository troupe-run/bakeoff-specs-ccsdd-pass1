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

## Introduction
This specification covers contract step 2, "Sync with files". Developers edit the configuration files in
`<theatre>/.troupe/` (and the files they include) by hand, often while troupe is running. This feature keeps the
recorded configuration history in step with those files. It adds a **reconcile**: compare the files with the
configuration as recorded and record one `set` or `remove` event per leaf that differs. Every `troupe config`
command except `history` reconciles before doing its own work, and the new `troupe config watch` reconciles
continuously, within 2 seconds of each change, while it runs. A change that troupe itself writes back (`set`) is
recorded once, by the `set`, and never again as a hand edit.

Terms used below:
- **The files**: the root configuration file (`troupe.yml` or `troupe.yaml`) together with every file it
  includes, directly or indirectly, with YAML and text includes, as loaded under the step 1 rules.
- **Leaf**: as in step 1. Every scalar value (a text include counts as one string leaf) at its full dotted path,
  with list items addressed by index. Empty mappings and empty lists have no leaves.
- **The recorded configuration**: the leaves and values that the history currently records. Start from the
  import events, then apply every later `set` event (the leaf gets value `new`) and `remove` event (the leaf no
  longer exists), in `seq` order.
- **Differs**: a leaf exists in the files but not in the recorded configuration, or the other way round, or it
  exists in both with values that are not equal. Values are compared by type and value, as for `set`
  (config-set-writeback 15.3), so `1`, `1.0`, `true` and `"1"` are all different.

## Boundary Context
- **In scope**: reconcile and the events it records; reconciling first in every `troupe config` command except
  `history`; `history` staying reconcile-free; the `troupe config watch` subcommand (start-up, readiness line,
  observing the config file and every included file, following includes added or removed by hand, stopping on
  SIGINT/SIGTERM); recording each change exactly once while `watch` and other `troupe config` commands run on the
  same theatre at the same time.
- **Out of scope**: how `set` writes values into files (config-set-writeback, unchanged); loading, includes, path
  rules, `show` output, `history` output format, exit-code and message conventions (config-foundation, reused
  unchanged; *step 3: that scalar output is now `get` output, and the new `show` output is owned by spec
  config-get-show*); new event kinds (reconcile records the existing `set` and `remove` events); any output from `watch`
  other than its readiness line and error messages; a hand edit made to a file at the same moment a `set` writes
  that same file (concurrent writers to one file stay out of contract, as in step 1); anchors, aliases and merge
  keys (still refused, step 6); Windows.
  *Note (step 6): anchors, aliases and merge keys now load and are reconciled and watched as any other file
  (spec config-aliases, requirements 1–3).*
- **Adjacent expectations**:
  - config-foundation: the configuration is loaded, and load failures are reported (exit 4), exactly as in step 1.
    A theatre that has never been imported is still imported by the first command, `history` included.
  - config-set-writeback: `set` keeps its rules for writing files, for no-ops and for its own events
    (Requirement 15 there). Because the history now matches the files before a `set` starts, a `set`'s `old`
    values, taken from the files, are also the recorded values.
- **Changes to existing specs** (step 2 supersedes step 1 here):
  - config-foundation 6.9: `show` still prints the value in the files as read from disk (decision A, unchanged),
    but a hand edit is now recorded by reconcile (Requirement 2 here) instead of never being recorded.
  - config-foundation 7.3: an already-imported theatre is still never imported again, but every command except
    `history` now records the differences between the files and the history (reconcile). "Importing is not
    reconciling" still describes `history`.
  - config-set-writeback 15.1: the wording still holds. In its example, a hand edit `a: 1 → 5` followed by
    `set a 7`, the history now shows a reconcile `set a` (old 1, new 5) followed by the set's `set a` (old 5, new 7).
- **Choices made where the contract is silent** (conservative; to be recorded in `research.md`): files that
  cannot be loaded while `watch` runs produce no events, never "every leaf removed", and `watch` keeps running
  (5.3). `watching <dir>` names the theatre directory as it was given (4.3). Edits that follow one another
  quickly may be reconciled together, so the history need not show every intermediate state (5.5). Event order
  within one reconcile follows `set`: `set` events first, then `remove` events (2.6). A history store failure
  while `watch` runs is reported and retried, not fatal (5.4).

## Requirements

### Requirement 1: Reconcile before every command except `history`
**Objective:** As a developer, I want every troupe command to pick up the edits I made while troupe was not running, so that the history never misses a change.

#### Acceptance Criteria
1. When `troupe config show` or `troupe config set` runs against an imported theatre whose configuration loads, the troupe CLI shall reconcile (Requirement 2) before resolving PATH or changing any file.
   *Note (step 3): "`show`" now reads "`get` or `show`"; both reconcile first (spec config-get-show, requirement 6.2). `history` still never reconciles.*
2. When `troupe config show` or `troupe config set` exits with code 3 or 5 after its reconcile, the troupe CLI shall keep the events that reconcile recorded.
   *Note (step 3): "`show`" now reads "`get` or `show`" (spec config-get-show, requirement 6.2).*
3. When `troupe config history` runs, the troupe CLI shall not reconcile, and shall print only the events already recorded (plus the import of a never-imported theatre, as in step 1).
4. When any `troupe config` command runs against a theatre whose history has never been imported, the troupe CLI shall import it (step 1) and shall record no reconcile events in the same command.
5. If the command line is malformed (exit 2) or the configuration cannot be loaded (exit 4), then the troupe CLI shall not reconcile and shall record no events.
6. When `show` reconciles, the troupe CLI shall still print the value in the files as read from disk by this command and shall not modify any configuration file.
   *Note (step 3): "`show`" now reads "`get` or `show`"; both print what is in the files as read from disk by this command (spec config-get-show, requirements 6.2, 6.4 and 6.5).*
7. When a hand edit was made before a `set` and the `set` then changes the same leaf, the troupe CLI shall record the reconcile event for the hand edit before the `set`'s own events, so that the `set` event's `old` is the hand-edited value.

### Requirement 2: Reconcile events
**Objective:** As a developer, I want each reconcile to record exactly the leaves that changed in the files, so that the history reads like the edits I made.

#### Acceptance Criteria
1. When a leaf exists in the files and in the recorded configuration with values that differ, the troupe CLI shall record one `set` event for it, with the recorded value as `old` and the value in the files as `new`.
2. When a leaf exists in the files but not in the recorded configuration, the troupe CLI shall record one `set` event for it, with `old` null and the value in the files as `new`.
3. When a leaf exists in the recorded configuration but not in the files, the troupe CLI shall record one `remove` event for it, with the recorded value as `old` and `new` null.
4. When no leaf differs, including after an edit that changes only formatting, comments, quoting style, key order, blank lines, line endings, or whether a value is written in place or through an include, the troupe CLI shall record no event.
5. When a leaf's value changes type (for example `1` to `1.0` or to `"1"`), the troupe CLI shall record a `set` event for that leaf.
6. The troupe CLI shall record the events of one reconcile together, with consecutive `seq` numbers: first the `set` events in the files' document order, then the `remove` events ordered by the `seq` of the event that most recently created each leaf (its `import` event, or a `set` event recorded while the leaf did not exist), oldest first.
   *Note (step 4): each reconcile that records events is one change: all its events carry `via` `"file"`, actor `{"kind": "unknown", "id": null}` and `reason` null whatever `TROUPE_ACTOR` holds, and share one `at`, and one `change` that no other change has, including the following `set` of the same command and every other reconcile of a `watch`; a reconcile that finds no difference records no change (spec config-lineage, requirements 3.2, 4.1–4.4, 5.2 and 6.3).*
7. If a reconcile is interrupted (including by a signal), then the troupe CLI shall leave either all of its events recorded or none of them.
8. When the content of a text-included file changes, the troupe CLI shall record one `set` event for the leaf that holds that include, with the previous text as `old` and the new text as `new`.
9. The troupe CLI shall print reconcile events in `history` with the same line format, value forms and PATH filtering as every other event (config-foundation Requirement 8).
   *Note (step 4): that line format now includes the lineage members `actor`, `via`, `at`, `reason` and `change` (spec config-lineage, requirement 1).*

### Requirement 3: Includes added or removed by hand
**Objective:** As a developer, I want includes I add or remove by hand to be followed, so that the history reflects the configuration they bring in or take away.

#### Acceptance Criteria
1. When a hand edit adds an include of a YAML file, the troupe CLI shall record a `set` event under 2.1 or 2.2 for each leaf the included file contributes whose value differs from the recorded configuration.
2. When a hand edit adds an include of a non-YAML file, the troupe CLI shall record one `set` event for the including leaf, with the included file's exact text as `new`.
3. When a hand edit removes an include, the troupe CLI shall record a `remove` event for each leaf it contributed that no longer exists.
4. While `watch` runs, when a hand edit adds an include, the troupe CLI shall from then on reconcile changes to the newly included file (and to files it includes) within the time given in 5.1.
5. While `watch` runs, after a hand edit removes an include, the troupe CLI shall record no event for later changes to the file that is no longer included.

### Requirement 4: Starting and stopping `watch`
**Objective:** As a developer, I want to run `troupe config watch` in the foreground and know exactly when it is watching, so that I can rely on it from scripts and terminals.

#### Acceptance Criteria
1. When a developer runs `uv run troupe config watch [--theatre DIR]` from the repository root, the troupe CLI shall choose the theatre directory by the same rules as the other subcommands, including the default when `--theatre` is not given.
2. When `watch` starts against a theatre whose configuration loads, the troupe CLI shall import (if never imported) or reconcile it, and then begin watching.
3. When `watch` is able to observe changes to the config file and to every included file, the troupe CLI shall print exactly one line `watching <dir>`, where `<dir>` is the theatre directory as given by `--theatre` (or the default theatre directory when it is not given), followed by a newline, and shall flush stdout immediately.
4. The troupe CLI shall print the `watching` line only once any change made from that moment on is certain to be observed.
5. While `watch` runs, the troupe CLI shall write nothing to stdout other than the `watching` line.
6. When `watch` receives SIGINT or SIGTERM, the troupe CLI shall stop watching and exit with code 0 without printing a traceback.
7. If `watch` is given a malformed command line (for example a surplus argument or an unknown option), then the troupe CLI shall exit with code 2 under the step 1 usage rules and print no `watching` line.
8. If the configuration cannot be loaded or the history store cannot be read when `watch` starts, then the troupe CLI shall exit with code 4 under the step 1 rules (empty stdout, stderr naming the file or the store) and print no `watching` line.
9. The troupe CLI shall never create, modify, rename or delete a configuration file or an included file while `watch` runs.

### Requirement 5: Reconciling while `watch` runs
**Objective:** As a developer, I want edits I save in my editor to be recorded within seconds while `watch` runs, so that the history follows my work without my running another command.

#### Acceptance Criteria
1. While `watch` runs, when the config file or any included file (at any depth, YAML or text) is changed after the `watching` line appeared, the troupe CLI shall reconcile within 2 seconds of the change.
2. While `watch` runs, the troupe CLI shall observe a change whether the file is rewritten in place or replaced (for example saved by writing a new file and renaming it over the old one), and when the root configuration file is renamed between `troupe.yml` and `troupe.yaml`.
3. If the files cannot be loaded at a moment when `watch` would reconcile (for example invalid YAML in a half-saved file, a missing include target, an include cycle, or a missing or duplicated root configuration file), then the troupe CLI shall record no event, write to stderr a message naming the file under the step 1 load-error rules, keep running, and reconcile within 2 seconds once the files load again.
4. If the history store cannot be read or written while `watch` runs, then the troupe CLI shall write to stderr a message naming the history store location, keep running, and record the outstanding differences at a later reconcile, so that no hand edit is lost.
5. When several edits are made in quick succession while `watch` runs, the troupe CLI shall make the recorded configuration equal to the files within 2 seconds of the last edit, recording either each intermediate state or only the net difference.
6. While `watch` runs, the troupe CLI shall record no event for a change to a file that is neither the config file nor an included file.
7. While `watch` runs, the troupe CLI shall record no event when a file is touched or rewritten without any leaf differing.

### Requirement 6: Changes written by troupe itself
**Objective:** As a developer, I want a change made with `troupe config set` recorded exactly once, so that running `watch` never doubles my history.

#### Acceptance Criteria
1. While `watch` runs, when `troupe config set` succeeds, the troupe CLI shall record exactly the `set`'s own events for that change (preceded by any reconcile events for hand edits made before it) and no further event caused by the set's write-back.
2. While `watch` runs, when `troupe config set` is refused or fails, the troupe CLI shall record no event for the set itself.
3. While `watch` and other `troupe config` commands run against the same theatre at the same time, the troupe CLI shall record each hand edit exactly once, whichever of them reconciles it.
4. While `watch` and other `troupe config` commands run against the same theatre at the same time, the troupe CLI shall keep `seq` numbers consecutive and never record the events of two reconciles, or of a reconcile and a `set`, interleaved.

### Requirement 7: State and clean-up
**Objective:** As a developer, I want syncing to leave my theatre as clean as step 1 did, so that nothing new appears in my repository.

#### Acceptance Criteria
1. The troupe CLI shall keep all state for reconcile and `watch` only under `<theatre>/.troupe/`, and shall not create, modify, rename or delete any file or directory outside it.
2. When any `troupe config` command, including `watch` after SIGINT or SIGTERM, finishes, the troupe CLI shall leave no file or directory under `<theatre>/.troupe/` other than the configuration files and the history store files present after a step 1 command.
3. When any `troupe config` command, including `watch` after SIGINT or SIGTERM, finishes, the troupe CLI shall have closed the history store.
