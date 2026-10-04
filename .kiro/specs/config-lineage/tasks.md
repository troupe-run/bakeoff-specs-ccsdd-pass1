# Implementation Plan

**Conventions.** Every test written for this spec names in its docstring the requirement IDs it proves (for
example `"""Req 4.2: ..."""`), as in the earlier specs. Tests use real theatres on disk and the real CLI
(`testing.md`); `TROUPE_ACTOR` is set or removed explicitly in every test that depends on it (never inherited
from the developer's shell): an autouse fixture removes it for every test, and tests that need it set it with
`monkeypatch.setenv`/`delenv` (subprocesses inherit the test process environment), so no shared CLI helper changes. Each task ends with `uv run pytest -n auto`, `uv run mypy --strict .`,
`uv run ruff check .` and `uv run ruff format --check .` passing.

**External prerequisites.** config-foundation, config-set-writeback, config-file-sync and config-get-show are
complete. No new runtime dependency is added.

- [ ] 1. Foundation: lineage values and the command's actor
- [x] 1.1 Provide the lineage values, the batch stamp and the timestamp format
  - Define the actor (kind human, agent or unknown, and an optional id), the three origins (import, cli, file), the context of a batch (actor, origin, optional reason), the fixed import and file contexts (unknown actor, no reason), the cli context built from an actor and a reason, and the stamped lineage (context plus time and change id)
  - Stamping takes the current UTC time and a fresh random change id; the time format is `YYYY-MM-DDTHH:MM:SS.ffffffZ`, produced by one function that converts any aware time to UTC and refuses a naive one
  - The module is core: standard library only, no environment access; extend the core boundary test (AST-based, like the existing import checks) so it fails if the lineage module imports `os` or any CLI module
  - Done when unit tests prove the format (six digits, `Z`, a non-UTC aware time converted), that stamping keeps actor/origin/reason, that many stamps never share a change id, and that the import and file contexts carry the unknown actor and no reason
  - _Requirements: 1.2, 1.3, 1.4, 1.6, 3.4, 4.3, 5.1, 5.3_

- [ ] 1.2 Read the command's actor from `TROUPE_ACTOR` and refuse invalid values for every subcommand
  - Parse `human:<id>` / `agent:<id>` (split at the first `:`, lower-case kind, id kept verbatim, not empty and not only whitespace); unset → human with the login name, or a null id when the login name cannot be determined; anything else (including a set-but-empty variable) is a usage error whose message names `TROUPE_ACTOR`, quotes the value and states the accepted forms
  - The CLI entry point parses the actor once per command, right after argument parsing and before any handler runs, and makes it available to handlers; the variable name lives in one place
  - Add the autouse test fixture that removes `TROUPE_ACTOR` for every test, so the existing suite never depends on the developer's shell
  - Done when unit tests cover the valid forms, each invalid form and the default actor (login name lookup patched to fail for the null case), and an in-process CLI test shows an invalid value gives exit 2, empty stdout and the message for `get`, `show`, `set`, `history` and `watch` without creating `.troupe/history.sqlite`
  - _Requirements: 2.1, 2.2, 2.3, 2.4_

- [ ] 2. Core and CLI: record and print lineage
- [ ] 2.1 Store lineage with every history event and stamp each recorded batch once (integration across the history events, store, theatre opening, reconcile and `set`)
  - The three history events gain the lineage fields (fresh stores only; the schema version is raised in 2.2 together with its upcaster); history records carry their lineage; an unknown stored actor kind or origin is reported as an unreadable store (exit 4)
  - Importing and recording a batch take a context; each non-empty batch is stamped once, so all its events share time and change id; an empty batch records nothing
  - Callers pass their context in the same change (the store's signature forces it): the theatre opening passes the import context, reconcile passes the file context, and `set` passes a cli context built from the command's actor (no reason yet) through the set use case
  - Existing tests that build history records or call the import/record operations are updated to pass a context or compare the step 1 fields only, asserting the same facts as before
  - Done when store tests prove one lineage per import and per batch, distinct change ids and one time per batch, nothing recorded for an empty batch, and lineage identical after close and reopen; and theatre/reconcile/setter tests prove the import, file and cli contexts reach the store
  - _Requirements: 3.1, 3.2, 3.3, 4.1, 4.2, 4.4, 5.2, 7.1, 7.4_

- [ ] 2.2 Read histories recorded before step 4
  - The three history events get schema version 2, and events stored without lineage are upcast on read: unknown actor, null reason, `at` from the stored event time, `via` import for imports and cli otherwise, one shared change id for the imports and a separate deterministic one per other event
  - Done when a store test rewrites a real store's rows to the pre-step-4 shape (lineage fields and schema version removed from the stored state), reads it with the legacy lineage twice with identical results, and records new events with full lineage after it
  - _Requirements: 7.2, 7.3_

- [ ] 2.3 Print the five lineage members on every history line
  - After `seq`, `op`, `path`, `old`, `new`, each line has `actor` (`kind`, `id`), `via`, `at`, `reason` and `change`, in that order; filtered listings print the same lineage
  - Step 1–3 tests that compare whole history lines or dicts keep asserting the same facts through one shared projection of the five step-1 members
  - Done when render tests prove the member order and values (including a null id and a null reason) and the whole suite passes with the projection helper in place
  - _Requirements: 1.1, 1.2, 1.5, 1.7, 1.8_

- [ ] 2.4 Accept `--reason TEXT` on `set`
  - `--reason TEXT` and `--reason=TEXT` anywhere among the arguments; the last one counts; a missing TEXT is a usage error (exit 2) with no state touched; the reason reaches only the set's own events
  - Done when CLI tests prove the reason is recorded verbatim on the set's events, is null without the option and on preceding reconcile events, and `set --reason` with no TEXT exits 2 without creating history state
  - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5_

- [ ] 3. Validation through the real CLI and spec alignment
- [ ] 3.1 (P) Validate the lineage of each origin, change grouping and time end to end
  - First `history` gives import events with unknown actor, `via` import, null reason and one change; a hand edit then `get`/`show` gives file events with unknown actor even with `TROUPE_ACTOR` set; a `set` of a mapping changing several leaves gives one cli change with the declared actor; a hand edit then `set` gives two changes (file, then cli); successive commands never share a change; `watch` rounds are separate file changes
  - Each line's members appear in the order `seq`, `op`, `path`, `old`, `new`, `actor`, `via`, `at`, `reason`, `change`, with `actor` holding exactly `kind` and `id`
  - `at` lies between instants taken before and after the command, is identical within a change, and is UTC with `TZ` set to a non-UTC zone; reprinting `history` gives identical lines in a new process; `history PATH` lines equal their unfiltered lines; no extra file remains
  - Done when these e2e tests pass against the real console script
  - _Boundary: test_e2e_lineage_
  - _Requirements: 1.1, 1.3, 1.4, 1.6, 1.7, 1.8, 3.1, 3.2, 3.3, 3.4, 4.1, 4.2, 4.3, 4.4, 5.1, 5.2, 5.3, 7.1, 7.2, 7.4_

- [ ] 3.2 (P) Validate `TROUPE_ACTOR` end to end
  - `human:alice`, `agent:planner` and an id containing `:` appear on `set` events; unset uses the login name from the environment; each invalid form exits 2 for every subcommand with empty stdout, a message naming `TROUPE_ACTOR` and the value, files byte-identical and no history state created or changed
  - Done when these e2e tests pass against the real console script
  - _Boundary: test_e2e_actor_
  - _Requirements: 2.1, 2.2, 2.3, 2.4, 1.2_

- [ ] 3.3 (P) Validate `set --reason` end to end
  - Positions before, between and after the positionals and `--reason=TEXT`; spaces, non-ASCII and empty text kept exactly; absent → null; given twice → last; missing TEXT → exit 2 with no state; a no-op `set --reason` records nothing and exits 0
  - Done when these e2e tests pass against the real console script
  - _Boundary: test_e2e_reason_
  - _Requirements: 1.5, 6.1, 6.2, 6.3, 6.4, 6.5, 6.6_

- [ ] 3.4 Run the full quality gate, check traceability and align the earlier specs with step 4
  - Traceability: every requirement ID of this spec is named by at least one test docstring, and every new test names its IDs
  - Add step-4 notes to config-foundation (Requirements 7 and 8), config-set-writeback (Requirements 1 and 15) and config-file-sync (Requirement 2) pointing to config-lineage, and update module docstrings that describe the history line or recorded batches (design: This Spec Owns)
  - Done when the full suite, `mypy --strict` and ruff pass and the traceability check lists no missing ID
  - _Requirements: 1.1, 3.1, 3.2, 3.3, 6.1, 7.3_
