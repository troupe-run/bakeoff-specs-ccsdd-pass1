# Implementation Plan

**Conventions.** Every test written for this spec names the requirement IDs it proves in its docstring (for
example `"""Req 5.1: ..."""`), as in the earlier specs. Tests use real theatres on disk and the real CLI
(`testing.md`). Preservation tests compare whole files byte for byte. Each task ends with
`uv run pytest -n auto`, `uv run mypy --strict .`, `uv run ruff check .` and `uv run ruff format --check .`
passing. Existing tests that pin behaviour a task changes are updated in that same task.

**Where this spec's tests live.** Every test written for this spec goes in one of these new files, and nowhere
else. Existing test files only lose or adjust rows. This keeps the traceability check of task 5.2 exact, because
earlier specs use the same `Req N.M` numbers.
- `tests/test_merge_keys.py` (1.1), `tests/test_compose.py` (1.2)
- `tests/test_loader_aliases.py` (2.1, 2.2), `tests/test_loader_merge_keys.py` (2.3)
- `tests/test_e2e_aliases.py` (e2e of 2.1–2.4, 5.1)
- `tests/writeback/test_layout_aliases.py` (3.1), `tests/writeback/test_shared_aliases.py` (3.2),
  `tests/writeback/test_alias_guard.py` (3.3), `tests/writeback/test_planner_aliases.py` (3.4, 3.5),
  `tests/writeback/test_expand.py` (3.6, 3.7), `tests/writeback/test_setter_aliases.py` (4.3)
- `tests/test_set_alias_refusals.py` (4.1), `tests/test_set_anchors.py` (4.2), `tests/test_set_expand_alias.py` (4.4)

**External prerequisites.** config-foundation, config-set-writeback, config-file-sync, config-get-show,
config-lineage and config-include-overrides are complete. No new runtime dependency is added. ruamel.yaml stays
pinned at 0.19.1 (the composer hook depends on it).

**Safety window.** From task 2.1 the loader accepts anchors, but `set` only learns to handle them in task 4.1.
In between, `set` refuses (exit 3, no file changed) any configuration that contains an anchor, an alias or a
merge key. Task 2.1 adds this temporary refusal, pinned by a test, and task 4.1 removes it together with its test.
No commit in between can write through an alias.

**Product owner approval (2026-10-06).** The plan is approved as consistent with the step 6 "Aliases" section and
the earlier contract sections in force. This covers the design clarifications recorded with it (provenance through
`$ref` deep-merge, refusal order, verification against the original files) and the temporary `set` refusal
between tasks 2.1 and 4.1. The out-of-contract choices are accepted as recorded.

- [ ] 1. Foundation: provenance model, merge-key merge and alias recording
- [x] 1.1 Record anchors, alias places and merge keys in the model, and provide the merge-key merge
  - Every node can record the anchor written on it (name and the span of the `&name` property) and, at an alias place, the alias it stands for (name, the `*name` token's position, and the node as built at its anchor). A mapping can record the mapping as written behind its merge key. An entry can say that it came from a merge key. All new fields default to "none", so every existing construction and test is unchanged.
  - The merge-key merge is pure. It builds a mapping's merged view from the mapping as written and its merge sources: own entries in written order, and at the place of the `<<` entry the keys of the sources that the mapping does not write itself, with earlier sources first. Merged entries are marked, and no `<<` key is in the result.
  - The loader does not accept `<<` yet (task 2.3), so these tests merge mappings loaded from real files. The tests add the `<<` entry to the written mapping themselves, and the merge drops it at its index.
  - Done when unit tests prove own-key precedence, earlier-source precedence, the order of the merged view, the marking of merged entries, and that the inputs are not changed, and the full existing suite still passes
  - _Boundary: Model, MergeKeys_
  - _Requirements: 2.1, 2.2, 2.4_

- [x] 1.2 (P) Compose each file while recording every alias occurrence
  - Composing a file records, for every alias, its name and the span of its `*name` token, keyed by the parent node and the slot it fills (the key of a mapping value, the index of a list item, or "a key" for an alias used as a mapping key). Each record is checked against the source text, and a mismatch is a load error.
  - A reused anchor name produces no warning output. Syntax errors, several documents and undefined aliases are reported as before (exit 4, naming the file, and for an undefined alias the alias name). The loader switches its composing to this module. Anchors themselves are still refused at this point.
  - Done when unit tests on real file texts pin the recorded spans for an alias as a mapping value, as a list item, as a `<<` value, inside a list of merge sources, inside an anchored node, and as a key. A test also proves that a reused anchor name writes nothing to stderr, and the full suite still passes.
  - _Boundary: Compose, Loader_
  - _Requirements: 1.1, 1.2, 1.3_

- [ ] 2. Core: loading anchors, aliases and merge keys
- [x] 2.1 Accept anchors on values, with value spans that exclude the property
  - An anchor on any value node is accepted and recorded. The node's span is its value without the property: the first key for a block mapping, the first `-` for a block list, the next token for scalars and flow collections, and empty at the property's end for an empty null. An anchor on a mapping key stays a load error (exit 4) naming the file and the key path. Tags stay refused.
  - Until task 2.2 builds alias places, every alias recorded by the composer is a load error (exit 4) naming the file and the alias's key path ("aliases are not supported yet"). This keeps an alias from silently loading as a plain copy. `anchor-then-alias` in `tests/test_loader.py` and the `a: &x 1\nb: *x` row of `tests/test_e2e_locate_load.py` change their expected key path and message to this interim refusal.
  - `set` temporarily refuses (exit 3, no file changed) a configuration that contains an anchor, an alias or a merge key (see Safety window). This is pinned by a test in `tests/test_set_alias_refusals.py` that task 4.1 removes.
  - The existing anchor refusal tests change in the same task. In `tests/test_loader.py` (`REFUSALS`): `anchor-scalar`, `anchor-on-mapping`, `anchor-on-list-item` and `anchor-on-root` are deleted there and become accepted cases in `tests/test_loader_aliases.py`. `anchor-on-key` stays refused with its message. `anchor-after-tag` is now refused for its tag, so its expected marker changes. In `tests/test_loader_includes.py`, the `a: &x 1` case is replaced by an included anchor on a key. In `tests/test_e2e_includes.py`, the `x: &a 1` case is replaced likewise. In `tests/test_e2e_locate_load.py`, the parametrised refusal test is split: the anchor-only row `a: &x 1` is deleted there and becomes an acceptance test in `tests/test_e2e_aliases.py`, and the alias and merge-key rows stay until 2.2 and 2.3.
  - Done when loader unit tests show the recorded anchors and value spans for each node form, the anchor-on-key refusal message, and that no file is changed by loading. The updated tests and the temporary `set` refusal test pass.
  - _Requirements: 1.1, 1.4, 1.5, 1.6_

- [x] 2.2 Build alias places in the expanded configuration
  - At each alias, the anchored node is built again for that path. Its nodes carry the anchored node's origins, and its root records the alias (name, token, anchored node). Aliases inside an anchored node are found again when it is rebuilt at an alias place. A reused name refers to the most recent definition before the alias. Aliases work in the root file and in included files, at any depth, in block and flow style, on include strings and `$ref` mappings, and in anchored nodes that contain includes (each alias place includes them again).
  - The merged mapping built for a `$ref` include with overrides keeps the included side's anchor and alias (design, Loader). Merge-key provenance through overrides follows in 2.3.
  - An alias used as a key and an alias inside its own anchored node are load errors (exit 4) naming the file and the key path. An undefined alias, including one defined only in another file, stays a load error naming the file and the alias.
  - The interim alias refusal of task 2.1 is removed. Existing tests: `anchor-then-alias` is deleted from `tests/test_loader.py` and becomes an accepted case in `tests/test_loader_aliases.py`. `undefined-alias` keeps its refusal. The alias row of `tests/test_e2e_locate_load.py` (`a: &x 1\nb: *x`) is deleted there and becomes an acceptance test in `tests/test_e2e_aliases.py`.
  - Done when loader unit tests show expanded values and origins at alias places, the recorded alias tokens, nested and reused-name aliases, aliases of includes and of `$ref` mappings, the provenance kept through `$ref` overrides, and each refusal with its message. An e2e `get` on each refused case exits 4 with no history state created.
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.7_

- [x] 2.3 Load merge keys into the merged view
  - A plain `<<` key whose value is a mapping, an alias of a mapping or a list of those is merged into its mapping, and the mapping as written is kept behind the merged view. Any other value, and a `<<` key among the overrides of a mapping-form include, are load errors (exit 4) naming the file and the key path of the merge key. A quoted `<<` stays an ordinary key.
  - Existing tests: `merge-key` and `nested-merge-key` are deleted from `tests/test_loader.py` and become accepted cases in `tests/test_loader_merge_keys.py`. The `<<: {a: 1}` case in `tests/test_loader_includes.py` is deleted there and becomes an accepted included merge in the same new file. The merge-key rows of `tests/test_e2e_locate_load.py` are deleted there and become acceptance tests in `tests/test_e2e_aliases.py`, and a bad merge value is refused in an e2e test in `tests/test_e2e_aliases.py`, which also proves that no history state is created. `test_quoted_merge_key_text_is_an_ordinary_key` still passes.
  - A merged mapping built for a `$ref` include with overrides keeps the included side's merge-key provenance (its `merge` and the merged entries' marks). A loader test covers an included file with `<<` under a `$ref` mapping with overrides.
  - A pinned test in `tests/test_set_alias_refusals.py` proves that the temporary `set` refusal also covers a file whose only special construct is an inline merge (`<<: {a: 1}`): exit 3, file unchanged. Task 4.1 removes it with the other temporary test.
  - Done when loader unit tests prove merge semantics, order, nested merges, merges through aliases and inline mappings, the refusals with their messages, and that no `<<` segment appears in any path of the loaded configuration
  - _Requirements: 2.1, 2.2, 2.3, 2.4, 1.7_

- [x] 2.4 Validate that every command reads the expanded configuration
  - `get` and `show` print expanded values, and `show` prints no anchor, alias or merge key. `show --sources` names the file holding the anchored node for scalars reached through an alias or a merge key. Import records leaves at every alias path. A hand edit at an anchor is reconciled as `set` events at every path it changes. A hand expansion of an alias into an equal copy records nothing. `watch` reconciles an edit at an anchor.
  - Done when e2e tests through the real CLI prove each of these
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

- [ ] 3. File layer: layout, replication, guard, planner and expansion
- [x] 3.1 Teach the file layout about anchor properties, alias places and merge-key mappings
  - For an anchored value, the layout puts the slot's indicator end after the `&name` property and counts the property as a token on its line, so end-of-line comments after it are found and replacements keep the anchor. In walks over the nodes written in a file, an alias place is a leaf at its `*name` token, and a mapping with a merge key is walked as written (own entries and the `<<` entry). Entry slots of a merged view resolve its own entries.
  - Done when layout unit tests show the indicator end after a property, the comment after `defaults: &d  # c`, the end of an entry whose value is an alias, the insertion point in a mapping whose last entry is `<<`, and the indentation step of a file with anchors. Existing layout tests still pass.
  - _Requirements: 4.3, 8.5, 10.1_

- [x] 3.2 (P) Replicate changes through merge keys when building the intended configuration
  - Rebuilding the configuration from written locations re-applies merge keys to every mapping that has one. A change at an anchored node therefore appears at the anchor, at every alias place, and in every mapping that merges it, unless that mapping writes the key itself. A change at PATH defined at an anchor counts as reached by PATH.
  - Independent of 3.1 (no layout use), so it may run alongside 3.1 and 3.3
  - Done when unit tests on loaded documents show the widened intended configuration for a changed value, an added key and a removed key at an anchor used by plain aliases and by `<<: *d` (with and without a shadowing own key)
  - _Boundary: Shared_
  - _Requirements: 4.1, 4.2, 4.4, 10.2_

- [x] 3.3 (P) Decide refuse, expand or proceed for a set, and index anchor uses
  - The guard walks PATH (to its node, or to the deepest existing ancestor) and, for a collection replacement, the changed parts of the subtree at PATH. It runs after target resolution's own refusals and before any planner refusal.
    - The first alias place met gives an expansion site when expansion is allowed. Otherwise it gives a refusal (exit 3) naming the alias's file, PATH and `*name`, saying the value is shared, and mentioning `--expand-alias`.
    - The first merged entry met gives the merge-key refusal (exit 3) naming the file, PATH and `<<`, with or without the flag.
    - Nothing on the walk means proceed. Alias places whose values do not change below PATH are ignored. The search does not descend below an alias place.
  - The anchor-use index maps each anchored node's written location to the aliases that use it. The writeback import-order docstring gains the new module.
  - Done when unit tests on loaded documents cover each refusal, with its message asserted:
    - PATH through an alias, the alias's own key, and a deepest ancestor through an alias;
    - an alias place that holds an include (the alias refusal comes first);
    - a merged entry, with and without the flag;
    - a replacement below PATH with changed and unchanged aliases;
    - nested aliases (outermost site first);
    - a PATH with no alias.
  - _Boundary: Guard_
  - _Requirements: 5.1, 5.2, 6.3, 8.1, 8.2, 9.2_

- [x] 3.4 Write own keys of a mapping that has a merge key
  - Target resolution and the planner change an own entry of a mapping with a merge key in place, and add a new key after the mapping's last written entry (which may be the `<<` entry). They never write into the `<<` entry or its value.
  - Done when planner tests on real files compare the patched text byte for byte for a changed own key, a new key after a trailing `<<` entry, and a new key in a flow mapping with `<<`, with the merge key's bytes unchanged
  - _Depends: 3.1_ (layout of merge-key mappings), so not parallel with 3.1
  - _Boundary: Target, Planner_
  - _Requirements: 9.1, 9.3_

- [x] 3.5 Refuse removing a used anchor and converting an anchored include
  - A set whose removals would remove an anchored node that an alias outside the removed part still uses is refused (exit 3), naming PATH, the file and `&name`. A set that would convert an anchored scalar-form include to mapping form is refused (exit 3), naming PATH, the file and `&name`.
  - Done when planner tests on real files show both refusals with their messages and no patch, and a removal of an anchored node whose aliases are all removed with it is still planned
  - _Depends: 3.3, 3.4_
  - _Requirements: 4.6, 4.7_

- [x] 3.6 Expand an alias on its line: preconditions, comment and inline copies
  - The expander replaces one alias in its file and returns the new texts. It does not load anything; the setter reloads. On the key's line (or the list item's line), the alias and any trailing spaces are replaced by the comment `# expanded from *<name> by troupe`, two spaces after the `:` or `-`. A scalar (its source text), an empty collection (`{}`/`[]`) or a block-scalar header goes before the comment, and a block scalar's content follows, re-indented.
  - An alias inside a flow collection and a line that already has an end-of-line comment are refused (exit 3), naming the file, PATH and the alias. An anchored node that holds a merge key is refused (exit 3) with the merge-key message of task 3.3 (naming the file, PATH and `<<`, and saying that merge keys are never written), extended with the alias's name. The writeback import-order docstring gains the new module.
  - Done when unit tests on real files compare the expanded text byte for byte for a scalar (plain, single- and double-quoted), an empty mapping, a literal and a folded scalar, and a multi-line flow scalar, and assert each refusal
  - _Depends: 3.1, 3.3_
  - _Requirements: 7.1, 7.3, 7.4, 7.6, 8.4, 8.5, 9.1_

- [x] 3.7 Expand collection aliases into block copies
  - A non-empty mapping or list is copied in block style on the lines after the key's line, at the file's indentation step, dash offset and line breaks. Nested collections are also in block style. Keys keep their source text. Includes are copied as written (directive or `$ref` mapping). Inner aliases are copied as aliases. No anchor and no comment from inside the anchored node is copied. A list-item alias gets its copy under the item.
  - Done when unit tests on real files compare the expanded text byte for byte for a mapping, a list, a flow-style anchored mapping, quoted keys, an include string, a `$ref` mapping, an inner alias with an inner anchor, a list-item alias, a CRLF file and a 4-space file
  - _Requirements: 7.2, 7.4, 7.5, 7.6, 8.3_

- [x] 3.8 Replicate a replacement of an anchored node itself to its alias places
  - Added during implementation (review of 3.5). A slot replacement of an anchored node (for example `set a 2` with `a: &d 1` and `u: *d`, or a kind change such as `set defaults 5` or `'{}'`) is planned as `a: &d 2`. But the intended configuration does not carry the replacement to the alias places, so read-back verification refuses a correct write. Replacements entry by entry (mapping over mapping) already work. The fix is in building the intended configuration: a replacement recorded at an anchored node's slot also applies at every alias place and merge use of that node. The end-of-line comment after the anchor keeps its column under config-set-writeback's comment rules (currently it drops to one space).
  - Done when unit tests on loaded documents show the widened intended configuration and a read-back that passes for a scalar replaced at an anchor, a kind change (scalar to mapping, mapping to scalar, to `{}`), and an anchored node used through `<<: *d`. The anchor's comment column is pinned byte for byte, and existing tests still pass.
  - _Boundary: Shared, Planner_
  - _Requirements: 4.1, 4.3, 4.5, 10.2_

- [x] 3.9 Keep an anchor on a block list item that is replaced by a mapping
  - Added during implementation (review of 3.8). Replacing an anchored block list item with a mapping (`l:\n  - &d 1\n`, then `set l.0 '{k: 1}'`) writes `- &d k: 1`. YAML reads that anchor as belonging to the key `k`, so read-back refuses the set (exit 3, nothing written). Requirement 4.3 expects the set to succeed with `&d` kept on the new value. The mapping therefore starts on the line after `- &d`, at the list's item content indentation, under config-set-writeback's rules for a list item's scalar replaced by a block mapping. A list item replaced by a block list is handled the same way when needed.
  - Done when byte-exact planner tests on real files show `- &d` followed by the mapping on the next lines for a block list item (with and without an alias of it elsewhere, and with an end-of-line comment after the anchor), each reading back with the anchor kept and every alias seeing the new value. Existing tests still pass.
  - _Boundary: Layout, Planner_
  - _Requirements: 4.3, 10.1, 10.2_

- [ ] 4. Integration: the `set` command
- [x] 4.1 Run the guard in `set` and refuse writes through aliases and merge keys
  - `set` runs the guard after target resolution and before its no-op check. The temporary refusal of task 2.1 and its tests (tasks 2.1 and 2.3) are removed. A set through an alias, including the alias's own key and a no-op, is refused with no file changed and no event other than the import or reconcile it ran first. A set through a merge key is refused. The 4.6 and 4.7 refusals reach the CLI.
  - Done when e2e tests through the real CLI prove each refusal of Requirements 4.6, 4.7, 5 and 9.2, with its exit code, empty stdout, stderr naming the file, PATH and the alias or anchor (and `--expand-alias` for Requirement 5), unchanged file bytes and unchanged history
  - _Depends: 3.3, 3.5_
  - _Requirements: 4.6, 4.7, 5.1, 5.2, 5.3, 5.4, 9.2_

- [x] 4.2 Validate writes at anchors end to end
  - A set at an anchor writes at the anchor, keeps `&name`, and records events at every alias path. An own key of a mapping with `<<` is changed or added in that mapping, and the merge key is unchanged.
  - Done when byte-exact e2e tests prove:
    - an anchored scalar and an anchored mapping value changed;
    - a new key and a list append inside an anchored node, seen at each alias and through `<<: *d`;
    - a replaced anchored node keeping `&d`;
    - a replacement containing an anchored node;
    - own-key writes in a mapping with `<<`;
    - the history events of each write.
  - _Depends: 3.2, 3.4, 3.8, 3.9, 4.1_
  - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 9.1, 9.3, 10.1, 10.2_

- [x] 4.3 Expand before setting in the set use case
  - With expansion requested, the set use case expands one alias at a time in memory, reloads, and re-checks until no alias on PATH (or in the changed subtree) remains. Then it runs the usual set on the expanded configuration and writes every changed file once, atomically. The request and its widened intended configuration come from the plan on the expanded document. Verification gets the original document with the final patches remapped to the original files. Events compare the original configuration with the result. With expansion requested and VALUE equal to the current value, the expansion is still written and no event is recorded.
  - Done when tests that call the set use case on real theatres (`tests/writeback/test_setter_aliases.py`) prove:
    - a set in the copy with the other alias still reading the anchor's value after read-back;
    - nested aliases expanded in two rounds;
    - a replacement that expands only the aliases whose values change;
    - the no-op case (files expanded, no event);
    - the restore of the original files when a write fails after an expansion.
  - _Depends: 3.6, 3.7, 4.1_
  - _Requirements: 7.7, 8.1, 8.2, 8.6, 10.2, 10.3, 10.4_

- [x] 4.4 Accept `--expand-alias` on the command line and validate it end to end
  - `set` accepts `--expand-alias` without a value, before, between or after PATH and VALUE, together with `--reason` and `--in-include`, and passes it to the set use case. `--expand-alias=TEXT` is a usage error (exit 2).
  - Done when byte-exact e2e tests prove:
    - a set in the copy, with the anchor and the other aliases unchanged and one `set` event with cli lineage and `--reason`;
    - nested aliases each getting their own comment;
    - a list-item alias and a scalar alias;
    - a no-op with the flag still expanding and recording no event;
    - the flag with no alias behaving as without it;
    - flag positions and combinations, and `--expand-alias=x` exiting 2;
    - the flow and comment refusals leaving every file unchanged.
  - _Requirements: 6.1, 6.2, 6.3, 6.4, 7.7, 8.1, 8.6, 10.3, 10.4_

- [ ] 5. Validation
- [x] 5.1 Validate the issue #2 scenario end to end
  - The tests use the reporter's exact file:
    - `set coder.settings.model sonnet` exits 3, stderr names the file, `coder.settings.model`, `*d` and `--expand-alias`, and every file and the history are unchanged.
    - `set defaults.model sonnet` changes only `opus` at the anchor. `get reviewer.settings.model` prints `sonnet`, and history shows `set` events for the three paths.
    - On a fresh copy, `set --expand-alias coder.settings.model sonnet` produces exactly `  settings:  # expanded from *d by troupe` / `    model: sonnet` under `coder`, with `defaults` and `reviewer` byte-identical. `get reviewer.settings.model` still prints `opus`.
    - The same holds for `troupe.yaml`.
  - Done when this e2e test passes byte for byte
  - _Requirements: 4.1, 4.5, 5.1, 7.1, 7.2, 7.6, 7.7, 10.1_

- [ ] 5.2 Align the earlier specs, docstrings and roadmap with step 6 and check traceability
  - config-foundation 3.9 and decision H gain a step-6 note pointing here. config-set-writeback 1.1 (the `--expand-alias` option) and 2.1 (writes at anchors) gain step-6 notes. VALUE refusal 1.3 is unchanged. Module docstrings that still say anchors are refused are updated. The roadmap's step 6 items are checked off.
  - Traceability is checked over this spec's own test files only (the list under "Where this spec's tests live"). Earlier specs' tests also name `Req N.M` with overlapping numbers. To keep the check exact, every test written for this spec, including accepted cases that replace old refusal rows, lives in those files. Existing files only lose or adjust rows. The check collects every `Req N.M` from those files' docstrings with a grep and compares the set with this spec's requirement IDs.
  - Done when that check lists no requirement of this spec without a test and no test naming an unknown ID, and `pytest`, `mypy --strict` and `ruff` all pass
  - _Requirements: 1.1, 2.1, 3.1, 4.1, 5.1, 6.1, 7.1, 8.1, 9.1, 10.1_

## Implementation Notes
- 1.1: the `<<` text currently has two private homes (`loader._MERGE_KEY`, `yaml_style._MERGE_KEY`). Task 2.3 must make one module its single named home and have the other import it (structure.md "one decision, one place"). `merge_mapping` requires `merge_index` to point at the plain `<<` entry of `written`.
- 1.2: every alias used as a key in one mapping shares the slot `(id(parent), None)`, and only the first is recorded. That is enough for the alias-as-key refusal (2.2). `yaml_style.py` composes on its own, so a later task writing at anchors should check that it cannot print `ReusedAnchorWarning`.
- 2.1: an anchor written on an include string or a `$ref` mapping is recorded on the included node, but its `Anchor.span` lies in the **including** file (the file of `include.directive`), not in the node's `origin` file. Layout (3.1) and planner (3.5) must use the directive's file for such anchors. Tags next to an anchor are detected by a token scan (`_tags_next_to_anchors`), because ruamel starts an anchored node's marks at the anchor. The interim alias-as-key refusal names the mapping's path; 2.2 owns the final message. The temporary `set` refusal sits at the top of `set_value` and walks `layers`, `include.form` and `merge.written`.
- 2.2: an alias place is a full rebuild, so its root (and any anchored descendant) **also carries the anchored node's `anchor`**, besides `alias`. Every consumer (layout 3.1, guard 3.3, `anchor_uses` and the 4.6 planner check, expander) must check `alias` before `anchor`, or it will take an alias place for a second anchored node. `$ref: *i` is accepted. The alias is recorded only on the `$ref` scalar inside `include.form` (not on the included node), so the guard must walk `include.form` to see it.
- 2.3: `MERGE_KEY` lives in `troupe/config/constants.py`, its single home. Includes (include strings, `$ref` mappings, aliases of either) are refused as merge sources (exit 4); a source's *nested* values may be includes. `<<: *list` is refused and `<<: []` merges nothing. A merged entry reused by `deep_merge` keeps `via_merge=True` even when an override deep-merges into it, so the guard (3.3) treats it as reached through a merge key, which is conservative.
- 3.1: `FileLayout.entry_slot` raises `ValueError` for an entry with `via_merge`. The guard (3.3) and target/planner (3.4) must refuse or resolve to own entries before asking the layout for a slot. An anchor on a scalar include is found as the `&name` text right before the directive in the including file. `_block_scalar_key_columns` still loops over an alias place's own entries, which is harmless because they keep the anchored node's columns.
- 3.2: the merge-key precedence rule lives only in `merge_keys.merged_pairs`, which both `merge_mapping` (on entries) and `shared._Rebuild` (on plain values) use. Alias places needed no rebuild change for changes *inside* an anchored node, because they share its written locations (a replacement of the anchored node itself needed 3.8). The reach check still refuses a change at an anchor when PATH runs through a merged entry, which is conservative; the guard (3.3) refuses that PATH first.
- 3.3: the guard walks the written layers side by side (research.md Decision 5, implementation note). An aliased `$ref` counts only for the include's included side, so override keys behind it proceed. `anchored_location` keys an include root by its `$ref` mapping (when the anchor is on the form) or its include string; 3.5 must reuse it for the 4.6 check. The refusal messages and helpers (`alias_refusal`, `merge_problem`) live in `writeback/aliases.py`; the expander (3.6) reuses `merge_problem`.
- 3.4: the planner refuses on its own (`_refuse_through_merge`, using `aliases.merge_error`) any PATH through a merged entry, as a backstop behind the guard. A whole replacement of a mapping with `<<` is refused (9.1). A replacement that drops an own key shadowing a merged key is refused only by read-back verification, with the generic message (4.x may give the specific one). New keys take the style of the last own entry, not of `<<`.
- 3.5: `aliases.alias_paths` gives the first alias-place path of each `*name` token, for messages. `_entry_bounds` now removes an anchored list item together with its `&name`. The 4.7 refusal fires whenever the include root carries an anchor, including an include chain converted at an inner include (`i: &i file:b.yml` where b.yml is `file:c.yml`). That is stricter than needed, kept as a conservative choice and recorded in research.md. Review of 3.5 found that replacing an anchored node itself fails read-back; task 3.8 was added for it.
- 3.6: expander refusals name the alias place's path (`site.path`), because `expand_alias(document, site)` has no PATH. Task 4.3 must re-raise them with the user's PATH (8.4, 8.5, 9.2), and 4.4's e2e tests must check it. Two temporary refusals (`COLLECTION_NOT_YET`, `INCLUDE_NOT_YET`) and their tests are removed by 3.7. `expand.py` imports `writeback.value` (`NewScalar` for `render_scalar`), one more allowed dependency than the design lists. An anchored empty null expands to `key:  # expanded from *d by troupe`. A full-line comment between a key and an alias written on the next line is refused, so it is never lost.
- 3.7: at the end of a file without a final line break, a copy adds no final break (config-set-writeback 11.6) unless its last line ends a clip or keep block scalar (`_Copy.final_break`). The layout measures the indentation step from the first nested mapping. When that mapping starts with an explicit `?` key, the layout measures after the `?`, which is existing behaviour outside this spec.
- 3.8: a slot replacement of an anchored node is also stored under the node's own written location (`WrittenChanges.anchored`), and `_Rebuild` returns it at every alias place and merge use. `_graft`/`_changed` use an `_ABSENT` marker, so a null value can be copied off PATH. A merge source replaced by a non-mapping merges nothing in the intended configuration, and read-back refuses it with exit 3. The one-space comment after `&d 5` follows config-set-writeback 12.2. Review of 3.8 found that an anchored block list item replaced by a mapping writes `- &d k: 1`; task 3.9 was added for it.
- 3.9: `FileLayout.has_property(slot)` tells whether a slot's value has its own `&name` written there. `_replace_value` uses the compact item form only when there is no end-of-line comment and no property, so an anchored item replaced by a collection keeps `- &d` alone on its line.
- 4.1: the temporary `set` refusal is gone. `set_value` calls `alias_sites(..., expand=False)` between `resolve_target` and the no-op check. Wiring it in exposed a guard crash when a mapping is replaced by a list, fixed with `_same_kind` (a change of collection kind removes every child). Task 4.4 must add the e2e case for 9.2 with `--expand-alias`, still refused.
- 4.3: after each expansion round, the setter checks that the whole configuration is unchanged. If it changed, it refuses with exit 3 (`EXPANSION_CHANGES_VALUES`, naming the expanded alias). This is the safety net for a copied inner alias that binds to a redefined anchor of the same name. A stuck expansion is refused with exit 3 too (`EXPANSION_STUCK`). Expander refusals are re-raised with the user's PATH. The expander module is imported as `expander`. Task 4.4 should cover the redefined-anchor refusal end to end.
- 4.4: an alias of an empty anchored mapping expands to `s: {}  # expanded from *d by troupe`. Setting a key in it then gives `s: {k: 1} # expanded from *d by troupe`, with one space before the comment as config-set-writeback 12.2 requires; 7.7 applies the usual comment rules to the set in the copy.
