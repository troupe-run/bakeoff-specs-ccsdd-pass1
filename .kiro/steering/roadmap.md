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
    reconciling"). The design must say whether `show` reads files or the projection, and why.
  - Error types and exit-code mapping live in one place (foundation). The set spec reuses them.

## Specs (dependency order)
- [x] config-foundation -- CLI scaffold, theatre/config-file location, YAML loading with recursive includes into a source-mapped model, path addressing, event-sourced projection with import, `show PATH`, `history [PATH]`, error/exit-code conventions. Dependencies: none
- [x] config-set-writeback -- `set PATH VALUE`: YAML 1.2 flow-node value parsing, target-file resolution, byte-surgical write-back rules, leaf-level set/remove events. Dependencies: config-foundation
