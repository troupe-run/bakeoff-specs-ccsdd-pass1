# Research & Design Decisions: config-include-overrides

## Summary
- **Feature**: `config-include-overrides` (step 5, CR-A "keep shared includes shared", contract section "Overrides")
- **Discovery Scope**: Extension (light discovery: integration points in the loader, model and writeback)
- **Key Findings**:
  - Every reader (`get`, `show`, `--sources`, leaf flattening, reconcile, history, watch) works on the loaded
    tree by path and per-node `origin`. If the loader produces the *merged* value with correct per-scalar
    origins and no `$ref` key, readers need no change.
  - The writer (`writeback/`) assumes that a collection and all its entries are written in one file
    (`FileLayout.entry_slot`, `insertion_point`, `_value_end`, and the tree walks `_token_floors`,
    `_block_entries_in_file`, `_block_scalar_key_columns`). A merged mapping breaks that assumption, so the
    writer must reach the *written* (physical) mappings behind a merged one.
  - The existing scalar → block-collection machinery (`_replace_value`) almost performs the scalar-form →
    mapping-form conversion, but it re-renders the value (the include string's bytes must be kept) and moves the
    key line's comment back to its column (config-set-writeback 11.5), while the contract example moves it next to
    the `:`. The conversion therefore needs its own small edit plan.

## Research Log

### How includes and set targets work today
- **Context**: where step 5 plugs in.
- **Findings**:
  - `loader._Builder.build` turns any string scalar starting with `file:` into an include root
    (`dataclasses.replace(embedded, include=IncludeRef(...))`). There is no `$ref` handling. A `$ref` key is an
    ordinary key today, and its `file:` value is expanded in place.
  - `target.resolve_target` → `AddKeyTarget(mapping, missing)`. `planner.add_entries` writes into
    `collection.origin.file`, which is the included file for a mapping inside an include. This is pinned by
    `tests/test_set_targets.py::test_new_key_below_yaml_include_is_added_in_the_included_file`.
  - `planner.change_slot` refuses a YAML include root that would change (`_change_include`, exit 3) before any
    layout work.
  - `compare.node_to_plain` wraps every include root in `IncludeDirective(text, content)`. Verification compares
    `node_to_plain(after.root)` with the spliced intended tree, ordered.
  - `layout._directive` gives the directive span for an include root whose directive is in the laid-out file.
    `inline_extent`, `body_lines` and `_value_end` use it.
- **Implications**: if a mapping-form include root is still "an include root" with the same `IncludeDirective`
  plain form, splicing, no-op detection, the rule-4 refusal and verification keep working unchanged. Only
  physical location lookups need the layers.

### Comment handling of the conversion
- **Context**: the contract example `coder: file:agents/coder.yaml  # c` → `coder:  # c`.
- **Findings**: `apply_edits` re-aligns the end-of-line comment after every INLINE edit (12.1). The step 1 rule for
  scalar → block collection (config-set-writeback 11.5) keeps the comment at its previous column. The example
  instead removes ` file:agents/coder.yaml` (the space after `:` and the include string) and keeps the two spaces
  before `#`.
- **Implications**: the conversion deletes `[end of ':', end of include string)` with an edit kind that does not
  trigger comment re-alignment. This is recorded as Decision 6.

## Architecture Pattern Evaluation

| Option | Description | Strengths | Risks / Limitations | Notes |
|--------|-------------|-----------|---------------------|-------|
| Merged view + layers on nodes (chosen) | Loader builds merged mappings that keep `MergeLayers(base, over)`. Mapping-form include roots keep their written `$ref` mapping in `IncludeRef.form`. | Readers unchanged; provenance travels with the node it describes; one merge implementation | Writer and layout walks must use written parts rather than merged entries | Small, typed additions to the frozen model |
| Physical tree + merge on read | Model stays physical; every reader merges | Writer unchanged | Every reader (`get`, `show`, leaves, reconcile, path walk) re-implements the merge; drift | Rejected |
| Overlay keyed by path | Side table of override provenance | Model untouched | Second source of truth; planner must join two structures | Rejected |

## Design Decisions

### Decision 1: The loaded tree is the merged view; layers stay attached
- **Context**: the contract defines the value of a mapping-form include as the deep merge (1.2); all commands must
  agree on it (3.1–3.5).
- **Alternatives**: see the table above.
- **Selected Approach**: `ConfigMapping.layers: MergeLayers | None` (`base`, `over`) on every merged mapping (the
  include root and every nested mapping present on both sides). `IncludeRef.form: ConfigMapping | None` holds the
  `$ref` mapping as written, for bare and overridden mapping-form includes. The merged node's `include` is the
  include's ref (root) or the base value's ref (nested), so `node_to_plain`, rule 4 and splicing behave as for
  scalar-form includes.
- **Rationale**: readers see the contract's value directly. The writer finds the written mapping through
  `layers`/`form` without a second structure.
- **Trade-offs**: layout walks must visit written parts instead of merged entries (one helper, `written_parts`).
- **Follow-up**: anchors/aliases (step 6) will have to decide how they interact with layers.

### Decision 2: Where a new key goes (placement walk)
- **Context**: Requirements 4 and 6 (and 7.3, which routes the new keys of a collection replacement there).
- **Selected Approach**: walk up from the deepest existing ancestor A:
  1. a merged mapping X → place in X's override layer (descend the remaining keys through what the override
     layer already has, then apply the same walk inside it, never above it);
  2. a YAML include root in mapping form without overrides → add to its `$ref` mapping;
  3. a YAML include root in scalar form → convert it to mapping form, in the file holding its directive;
  4. a list item → the step 1 rule (A's own file);
  5. the root → the step 1 rule.
  The keys between the stop node and A become intermediate mappings created in the override.
- **Rationale**: this is the contract's rule ("the file that contains the include reference"), with the nearest
  include winning for nested includes (choice 5) and an already-written override winning (choice 6). Overrides
  cannot address list items, so a list item stops the walk (6.2, 6.3).
- **Follow-up**: none.
- **Revision (validate-impl remediation)**: the first walk (stop at the first merged mapping, into its override
  side) did not implement choice 5 (PO-confirmed: "the file holding the reference to the include that the deepest
  existing ancestor's content comes from, even when that file is itself included") for chained includes: with
  a.yaml = `$ref: file:b.yaml` (or `file:b.yaml`) and `c.sub` only in b.yaml, `set c.sub.n` wrote into troupe.yml.
  The walk now follows the layers holding A's content (design.md "Placement walk"): at a merged mapping it takes
  the override side only when that holds A, else the included side; going up from A's layer the innermost YAML
  include root wins (its `$ref` mapping, or a conversion, also of a file's whole root), and an override side or
  list item places into the written mapping holding A. The loader keeps a chained include as `IncludeRef.inner`
  so the walk can see it. A consequence under 4.1: a root file that is itself `file:x.yaml` now takes new keys as
  overrides in the root file (`$ref: file:x.yaml` plus the entries) instead of in x.yaml.
  **Alternatives**: keep writing into the outermost including file (refused by the PO's
  reading of choice 5). **Revisit**: if included files must never change at all.

### Decision 3: Choices where the contract is silent
Recorded in requirements.md (Boundary Context, choices 1–11). The ones with design weight:
- `$ref` not an include string → exit 4. **Alternatives**: treat it as an ordinary key. **Why**: `$ref` is reserved
  now, and a typo must not silently become an ordinary key. **Revisit**: if the product owner wants `$ref` to be
  usable as a data key.
- Overrides on a non-mapping YAML include → exit 4. **Alternatives**: the overrides replace the content. **Why**:
  replacing would silently drop the included content. **Revisit**: if lists should be overridable.
- Nested includes → the innermost include's reference file. **Alternatives**: always the root file. **Why**: the
  contract names "the file that contains the include reference". **Revisit**: if the product owner says included
  files may never change at all.
- Removing a key in a collection replacement → removed from every layer that writes it. **Alternatives**: refuse
  (exit 3). **Why**: "existing settings still change where they live", and the result is exactly the requested
  configuration. **Revisit**: if removals must never touch shared files either.

### Decision 4: Removing every entry of a nested override
- **Context**: a collection replacement can remove every key of a nested override mapping, for example
  `params: {b: 2}`. Deleting the lines would leave `params:`, which is null. A null override replaces the included
  mapping, so the read-back would differ (exit 3).
- **Selected Approach**: when a layered merge would delete every entry of a nested override mapping and adds none
  to it, it deletes that override's own entry from the mapping holding it instead. This repeats upward, but never
  past a `$ref` mapping, which keeps its `$ref` entry.
- **Rationale**: the requested configuration is written instead of refused, and no empty override is left behind.
- **Trade-offs**: one more planner rule, covered by its own test.

### Decision 5: `$ref` in VALUE is written as written and verified as an include
- **Context**: choice 10; config-set-writeback 2.7 / decision D for `file:` strings.
- **Selected Approach**: `new_to_plain` turns a VALUE mapping whose `$ref` is a `file:` string into an
  `IncludeDirective(text, content=None, overrides=<plain overrides>)`. Equality with a loaded directive requires
  the same text and that the loaded content contains the overrides: mappings contained recursively, any other
  value equal. The mapping itself is rendered like any other mapping.
- **Rationale**: the read-back proves that the written mapping loads as the include it describes. Anything that
  cannot load is refused with exit 3 (verify wraps load errors).

### Decision 6: The conversion follows the contract example for the comment
- **Context**: choice 11.
- **Selected Approach**: block mapping or block list item with an end-of-line comment: delete from the end of `:`
  (or `-`) to the end of the include string, with an edit that does not re-align comments. Without a comment:
  delete to the line end, as 11.5 does. The `$ref` line and the overrides follow on new lines. A compact list
  item without a comment gets `$ref: <string>` in place of the string (like 8.4). In a flow collection, the
  include string is replaced by `{$ref: <string>, k: v}` and Requirement 12 applies as usual.
- **Rationale**: the step 5 contract is the newer and more specific text. 11.5 is unchanged for every other
  replacement.
- **Revisit**: if the product owner says the example was shorthand and 11.5 should apply.

### Decision 7: Include strings not on their owner's line
- **Context**: an include string written on the line after its key, or a quoted include string spanning lines.
- **Selected Approach**: the conversion is refused with exit 3 ("this layout cannot be written"). Nothing is
  changed.
- **Rationale**: this follows the conservative default and the existing 14.9 refusals. Such layouts are rare, and
  guessing could misplace the comment or the key line.

### Decision 8: `set --in-include` selects the step 1 placement (CR-B)
- **Context**: contract section "In-include": `set --in-include PATH VALUE` writes a new key into the included file
  instead (the step 1 rule).
- **Alternatives**:
  1. A flag that bypasses the placement walk and uses the deepest ancestor's included side (chosen).
  2. A separate command (`set-in-include`) — the contract names an option of `set`.
  3. Applying the option to existing values as well, for example removing an override so the included value
     shows — the contract speaks only of a new key, and "existing settings still change where they live" (CR-A).
- **Selected Approach**: `in_include` travels from the CLI to `resolve_target`. `place_new_keys` returns the
  included side of the deepest existing ancestor, with no prefix (requirements choices 12–15). The intended tree
  places keys written into a merged mapping's included side before its override-only keys, so the ordered
  read-back comparison holds (10.2).
- **Rationale**: the option only chooses between two rules for one decision, which this spec already owns. It
  needs no new write machinery: the step 1 insertion code writes into a written mapping.
- **Revisit**: if the product owner wants `--in-include` to also move existing overrides into the included file.

### Decision 9: A mapping that loses every key becomes `{}`, never null (found in implementation, task 3.7)
- **Context**: in a collection replacement, a written mapping (often in the included file) can lose every entry
  and gain none. For example, VALUE replaces a merged mapping, or `{}` is placed over one. Deleting its lines
  would leave `key:`, which is null. The read-back check can miss that when an override hides the null.
- **Selected Approach**:
  - A nested override that empties is pruned (Decision 4).
  - Any other written mapping that empties is rewritten as `{}` at its written entry.
  - `{}` placed over a merged mapping prunes the override and writes `{}` on the included side.
  - When no written entry can hold the `{}`, the set is refused with exit 3.
- **Alternatives**: refuse with exit 3 (safe but rejects valid requests); leave null (changes the type of the
  included value, which nobody asked for).
- **Rationale**: the file keeps the requested type, and the result matches the flow-style behaviour (step 1 writes
  `{}` for an emptied flow mapping).
- **Revisit**: if removals must never touch shared files.

### Product owner review (2026-10-05)
All six decisions presented (requirements.md Boundary Context choices 1, 2, 5, 7, 8, 9 and 11) are approved.
- **Contract meaning, confirmed**:
  - Decision 6 / choice 11: the comment position in the conversion follows the Overrides example literally. The
    `set` end-of-line comment rule (config-set-writeback 11.5, 12.x) still applies to every other change.
  - Choice 5: nested includes write in the file holding the reference to the include that the deepest existing
    ancestor's content comes from, even when that file is itself included.
  - Choice 9: removals during a collection replacement follow CR-A and the last Overrides bullet.
- **Out of contract, our choice, accepted**:
  - Choices 1 and 2: exit 4.
  - Choices 7 and 8: exit 3, consistent with rule 4 under `set`.
- **Status**: confirmed; revisit only if the contract changes.

### Decision 10: An include's own string over its overrides is refused (choice 16, 2026-10-05)
- **Context**: `set agents.coder file:agents/coder.yaml` while `agents.coder` is `{$ref: file:agents/coder.yaml,
  temperature: 0.5}`. Implementation had treated a contentless include VALUE as equal to any loaded include with
  the same string, so this exited 0 and kept the overrides.
- **Alternatives**: a no-op (config-set-writeback 2.8 read literally); removing the overrides (a new write path the
  contract does not define); exit 3.
- **Selected**: exit 3. Dropping the overrides changes the value at a place whose whole value comes from an include,
  which choice 8 and step 1 rule 4 refuse. A silent no-op would hide that the overrides were kept. The product owner
  confirmed this is out of contract and accepted the choice.
- **Detail**: a VALUE include equals the loaded include only when the string is the same and its overrides are
  exactly the written overrides (none for a scalar-form or bare include, so 2.8 still holds there).
- **Revisit**: if the contract adds a way to remove overrides.

### Decision 11: A shared including file receives the override (choice 17, 2026-10-05)
- **Context**: the file holding the include reference (for example a middle file of a chain) is itself included at
  two places. Step 1's read-back check (config-set-writeback 14.2) refused such a write, because the key then
  appears at both places.
- **Selected (product owner)**: follow the contract's definition. The override is written in that file, and the
  new key appears wherever the file is included. The intended configuration used by the read-back therefore gains
  the same keys at every place reaching that written mapping or include site, in merged position. It applies to
  new keys placed by the override rule (Requirements 4 and 7.3). Changes of existing values and `--in-include`
  keep the step 1 check (config-set-writeback 14.2), which this decision does not touch.
- **Revisit**: if the product owner wants the same for existing values in shared files.

### Decision 12: Every write follows the placement rules in shared files (choice 18, 2026-10-05; extends Decision 11)
- **Context**: Decision 11 covered only keys placed by the override rule. The product owner then confirmed that no
  contract rule refuses a write because its target file is included at more than one place. This covers existing
  values (rule 1), `--in-include`, new keys in list items, and removals.
- **Selected**: write to the shared file. The intended configuration replicates each written change (identified by
  the written location: file real path and span) at every other place reaching that location, merged with that
  place's own overrides. The read-back comparison stays exact. config-set-writeback 14.2's example ("because the
  changed file is included at more than one place") no longer leads to a refusal. A read-back that differs for any
  other reason still does.
- **Mechanism (task 6.1)**: the planner records each change as a semantic edit of its written location
  (a slot replaced, entries removed or added, an include string converted, a text file replaced;
  `shared.WrittenChanges`). The plain configuration is recomputed from the pre-write model with and without
  these edits, every merged mapping rebuilt from its written sides by the plain deep merge, so each place's
  own overrides keep precedence and merged order. Off PATH, wherever the two differ, the edited value
  replaces `request.intended`; along PATH `request.intended` is kept. This replaces Decision 11's search for
  receiving layers (and the `by_override` mark): override-rule keys are just one kind of recorded edit.
  Every recorded location must also be reached at PATH's own place (else exit 3), so off-PATH changes are
  only replicas. A new YAML include merged at another place is loaded from the pre-write files.
- **Conflict (out of contract, our choice)**: one VALUE asking for different values at two places that map to the
  same written location is refused with exit 3 naming PATH and the file. Agreeing values are written once. A refusal
  cannot lose data, while picking one value would silently drop the other.
- **Revisit**: if the product owner defines a precedence between places.

### Product owner review of CR-B (2026-10-05)
Requirement 10 and choices 12–15 (Decision 8) are approved.
- **Contract meaning, confirmed**:
  - Choice 12: In-include changes only where a new key is written.
  - Choice 13: "the included file" is the step 1 rule's file, the innermost one for nested includes, including when
    the ancestor is also, or only, in an override.
- **Out of contract, our choice, accepted**: choices 14 and 15.
- **Status**: confirmed; revisit only if the contract changes.

## Synthesis Outcomes
- **Generalization**: one placement walk serves both `AddKeyTarget` (4.x, 6.x) and the new keys of collection
  replacements (7.3). One physical-entry lookup serves replacement (7.1, 7.2), merge and deletion (7.4).
- **Build vs adopt**: no library involved. ruamel stays parse-only.
- **Simplification**: scalar-form and mapping-form includes share one plain form (`IncludeDirective`), so splice,
  no-op detection, rule 4 and verification need no new cases except the VALUE `$ref` form (Decision 5).

## Risks & Mitigations
- Layout walks miss written nodes inside merged mappings, which would break comment detection or indentation. A
  single `written_parts` helper is used by every walk, with layout unit tests over mapping-form files.
- Shared (diamond) included content is merged per include site. The merge builds new nodes and never mutates
  shared ones, and verification catches any cross-site effect (exit 3).
- Pinned step 1 tests change meaning. The one test of the old rule 2 is rewritten in this spec's tasks (same
  change as the behaviour).

## References
- `.kiro/specs/config-set-writeback/requirements.md` (rules 1–4, Requirements 2, 3, 5, 11, 12, 14)
- `.kiro/specs/config-foundation/requirements.md` (Requirement 4: Includes)
