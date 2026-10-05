# Roadmap

## Overview
Step 1 of troupe's configuration subsystem: a `troupe config` CLI (Python 3.12+, uv, run as
`uv run troupe config …` from the repository root) over a theatre's hand-edited `.troupe/troupe.yml`
(or `.yaml`), which may be split across files with `file:` includes. It provides `show PATH`,
`set PATH VALUE` with byte-surgical write-back, and `history [PATH]`. The configuration is held as an
event-sourced projection with the `eventsourcing` library, persisted under `<theatre>/.troupe/`.

The work splits into a read-side foundation (locate, load, includes, paths, event store, import, `show`,
`history`, CLI and error/exit-code conventions) and the write side (`set` and its byte-surgical rendering
rules). The write side carries most of the risk and rules, so it gets its own spec and review scope.

## Approach Decision
- **Chosen**: ruamel.yaml (YAML 1.2, round-trip mode) is used only to *parse* each file into nodes with
  source positions and scalar styles. A source-mapped tree records which file and byte span each value
  comes from. Writes are **text patches** to the original file contents at those spans. The whole document is
  never re-dumped. `eventsourcing` holds a per-leaf event history (import/set/remove) persisted under `.troupe/`.
- **Why**: ruamel's dumper does not reproduce files byte for byte, but its composer gives exact
  positions, styles and YAML 1.2 core-schema typing. That is all a surgical patcher needs. Checked: ruamel.yaml
  0.19.1 + eventsourcing 9.5.5 install together under Python 3.12. Node marks give start/end indices, and
  quoting style is exposed. `yes` loads as a string (1.2 core schema).
- **Rejected alternatives**:
  - *ruamel round-trip load → modify → dump*: changes indentation, quoting, comment spacing and line
    endings in places nobody asked to change, which breaks the byte-surgical contract.
  - *PyYAML with marks*: YAML 1.1 typing (`yes`/`on` as booleans, `0.2`-style quirks). It would need a
    custom resolver to match the 1.2 core schema, and it does not expose comments.
  - *Hand-written YAML parser/CST*: the most control, but far larger and riskier than needed for step 1.

## Scope
- **In**: contract sections General; Includes; `show PATH` (steps 1 and 2); `set PATH VALUE`; `history [PATH]`.
- **Out**: anchors/aliases/merge keys (until step 6), multi-document files, TAB before end-of-line comments,
  concurrent writers, keys containing `.`, digit-only mapping keys, Windows. No speculative hooks for later steps.

## Constraints
- Python 3.12+, uv, `eventsourcing` (required), console script `troupe`, src layout `src/troupe/`.
- `mypy --strict`, ruff lint+format clean, pytest with real-file/real-CLI tests (see steering `testing.md`).
- State only under `<theatre>/.troupe/`. Never create or modify other files except as write-back requires.
- Exit codes 0/2/3/4/5. On non-zero exit: empty stdout, stderr names the offending path or file.
- Files are written atomically. No file changes on any refused/failed `set`.

## Boundary Strategy
- **Why this split**: the read side (loading, includes, path addressing, event store, import) is needed by
  every command and can be built and tested on its own. The write side is a large, rule-dense area
  (quoting, block/folded scalars, flow vs block, comment columns, CRLF, append positions) with byte-exact
  tests. Keeping it separate keeps reviews focused and lets it change at later steps (e.g. step 5 changes
  rule 2) without touching the loader.
- **Shared seams to watch**:
  - The **source-mapped config model** (node → file, span, style, include kind) produced by the loader and
    consumed by the patcher. Define it in config-foundation and keep it narrow.
  - **Leaf flattening and event emission**: foundation owns the aggregate, the event types and import.
    config-set-writeback computes leaf diffs and records `set`/`remove` events through the foundation's API.
  - **Authority between files and event store** (open design decision for config-foundation; record it in
    design.md/research.md). Hand edits after import are not reconciled into history ("importing is not
    reconciling"; superseded by step 2, config-file-sync, for every command except `history`). The design must say whether `show` reads files or the projection, and why.
  - Error types and exit-code mapping live in one place (foundation). The set spec reuses them.

## Specs (dependency order)
- [x] config-foundation -- CLI scaffold, theatre/config-file location, YAML loading with recursive includes into a source-mapped model, path addressing, event-sourced projection with import, `show PATH`, `history [PATH]`, error/exit-code conventions. Dependencies: none
- [x] config-set-writeback -- `set PATH VALUE`: YAML 1.2 flow-node value parsing, target-file resolution, byte-surgical write-back rules, leaf-level set/remove events. Dependencies: config-foundation
- [x] config-file-sync -- (step 2) reconcile files against the recorded configuration (set/remove per differing leaf), reconcile-first for every command except `history`, `troupe config watch` (polling, readiness line, include following, SIGINT/SIGTERM → 0), inter-process lock so a `set`'s write-back is never re-recorded. Dependencies: config-foundation, config-set-writeback
- [x] config-get-show -- (step 3) `get PATH` (scalar; mapping/list → 5 mentioning `config show`), `show [PATH]` (scalar as `get`; otherwise block YAML with includes resolved, text includes as literal blocks; whole config without PATH), `--sources` end-of-line `# from <file>` provenance. Dependencies: config-foundation, config-set-writeback, config-file-sync
- [x] config-lineage -- (step 4) actor/via/at/reason/change on every history event; `TROUPE_ACTOR` (exit 2 when invalid) and login-name default; `set --reason`; one change id per command, reconcile or import; persisted, legacy stores readable. Dependencies: config-foundation, config-set-writeback, config-file-sync, config-get-show
- [ ] config-include-overrides -- (step 5) mapping-form includes with `$ref` and deep-merged overrides, new keys under includes written as overrides in the including file (scalar-form → mapping-form conversion), list exceptions, overridden vs non-overridden existing values, exit 4 for non-YAML overrides; CR-B: `set --in-include` keeps the step 1 rule for new keys. Dependencies: config-foundation, config-set-writeback, config-get-show

## Product owner decisions (2026-10-01)
Full records (decision, alternatives, why, revisit trigger, status) are in each spec's `research.md`.
- **A** (confirmed): files are the truth; `show`/`set` read the files as on disk; history is change history only (hand edits add no event; a later `set` records the on-disk value as `old`). The "hand edits add no event" part is superseded by step 2 (config-file-sync) for every command except `history`.
- **B** (PO delegated → changed): a `set` that must change several files is carried out: all patches verified in memory first, each file written atomically, already-written files restored on a write failure (exit 3); 14.2 refusals unchanged.
- **C** (confirmed): exit 3 with no file changed when the in-memory read-back is not the intended configuration.
- **D** (PO delegated → kept): a `file:` VALUE is written as a plain string; missing/cyclic/unloadable target → exit 3, no file changed.
- **E** (confirmed with condition): temp-file-then-rename is fine, but no extra file may remain after any command; state only under `.troupe/`; history store closed at command end.
- **F** (PO delegated → kept): non-finite floats appear in history as the JSON strings `".inf"`, `"-.inf"`, `".nan"`.
- **G** (PO delegated → kept): malformed PATH (empty, empty segment, leading/trailing dot) → exit 2.
- **H** (our choice): anchors, aliases and merge keys → exit 4 naming the file (and key path where known).
- **I** (PO delegated → kept): duplicate keys, explicit tags and non-UTF-8 files → exit 4 naming the file (and key path where known).

## Step 2 — Sync with files (2026-10-02)

### Overview
People edit `.troupe/` files in their editor while troupe runs. Step 2 adds the contract section "Sync with
files": `troupe config watch --theatre <dir>` reconciles hand edits within 2 s, every command except `history`
reconciles first, and troupe's own write-back is never recorded a second time. This supersedes step 1's
"importing is not reconciling" for every command except `history`. Decision A (files are the truth; show/set
read the files) still holds.

### Approach Decision
- **Chosen**: reconcile = diff the loaded files' leaves against a projection of the *recorded* configuration
  (fold of import/set/remove), recorded as `set`/`remove` events in one transaction. `watch` polls the watched
  files' contents with the stdlib (watch set = `ConfigDocument.files` + config file candidates, recomputed
  after each load). An exclusive inter-process lock (no leftover file, e.g. `flock` on `.troupe/`) serialises
  each reconcile and each `set` (reconcile + write + record).
- **Why**: diffing against what was recorded is idempotent. Edits made while nothing ran are found, and a
  `set`'s write-back finds nothing new, with no self-write bookkeeping. The lock closes the gap between a
  `set`'s file write and its event record. Polling needs no new dependency, handles editors that save by
  renaming, and makes the "watching" readiness promise easy to keep.
- **Rejected alternatives**: watchfiles/watchdog (new dependency, readiness and coalescing subtleties, needs
  directory watches for includes); remembering own writes to suppress them (racy); diffing against the
  previous file snapshot (misses offline edits, double-counts write-back).

### Existing Spec Updates
- [x] config-foundation -- supersede 6.9's "record no history event for the hand edit" and 7.3's "shall not record events for differences between the files and the history" for every command except `history`; `history` still imports a never-imported theatre and never reconciles. Dependencies: none
- [x] config-set-writeback -- 15.1 unchanged as worded; update the decision-A notes and the example so a hand edit before a `set` now appears as a preceding reconcile `set` event; `set` runs reconcile-first under the shared lock. Dependencies: config-file-sync

### Direct Implementation Candidates
- (none) -- the step-1 tests that assert "hand edits add no events" are updated inside config-file-sync's tasks, in the same change as the behaviour (collaboration.md: spec, code and tests change together).

### Constraints (step 2 additions)
- No extra file may remain after any command, including `watch` and its lock (PO decision E). State only under `.troupe/`.
- A load error during `watch` must never be read as "every leaf removed". Record nothing and keep watching
  (conservative default; to be confirmed in requirements).

## Step 3 — `get` for values, `show` for anything (2026-10-03)

### Overview
Step 3 replaces the contract section "show PATH (step 1 and 2)" with "`get` and `show`". `troupe config get PATH`
prints a single scalar, which is what `show` used to do. A mapping or list gives exit 5, and stderr mentions
`config show`. `troupe config show [PATH]` prints a scalar like `get`, and a mapping, list or the whole
configuration (PATH omitted) as block-style YAML with includes resolved and text includes as literal blocks.
`--sources` adds `# from <file>` (relative to the theatre directory, `/` separators) to every line that holds a
scalar. Decision A (files are the truth) and reconcile-first (step 2) apply to both commands.

### Approach Decision
- **Chosen**: our own small block-style emitter over the source-mapped `ConfigNode` tree. Each output line
  records the scalar it holds, so `--sources` comments are attached when the line is built. Scalar, key and
  literal-block rendering reuses config-set-writeback's `scalar_render`/`block_scalar` rules, moved to a
  write-neutral shared home if needed. The output must load back to the same configuration.
- **Why**: the loader already records the file every node comes from (`origin.file`, and text includes point at
  the included file), so provenance costs nothing. Owning the emitter gives exact control over which line
  carries which comment, and keeps scalar typing aligned with our YAML 1.2 loader.
- **Rejected alternatives**: ruamel dump and then matching lines to values to insert comments (fragile);
  ruamel `CommentedMap` with eol comments (ruamel decides columns, widths, float forms and quoting, and needs a
  second tree); printing source spans (no include resolution, not block style).

### Existing Spec Updates
- [x] config-foundation -- Requirement 6 ("show a value") becomes `get PATH`; 6.7's exit-5 message also mentions `config show`. 1.1 and the Introduction name `get`/`show`. Add step-3 notes pointing to config-get-show. Dependencies: config-get-show
- [x] config-file-sync -- the reconcile-first command list gains `get` (`show` stays; `history` still never reconciles). Dependencies: config-get-show

### Direct Implementation Candidates
- (none) -- test and docstring updates for the rename are done inside config-get-show's tasks, in the same change as the behaviour (collaboration.md).

## Step 4 — Lineage (2026-10-04)

### Overview
Step 4 adds the contract section "Lineage". Every `history` event also carries `actor`
(`{"kind": "human"|"agent"|"unknown", "id"}`), `via` (`import`|`cli`|`file`), `at` (RFC 3339 UTC ending in `Z`),
`reason` (from `set --reason TEXT`, else null) and `change` (shared by every event one command, file
reconcile or import produced; never shared by two of them). A CLI command's actor comes from `TROUPE_ACTOR`
(`human:<id>`/`agent:<id>`, anything else → exit 2; unset → human with the login name). Imports and
file-originated events have actor unknown. Lineage is persisted in the event store and survives restarts.

### Approach Decision
- **Chosen**: one `Lineage(actor, via, at, reason, change)` value per recorded batch. `ensure_imported` and
  `record_changes` take it, and every event of that transaction carries its fields. Import → `import`/unknown,
  reconcile (before `get`/`show`/`set`, and each `watch` round) → `file`/unknown, and `set`'s own events →
  `cli`/actor from `TROUPE_ACTOR`. Change id: a random UUID per batch. `at`: one UTC instant per batch with
  microseconds. Events get `class_version = 2` with an upcaster, so pre-step-4 stores still read, with their
  lineage shown as unknown.
- **Why**: a batch is already one transaction and one "command (or file reconcile, or import)". Putting the
  lineage on the events keeps one store, one `seq` numbering and one event → record mapping. The environment
  is read only in the CLI layer.
- **Rejected alternatives**: a separate "change recorded" event (shifts `seq`, splits one fact in two);
  eventsourcing's per-event `timestamp` as `at` (differs within a change); a side table keyed by seq (second
  store to keep consistent); reading `TROUPE_ACTOR` in the core (hidden input, breaks layering).

### Existing Spec Updates
- [x] config-foundation -- Requirement 8's history line gains the five lineage keys, and Requirement 7's import events carry import lineage; add step-4 notes pointing to config-lineage. Dependencies: config-lineage
- [x] config-set-writeback -- `set` accepts `--reason TEXT`, and its events carry cli lineage; add a step-4 note. Dependencies: config-lineage
- [x] config-file-sync -- each reconcile is one change with `via file` and actor unknown; add a step-4 note. Dependencies: config-lineage

### Direct Implementation Candidates
- (none) -- updating step 1–3 tests that pin the exact history line shape is done inside config-lineage's tasks, in the same change as the behaviour (collaboration.md).

## Step 5 — Keep shared includes shared (CR-A, 2026-10-05)

### Overview
Included files are shared between projects, so adding a new setting under an included section must no longer
modify the shared file. The step 5 contract section "Overrides" replaces rule 2 under `set PATH VALUE`: an include
may be written in mapping form (`{$ref: "file:…", <overrides>}`, value = included content deep-merged with the
overrides); a new key whose deepest existing ancestor comes from an included file is written as an override in the
including file (a scalar-form include is first converted to mapping form, keeping the key line's comment); list
appends and new keys inside list items still go to the file holding the list; a mapping-form include of a non-YAML
file with siblings → exit 4; an overridden existing value is changed in the override, a non-overridden one where it
comes from.

### Approach Decision
- **Chosen**: the loader builds the merged view (`$ref` never appears in it), and each merged mapping keeps its
  physical layers (base mapping in the included file, override mapping and `$ref` directive in the including
  file). The writeback layer resolves the physical target through the layers; new keys under an include become
  overrides of the nearest include above the deepest existing ancestor unless a list item comes first. Read-back
  verification models mapping-form directives in the intended tree.
- **Why**: every reader (`get`, `show --sources`, leaves, reconcile, history, watch) already works on the loaded
  tree, so a merged tree with per-scalar origins gives them the contract's value for free; only the writer needs
  the physical layers, and they stay attached to the nodes they describe.
- **Rejected alternatives**: merging on demand in every reader (drift); a separate path-keyed overlay (second source
  of truth); always writing overrides in the root file (contradicts "the file that contains the include reference").

### Existing Spec Updates
- [ ] config-foundation -- Requirement 4 (Includes) gains a step-5 note pointing to config-include-overrides for mapping-form includes, the merge and the new exit-4 cases. Dependencies: config-include-overrides
- [ ] config-set-writeback -- rule 2 / Requirement 3.1–3.2 (new keys) and Requirement 2.1/2.6 (existing values) gain step-5 notes; the test pinning the old rule 2 is updated inside config-include-overrides' tasks. Dependencies: config-include-overrides
- [ ] config-get-show -- step-5 note: `show` prints the merged view and `--sources` names the including file for overridden scalars (no format change). Dependencies: config-include-overrides

### Direct Implementation Candidates
- (none) -- test and docstring updates for the replaced rule 2 are done inside config-include-overrides' tasks, in the same change as the behaviour (collaboration.md).

### CR-B — `set --in-include` (2026-10-05)
Sometimes a new setting genuinely belongs in the shared file. Contract section "In-include": `set --in-include
PATH VALUE` writes a new key into the included file instead (the step 1 rule).
- **Path**: extension of config-include-overrides, which is specified but not yet implemented. The option only
  selects which new-key rule the set uses (the step 1 rule instead of the step 5 override placement), so it
  belongs to the same requirement area, placement walk and tests. No new spec.
- **Open choices to settle in requirements** (proposed defaults, conservative):
  1. Only the target of *new* keys changes, including the new keys of a collection replacement. Existing values,
     list appends and removals follow the same rules as without the option.
  2. "The included file" is the file of the deepest existing ancestor (the step 1 rule, literally). With nested
     includes it is the innermost included file. When that ancestor is written only in an override, the step 1
     rule gives the including file.
  3. When the new key is not below any include, the option changes nothing (the step 1 rule and the step 5 rule
     agree there). It is accepted, not refused.
  4. `--in-include` is a flag that takes no value. It may appear anywhere among the arguments, like `--reason`, and
     combines with it. It does not change events or lineage.

### Existing Spec Updates (CR-B)
- [ ] config-include-overrides -- add the In-include requirement, the option's effect on placement and its tests (before implementation starts). Dependencies: none
- [ ] config-set-writeback -- step-5 note: `set` accepts `--in-include` (config-include-overrides). Dependencies: config-include-overrides
