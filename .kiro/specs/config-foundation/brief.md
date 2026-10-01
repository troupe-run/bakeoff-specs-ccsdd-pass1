# Brief: config-foundation

## Problem
Developers who run troupe keep each theatre's configuration in hand-edited YAML (`.troupe/troupe.yml`),
often split across files with `file:` includes. They need to read a setting from the command line and see
how the configuration has changed. Errors must name the file or path at fault. There is no code yet.

## Current State
Greenfield repository: steering documents only, no package, no tests. The steering file `structure.md`
proposes a layout (`src/troupe/`, CLI → core → file layer) that the first design must confirm.

## Desired Outcome
- `uv run troupe config show PATH [--theatre DIR]` and `uv run troupe config history [PATH] [--theatre DIR]`
  work from the repository root, following the step 1 contract (General, Includes, show, history).
- The configuration is loaded with recursive includes into a model that records, for every node, the file
  and source span it came from, its scalar style, and whether it came from an include (YAML or text).
  config-set-writeback builds on this model.
- An `eventsourcing`-based aggregate/projection persisted under `<theatre>/.troupe/`. The first command run
  against a theatre imports it as one `import` event per leaf (`old` null). Later commands do not reconcile
  hand edits.
- Exit codes and error messages behave as the contract says (2 usage, 4 load, 5 path), with empty stdout on
  any failure.

## Approach
ruamel.yaml (YAML 1.2 core schema) is used to compose nodes with positions and styles. A source-mapped tree
is built over all included files, and `eventsourcing` holds the per-leaf event log (see roadmap.md
Approach Decision). The CLI uses a standard argument parser, with usage errors mapped to exit 2.

## Scope
- **In**: project scaffold (pyproject, uv, `troupe` console script, mypy/ruff/pytest config); `--theatre`
  handling; locating `troupe.yml`/`troupe.yaml` (neither or both → 4); loading, including `file:` includes
  (`.yml`/`.yaml` embedded, other targets as exact text, relative to the including file, recursive; missing
  target or cycle → 4); dotted path addressing with digit segments as list indices; leaf flattening; scalar
  printing rules (strings exact plus `\n` unless already present, bool, null, int, float `repr`);
  `show PATH` (mapping/list → 5, missing → 5); event store + projection + import; `history [PATH]` JSON
  Lines with `seq`, `op`, `path`, `old`, `new` and prefix filtering; central error types and exit-code
  mapping.
- **Out**: `set` and all write-back rules (config-set-writeback); anchors/aliases/merge keys; multi-document
  files; concurrency.

## Boundary Candidates
- File location and loading (the only reader of the user's files)
- Source-mapped config model and path resolution
- Event store / aggregate / projection and import
- CLI layer (argument parsing, rendering, exit codes)

## Out of Boundary
- Any modification of the user's YAML or text files
- Rendering/quoting rules for written values
- Emitting `set`/`remove` events (owned by config-set-writeback via this spec's API)

## Upstream / Downstream
- **Upstream**: Python 3.12+, uv, ruamel.yaml, eventsourcing; steering documents.
- **Downstream**: config-set-writeback (uses the model, path resolution, event API, error types). Later
  contract steps (step 2 `show` changes, step 5 write-target change, step 6 anchors).

## Existing Spec Touchpoints
- **Extends**: none
- **Adjacent**: config-set-writeback (shared model and event API seam)

## Constraints
- State only under `<theatre>/.troupe/`. Never create or modify any other file. Read commands must not alter
  the user's config files.
- Open design decision to record: whether files or the projection are authoritative for `show`, and how
  the event store is persisted (e.g. eventsourcing SQLite under `.troupe/`).
- `mypy --strict`, ruff, tests on real files and through the real CLI. Cover both `.yml` and `.yaml`.
