# Brief: config-aliases

## Problem
Teams share settings between agents with YAML anchors and aliases (tracker issue
troupe-run/bakeoff-specs-ccsdd-pass1#2, "Setting one agent's model changed another agent's model"):

```yaml
defaults: &d
  model: opus
coder:
  settings: *d
reviewer:
  settings: *d
```

The reporter ran `troupe config set coder.settings.model sonnet` and saw `reviewer` and `defaults` change too. A
naive surgical writer goes through the alias and edits the anchored node, so one `set` silently changes every
other place that uses the anchor. The reporter expects: changing `defaults.model` changes everyone (that is what
the anchor is for); changing `coder.settings.model` is refused, and they can explicitly ask for coder's copy to be
expanded so they can override it.

## Current State
- On `main` today the reported file cannot be used at all. `get` and `set` both exit 4 with
  `…/troupe.yml: defaults: anchor '&d' is not supported (anchors, aliases and merge keys are refused)`. This is
  product-owner decision H (config-foundation research.md, Requirement 3.9), which deferred anchors to step 6.
  No file is changed, so the reported corruption cannot happen. But no theatre that uses anchors can be read or
  changed either.
- config-foundation (`config/loader.py`): `_refuse_properties` refuses any `&anchor` on a node. A defined alias
  is caught through its anchor, and an undefined alias is a composer error. A plain `<<` key is refused in the
  mapping builder. The model (`config/model.py`) has as an invariant that "no node has an explicit tag or anchor".
  It has no notion of a node reached through an alias.
- config-set-writeback (`config/writeback/`): `resolve_target` → `ReplaceTarget`/`AddKeyTarget`/`AppendTarget`
  use each node's `origin` (file + span) as the physical place to patch. If the loader simply expanded aliases,
  every node reached through `*d` would carry the anchored node's origin, so a write would land at the anchor.
  That is the reported bug. `value.py` refuses anchors and aliases in VALUE (exit 2).
- config-include-overrides: a merged mapping keeps its physical `layers`. A file included at several places is
  shared, and a write is replicated at every place that reaches its location (choice 18). Read-back verification
  (`verify.py`, `compare.py`) compares the reloaded tree with an intended tree.
- config-get-show / config-file-sync / config-lineage work on the loaded tree by path. With aliases expanded in
  the loaded tree, `get`, `show`, leaves, reconcile and history see every alias path with the anchor's value.
- Gap: anchors and aliases cannot be loaded. There is no rule for which writes are allowed through an alias, and
  no expansion operation.

## Desired Outcome
The step 6 contract section "Aliases" holds:
- Setting a path whose value is defined at an anchor (`&name`) changes it at the anchor, and so everywhere the
  alias is used.
- Setting a path reached **through an alias** (`*name`) → exit 3, no file changed. stderr names PATH and mentions
  `--expand-alias`.
- `set --expand-alias PATH VALUE` replaces that alias with a block-style copy of the anchored node and puts the
  end-of-line comment `# expanded from *<name> by troupe` on the key's line (two spaces before `#`). It then sets
  the value in the copy. Nothing else changes, and other aliases still refer to the anchor. For the issue's file:
  `coder:` / `  settings:  # expanded from *d by troupe` / `    model: sonnet`, with `defaults` and `reviewer`
  byte-identical.
- Merge keys (`<<`) are never written.
Every reader (`get`, `show`, `show --sources`, `history`, reconcile, `watch`) accepts files with anchors and
aliases and sees the expanded configuration. All earlier `set` rules (byte-surgical rendering, events and lineage,
atomic writes, read-back verification, step 5 overrides, `--in-include`, `--reason`) still hold.

## Approach
Chosen: **the loader expands aliases into the tree, and every node reached through an alias records that alias.**
- The loader accepts `&name` on any value node and expands each `*name` into the anchored node's content. The
  expanded nodes keep the anchor's origins, so a write that is allowed lands at the anchor. Each node reached
  through an alias also carries an `AliasRef` with the alias name, the alias node's span and file, and the key
  line or list item that holds it. This is a narrow addition to the model, like step 5's `layers`. Readers keep
  working on the plain tree and need no change.
- The writeback layer checks the path before resolving a target. If any node on PATH (the target, its ancestors,
  or for new keys the deepest existing ancestor) is reached through an alias, `set` refuses with exit 3 naming
  PATH and mentioning `--expand-alias`. A path whose nodes are only anchored, not aliased, is patched at the
  anchor as usual.
- `--expand-alias` first plans a text edit that replaces the alias token with a block-style rendering of the
  anchored node at the key's indentation. The rendering reuses config-get-show's block emitter and
  config-set-writeback's scalar rules, and adds the end-of-line comment on the key's line. Then the normal `set`
  plan runs on the patched text, so placement, rendering, events and read-back verification are unchanged. Both
  edits go into one atomic write with one read-back.
- The intended tree for read-back verification models anchors and aliases, so verification still proves that
  other alias paths kept the anchor's value, and that an anchor write changed them all.

Rejected alternatives:
- *Keep aliases as a separate node kind that each reader resolves*: every reader would need alias handling, and
  they would drift apart (the same reason step 5 rejected merging on demand).
- *Expand without recording provenance*: this is exactly the reported bug. Writes through an alias would land at
  the anchor.
- *Expand by re-dumping the anchored subtree with ruamel*: ruamel decides quoting, widths and float forms, which
  breaks the byte-surgical contract. Our own emitter already exists.
- *Write the expansion as `<<: *d` plus an override key*: forbidden by the contract ("merge keys are never
  written").

## Scope
- **In**: loading anchors (`&name`) on scalars, mappings and lists, and aliases (`*name`) to them, at any depth
  and in included files. Expanded reads in every command. The anchor write rule (including new keys added inside
  an anchored mapping, list appends to an anchored list, and replacement of an anchored node). The exit-3 refusal
  for paths reached through an alias. `set --expand-alias` (parsing, combining with `--reason` and
  `--in-include`, the copy, the comment, then the set). Read-back verification with aliases. Events for every
  leaf path that a write at an anchor changes. Superseding decision H and Requirement 3.9 for anchors and aliases
  (config-foundation note). Updating the step 1–5 tests that pin "anchors are refused".
- **Out**: writing merge keys in any form. Creating new anchors or aliases from VALUE (VALUE with an anchor or
  alias stays exit 2). Renaming or removing anchors. Expanding every alias of an anchor at once. Windows.
  Concurrent writers.

## Boundary Candidates
- Loader and model: accept anchors, expand aliases, attach `AliasRef` provenance (core/file layer).
- Writeback guard: detect "reached through an alias" on PATH, with exit 3 and the `--expand-alias` hint.
- Alias expansion: render the block copy at the right indentation, place the comment, plan the text edit.
- CLI: the `--expand-alias` flag next to `--reason` and `--in-include`.
- Read-back verification: an intended tree with anchors and aliases.

## Out of Boundary
- Rendering rules for scalars, comments and indentation: reused from config-set-writeback and config-get-show.
- Event shapes, lineage, reconcile and watch mechanics: unchanged. They see more leaf paths because aliases are
  expanded.
- Step 5 placement rules for overrides and `--in-include`: unchanged. They apply to the copy after expansion.

## Upstream / Downstream
- **Upstream**: config-foundation (loader, model, errors), config-set-writeback (target, planner, layout,
  verify, compare, value parsing), config-get-show (block emitter, `source_label`), config-include-overrides
  (layers, shared-location replication, `--in-include` parsing), config-lineage (`--reason` parsing; event
  lineage unchanged).
- **Downstream**: none specified yet.

## Existing Spec Touchpoints
- **Extends**: config-foundation Requirement 3.9 and decision H. Anchors and aliases are now loaded (step-6
  note). Merge keys follow open choice 1. config-set-writeback gets step-6 notes for writes at anchors and for
  `--expand-alias` on `set`. The VALUE refusal (exit 2) is unchanged.
- **Adjacent**: config-get-show (`show` prints the expanded value, and `--sources` names the file holding the
  anchor). config-include-overrides (an alias whose anchored node holds an include or a `$ref` mapping). Tests
  that assert exit 4 for anchors (e.g. in `tests/test_loader.py`) change in this spec's tasks.

## Constraints
- Python 3.12+, uv, existing dependencies only, `mypy --strict`, ruff, real-file/real-CLI byte-exact tests.
- Byte-surgical: an anchor write changes only the anchored bytes. An expansion changes only the alias token on the
  key's line (now the comment), plus the inserted copy and the requested value. `defaults`, other aliases and
  everything else stay byte-identical.
- No file changes on any refusal. Read-back verification stays the final guard (exit 3).
- Open choices to settle and record in requirements/research (proposed defaults, conservative):
  1. *Merge keys on load*: still exit 4 (decision H kept for `<<` only). The contract only says they are never
     written. Reading them would need merge semantics, and refusals for writes through them, that the contract
     does not define. Building them now would be building ahead.
  2. *"Reached through an alias"*: PATH's walk passes through an alias node. This includes PATH naming the
     alias's own key (`set coder.settings {…}`), and new keys whose deepest existing ancestor is reached through
     an alias. All refuse with exit 3 unless `--expand-alias` is given. Replacing the alias itself would be
     harmless, but the literal reading is the safer one, and `--expand-alias` covers the need.
  3. *Several aliases on PATH* (an alias inside an anchored node that is itself aliased): `--expand-alias`
     expands each alias on PATH, outermost first, each with its own comment. Nested anchors inside a copy are not
     repeated (no `&name` in a copy), so later aliases keep referring to the original anchor.
  4. *Alias not on a key's line* (a list item `- *d`, or an alias inside a flow collection): `- *d` gets the
     comment on the item's line. An alias inside a flow collection, or a copy that cannot be written in block
     style at that place, → exit 3 with no file changed.
  5. *Key line already has an end-of-line comment*: exit 3 with no file changed. Replacing or merging the user's
     comment would lose or alter their text. Revisit if the PO wants both comments kept.
  6. *The copy's content*: values only, in block style with the file's indentation step. Scalars keep their
     source style (quoting, block scalars). Comments inside the anchored node are not copied.
  7. *Anchored scalar*: `name: &n foo` / `other: *n`. Setting `name` replaces the value after `&n` and keeps the
     anchor. Expanding `other` writes the scalar in its source style followed by the comment.
  8. *Replacing an anchored node* (e.g. `set defaults '{model: x}'`): the anchor `&name` stays on the new value,
     so every alias sees the new value.
  9. *`--expand-alias` when PATH is not reached through an alias*: accepted, and it changes nothing extra (like
     `--in-include` choice 3).
  10. *Events*: a write at an anchor records a `set`/`remove` event for every leaf path whose value changed,
     including each alias path. The expansion itself records no event, because no leaf value changes.
  11. *Anchors or aliases involving includes* (an anchor on an include string or `$ref` mapping, or an anchored
     node containing an include): loaded and expanded like any other node. Any write that would have to change
     an include through an alias is refused with exit 3, the same as rule 4 for include roots.
  12. *`--sources`*: a scalar reached through an alias is labelled with the file holding the anchor. Aliases are
     file-local in YAML, so this is the file holding the alias.
