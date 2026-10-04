# Brief: config-lineage

## Problem
Configuration changes in troupe are made by people and by AI agents. Today `troupe config history` says *what*
changed (`seq`, `op`, `path`, `old`, `new`) but not who made the change, how, when, why, or which other
changes belong with it. An auditor cannot tell an agent's `set` from a person's, or a hand edit from a CLI
change. They also cannot tell which events one multi-leaf `set` produced together.

## Current State
- config-foundation: one `ConfigHistory` aggregate per theatre (`config/history.py`), with events
  `LeafImported(path, new)`, `LeafValueSet(path, old, new)` and `LeafRemoved(path, old)`, persisted with
  eventsourcing's SQLite module at `<theatre>/.troupe/history.sqlite` (`config/store.py`). `HistoryStore`
  writes in two places: `ensure_imported(leaves)` (one transaction for the whole import) and
  `record_changes(changes)` (one transaction per batch). `to_history_record` is the single event → record
  mapping. `format_history_line` (`cli/render.py`) prints `{"seq","op","path","old","new"}`.
- config-file-sync: `open_theatre` imports a never-imported theatre, or else reconciles hand edits
  (`reconcile.reconcile` → `record_changes`). This applies to every command except `history`, which only
  imports. `watch` reconciles through `open_theatre` once per sync round.
- config-set-writeback: `handle_set` → `set_value` → `theatre.history.record_changes(changes)`, a second
  transaction after the reconcile one.
- Every eventsourcing event already carries a UTC `timestamp`, but nothing reads it. Stored events are
  rebuilt without running `__init__`, so fields added later are missing on events stored earlier.
- CLI: the only environment variable read is `TROUPE_THEATRE` (`cli/shell.py`). Usage errors are
  `UsageError` → exit 2. The `set` parser accepts `--` long options only.
- Gap: no actor, no via, no change id, no reason, and no `--reason` option.

## Desired Outcome
The step 4 contract section "Lineage" holds. Every `history` line also has, after the existing keys:
- `"actor"`: `{"kind": "human"|"agent"|"unknown", "id": string|null}`
- `"via"`: `"import"` | `"cli"` | `"file"`
- `"at"`: an RFC 3339 UTC timestamp ending in `Z`
- `"reason"`: the text given with `set --reason TEXT`, otherwise `null`
- `"change"`: a string that is the same for every event one command (or one file reconcile, or one import)
  produced, and different for any two of them

A CLI command's actor comes from `TROUPE_ACTOR` (`human:<id>` or `agent:<id>`; anything else → exit 2). When it
is unset, the actor is `{"kind": "human", "id": <login name>}`. Imports and file-originated events have actor
`{"kind": "unknown", "id": null}`. Lineage is stored in the event store, so it survives restarts.

## Approach
Chosen: **lineage stored on every leaf event, one `Lineage` value per recorded batch.**
- A small value type `Lineage(actor, via, at, reason, change)`, plus `Actor(kind, id)`, lives in the core.
  Every write to the history (`ensure_imported`, `record_changes`) takes one `Lineage`. Every event in that
  transaction carries its fields. A batch therefore *is* a change: one transaction, one change id.
- Who builds which lineage:
  - import → `via import`, actor unknown;
  - reconcile → `via file`, actor unknown. This covers the reconcile before `get`/`show`/`set`, and each
    `watch` round;
  - `set`'s own events → `via cli`, actor from the CLI context, reason from `--reason`.
  So a `set` on a hand-edited theatre produces two changes: the file reconcile, then the CLI change. This
  matches the contract's "command (or file reconcile, or import)".
- Change id: a fresh random UUID string per batch. Timestamp: one UTC instant per batch, taken when the
  batch is recorded, printed with microseconds and `Z` (e.g. `2026-10-04T09:30:00.123456Z`).
- `TROUPE_ACTOR` is parsed in the CLI layer (one named home for the variable name and its grammar). The result
  is passed inward as a value. The core never reads the environment. The login name comes from the standard
  library (`getpass.getuser()`).
- Events get `class_version = 2` with an upcaster. A store written before step 4 still reads, and its lineage
  is shown as explicitly unknown, never guessed (see Constraints).

Rejected alternatives:
- *A separate "change recorded" event carrying the lineage, followed by plain leaf events*: it would shift
  `seq` (every event is a history line today), or need a filter that skips it. It also splits one fact across
  two events.
- *Using eventsourcing's own event `timestamp` as `at`*: each event gets its own instant, so the events of one
  change would differ by microseconds. We also want the time format to be decided in our code. (It is still
  the best source for `at` on legacy events.)
- *A side table/file for lineage keyed by seq*: a second store to keep consistent, and more state under
  `.troupe/`. It could drift from the events.
- *Reading `TROUPE_ACTOR` inside the core/store*: hides an input, makes tests depend on the environment, and
  breaks "CLI → core" layering.

## Scope
- **In**: the five lineage fields on every history line (import, reconcile/file, `set`/cli, `watch`
  reconciles); `TROUPE_ACTOR` parsing and its exit 2; login-name default; `set --reason TEXT`; change-id
  grouping per batch; persistence across restarts; reading pre-step-4 stores; updating the step 1–3 tests that
  pin the exact history line shape; step-4 notes in config-foundation (history line shape),
  config-set-writeback (`--reason`, cli lineage) and config-file-sync (reconcile events are `via file`).
- **Out**: filtering `history` by actor/via/change/time; authentication or verification of `TROUPE_ACTOR`
  (it is a declaration, not a credential); attributing hand edits to a person (the contract says unknown);
  `--reason` on commands other than `set`; changes to `get`/`show`/`watch` output; Windows.

## Boundary Candidates
- Lineage model (core): `Actor`, `Lineage`, the `via` vocabulary, and the timestamp format. Pure values.
- History persistence: event fields, the upcaster for legacy events, `ensure_imported`/`record_changes`
  taking a `Lineage`, and `HistoryRecord` carrying it.
- Who-builds-which-lineage: `open_theatre` (import/file) and `set_value` (cli, passed in from the handler).
- CLI: `TROUPE_ACTOR` parsing → actor or exit 2 (before any theatre is opened), login-name default,
  `set --reason`, and the five extra keys in `format_history_line`.

## Out of Boundary
- How files are loaded, reconciled or written back (config-foundation, config-file-sync,
  config-set-writeback). Lineage only labels the batches they already produce. Batch boundaries and event
  order do not change.
- `history`'s filtering and `seq` numbering (unchanged).

## Upstream / Downstream
- **Upstream**: config-foundation (store, aggregate, `to_history_record`, `format_history_line`, errors),
  config-file-sync (`open_theatre`, reconcile, watch), config-set-writeback (`handle_set`, `set_value`).
- **Downstream**: later steps that add commands which write configuration will need to pass a cli
  lineage. Audit tooling reads `history` JSON Lines.

## Existing Spec Touchpoints
- **Extends**: config-foundation Requirement 8 (history line shape gains five keys; step-4 note), Requirement 7
  (import events carry import lineage). config-set-writeback (set's events carry cli lineage and `--reason`).
  config-file-sync (reconcile events carry file lineage; one reconcile = one change).
- **Adjacent**: config-get-show (its reconcile-first now records file lineage; no output change).

## Constraints
- Python 3.12+, uv, `eventsourcing` (existing), `mypy --strict`, ruff, real-file/real-CLI tests. No new
  dependencies.
- State only under `<theatre>/.troupe/`; no extra file remains (PO decision E). An invalid `TROUPE_ACTOR` is a
  usage error: exit 2, empty stdout, stderr names `TROUPE_ACTOR` and the bad value, and **no history state is
  created or changed** (checked before the theatre is opened).
- Open choices to settle and record in requirements/research (proposed defaults):
  1. *Which commands validate `TROUPE_ACTOR`*: every `troupe config` subcommand. The contract says "a CLI
     command's actor", and failing the same way everywhere is predictable. Only `set` records cli events.
  2. *Grammar*: exactly `human:<id>` or `agent:<id>`, with a non-empty `<id>` (the text after the first `:`,
     kept verbatim, and it may contain `:`). Kind is case-sensitive. Empty, `human:`, `robot:x`, `Human:x` and
     `x` are all invalid → exit 2. A *set but empty* `TROUPE_ACTOR` is invalid (it is not "unset").
  3. *Login name unavailable* (`getpass.getuser()` fails): actor `{"kind": "human", "id": null}`. The kind is
     known and the id is genuinely unknown, so we say so rather than guess.
  4. *`at`*: one instant per change, UTC, `YYYY-MM-DDTHH:MM:SS.ffffffZ` (always 6 fractional digits).
  5. *`change`*: an opaque random UUID string. The contract promises only equality within a change and
     uniqueness across changes.
  6. *`--reason`*: any text is kept verbatim (including empty), stored on every cli event of that `set`. It is
     not stored on the reconcile events before it. A no-op `set` records nothing, so the reason is not kept.
     A reason given more than once: the last one wins (argparse default).
  7. *Pre-step-4 stores*: legacy events are upcast to actor unknown, `at` from the stored event timestamp,
     `reason` null. `via` is `import` for imports. For set/remove, `via` is the PO question below. `change`:
     all legacy import events share one id (they were one transaction); every legacy set/remove gets its own
     id (we cannot prove which belonged together, and claiming a grouping would be a guess).
  8. *PO question (recorded, default chosen)*: `via` for legacy set/remove events cannot be known. The contract
     allows only `import|cli|file`. Default `"cli"`: before step 2 every set/remove came from `set`. Revisit if
     the PO wants a fourth value or `null` for legacy data.
