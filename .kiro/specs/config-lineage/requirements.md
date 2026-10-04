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

## Introduction
This specification covers contract step 4, "Lineage". Configuration changes in troupe are made by people and by
AI agents, and the history must be auditable. For every event that `troupe config history` prints, an auditor can
tell:
- **who** made the change (a human or an agent, and which one, or that it is unknown),
- **how** it was made (through the CLI, by editing a file, or by the first import),
- **when** it was recorded (a UTC timestamp),
- **why** it was made (an optional reason given with `troupe config set --reason TEXT`),
- **what else** changed with it (every event one command produces shares one change identifier).

Where something is genuinely unknown (an edit made in a text editor has no known author), the history says so
explicitly rather than guessing. Lineage is stored with the history, so it survives restarts.

Terms used below:
- **Event**: one line printed by `troupe config history`, as defined by config-foundation Requirement 8.
- **Change**: the set of events recorded together by one origin: one import, one file reconcile (one run of the
  reconcile defined by config-file-sync, including each reconcile `watch` performs), or the events one `set`
  command records for its own change.
- **CLI command**: one run of `troupe config <subcommand>`.
- **Login name**: the name of the operating-system user running the command, as the platform reports it.

## Boundary Context
- **In scope**: the members `actor`, `via`, `at`, `reason` and `change` on every event; the `TROUPE_ACTOR`
  environment variable and its validation; the default actor; the `--reason` option of `set`; keeping lineage
  across restarts; reading a history recorded before step 4.
- **Out of scope**: which events are recorded, when, in which order and with which `seq`, `op`, `path`, `old` and
  `new` (config-foundation, config-set-writeback, config-file-sync; unchanged); `history`'s PATH filter
  (unchanged); filtering or querying the history by actor, via, change or time; authenticating the actor
  (`TROUPE_ACTOR` is a declaration, not a credential); attributing hand edits to a person; a reason for commands
  other than `set`; the output of `get`, `show`, `set` and `watch` (unchanged); Windows.
- **Adjacent expectations**:
  - config-foundation records one import as one group of events and prints events as JSON Lines (Requirements 7
    and 8). This feature adds members to each line and keeps the existing members, their values and their order.
  - config-file-sync records each reconcile as one group of events, before the command's own work. A `set` on a
    theatre with hand edits therefore records two changes: first the file reconcile, then the `set`'s own change.
  - config-set-writeback records the events of one `set` as one group, and records nothing for a `set` that
    changes nothing.
- **Changes to existing specs** (added in the same change as the behaviour, collaboration.md):
  - config-foundation Requirement 8.2: each line also has the lineage members (Requirement 1 here). Requirement 7:
    import events carry import lineage (3.1 here).
  - config-set-writeback Requirement 1: `set` accepts `--reason TEXT` (Requirement 6 here). Requirement 15: its
    events carry CLI lineage (3.3 here).
  - config-file-sync Requirement 2: reconcile events carry file lineage, one change per reconcile (3.2 here).
- **Choices made where the contract is silent** (conservative; recorded in `research.md`):
  1. Every CLI command, not only `set`, validates `TROUPE_ACTOR` (2.4). The contract speaks of "a CLI command's
     actor", and failing the same way in every command is predictable. Only `set` records events with that actor.
  2. `TROUPE_ACTOR` is valid only as `human:<id>` or `agent:<id>`, with lower-case kind and an `<id>` that is not
     empty and not only whitespace. The `<id>` is everything after the first `:`, kept exactly (it may contain
     `:`). A variable that is set but empty is invalid, not "unset" (2.3, 2.4).
  3. When the login name cannot be determined, the default actor is `{"kind": "human", "id": null}`: the kind is
     known, the id is not, and the history says so instead of guessing (2.2).
  4. Every event of one change has the same `at` (5.2). `at` always has six fractional-second digits (5.1).
  5. The change identifier is an opaque string. Only its equality within a change and its uniqueness across
     changes are promised (4.3).
  6. A reason is kept exactly as given, including an empty text. When `--reason` is given more than once, the last
     one counts (6.1, 6.4).
  7. A history recorded before step 4 has no lineage. Its events are shown with actor unknown, `reason` null, and
     `at` taken from when each event was originally stored. `via` is `import` for import events and `cli` for
     `set` and `remove` events. This last point is an open question for the product owner, see below. Recorded
     import events share one change. Every other recorded event is its own change, because which events belonged
     together cannot be known (7.3).
- **Open question for the product owner** (default chosen, decision recorded in `research.md`): `via` for `set`/
  `remove` events recorded before step 4 cannot be known. Before step 2, every such event came from `set`; since
  step 2, a reconcile may also have produced them. The contract allows only `import`, `cli` and `file`. Default:
  `cli`. Alternatives: `file`, or a fourth value or `null` for unknown (which would extend the contract).

## Requirements

### Requirement 1: Lineage on every history event
**Objective:** As an auditor, I want every history event to state who, how, when, why and with what else it was recorded, so that I can audit any setting's changes.

#### Acceptance Criteria
1. When `troupe config history` prints an event, the troupe CLI shall include in its JSON object the members `actor`, `via`, `at`, `reason` and `change`, in addition to the members required by config-foundation Requirement 8.
2. The troupe CLI shall print `actor` as a JSON object with exactly the members `kind` (one of the strings `"human"`, `"agent"` or `"unknown"`) and `id` (a JSON string, or `null`).
3. The troupe CLI shall print `via` as one of the JSON strings `"import"`, `"cli"` or `"file"`.
4. The troupe CLI shall print `at` as a JSON string holding an RFC 3339 timestamp in UTC that ends in `Z`.
5. The troupe CLI shall print `reason` as a JSON string, or `null` when no reason was given.
6. The troupe CLI shall print `change` as a JSON string.
7. When `troupe config history` prints an event, the troupe CLI shall print the members `seq`, `op`, `path`, `old` and `new` first, in that order and with the same values as before this feature, followed by `actor`, `via`, `at`, `reason` and `change` in that order.
8. When `troupe config history PATH` filters events, the troupe CLI shall print each selected event with the same lineage members it has in the unfiltered listing.

### Requirement 2: The actor of a CLI command
**Objective:** As a person or an AI agent running troupe, I want to declare who I am through an environment variable, so that my changes are attributed to me.

#### Acceptance Criteria
1. While the `TROUPE_ACTOR` environment variable is set to `human:<id>` or `agent:<id>`, where `<id>` is not empty and not only whitespace, the troupe CLI shall use as the command's actor the kind `human` or `agent` respectively and the id `<id>`: every character after the first `:`, exactly as given.
2. While `TROUPE_ACTOR` is not set, the troupe CLI shall use as the command's actor the kind `human` with the login name as id, or with id `null` when the login name cannot be determined.
3. If `TROUPE_ACTOR` is set to any other text, including an empty text, a kind other than lower-case `human` or `agent`, a text without `:`, or an `<id>` that is empty or only whitespace, then the troupe CLI shall exit with code 2, write nothing to stdout, and write a stderr message that names `TROUPE_ACTOR`, quotes its value and states the accepted forms.
4. If `TROUPE_ACTOR` is invalid, then the troupe CLI shall reject every `troupe config` subcommand (`get`, `show`, `set`, `history`, `watch`) as in 2.3, before reading or changing any configuration file and without creating or changing any history state.

### Requirement 3: How each change was made
**Objective:** As an auditor, I want each event to say whether it came from the first import, a hand edit to a file, or the CLI, and who is behind it where that is known, so that I can tell troupe changes from editor changes.

#### Acceptance Criteria
1. When a theatre's configuration is imported, the troupe CLI shall record every import event with `via` `"import"` and actor `{"kind": "unknown", "id": null}`, whichever command performed the import and whatever `TROUPE_ACTOR` holds.
2. When a reconcile records events for differences between the files and the recorded configuration, whether before `get`, `show` or `set` or while `watch` runs, the troupe CLI shall record every such event with `via` `"file"` and actor `{"kind": "unknown", "id": null}`, whatever `TROUPE_ACTOR` holds.
3. When `troupe config set` records the events of its own change, the troupe CLI shall record each of them with `via` `"cli"` and the command's actor (Requirement 2).
4. The troupe CLI shall record actor `{"kind": "unknown", "id": null}` only for events whose author is not known, and shall never record a guessed person or agent for an import or a file-originated event.

### Requirement 4: Events that changed together
**Objective:** As an auditor, I want every event to carry the identifier of the change it belongs to, so that a multi-value change can be seen as a unit.

#### Acceptance Criteria
1. When one change records more than one event (for example a `set` of a mapping that changes several leaves, a reconcile of several hand edits, or an import), the troupe CLI shall give all of its events the same `change`.
2. When a `set` command first records reconcile events and then the events of its own change, the troupe CLI shall give the reconcile events one `change` and the `set`'s own events a different `change`.
3. The troupe CLI shall never give the same `change` to events of two different changes, including changes recorded by different commands, by different runs of the same command, by successive reconciles of one `watch`, or after the history has been closed and reopened.
4. When a command records no events (for example a `set` that changes nothing, or a reconcile that finds no differences), the troupe CLI shall record no change.

### Requirement 5: When each change was recorded
**Objective:** As an auditor, I want a UTC timestamp on every event, so that I can place changes in time regardless of the machine's time zone.

#### Acceptance Criteria
1. When the troupe CLI records an event, it shall set `at` to the UTC time at which the event's change was recorded, in the form `YYYY-MM-DDTHH:MM:SS.ffffffZ` (six fractional-second digits).
2. The troupe CLI shall give every event of one change the same `at`.
3. The troupe CLI shall express `at` in UTC whatever the time zone settings of the environment running the command.

### Requirement 6: Why a change was made
**Objective:** As a person or an agent changing configuration, I want to give a reason with `set`, so that auditors can see why the change was made.

#### Acceptance Criteria
1. When `troupe config set PATH VALUE --reason TEXT` (or `--reason=TEXT`, with the option before, between or after the positional arguments) records events for its own change, the troupe CLI shall record TEXT exactly as given as the `reason` of each of those events.
2. When `troupe config set` runs without `--reason`, the troupe CLI shall record `reason` `null` on the events of its own change.
3. The troupe CLI shall record `reason` `null` on every import and file-originated event, including reconcile events recorded by a `set` that was given `--reason`.
4. When `--reason` is given more than once, the troupe CLI shall use the last one.
5. If `--reason` is given without TEXT, then the troupe CLI shall exit with code 2, write nothing to stdout, write a stderr message describing the usage problem, and change no file and no history state.
6. When `troupe config set` with `--reason` changes nothing, the troupe CLI shall record no event and exit as config-set-writeback specifies for a set that changes nothing.

### Requirement 7: Lineage that lasts
**Objective:** As an auditor, I want lineage to stay with the history across restarts and to be readable for history recorded before step 4, so that the audit trail is never lost.

#### Acceptance Criteria
1. When the troupe CLI records an event, it shall store the event's lineage together with the event in the theatre's history under `<theatre>/.troupe/`, so that later commands, in new processes, print the same `actor`, `via`, `at`, `reason` and `change` for it.
2. When `troupe config history` runs several times without any change in between, the troupe CLI shall print identical lines each time.
3. When the history holds events recorded before this feature existed, the troupe CLI shall print them with all lineage members as stated in Boundary Context choice 7 (actor `{"kind": "unknown", "id": null}`, `reason` `null`, `at` from the time each event was originally stored, `via` `"import"` for import events and `"cli"` otherwise, one shared `change` for the import events and a separate `change` for every other event), shall keep printing them unchanged, and shall record new events after them with full lineage.
4. The troupe CLI shall create no file or directory outside `<theatre>/.troupe/` and leave no extra file behind to store lineage.
