# Research & Design Decisions: config-lineage

## Summary
- **Feature**: `config-lineage`
- **Discovery Scope**: Extension (light discovery): integration with the existing history store, reconcile, `set`
  and CLI entry point
- **Key Findings**:
  - Every write to the history already happens in exactly one of two places, `HistoryStore.ensure_imported` and
    `HistoryStore.record_changes`, and each is one `Application.save` (one SQLite transaction). A batch is
    therefore exactly one "change" in the contract's sense, so lineage can be attached per batch with no new
    transaction boundaries.
  - eventsourcing 9.5.5 supports per-class schema evolution: `class_version` on an event class plus
    `upcast_v1_v2(state)` static methods. The upcaster sees the decoded stored state, including the original
    `timestamp` (a tz-aware UTC `datetime`) and `originator_version`. This was checked by experiment: a v1 event
    was stored, read back through a v2 class, and a new v2 event appended to the same aggregate.
  - `run()` in `cli/main.py` is the single point every subcommand passes through after argument parsing, which
    makes it the one place to validate `TROUPE_ACTOR` for every subcommand.

## Research Log

### Where events are recorded today
- **Context**: lineage must label every event with its origin.
- **Sources Consulted**: `config/store.py`, `config/theatre.py` (`open_theatre`), `config/reconcile.py`,
  `config/writeback/setter.py`, `config/watch.py`, `cli/*.py`.
- **Findings**:
  - Import: `open_theatre` → `history.ensure_imported(flatten_leaves(root))`, for every command (including
    `history`) on a never-imported theatre.
  - File reconcile: `open_theatre` → `reconcile.reconcile(document, history)` → `record_changes`, for every
    command except `history`. `watch` calls `open_theatre` once per sync round, so every round is one
    reconcile.
  - CLI: `handle_set` → `set_value(theatre, path, value)` → `record_changes(changes)` once, after all writes.
    A no-op `set` returns before recording.
  - A `set` on an imported theatre with hand edits therefore records two batches: the reconcile, then its own.
- **Implications**: three origins, three call sites. Each passes a context (who/how/why). The store stamps
  time and change id at save time.

### Persisting lineage and reading older stores
- **Context**: Requirement 7 (survive restarts, read pre-step-4 stores).
- **Sources Consulted**: `.venv/.../eventsourcing/persistence.py` (`Mapper.from_domain_event`,
  `Mapper.to_domain_event`, l.385-440), `eventsourcing/domain.py` (`@event`, `timestamp`); experiment
  in a temporary directory with a v1 class then a v2 class over the same SQLite file.
- **Findings**:
  - Stored events are rebuilt with `object.__new__` and their decoded `__dict__`. No `__init__` runs, so a field
    added to a class is simply missing on older events. Without an upcaster, reading it raises `AttributeError`.
  - `class_version > 1` is written into the stored state. On read, the upcasters `upcast_v{n}_v{n+1}` run in turn
    on the state dict. The dict already holds `originator_version` and the decoded `timestamp`.
  - New events with the extra fields are appended normally after old ones. `@event` methods accept the extra
    keyword parameters as event fields.
- **Implications**: give `LeafImported`, `LeafValueSet` and `LeafRemoved` `class_version = 2` and an upcaster
  that fills in the legacy lineage. `HistoryStarted` carries no lineage (it is not a history line). Event class
  names stay unchanged, because they are the stored topics.

### Actor from the environment and login name
- **Context**: Requirement 2.
- **Sources Consulted**: Python 3.12 `getpass.getuser` docs (checks `LOGNAME`, `USER`, `LNAME`, `USERNAME`, then
  the password database; raises `OSError`, or `KeyError` from `pwd` in 3.12, when nothing is found);
  `cli/shell.py` (`TROUPE_THEATRE` read when the parser is built).
- **Findings**: `getpass.getuser()` is the standard "login name" helper. `os.getlogin()` fails without a
  controlling terminal (CI, containers, agents), so it is unsuitable.
- **Implications**: default id = `getpass.getuser()`. On `OSError`/`KeyError` the id is `null` (choice 3).

### Tests that pin the history line shape
- **Context**: adding members changes every history line.
- **Findings**: about 15 test modules compare whole history lines or dicts (for example `test_e2e_history.py`,
  `test_e2e_reconcile.py`, `test_e2e_watch*.py`, `test_render.py`, `test_set_history.py`). About 49 places build
  `HistoryRecord(...)` with keywords.
- **Implications**: the tests keep asserting their step 1–3 facts through one shared projection helper (the five
  step-1 members only). Lineage is asserted by new tests. These updates go in the same change as the
  behaviour.

## Architecture Pattern Evaluation

| Option | Description | Strengths | Risks / Limitations | Notes |
|--------|-------------|-----------|---------------------|-------|
| Lineage fields on every leaf event (chosen) | each event stores actor, via, at, reason, change | one store, one mapping, `seq` unchanged, atomic with the event | fields repeated per event (tiny) | needs `class_version` 2 + upcaster |
| Separate "change recorded" event before leaf events | lineage once per change | no repetition | shifts `seq` or needs filtering; one fact split over two events | rejected |
| Side table/file keyed by seq | lineage stored beside events | no event schema change | second store, consistency, extra state | rejected |

## Design Decisions

### Decision 1: Callers give the context, the store stamps time and change id
- **Context**: every event of one change shares `at` and `change` (4.1, 5.2). Ids are never reused (4.3). `at`
  is "when the change was recorded" (5.1).
- **Alternatives Considered**:
  1. Callers build the complete lineage (including time and id).
  2. Callers pass a `ChangeContext(actor, via, reason)`, and `HistoryStore` stamps it into a `Lineage` (fresh UUID4
     string, current UTC time) right before the one `save` of the batch.
- **Selected Approach**: 2.
- **Rationale**: the batch is the change, and the store is the only place that knows where a batch begins and
  ends. Stamping there makes "one id per batch" true by construction, and puts the time as close as possible to
  the record.
- **Trade-offs**: the time and the random id are not injectable. Tests check the format, equality within a
  change, and difference across changes, which is what the contract promises.
- **Follow-up**: none.

### Decision 2: `TROUPE_ACTOR` is parsed once, in `run()`, for every subcommand
- **Context**: 2.3, 2.4. Invalid actor → exit 2, before any state is touched, for every subcommand (choice 1).
- **Alternatives Considered**: parse it in each handler; parse it only in `set`.
- **Selected Approach**: `troupe.cli.actor.actor_from_environment(environ)` is called by `run()` right after
  argument parsing. The resulting `Actor` is stored on the namespace as `args.actor`. Handlers that record CLI
  changes (`set`) read it from there. The parse happens before any handler runs, so no theatre is opened.
- **Rationale**: one place for the variable name, its grammar and its message. The core never reads the
  environment.
- **Trade-offs**: `get`/`show`/`history`/`watch` fail with an invalid `TROUPE_ACTOR` even though they record no
  CLI events. Predictable, and recorded as choice 1. Revisit if the PO wants read commands to ignore it.

### Decision 3: Timestamp form
- **Selected Approach**: `at` is stored as text, already formatted: UTC, `%Y-%m-%dT%H:%M:%S.%fZ` (always six
  fractional digits). The format lives in one function, `troupe.config.lineage.format_utc`. Legacy events
  format their stored `timestamp` with the same function.
- **Rationale**: RFC 3339 profile with `Z`. Fixed width sorts lexically. Microseconds mean a timestamp read
  between two instants taken around the command lies between them. Storing the text means the printed value
  is exactly the stored one (7.2), independent of the library's datetime encoding or the process time zone
  (5.3).

### Decision 4: Change id form
- **Selected Approach**: `str(uuid.uuid4())` for new changes. Legacy: `"legacy-import"` for the pre-step-4
  import (a theatre has at most one import) and `"legacy-<seq>"` for each other legacy event.
- **Rationale**: random UUIDs never collide across processes or restarts in practice (4.3). Legacy ids are
  deterministic, so the output stays identical across runs (7.2). They cannot collide with UUID text.

### Decision 5: How history recorded before step 4 is shown (our choice, outside the contract)
- **Context**: the product owner confirmed (2026-10-04) that the contract says nothing about events recorded
  before step 4 or about upgrading stored history. How those events are shown is our choice, provided every
  printed event still has the lineage members. The open part was `via` for a pre-step-4 set/remove: it came from
  `set` (cli) or, since step 2, from reconcile (file), and the stored data cannot tell which. The contract allows
  only `import|cli|file`.
- **Alternatives**: `"cli"`; `"file"`; `null` or a fourth value such as `"unknown"` (extends the contract).
- **Selected Approach**: `"cli"`, with actor unknown, so the actor makes it explicit that nobody is attributed.
- **Rationale**: stays inside the contract's vocabulary, and matches the most common origin of pre-step-4
  events (all step 1 events, and every event a `set` recorded). Every line keeps the contract's shape, so tools
  that read `history` need no special case. The product owner accepted this choice on that basis. The rest of
  the legacy lineage (actor unknown, `reason` null, `at` from the stored event time, change ids per Decision 4)
  follows the same rule: say "unknown" where the vocabulary allows it, and never claim a grouping that cannot
  be proven.
- **Follow-up**: none. Revisit only if a later contract step specifies how older history is shown.

### Decision 6: `--reason` on `set`
- **Selected Approach**: a normal `--reason TEXT` option (metavar `TEXT`, default `None`). It goes through the
  existing `_SetArgumentParser` rule: only `--` arguments are options, so a reason that starts with a single
  `-` must be given as `--reason=-x`. That is argparse's standard behaviour for a value that looks like an
  option. A missing TEXT is argparse's usage error (exit 2). If given twice, the last one counts (argparse
  `store`).
- **Rationale**: no custom parsing. The reason is passed into `cli_context(actor, reason)` and reaches only
  the `set`'s own batch.

## Synthesis Outcomes
- **Generalization**: import, reconcile and `set` are three instances of "record a batch with a context". One
  `ChangeContext` type covers all three. `IMPORT_CONTEXT` and `FILE_CONTEXT` are constants. `cli_context(actor,
  reason)` builds the third.
- **Build vs adopt**: adopt eventsourcing's own `class_version`/upcaster mechanism for the schema change, and
  `uuid4`, `datetime` and `getpass` from the standard library. Nothing new to build or depend on.
- **Simplification**: no separate lineage store, no new event type, no injectable clock. `watch` needs no change
  because it already reconciles through `open_theatre`. `get`/`show`/`history` handlers need no change.

## Risks & Mitigations
- Breaking older stores. Mitigation: upcaster, plus a test that rewrites stored rows to the pre-step-4 shape and
  reads them back.
- Exact-shape tests across step 1–3 suites break. Mitigation: one shared projection helper, updated in the same
  change.
- Environment leakage in tests (`TROUPE_ACTOR` set in a developer's shell). Mitigation: tests set or unset it
  explicitly with `monkeypatch`.

## References
- eventsourcing 9.5.5 `Mapper.to_domain_event` (upcasting loop), `.venv/lib/python3.12/site-packages/eventsourcing/persistence.py`.
- RFC 3339 §5.6 (date-time with `Z`).
- Python `getpass.getuser` documentation.
