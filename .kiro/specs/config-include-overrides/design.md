# Design Document: config-include-overrides

## Overview
Step 5 lets a theatre adjust a shared included YAML file without changing it. An include may be written in
mapping form, `{$ref: "file:…", <overrides>}`. Its value is the included content deep-merged with the overrides.
`troupe config set` writes a new key below an included section as an override in the including file. Where the
include is still in scalar form, it is first converted to mapping form.

The loader builds the **merged view** as the configuration tree. Every reader (`get`, `show`, `--sources`,
history, reconcile, `watch`) therefore sees the contract's value unchanged. Each merged mapping keeps its
**layers**: the included mapping and the override mapping as written. The writer uses these to find the file and
the written mapping behind a merged value.

### Goals
- Load mapping-form includes and deep-merge their overrides (Req 1), with the exit-4 refusals (Req 2).
- The merged configuration is what every command reads and records (Req 3).
- New keys under includes go to the including file as overrides (Req 4, 5, 6). Existing values change where they
  are written (Req 7).
- `$ref` is reserved (Req 8). Every write keeps the step 1 safety guarantees (Req 9).

### Non-Goals
- Deleting an included key through an override, overriding list items, `$ref` to anything but `file:` strings.
- Any change to rendering rules, events, lineage, reconcile or `show` formats.

## Boundary Commitments

### This Spec Owns
- Recognising, validating and deep-merging mapping-form includes in the loader, and the model fields that record
  their layers (`IncludeRef.form`, `ConfigMapping.layers`, `MergeLayers`).
- The helpers that map a merged value to the mappings written in files (`config/overrides.py`).
- Write-target placement for new keys (the replaced rule 2), and the physical target of changed and removed
  values below merged mappings.
- The scalar-form → mapping-form conversion edits.
- The `$ref` PATH refusal and the VALUE `$ref` directive form for verification.
- Updating the tests and spec notes that pinned the old rule 2.
- CR-B: the `set --in-include` option and the step 1 placement it selects (Requirement 10).

### Out of Boundary
- Quoting, indentation, comment, insertion-point and removal rules (config-set-writeback, reused as is).
- History events, lineage, reconcile, watch and lock mechanics (config-file-sync, config-lineage).
- `show` output format and source labels (config-get-show; labels follow node origins).
- Scalar-form include semantics, path syntax, error format (config-foundation).

### Allowed Dependencies
- `config/loader.py` → `config/overrides.py` (merge) → `config/model.py`.
- `config/writeback/*` → `config/overrides.py`, `config/model.py`, `config/resolve.py`.
- CLI: only `cli/set_command.py` changes (the `--in-include` flag, passed inward as a boolean). Errors flow through
  the existing `TroupeError` handling.
- No new third-party dependency.

### Revalidation Triggers
- Any change to `IncludeRef`, `ConfigMapping` or `MergeLayers` fields (loader, layout, planner, target, compare
  all read them).
- A change to the deep-merge order rule (1.3), because verification compares order.
- Step 6 (anchors/aliases/merge keys) touching `$ref` or merged nodes.

## Architecture

### Existing Architecture Analysis
- Layers: CLI → core (`config/`) → file layer (`config/writeback/` owns every byte written). The loader is the
  only reader of files.
- The model is frozen and source-mapped. An include root has its `origin` in the target and its `directive` in
  the including file.
- The writer resolves a `SetRequest` (`target.py`), plans text edits per file (`planner.py` with `layout.py`),
  verifies by reloading (`verify.py`), diffs leaves and writes atomically (`setter.py`).

### Architecture Pattern & Boundary Map
```mermaid
graph TD
    Loader[loader.py: $ref recognition] --> Overrides[overrides.py: deep_merge, physical lookups]
    Overrides --> Model[model.py: IncludeRef.form, ConfigMapping.layers]
    Readers[resolve, get/show, reconcile, history] --> Model
    Target[writeback/target.py: placement walk, $ref refusal] --> Overrides
    Planner[writeback/planner.py: layered merge, conversion] --> Overrides
    Planner --> Layout[writeback/layout.py: written parts]
    Layout --> Overrides
    Compare[writeback/compare.py: directive with overrides] --> Model
    Verify[writeback/verify.py] --> Loader
```

### Technology Stack
| Layer | Choice / Version | Role in Feature | Notes |
|-------|------------------|-----------------|-------|
| YAML parsing | ruamel.yaml 0.19 (existing, compose only) | positions and styles of `$ref` mappings | no dumping |
| Runtime | Python 3.12+, uv | — | no new dependency |

## File Structure Plan

### Directory Structure
```
src/troupe/config/
├── constants.py          # + INCLUDE_REF_KEY = "$ref"
├── model.py              # + IncludeRef.form, MergeLayers, ConfigMapping.layers
├── overrides.py          # NEW: deep_merge, is_ref_key, override_entries, physical_entry, physical_holders, written_parts
├── loader.py             # $ref recognition, validation (2.1-2.3), building merged/bare include roots
└── writeback/
    ├── compare.py        # IncludeDirective.overrides, VALUE $ref, written-overrides equality, root_plain
    ├── target.py         # KeyPlacement walk, AddKeyTarget.placement, $ref PATH refusal (8.1)
    ├── planner.py        # physical replace, layered merge/deletion/pruning, routed additions, conversion, plan_set
    ├── shared.py         # NEW (5.2, 6.1): every written change at every other place reaching its location (4.8, 11)
    ├── collection_render.py  # render_ref_form_lines / render_flow_ref_form for the conversion
    ├── layout.py         # written form at slots; tree walks over written parts
    ├── verify.py         # compare root plain (root may be a mapping-form include)
    └── setter.py         # set_value(..., in_include=False); verifies against plan_set's intended
src/troupe/cli/
└── set_command.py        # --in-include flag (CR-B, 10.6)
tests/
├── test_loader_overrides.py        # NEW unit: recognition, merge, order, refusals, layers
├── test_e2e_overrides_read.py      # NEW e2e: get/show/--sources/history/reconcile/watch over overrides
├── test_set_overrides.py           # NEW e2e byte-exact: new keys, conversion, lists, existing values, $ref
├── test_set_targets.py             # updated: the old rule-2 test now expects an override
├── test_set_in_include.py          # NEW e2e byte-exact: --in-include placement, parsing, events (Req 10)
└── writeback/
    ├── test_overrides_lookup.py    # NEW unit: physical_entry/holders, written_parts
    ├── test_target_placement.py    # NEW unit: placement walk cases
    ├── test_planner_overrides.py   # NEW unit: layered merge, pruning, conversion edits
    ├── test_shared_placement.py    # NEW unit (5.2, 6.1): writes into a file included at several places
    ├── test_layout_overrides.py    # NEW unit: slots/extents/comments in mapping-form files
    └── test_compare.py             # updated: directive overrides equality, root_plain
```

### Modified Files
- `.kiro/specs/config-foundation/requirements.md` — step-5 note under Requirement 4.
- `.kiro/specs/config-set-writeback/requirements.md` — step-5 notes on rule 2 / Requirement 3.1–3.2, Requirement
  2.1/2.6, 11.5 (conversion exception).
- `.kiro/specs/config-get-show/requirements.md` — step-5 note: merged view, override provenance.
- `.kiro/steering/roadmap.md` — tick the step-5 items when done.

## System Flows

### `set` of a new key below an include
```mermaid
sequenceDiagram
    participant S as setter.set_value
    participant T as target.resolve_target
    participant P as planner.plan_patch
    participant V as verify.verify_patch
    S->>T: PATH, VALUE
    T->>T: refuse $ref segment (8.1)
    T->>T: walk_path (merged) -> deepest A; placement walk up from A
    T-->>S: AddKeyTarget(placement: mapping | include-to-convert, prefix)
    S->>P: plan
    alt placement.mapping
        P->>P: add_entries(written mapping, nest(prefix + missing, VALUE))
    else placement.include (scalar form)
        P->>P: conversion edits in the directive's file
    end
    S->>V: reload with patches; compare root plain (ordered)
```

### Placement walk (Decision 2 of research.md)
The walk follows the layers that actually hold A's content (A = the deepest existing mapping), so the key goes into
the file holding the reference to the include that A's content comes from, even when that file is itself included
(choice 5, PO-confirmed):
1. **Holder chain.** From the root along PATH's existing keys, append each node; a node standing for YAML includes
   is appended once per include, outermost first (`include`, then its chained `inner`s). At a merged mapping N
   (`layers`), continue with the side holding A's content: `N.layers.over` when it holds every remaining key down
   to A (A written in an override, including A merged itself, 4.3), else `N.layers.base` (whose own include, if
   any, has its directive at its file's root); a side may itself be merged and gets the same choice. Children are
   then taken from that side.
2. **Walk up** the chain from its end (innermost first) and stop at the first element that is:
   - a YAML include root with `include.form` (bare or with overrides) → its `$ref` mapping, prefix = keys from the
     element to A; the `$ref` mapping is descended through the prefix keys it already holds as mappings and the
     walk is rerun on that sub-chain, never above it (an existing override mapping takes the keys, 4.3, 4.4);
   - a YAML include root in scalar form → convert it, same prefix; this includes an include string that is a
     file's whole root (a root file `file:x.yaml`, or a.yaml = `file:b.yaml`), converted to `$ref: <string>`
     followed by the entries at the string's column;
   - an override side (reached as `over`) → the written mapping holding A's content (the chain's last node),
     prefix `()` (4.3);
   - a list item (its parent is a sequence) → the same, prefix `()` (step 1 rule, 6.2).
3. Reaching the top of the chain → the same, prefix `()`.

The result mapping is always a **written** mapping: none of its entries comes from another layer.

### Placement with `--in-include` (CR-B, Requirement 10)
With `in_include=True` the walk is not run. The placement is the step 1 rule: mapping = the *included side* of A
(`A.layers.base`, repeatedly while that is itself merged; A itself when A is not merged), prefix `()`. An A written
only in an override is not merged, so it is A itself in the including file (choice 13). Below no include, this is
A, the same as without the option (10.5).

**Intended order.** `splice` appends new keys at the end of a mapping. Merged order (1.3) puts the included side's
keys before the override-only keys. So when keys are written into the included side of a merged mapping, the
intended tree moves them to just before that mapping's first override-only key (10.2). `resolve_target` does this
by walking the merged nodes along PATH (and below it for a mapping VALUE). The read-back stays an ordered
comparison.

## Requirements Traceability

| Requirement | Summary | Components | Flows |
|-------------|---------|------------|-------|
| 1.1, 1.4, 1.6, 1.7, 1.8 | Recognise mapping-form includes anywhere; bare = scalar form; overrides resolved first; no writes | Loader | — |
| 1.2, 1.3, 1.5 | Deep merge, order, no `$ref` in value | Overrides.deep_merge, Loader | — |
| 2.1, 2.2, 2.3, 2.4 | Exit-4 refusals | Loader | — |
| 3.1, 3.2, 3.3, 3.4, 3.5 | Merged view in get/show/sources/history/reconcile/watch | Model (merged tree), unchanged readers | — |
| 4.1, 4.2, 4.3, 4.4, 4.5, 4.6 | New-key placement | Target.placement, Planner.add_entries | set new key |
| 4.8 | Override in a file included at several places | Planner.plan_set (`WrittenChanges`), shared.with_other_places, Verify | set new key |
| 11.1, 11.2, 11.3 | Every write into a file included at several places | Planner (`WrittenChanges` at every edit), shared.with_other_places, Verify, existing leaf_diff | — |
| 4.7 | Events per created leaf, none for conversion | existing leaf_diff over read-back | — |
| 5.1, 5.2, 5.3, 5.4, 5.5 | Conversion edits | Planner.convert_include, CollectionRender | set new key |
| 6.1, 6.2, 6.3 | Lists | Target.placement (list-item stop), existing AppendTarget | — |
| 7.1, 7.2 | Overridden vs not | Overrides.physical_entry, Planner.replace | — |
| 7.3, 7.4 | Collection replacement across layers, removals, pruning | Planner._merge_layered, routed additions | — |
| 7.5, 7.6 | Include root refusal, bare text include | existing `_change_include` (include set on merged roots) | — |
| 8.1 | `$ref` PATH refused | Target | — |
| 8.2 | VALUE `$ref` | Compare.new_to_plain / plain_equal, Verify | — |
| 8.3 | get/show `$ref` not found | merged tree (no `$ref` key) | — |
| 10.1, 10.2, 10.3, 10.5 | `--in-include` placement and merged order | Target (in-include placement, intended order), Planner (routed additions use the same choice) | Placement with `--in-include` |
| 10.4 | Other changes unaffected | Planner (option only reaches placement) | — |
| 10.6 | Option parsing | SetCommand | — |
| 10.7 | Events, lineage, safety unchanged | existing setter, leaf_diff, verify | — |
| 9.1, 9.2, 9.3 | Byte-surgical, read-back, atomic | Planner, Verify (root_plain), existing atomic writer | — |

## Components and Interfaces

### Core

#### Model (`troupe/config/model.py`)
```python
@dataclass(frozen=True)
class IncludeRef:
    kind: IncludeKind
    spec: str
    target: SourceFile
    directive: Origin            # the include string: scalar form, or the `$ref` value
    form: "ConfigMapping | None" = None
    """Mapping form: the `$ref` mapping as written in the including file (its entries: `$ref` with the raw
    include string scalar, then the overrides as loaded). None for the scalar form."""
    inner: "IncludeRef | None" = None
    """Without overrides: the include written as the target file's own root (a chained include), else None.
    With overrides the inner include stays on `layers.base.include`."""

@dataclass(frozen=True)
class MergeLayers:
    base: "ConfigMapping"   # the included side, as loaded (an include root's base keeps only the target root's own include, if any)
    over: "ConfigMapping"   # the override side as loaded; for an include root, its `$ref` mapping

@dataclass(frozen=True)
class ConfigMapping:
    entries: tuple[MappingEntry, ...]
    style: CollectionStyle
    origin: Origin
    include: IncludeRef | None = None
    layers: MergeLayers | None = None   # set on every merged mapping
```
- A merged mapping has `style` and `origin` of its base. An include root with overrides carries `include` (with
  `form`). A nested merged mapping carries its base value's `include` (usually None).
- Merged entries: base entries in base order (an overridden one replaced by the override entry, or by a merged
  entry when both are mappings), then the override-only entries in override order. Scalars and lists are the same
  objects as in their layer, so their origins name their own file.

#### Overrides (`troupe/config/overrides.py`, new)
```python
def deep_merge(base: ConfigMapping, over: ConfigMapping, *, include: IncludeRef | None) -> ConfigMapping
def is_ref_key(key: str) -> bool
def override_entries(mapping: ConfigMapping) -> tuple[MappingEntry, ...]   # entries without `$ref`
def physical_entry(mapping: ConfigMapping, key: str) -> tuple[ConfigMapping, MappingEntry]
def physical_holders(mapping: ConfigMapping, key: str) -> tuple[Holder, ...]
def written_parts(node: ConfigNode) -> tuple[ConfigNode, ...]
```
- `deep_merge`: pure. It never mutates or reuses a merged node as a layer of another site. `over` may hold `$ref`
  (it is skipped).
- `physical_entry`: for a merged mapping, the override side if it has the key, else the base side, recursively.
  Otherwise `(mapping, entry)`. The returned mapping is written in one file.
- `physical_holders`: every written mapping holding `key` below `mapping` (override side first, then base), each
  with the chain of written override mappings and keys that lead to it, for pruning (Decision 4).
- `written_parts`: what is written for a node, for layout walks. A merged mapping gives `(base, over)`. A bare
  mapping-form include gives `(form, node with form cleared)`. Any other node gives `(node,)`.

#### Loader (`troupe/config/loader.py`)
- In `_build_mapping`, a key whose text is `$ref` (any key style) marks the mapping:
  - The `$ref` value is built **without** include expansion. If it is not a string scalar starting with `file:`
    → `ConfigLoadError` at the mapping's path: "`$ref` must be an include string (file:…)" (2.2).
  - The other entries are built as usual (overrides may themselves contain includes, 1.7).
  - `form` = the mapping as written. Target = `<dir of file>/spec`.
  - Text target: with overrides → `ConfigLoadError` naming the including file, the path and the target, "overrides
    apply only to YAML includes" (2.1). Otherwise a text include scalar with `include.form`.
  - YAML target: cycle check and read as for scalar form (1.8). For an empty target or non-mapping content with
    overrides → `ConfigLoadError` naming the including file, the path and the included file, "overrides need a
    mapping; the included file … holds a list | a scalar | nothing" (2.3). With overrides: `deep_merge(content, form, include=ref)`. Bare:
    `replace(content, include=ref)`.
- `load_config` accepts a merged or bare mapping-form root (the root's value must still be a mapping).

### File layer (`troupe/config/writeback/`)

#### Compare
- `IncludeDirective` gains `overrides: PlainMapping | None = field(default=None, compare=False)`.
- `new_to_plain(NewMapping)` with a `$ref` entry whose value is a `file:` string →
  `IncludeDirective(text, None, overrides=<other entries>)`.
- `node_to_plain` records on a loaded include root's directive its written overrides (the other entries of its
  `$ref` mapping as loaded; `{}` for a scalar-form or bare include).
- `plain_equal`: when either directive has no content (an include from VALUE), the texts must be equal and the
  overrides exactly equal (unordered; a `file:` string has none), so the include's own string over an include
  with overrides differs (choice 16, research Decision 10); two directives with content compare their contents.
- `root_plain(document) -> PlainMapping`: the root's plain mapping, unwrapping a root include directive. Used by
  `resolve_target` (current) and `verify_patch` (after).

#### Verify
- `verify_patch` is unchanged: an ordered comparison of the read-back root plain with `request.intended`.
  The setter passes the request with `plan_set`'s intended, which holds every planned change at every place
  reaching its written location, merged with that place's own overrides (4.8, 11.2, 9.2). The intended
  configuration is derived only from the pre-write document and the plan's semantic edits, never from the
  read-back, so the comparison still proves the written texts; a read-back that differs for any other reason
  is still refused (exit 3).

#### Target
```python
@dataclass(frozen=True)
class IncludeSite:
    node: ConfigNode                       # scalar-form YAML include root to convert
    parent: ConfigMapping | ConfigSequence # merged parent (physical entry found via overrides)
    key: str | int

@dataclass(frozen=True)
class KeyPlacement:
    mapping: ConfigMapping | None   # a written mapping receiving the entries, or
    convert: IncludeSite | None     # a scalar-form include to convert
    prefix: tuple[str, ...]         # existing keys to create as intermediate mappings first

@dataclass(frozen=True)
class AddKeyTarget:
    mapping: ConfigMapping          # deepest existing (merged) mapping, for messages
    missing: tuple[str, ...]
    placement: KeyPlacement

def place_new_keys(document: ConfigDocument, mapping_path: ConfigPath, *, in_include: bool) -> KeyPlacement
def resolve_target(document: ConfigDocument, path: ConfigPath, value: NewValue, *, in_include: bool = False) -> SetRequest
```
- `resolve_target` refuses any `$ref` segment first: `WriteRefusedError` "`$ref` is reserved for includes", naming
  PATH and the root file (8.1).
- `place_new_keys` implements the placement walk, or the step 1 placement when `in_include` is true. The planner
  also uses it for the new keys of a collection replacement (7.3, 10.3). `SetRequest` carries `in_include` so the
  planner passes the same choice.

#### Planner
- `replace`: the parent's written entry comes from `physical_entry`. The slot is in that entry's file, and
  `change_slot` gets the merged node for comparisons (7.1, 7.2).
- `_merge_mapping` on a merged mapping → `_merge_layered`:
  - Kept keys: `change_slot` at their physical entry.
  - Removed keys: deleted from every holder. A holder that is a nested override losing every entry (and gaining
    none) is pruned: its own entry is deleted from its holder instead, upward, never past a `$ref` mapping (7.4,
    Decision 4).
  - New keys go through `place_new_keys(at)` (7.3).
- `_merge_mapping` on an unmerged mapping: new keys go through `place_new_keys(at)`. When the placement is the
  mapping itself with no prefix, the existing single insertion with `deleted` styles is used.
- `add_key` / routed additions: `placement.mapping` → `add_entries(mapping, nest(prefix, entries))`.
  `placement.convert` → `convert_include` (a site with no parent is an include string that is its file's whole
  root: `$ref: ` is inserted before it, the entries follow at its column; one not alone on one line is refused,
  exit 3). Placements sharing a target (one written mapping, or one include site)
  are grouped and their nested `nest(prefix, entries)` trees deep-merged by key (first-seen order) before rendering,
  when planning ends: each written mapping gets one insertion and each include one conversion (4.4, 7.3).
- `plan_set(document, request) -> Plan(patches, intended)` (`plan_patch` returns its patches). Every planned
  change is also recorded in `planner.changes`, a `shared.WrittenChanges` keyed by written location (file real
  path + span, since diamond includes are separate node objects per place; research.md Decisions 11, 12):
  - `replace(holder, key|index, value)`: a slot replaced in place (scalar, block scalar, kind change, the
    override's own value of a merged mapping, `{}` for the included side in `_empty_layered`), recorded in
    `_change_value` with the written collection and key or index that `change_slot` now receives (`where`);
  - `remove(holder, key|index)`: entries deleted by `_merge_mapping`, `_remove` (layered removals and
    pruning links) and surplus list items;
  - `add(holder, entries)`: new keys at a mapping placement or at the merged-into mapping itself, appended
    list items (nested `prefix` trees merged by key as the planner merges them);
  - `convert(include, entries)`: overrides of a converted scalar-form include string (its file + span);
  - `text(target, content)`: a text-include target file's new content.
  `shared.with_other_places(root, request.intended, path, changes)` then recomputes the plain configuration
  from the pre-write model twice, as loaded and with the changes applied wherever their location is reached.
  The recomputation (`_Rebuild`) mirrors `node_to_plain`, but rebuilds every merged mapping from its two
  written sides with the plain deep merge (1.2, 1.3), so each place's own overrides keep precedence (an outer
  override still wins) and new keys come in merged order. `request.intended` is kept along PATH (the
  requested change at its own place stays what the request asked for); at every path off PATH whose value
  the two recomputations give differently, the recomputation with the changes is used. A change reached at
  one place only therefore leaves `request.intended` unchanged (11.1, 11.2; choices 17, 18).
  Hardening (Principle 2): every recorded location must be reached at PATH's own place (the layers, include
  strings, `$ref` mappings and text targets of every node from the root along PATH, and of every node at or
  below PATH), so off-PATH differences can only be replicas; any other recorded location is refused (exit 3,
  naming PATH and its file). A new YAML include from VALUE is recorded with the file it is written in; where
  another place merges it with a mapping override, its target is loaded from the pre-write files
  (`loader.load_include`) and deep-merged as `deep_merge` does; a load error is refused (exit 3) as the
  read-back would refuse it. A parametrised unit test pins that the unedited recomputation equals
  `node_to_plain(root)`.
- `convert_include(site, entries)`: the written slot of the include string comes from `physical_entry` or
  `item_slot`. Then:
  - Flow parent: one INLINE edit replacing the include string with `{$ref: <raw>, <flow entries>}` (5.4).
  - Block parent: the include string must end on the owner's line, else refuse (Decision 7).
    - With an end-of-line comment: delete `[indicator end, string end)` with `EditKind.LINES` (no comment
      re-alignment, 5.2).
    - Without a comment: delete `[indicator end, line content end)`, but for a list item without a comment
      replace the string with `$ref: <raw>` (compact, 5.3).
    - Insert the following lines after the owner's line: `$ref: <raw>` (unless compact) and the new entries,
      at column owner + indent step (mapping) or `-` + 2 (list item) (5.1, 5.3).
  - `<raw>` is the include string's exact source text. New-entry styles follow the `$ref` entry as sibling.

#### Setter and SetCommand (CR-B)
- `set_value(theatre, path, value, context, *, in_include: bool = False)` passes the flag to `resolve_target`.
- `set_command` registers `--in-include` as `store_true`. argparse rejects `--in-include=TEXT` as a usage error
  (exit 2, empty stdout, no file or history change: the parse happens before the theatre is opened) (10.6). The
  option may appear anywhere among the arguments and combines with `--reason`.

#### CollectionRender
```python
def render_ref_form_lines(raw: str, entries: Sequence[tuple[str, NewValue]], column: int,
                          siblings: SiblingStyles, ctx: RenderContext, *, with_ref: bool) -> str
def render_flow_ref_form(raw: str, entries: Sequence[tuple[str, NewValue]],
                         siblings: SiblingStyles, ctx: RenderContext) -> str
```

#### Layout
- `_local(node)`: what is written at a slot. A mapping-form include root gives its `form` (when here). A
  scalar-form root gives its directive span (when here). Otherwise the node (when here), else `ValueError`.
  `inline_extent`, `body_lines`, `_value_end` and `_value_start` work on `_local(node)`.
- `_token_floors`, `_block_entries_in_file`, `_block_scalar_key_columns` and the `_children` walk use
  `written_parts`, so `$ref` lines and override lines get their token floors, indentation detection and
  block-scalar owner columns.

## Data Models
- The plain form of a mapping-form include is `IncludeDirective("file:…", merged content, kind)`, the same as the
  scalar form. Changing between the forms therefore changes neither the plain tree nor any leaf (3.4, 4.7).

## Error Handling

### Error Strategy
| Case | Error | Exit | Names |
|------|-------|------|-------|
| `$ref` not an include string | ConfigLoadError | 4 | file, mapping path |
| Non-YAML target with overrides | ConfigLoadError | 4 | including file, path, target |
| Overrides on non-mapping YAML content | ConfigLoadError | 4 | including file, path, included file |
| `$ref` segment in `set` PATH | WriteRefusedError | 3 | root file, PATH |
| Mapping-form include root replaced with a different value | WriteRefusedError (existing) | 3 | PATH, included file |
| Conversion of an include string not on its owner's line | WriteRefusedError | 3 | directive file, PATH |
| Read-back mismatch / VALUE `$ref` that cannot load | WriteRefusedError (existing verify) | 3 | file, PATH |

A load error is raised before any history state is opened (2.4, existing `open_theatre` order).

## Testing Strategy

### Unit Tests
- Loader: bare YAML/text forms equal scalar form, deep merge (nested mappings, scalar over mapping, list
  replaces), merged order (1.3), no `$ref` in entries, `$ref` at root, in list item, in included file, flow form,
  override value that is itself an include, refusals 2.1–2.3 with messages, origins of overridden scalars.
- Overrides lookups: `physical_entry` for override-only, base-only and both-sides keys; holders with pruning
  chains; `written_parts`.
- Target placement: each walk case (merged root, bare form, scalar form, nested includes, list item stop, existing
  override mapping, root), `$ref` refusal.
- Planner: overridden scalar changed in the override; layered merge with removals in both layers and pruning;
  routed additions; conversion edits (comment / no comment / list item / flow / directive on next line refused).
- Layout: comment detection and indentation step in files with `$ref` mappings; slot extents for a mapping-form
  include root.
- Compare: directive equality by written overrides; `root_plain`.

### E2E Tests (real `troupe` console script, real files)
- The contract example byte for byte: `coder: file:agents/coder.yaml  # c` + `set agents.coder.temperature 0.3`
  gives exactly the three lines, with `coder.yaml` unchanged and one `set` event.
- A second new key goes after the first override. Nested missing keys create intermediate override mappings. A key
  under an included nested mapping becomes `params:` / `  k: v` in the override. Nested includes write in the
  middle file.
- Overridden value changed in the override. A non-overridden value inside the include is changed in the included
  file.
- List append in the included file. A new key in a list item inside the include goes to the included file. A
  list-item include is converted in the list file.
- Exit 4 cases through `get`. A `$ref` PATH is refused with exit 3. A VALUE with `$ref` round-trips.
- `get`/`show --sources` over overrides. History after hand-adding an override. No event for a form-only hand edit.
- The updated old rule-2 test in `test_set_targets.py`.
- `--in-include` (Req 10), byte-exact:
  - a scalar-form include gains the key in the included file;
  - a merged mapping gains the key on its included side, and `show` lists it before the override-only keys;
  - nested includes write in the innermost file;
  - an ancestor written only in an override writes in the including file;
  - a mapping VALUE's new keys go to the included file;
  - existing values and appends are unchanged by the option;
  - no include involved gives the same result as without the option;
  - the option's position among the arguments and its combination with `--reason`;
  - `--in-include=x` gives exit 2;
  - one `set` event with cli lineage.

## Security Considerations
No new inputs beyond YAML already read. Include targets resolve as before (relative to the including file).

## Migration Strategy
Configurations without `$ref` keys load exactly as before. A configuration that used `$ref` as an ordinary key
with a non-include value now fails to load (exit 4, choice 1). With an include value, it is now read as an
include.
