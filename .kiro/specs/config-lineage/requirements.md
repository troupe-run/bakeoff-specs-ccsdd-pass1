# Requirements Document

## Project Description (Input)
### Problem
Configuration changes in troupe are made by people and by AI agents. Today `troupe config history` says *what*
changed (`seq`, `op`, `path`, `old`, `new`) but not who made the change, how, when, why, or which other
changes belong with it. An auditor cannot tell an agent's `set` from a person's, or a hand edit from a CLI
change. They also cannot tell which events one multi-leaf `set` produced together.

### Current State
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

### Desired Outcome
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

See `brief.md` for the chosen approach, scope, boundaries and the open choices to settle.

## Requirements
<!-- Will be generated in /kiro-spec-requirements phase -->
