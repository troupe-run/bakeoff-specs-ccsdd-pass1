# Implementation Plan

**Conventions.** Every test written for this spec names the requirement IDs it proves in its docstring (for
example `"""Req 4.1: ..."""`), as in the earlier specs. Tests use real theatres on disk and the real CLI
(`testing.md`). Preservation tests compare whole files byte for byte. Each task ends with
`uv run pytest -n auto`, `uv run mypy --strict .`, `uv run ruff check .` and `uv run ruff format --check .`
passing.

**External prerequisites.** config-foundation, config-set-writeback, config-file-sync, config-get-show and
config-lineage are complete. No new runtime dependency is added.

- [x] 1. Foundation: the layered model and its lookups
- [x] 1.1 Record mapping-form includes and merged mappings in the model, and provide the deep merge and the written-mapping lookups
  - The model records the `$ref` mapping as written for a mapping-form include, and both layers (included side, override side) for every merged mapping. Scalar-form includes and unmerged nodes look exactly as before. The reserved key `$ref` has one named home.
  - The deep merge keeps the included keys in their order, replaces an overridden value in place (merging recursively where both sides are mappings), appends the override-only keys in their order, never carries the `$ref` entry, and never mutates its inputs.
  - The lookups give, for a key of a merged mapping, the written mapping and entry that hold its value (override side first). They also give every written mapping that holds a key, with the override chain leading to it, and the written parts of a node for walks over the files.
  - The loader does not recognise `$ref` yet (task 2.1), so these tests build merged mappings by calling the deep merge on mappings loaded from real files
  - Done when unit tests prove the merge result and order (nested mappings, scalar over mapping, list replaces, mapping over scalar), that overridden scalars keep their own origin, and each lookup for override-only, included-only and two-sided keys
  - _Boundary: Model, Constants, Overrides_
  - _Requirements: 1.2, 1.3, 1.5, 7.1, 7.2_

- [x] 2. Core: loading mapping-form includes
- [x] 2.1 Load mapping-form includes wherever a value may be written
  - A mapping with a `$ref` key whose value is an include string becomes an include of that target, relative to the file holding it. With overrides, its value is the deep merge. Bare, it is exactly the scalar-form include (YAML or text).
  - Works in the root file and in included files, at the document root, as a list item, in block and flow style, inside overrides and inside included content. An override value that is itself an include is resolved before merging. Missing targets, cycles and unloadable targets fail as for scalar-form includes, and the targets are listed among the files read.
  - Done when loader unit tests show these cases loading to the expected values (and the same plain form as the scalar form for a bare include), with no `$ref` key anywhere in the loaded value
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8_

- [x] 2.2 Refuse mapping-form includes that cannot mean anything
  - A non-YAML target with overrides, a `$ref` whose value is not an include string, and overrides on a YAML target whose content is not a mapping (including an empty file) are load errors (exit 4). Each message names the file, the key path and, where there is one, the target, and states the problem.
  - Done when loader unit tests assert each message, and an e2e test runs `get` on each case and gets exit 4, empty stdout, the message, and no history store created
  - _Requirements: 2.1, 2.2, 2.3, 2.4_

- [x] 2.3 Validate that every command reads the merged configuration
  - `get` and `show` print merged values. `show --sources` names the including file for overridden scalars and the included file for the others. Import and reconcile record the merged leaves: a hand-added override is recorded as `set` events of the leaves it changes, and rewriting an include between scalar and bare mapping form records nothing. `watch` follows `$ref` targets. A `$ref` segment in a `get`/`show` PATH is not found.
  - Done when e2e tests through the real CLI prove each of these, including a `watch` that reconciles an edit to a `$ref` target file
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 8.3_

- [ ] 3. File layer: writing below mapping-form includes
- [x] 3.1 Teach the file layout about `$ref` mappings and merged mappings
  - At a slot whose value is a mapping-form include root, the layout uses the `$ref` mapping as written (inline extent, body lines, content end). The walks that find token ends (comment detection), indentation step, dash offset and block-scalar owner columns visit the written parts of merged mappings and `$ref` mappings.
  - Done when layout unit tests on files with block and flow `$ref` mappings show the right comment positions, indentation step, content ends, and insertion point of a `$ref` mapping, and an existing-value change next to such a mapping leaves its lines untouched
  - _Requirements: 5.5, 9.1_

- [x] 3.2 (P) Compare and verify configurations whose root or values are mapping-form includes
  - The plain form of the root unwraps a root include, and read-back verification uses it (target resolution adopts it in 3.3).
  - A VALUE mapping whose `$ref` is an include string is an include directive with overrides. It equals a loaded include with the same string whose content contains the overrides (mappings recursively, other values equal).
  - Done when compare unit tests prove the containment equality (equal, missing override, differing override, nested) and the root unwrapping, and a verify test accepts a patched root `$ref` file that reads back as intended and refuses one that does not
  - _Boundary: Compare, Verify_
  - _Requirements: 8.2, 9.2_

- [x] 3.3 Decide where new keys are written and refuse `$ref` in a set PATH
  - A set PATH with a `$ref` segment is refused (exit 3) before anything else, naming PATH and stating that `$ref` is reserved for includes. Target resolution takes the current configuration from the unwrapped root plain form.
  - The placement walk goes up from the deepest existing mapping. A merged mapping places into its override side (descending through what the override already holds, and creating the keys it lacks as intermediate mappings). A bare mapping-form include places into its `$ref` mapping. A scalar-form YAML include is marked for conversion in its directive's file. A list item, or reaching the root, keeps the step 1 rule. The add-key target carries the result. The planner keeps writing into the deepest mapping until 3.4, so behaviour does not change yet.
  - Done when target unit tests cover each walk case (merged root, bare form, scalar form, nested includes choosing the innermost directive file, an existing override mapping, a key below an included nested mapping, a list item inside an include, a list-item include, a plain root mapping) and the `$ref` refusal
  - _Boundary: Target_
  - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.6, 6.2, 6.3, 8.1_

- [x] 3.4 Write a new key at its placement when that is a written mapping
  - The planner writes the new key (with any intermediate mappings from the placement) into the written mapping the placement names, under the step 1 rules for new keys there (insertion point, sibling styles, block or flow). A placement that needs a conversion keeps the step 1 behaviour (the deepest mapping) until 3.5 switches it over, so the suite keeps passing.
  - Done when planner unit tests show the exact texts for a key added to an existing override, to a bare `$ref` mapping, below an included nested mapping (creating `params:` in the override), and to a plain mapping (unchanged behaviour), and the full suite passes
  - _Requirements: 4.1, 4.3, 4.4, 4.5, 4.6_

- [ ] 3.5 Convert a scalar-form include to mapping form when its first override is added
  - Rendering helpers produce the `$ref` line and the new entries in block style (at a given column) and the `{$ref: <string>, k: v}` flow form, with the include string kept as written.
  - In a block mapping, the key line keeps its bytes through the `:`, then the whitespace before the end-of-line comment and the comment (or it ends at the `:`). Then follow `$ref: <include string as written>` and the new entries, one indentation step deeper. A block list item is compact without a comment, and starts on the next line with one. In a flow collection, the include string becomes `{$ref: <string>, k: v}`. An include string that does not end on its owner's line is refused (exit 3). The included file never changes.
  - Conversion placements switch over from the step 1 behaviour here. In the same change, the step 1 test that expected a new key below a scalar-form YAML include in the included file (and any other test pinning that) is rewritten to expect the step 5 result, and documented as superseding the old rule 2.
  - Done when render unit tests cover the helpers, and planner unit tests reproduce the contract example byte for byte and cover no comment, a list item with and without a comment, a flow mapping, a quoted include string kept verbatim, nested intermediate keys and the refused layout
  - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 6.3_

- [ ] 3.6 Change existing values at their written place
  - An existing value is changed where it is written: in the override when it is overridden, otherwise in the file it comes from. A whole mapping-form include root that would change is still refused, and a bare text include is still replaced in its target.
  - Done when planner unit tests show the exact file texts for an overridden scalar change (included file unchanged), a non-overridden change inside the include (including file unchanged), a merged mapping replaced by a scalar (only the override changes), the mapping-form root refusal and the bare text include replacement
  - _Requirements: 7.1, 7.2, 7.5, 7.6_

- [ ] 3.7 Merge mappings across layers: removals, pruning and new keys
  - A mapping placed over a merged mapping changes kept keys at their written place and removes dropped keys from every layer that writes them. An override mapping that would lose every entry is removed from its holder instead, never past a `$ref` mapping.
  - New keys of any mapping replacement (merged or not) go through the placement walk, into a written mapping or through the conversion.
  - Done when planner unit tests show the exact texts for a merge with removals in both layers, pruning of an emptied override, a merge whose new key becomes an override in an existing `$ref` mapping, and one whose new key converts a scalar-form include
  - _Requirements: 4.5, 7.3, 7.4_

- [ ] 3.8 Write new keys into the included file when `--in-include` is chosen (CR-B)
  - A set can ask for the step 1 placement. The new key (and the new keys of a mapping VALUE) then goes into the included side of the deepest existing ancestor, or into that ancestor itself when it is not merged. Existing values, appends and removals do not depend on the choice.
  - When keys go into the included side of a merged mapping, the intended configuration lists them after the included keys and before the override-only keys, so the ordered read-back holds.
  - Done when target and planner unit tests show the exact texts for: a scalar-form include, a merged mapping (with the merged order checked after reload), nested includes (innermost file), an ancestor written only in an override, a mapping VALUE with new keys, and a plain root mapping (same as without the choice)
  - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5_

- [ ] 4. Integration and validation
- [ ] 4.1 Validate new keys below includes end to end
  - E2E (byte-exact, every other file unchanged, history checked): the contract example with exactly one `set` event; a second new key after the first override; missing intermediate keys; a key below an included nested mapping; nested includes writing in the middle file; a bare `$ref` mapping gaining an override; a flow include; a `troupe.yaml` theatre.
  - Done when these e2e tests pass through the real CLI and each fails if the override is written into the included file
  - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 5.1, 5.2, 5.3, 5.4, 5.5, 9.1, 9.3_

- [ ] 4.2 Validate lists, existing values, collection replacement and `$ref` end to end
  - Appending to a list in an included file stays there. A new key inside a list item of an included list goes to the included file. A list-item include is converted in the list's file.
  - An overridden value changes in the override. A non-overridden value changes in the included file. A collection replacement under an include changes, removes and adds across layers. Replacing a mapping-form include root is refused with exit 3 and no change.
  - A `$ref` PATH segment gives exit 3 with no change. A VALUE with `$ref` round-trips as an include, and one with a missing target is refused with exit 3 and no change.
  - Done when these e2e tests pass byte-exactly through the real CLI, with no extra file left behind
  - _Requirements: 6.1, 6.2, 6.3, 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 8.1, 8.2, 9.1, 9.2, 9.3_

- [ ] 4.3 Accept `--in-include` on `set` and validate it end to end (CR-B)
  - `set` accepts `--in-include` without a value, anywhere among the arguments and together with `--reason`, and passes the choice to the set use case. `--in-include=TEXT` is a usage error (exit 2, empty stdout, no file or history change).
  - Done when e2e tests through the real CLI show byte-exact results for the cases of 3.8, the parsing cases above, one `set` event per created leaf with cli lineage and the given reason, and that without the option the same commands write overrides
  - _Depends: 3.8_
  - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7_

- [ ] 4.4 Align the earlier specs, docstrings and roadmap with step 5 and check traceability
  - Add step-5 notes to config-foundation Requirement 4, config-set-writeback rule 2 / Requirements 2 and 3 and 11.5 (the conversion exception), and config-get-show (merged view, override provenance), and the `--in-include` option on config-set-writeback's `set` usage (CR-B). Update module docstrings that describe includes or the new-key rule. Tick the step-5 items in the roadmap.
  - The spec, docstring and roadmap edits are the bookkeeping of the same change (collaboration.md). This task adds no behaviour.
  - Done when every requirement ID of this spec is named by at least one test docstring, the notes are in place, and the full test suite, mypy and ruff pass

## Implementation Notes
- 1.1: merged entries for a key that is a mapping on both sides keep the base entry's key/key_origin; writers must locate written positions through `physical_entry`/`physical_holders`, never merged entries. Pruning (3.7) uses `Holder.is_override` and `holder_of(parent, key, written)`. `ConfigMapping` equality includes `layers`: never compare merged and plain trees with `==` (use plain trees).
- 2.1: a mapping-form root now loads with `root.include` set; writeback must unwrap it (3.2 root plain, 3.3 resolve_target). Guards for 2.2's three refusals already raise ConfigLoadError in loader.py; 2.2 adds their tests and final messages.
- 3.1: layout `_local(node)` resolves what is written at a slot; walks use `written_parts`. Planner callers must pass written (physical) mappings to `content_end`/`insertion_point`, never merged ones. The loader now keeps a target root's own `$ref` include on the included side of a merge (fixed after 3.1).
- 3.2: `plain_equal` containment for VALUE `$ref` directives ignores key order (the renderer writes VALUE order); a contentless directive (plain `file:` VALUE, or `$ref` without overrides) still equals any loaded include with the same text, so such a `set` over an include with overrides is a no-op rather than a removal of the overrides (pre-existing leniency, recorded for validation).
