# Research & Design Decisions: config-aliases

## Summary
- **Feature**: `config-aliases` (step 6, tracker issue #2, contract section "Aliases")
- **Discovery Scope**: Extension (light discovery: integration points in the loader, model and writeback, plus
  a check of how ruamel.yaml 0.19.1 composes anchors, aliases and merge keys)
- **Key Findings**:
  - ruamel's round-trip composer returns the **same `Node` object** for an alias as for its anchor. The alias's
    own position (`*d`) is not on the node, and the anchor's marks start at the `&d` property. The parser's
    `AliasEvent` does carry the `*name` span. A `Composer.compose_node(parent, index)` override sees every alias
    before it is resolved, together with its parent node and slot (the key node for a mapping value, the int
    index for a list item, `None` for a mapping key). That is an exact way to find each alias occurrence.
  - The model already builds **separate node objects per place**, even for a file included twice (diamond).
    Writes are keyed by **written location** `(real_path, start, end)` (`writeback/shared.py`), and since
    choice 18 of config-include-overrides a write is replicated at every place that reaches its location. An
    alias place whose nodes carry the anchored node's origins is exactly such a second place. The "change at
    the anchor appears everywhere" rule (4.1, 10.2) therefore falls out of the existing `with_other_places` and
    `_Rebuild`, once they also re-apply merge keys.
  - Every reader (`get`, `show`, `--sources`, leaves, reconcile, history, watch) works on the loaded tree. If
    the loader expands aliases and merge keys into it, with per-scalar origins at the anchored node, readers
    need no change (Requirement 3). Only the writer needs to know where aliases and merge keys are physically
    written.

## Research Log

### How ruamel.yaml 0.19.1 composes anchors, aliases and merge keys
- **Context**: the loader works on composed nodes (`YAML(typ="rt").compose`), never on events.
- **Sources Consulted**: `ruamel/yaml/composer.py` (installed 0.19.1, `Composer.compose_node`,
  `return_alias`); experiments in this repository (`uv run python`).
- **Findings**:
  - `defaults: &d {…}` / `settings: *d`: both places hold the same `MappingNode` (`a is c`), with
    `anchor == "d"` and the anchor's marks. `start_mark` of an anchored node is at `&`.
  - `compose_node(self, parent, index)` begins with `if self.parser.check_event(AliasEvent)`. A subclass can
    `peek_event()` there and record `(id(parent), slot) → (name, start, end)` before delegating. Observed
    records were `('MappingNode', 's', 'd', 21, 23)`, `('SequenceNode', 0, 'd', 30, 32)`,
    `('MappingNode', '<<', 'd', 47, 49)`, and for an alias used as a key `('MappingNode', None, 'd', 50, 52)`.
    `YAML().Composer` can be replaced per instance.
  - A recursive alias (`a: &a {b: *a}`) composes without error. The loader must detect it itself.
  - A reused anchor name composes, and the later alias refers to the latest definition, but ruamel emits a
    `ReusedAnchorWarning` through `warnings` (it would reach stderr).
  - An undefined alias raises `ComposerError("found undefined alias 'x'")` with a mark. The loader already
    reports composer errors as exit 4 naming the file.
  - A merge key is an ordinary key node with tag `tag:yaml.org,2002:merge`, value `<<` and plain style. At
    compose time nothing is merged (`MappingNode.merge is None`, both entries present). Merging is up to us.
  - An anchor on a key (`&k a: 1`) composes.
- **Implications**:
  - Alias occurrences are recorded by a composer subclass keyed by parent node and slot. A record is checked by
    the token text: the span must read `*<name>`. Keys use `id()` of the slot's key node, which lives as long
    as the composed tree.
  - The loader keeps the refusal for anchors on keys, adds a refusal for aliases as keys, detects recursion with
    a build stack, and silences `ReusedAnchorWarning` narrowly around composing.
  - Merge keys are implemented in our own code (like `$ref` overrides), so their typing, order and refusals are
    ours to define (Requirement 2).

### Where the writer depends on "one node, one written place"
- **Context**: what breaks when the content at an alias place is the anchored node's content.
- **Findings**:
  - `layout.FileLayout` walks the nodes written in a file (`_written`, `_children`, `written_parts`,
    `_token_floors`, `_block_entries_in_file`) to find entry ends, insertion points, comment floors and the
    indentation step. If an alias place were walked like a written node, its span would be the anchor's span
    (earlier in the file). Entry ends, insertion points and comment detection after an alias would all be wrong.
  - `ValueSlot.indicator_end` (after `:` or `-`) is where inline replacements start. An anchored value starts at
    its `&name` property, so a replacement over the node's span would drop the anchor.
  - `planner.change_slot` / `_replace_value` / `_replace_scalar` / `rewrite_block` take the extent from
    `layout.inline_extent(slot)`, which starts at the node's span or at `indicator_end`.
  - `shared._Rebuild` recomputes the configuration from written layers and changes keyed by written location,
    then re-merges `layers`. It does not know about merge keys.
- **Implications**:
  - Model: an anchored node's `origin.span` is the value **without** the property, and the property is recorded
    as `Anchor(name, span)`. The layout treats the property as part of the indicator (`indicator_end` moves past
    it) and as a token for comment floors. Every existing rule then keeps the anchor (4.3) without change.
  - Layout: an alias place is a **leaf** written at its `*name` token. A mapping with a merge key is walked
    through its **written** mapping (own entries plus the `<<` entry), never through the merged view. This
    mirrors step 5's `include.form` for `$ref` mappings.
  - `_Rebuild` re-applies merge keys when it rebuilds a mapping that has one.

### How `set` composes its steps today
- **Context**: where the refusal and the expansion fit.
- **Findings**: `setter.set_value` runs `resolve_target` (walk, target, `current`/`intended`), a no-op check, then
  `plan_set` → `Plan(patches, intended)` (intended widened by `with_other_places`), `verify_patch` (reload with
  in-memory texts via `load_config(config_file, overrides)` keyed by real path), `diff_leaves`, `write_patches`
  and `record_changes`. `FilePatch(file: SourceFile, new_text)`. `restore_file(file)` writes `file.text` back.
- **Implications**: the expansion can be done in memory first, by producing new texts, re-loading the document
  from them with `load_config(…, overrides)`, and running the unchanged pipeline on that document. The final
  patches must pair each **original** `SourceFile` (for the atomic write and the restore) with the final text.
  The alias check must run before the no-op check, because a no-op through an alias is still refused (5.3) and
  `--expand-alias` still expands (8.6).

### Rendering a copy of the anchored node
- **Context**: Requirement 7 needs a block-style copy that keeps styles. The available renderers are
  `collection_render.render_block_value` (renders `NewValue`, with file-specific step, dash offset and line
  break) and `cli/yaml_output.render_yaml` (renders `ConfigNode`, fixed indent 2, LF only, normalises styles,
  lives in the CLI layer).
- **Findings**: neither fits. `render_yaml` loses quoting and line endings and must not be imported by the
  writeback layer. `render_block_value` takes `NewValue`, which cannot hold an alias or an include as written,
  and re-renders scalars from their value.
- **Implications**: a small copy renderer in the writeback layer walks the **written** anchored node. It copies
  each single-line key and scalar token's source text verbatim (which keeps quoting and include strings), writes
  inner aliases as `*name`, re-indents block scalars line by line, and uses the column rules and
  `RenderContext` of `collection_render`, so indentation stays decided in one place. Multi-line flow scalars
  fall back to `render_scalar` with the source style as `preferred` (7.4).

## Architecture Pattern Evaluation

| Option | Description | Strengths | Risks / Limitations | Notes |
|--------|-------------|-----------|---------------------|-------|
| Expanded tree with per-place alias provenance (chosen) | Loader expands aliases and merge keys. Each alias place is its own node object marked with `AliasRef`. Merged entries are marked `via_merge`. Origins stay at the anchored node | Readers unchanged. Anchor writes replicate through the existing written-location machinery. Same pattern as step 5 layers | Layout must stop at alias places and walk merge-key mappings as written. Composer subclass relies on ruamel internals | Matches the brief and roadmap |
| Alias as a separate node kind resolved by each reader | Model keeps `ConfigAlias(name)`, and readers resolve it | Physical structure explicit | Every reader (get, show, leaves, reconcile, path walk) changes and they drift | Rejected (step 5 rejected merging on demand for the same reason) |
| Expand without provenance | Plain YAML-loader semantics | Smallest change | Writes through an alias land at the anchor. That is the reported bug | Rejected |
| Event-level parser instead of composer | Build the model from parser events | Exact positions for everything | Rewrites the loader | Rejected; the composer hook gives the one missing fact |

## Design Decisions

### Decision 1: Record alias occurrences with a composer subclass
- **Context**: 1.1, 5.1, 7.1. The writer needs each alias's own `*name` span, and the loader needs to tell an
  alias occurrence from the anchored node.
- **Alternatives Considered**:
  1. Track `id(node)` of visited nodes and match the n-th revisit to the n-th `AliasEvent` from a second parse.
     This is order-dependent and breaks when the anchored node is rebuilt at an alias place, because the inner
     aliases are visited again.
  2. Search the source text between siblings for `*name`. Fragile with comments, flow style and quoted text.
  3. Override `Composer.compose_node(parent, index)`. **Chosen.**
- **Selected Approach**: a `_RecordingComposer` records `AliasOccurrence(name, span)` keyed by
  `(id(parent), slot)` before delegating. The composed file is returned as `ComposedFile(root, aliases)`. The
  builder looks up each child slot, so inner aliases are found whether the anchored node is built at its anchor
  or rebuilt at an alias place. Each record is checked against the source text (`*<name>`).
- **Rationale**: exact, and independent of visiting order.
- **Trade-offs**: depends on a non-public ruamel method signature. ruamel is pinned in `uv.lock` (0.19.1), and
  tests fail loudly if the hook stops seeing aliases.
- **Follow-up**: a unit test pins the recorded spans for key, list-item, merge-key and nested cases.

### Decision 2: Alias places are rebuilt nodes with the anchored node's origins
- **Context**: 1.1, 3.x, 4.1, 4.5, 10.2.
- **Selected Approach**: at an alias occurrence the builder builds the anchored ruamel node again for that path,
  giving fresh `ConfigNode` objects whose origins are the anchored node's written spans. It then sets
  `alias=AliasRef(name, origin of the *name token, anchored)` on the place's root node. `anchored` is the node
  built at the anchor. Merge-key sources are built the same way. Includes inside an anchored node are included
  again for the alias place, like any other include place.
- **Rationale**: readers see per-path nodes as for diamonds. Written locations are shared, so `with_other_places`
  replicates a write at the anchor to every alias place, and history derives events for every path.
- **Trade-offs**: an anchored node used n times is built n+1 times. Configurations are small.

### Decision 3: An anchored node's span excludes its property, and the layout treats the property as indicator
- **Context**: 4.1–4.3 (the anchor must survive every change at it).
- **Alternatives Considered**: keep the span at `&` and teach every edit path to skip the property, or exclude
  the property and move `indicator_end` past it. **Chosen: the latter.**
- **Selected Approach**: `Anchor(name, span)` on the node. `origin.span` starts at the value: the first key for a
  block mapping, the first `-` for a block list, the next token for scalars and flow collections, and empty at
  the property's end for an empty null. `FileLayout` reports `indicator_end` after the property and counts the
  property as a token on its line.
- **Rationale**: every existing replacement starts at `indicator_end` or at the node's span, so all of
  config-set-writeback's rules keep `&name` without per-rule changes. `name: &n foo` → `name: &n bar`, and a
  scalar becoming a block mapping gives `name: &n` followed by the mapping.

### Decision 4: Merge keys become a merged view with the written mapping kept
- **Context**: Requirement 2, 9.
- **Selected Approach**: `ConfigMapping.merge = MergeKey(written, key_origin)`. `written` is the mapping as
  written, whose entries include the `<<` entry with its value built as usual (an alias place, a mapping, or a
  list of those). The node's `entries` are the merged view: own entries in written order, and at the position
  of `<<` the merged keys that the mapping does not write itself. Earlier sources come first. Merged entries
  carry `via_merge=True`. The view never has a `<<` key. Merging lives in `config/merge_keys.py`, next to
  `overrides.py`.
- **Rationale**: same shape as step 5 (`include.form` keeps the written `$ref` mapping). Readers see the YAML
  meaning, and the writer reaches the written mapping for layout and insertion.
- **Trade-offs**: a merge key in `$ref` overrides is refused (2.3) rather than combining two merge mechanisms.

### Decision 5: One guard decides refuse vs expand before planning
- **Context**: 5.1–5.4, 8.1, 8.2, 9.2, 6.3.
- **Selected Approach**: `writeback/aliases.py` `alias_sites(document, request, *, expand)` walks PATH (to the
  node, or to the deepest existing ancestor) and, for a collection replacement, the subtree at PATH alongside
  the intended value. The first alias place or merged entry met on the walk decides the outcome. An alias place
  gives `AliasSite` when expansion is allowed and the refusal of 5.1/5.2 otherwise. A merged entry gives the
  refusal of 9.2, with or without the flag. Below PATH, only alias places and merged entries whose plain value
  would change, appear or disappear count. The search does not descend below an alias place, because outer
  aliases come first.
- **Rationale**: one place for "reached through an alias". It runs before the no-op check, so 5.3 and 8.6 hold.
- **Implementation note (task 3.3)**: the walk follows the layers as written. At a merged mapping (an include
  with overrides) only the side or sides holding the next PATH key are followed (both when both write it as
  mappings, as `deep_merge` merges it). An alias as a `$ref` value counts only for the include's included side,
  so a key written only in the include's own overrides proceeds. Conservative leftovers, kept on purpose:
  - Where PATH ends (its node, or the deepest existing ancestor of a new key) every side counts. A new key below
    an include root whose `$ref` value is an alias is therefore refused, wherever the key would be placed. 5.1
    tests the deepest existing ancestor, and the include root's value is partly reached through the alias.
  - Below PATH, a child that changes as a whole (removed, replaced by another kind or another include, or given
    new keys or items) counts on every side, and an include written by VALUE counts as changing everything below
    it, because the guard does not decide where new content is written.
  - A merged entry kept by `deep_merge` when both sides write the key as mappings keeps `via_merge`, so a PATH
    through it is refused (see the 2.3 implementation note).

### Decision 6: Expand in memory, reload, repeat; then run the unchanged set pipeline
- **Context**: 7.x, 8.1, 8.2, 10.1–10.4.
- **Alternatives Considered**:
  1. Plan the expansion edits and the set edits in one `_Planner`. The edits overlap, for example removing the
     copy's entry or changing a line inside the copy, which `apply_edits` refuses. Positions inside the copy
     also do not exist in the loaded document.
  2. Expand, reload from the in-memory texts, re-resolve, and repeat while the guard returns sites. **Chosen.**
- **Selected Approach**: the setter loop is `sites = alias_sites(…)`, then for the first site
  `texts = expand_alias(document, site)` and `document = load_config(config_file, overrides=texts)`, then
  re-resolve. With nested aliases, the outermost one is expanded first, and the copy's inner `*e` becomes a new
  alias place that the next round finds (8.1). After the loop, the normal no-op check, plan, `verify_patch`
  (against the **original** document, with final texts), `diff_leaves(original, after)` and write run. Final
  patches pair each original `SourceFile` with its final text.
- **Rationale**: the set after expansion is literally "a set in the copy" (7.7), with every earlier rule. One
  atomic write and one read-back (10.3). Events compare the original and final configurations, so the expansion
  adds none (10.4).
- **Trade-offs**: one extra in-memory load per expanded alias.

### Decision 7: Copy renderer over the written anchored node
- **Context**: 7.2–7.6. See the research log entry "Rendering a copy of the anchored node".
- **Selected Approach**: `expand.py` renders the anchored node as written. It walks `merge.written` for mappings
  with merge keys and refuses them (a copy would write `<<`, 9.1). It uses `include.form` / the directive token
  for includes, `*name` for inner alias places, and source tokens for keys and single-line scalars. Block
  scalars keep their header, and their content lines are re-indented by the column difference. Columns follow
  `collection_render`'s rules with the file's `RenderContext`. Inner anchors are dropped by construction,
  because only tokens after the property are copied.
- **Trade-offs**: comments inside the anchored node are not copied (choice 7, accepted).

### Decision 8: Anchor removal and anchored include conversion are refused in the planner
- **Context**: 4.6, 4.7.
- **Selected Approach**: `aliases.anchor_uses(document)` maps each anchored node's written location to the alias
  places that use it. The planner refuses (exit 3, naming PATH, file and `&name`) a removal whose removed
  written region contains an anchored node with uses outside that region. It also refuses a scalar-form →
  mapping-form conversion of a node that has an anchor.
- **Rationale**: the read-back would also fail (undefined alias), but with a message that does not name the
  anchor. The explicit check gives the stated message.

## Choices where the contract is silent (decision records)
All 17 choices are listed in `requirements.md` ("Choices made where the contract is silent"). The product owner
approved them on 2026-10-06. Each record: decision / alternatives / why / revisit when.
1. **Merge keys load** (Req 2). *Alternatives*: keep exit 4 (the brief's proposal). *Why*: the step 6 contract
   names merge keys, so files with them are in contract, and loading cannot change a byte. *Revisit*: if a
   later step defines writes through merge keys.
2. **The alias's own key is reached through the alias** (5.1). *Alternatives*: allow replacing the alias. *Why*:
   literal reading, and `--expand-alias` covers the need. *Revisit*: if users find the extra flag burdensome.
3. **Several aliases on PATH expand outermost first** (8.1). *Alternatives*: expand only the first and refuse the
   rest. *Why*: otherwise the command could never succeed. *Revisit*: never expected.
4. **No anchors repeated in a copy, inner aliases copied as aliases** (7.5). *Alternatives*: copy anchors
   (redefines names and silently changes later aliases), or expand inner aliases too (changes more than asked).
   *Why*: "other aliases still refer to the anchor".
5. **`- *d` comment on the item line, flow collections refused** (8.3, 8.4). *Why*: a block copy cannot be
   written in flow. *Revisit*: if a flow copy is wanted.
6. **Existing comment on the key line refused** (8.5). *Alternatives*: replace it, or append ours. *Why*: both
   lose or alter the user's text. *Revisit*: if the PO wants both comments kept.
7. **Values only in the copy, styles kept, comments not copied** (7.2–7.4, 7.6).
8. **The anchor stays on a replaced anchored node** (4.3). *Why*: "changes it at the anchor".
9. **`--expand-alias` without an alias is accepted** (6.3), as for `--in-include`.
10. **`--expand-alias` with an equal VALUE still expands, with no event** (8.6).
11. **A collection replacement through aliases below PATH is refused, or expanded with the flag** (5.2, 8.2).
12. **Removing a used anchor is refused** (4.6). *Why*: the file would no longer load.
13. **Anchors on includes load; converting an anchored scalar-form include is refused** (1.5, 4.7). *Why*: no
    written rule says where the anchor goes in the conversion.
14. **`--sources` names the anchored node's file** (3.2). Aliases are file-local.
15. **Forms refused on load** (1.3, 1.4, 2.3); a reused anchor name follows YAML (1.2).
16. **Merged keys take the place of `<<`** (2.2).
17. **A no-op set through an alias is still refused** (5.3).

Design-level consequences recorded here (no new requirement):
- An anchored node that contains a merge key cannot be copied without writing `<<`, so `--expand-alias` on it is
  refused with the 9.2 message (it falls under 9.1).

**Product owner review of the design (2026-10-06)**: approved as consistent with the step 6 "Aliases" section and
the earlier contract sections in force.
- *Confirmed as the contract's meaning*: a set at an anchor is written at the anchor and so appears at every alias.
  A set reached through an alias (including the alias's own key and a no-op) exits 3 before any write, and stderr
  mentions `--expand-alias`. `--expand-alias` replaces that one alias with a block-style copy commented
  `  # expanded from *<name> by troupe` on the key's line, then sets the value in the copy, with the anchor and the
  other aliases unchanged. Merge keys are never written. Every refusal comes before any write and names the file
  and PATH. The issue #2 end-to-end test pins the reporter's scenario byte for byte.
- *Out of contract, our choice, accepted*: reading merge keys, nested aliases, an alias in a flow collection, a key
  line that already has a comment, refusing to copy an anchored node that holds `<<`, and refusing to remove an
  anchor that is still used.

**Design clarifications after approval (2026-10-06, found during task planning; no behaviour change)**:
- `overrides.deep_merge` must keep the included side's `anchor`, `alias` and `merge` on the merged mapping. It
  currently drops them, so an alias place behind `$ref` overrides would lose its marker, and the 5.1/9.2 guard
  would miss it.
- Refusal order: target resolution's own refusals come first, then the guard, then planner refusals.
- In the expansion loop, the widened intended configuration comes from the plan on the expanded document.
  Verification uses the original document and patches remapped to the original files.
- An anchored node holding `<<` is refused with the 9.2 message, extended with the alias's name. The diagram
  edge from the expander to the loader was removed (only the setter reloads).

## Risks & Mitigations
- **ruamel internal hook changes**. Mitigation: the version is pinned. A unit test asserts the recorded spans,
  and each record is validated against the source text (a mismatch is an internal load error, not a wrong write).
- **Layout regressions in files without anchors**. Mitigation: alias-place and merge-key handling only applies to
  nodes that have `alias`/`merge`/`anchor`, and the full existing test suite must stay green.
- **Replication through merge keys missed by `_Rebuild`**. Mitigation: read-back verification compares against
  the intended tree. A missed replication shows up as exit 3, never as a wrong write. A test covers a write at
  an anchor used by `<<: *d`.
- **Expansion edge cases producing invalid YAML**. Mitigation: every expansion is followed by an in-memory reload
  (Decision 6), and the final read-back runs before any write.

- **Exponential expansion through nested aliases ("billion laughs")**. Accepted: configurations are small, local
  and hand-edited, and the build is linear in the expanded size. No limit is added, because none is in the
  contract. Revisit if theatres are ever loaded from untrusted sources.

## References
- YAML 1.2.2 specification, §3.2.2.2 (anchors and aliases), §7.1 (alias nodes): https://yaml.org/spec/1.2.2/
- YAML merge key type (1.1 type repository): https://yaml.org/type/merge.html
- ruamel.yaml 0.19.1 `composer.py` (installed in `.venv`)
- config-include-overrides `research.md` choice 18 and `writeback/shared.py` (written-location replication)
