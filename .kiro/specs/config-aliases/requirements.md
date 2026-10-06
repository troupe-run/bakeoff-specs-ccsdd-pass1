# Requirements Document

## Project Description (Input)
### Problem
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

### Current State
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

### Desired Outcome
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

### Approach
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

### Scope
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

### Boundary Candidates
- Loader and model: accept anchors, expand aliases, attach `AliasRef` provenance (core/file layer).
- Writeback guard: detect "reached through an alias" on PATH, with exit 3 and the `--expand-alias` hint.
- Alias expansion: render the block copy at the right indentation, place the comment, plan the text edit.
- CLI: the `--expand-alias` flag next to `--reason` and `--in-include`.
- Read-back verification: an intended tree with anchors and aliases.

### Out of Boundary
- Rendering rules for scalars, comments and indentation: reused from config-set-writeback and config-get-show.
- Event shapes, lineage, reconcile and watch mechanics: unchanged. They see more leaf paths because aliases are
  expanded.
- Step 5 placement rules for overrides and `--in-include`: unchanged. They apply to the copy after expansion.

### Upstream / Downstream
- **Upstream**: config-foundation (loader, model, errors), config-set-writeback (target, planner, layout,
  verify, compare, value parsing), config-get-show (block emitter, `source_label`), config-include-overrides
  (layers, shared-location replication, `--in-include` parsing), config-lineage (`--reason` parsing; event
  lineage unchanged).
- **Downstream**: none specified yet.

### Existing Spec Touchpoints
- **Extends**: config-foundation Requirement 3.9 and decision H. Anchors and aliases are now loaded (step-6
  note). Merge keys follow open choice 1. config-set-writeback gets step-6 notes for writes at anchors and for
  `--expand-alias` on `set`. The VALUE refusal (exit 2) is unchanged.
- **Adjacent**: config-get-show (`show` prints the expanded value, and `--sources` names the file holding the
  anchor). config-include-overrides (an alias whose anchored node holds an include or a `$ref` mapping). Tests
  that assert exit 4 for anchors (e.g. in `tests/test_loader.py`) change in this spec's tasks.

### Constraints
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

### Step 6 contract, section "Aliases" (authoritative, as supplied by the product owner)
Tracker issue troupe-run/bakeoff-specs-ccsdd-pass1#2, "Setting one agent's model changed another agent's model":
the reporter's `troupe.yml` shares settings with `defaults: &d` / `coder: {settings: *d}` / `reviewer: {settings: *d}`.
`troupe config set coder.settings.model sonnet` changed `reviewer` and `defaults` too. Expected: changing
`defaults.model` changes everyone (that is what the anchor is for). Changing `coder.settings.model` must not silently
change anyone else. It must be refused, with an explicit way to ask for coder's copy to be expanded so it can be
overridden. Contract sections added: Aliases.

**Aliases (from step 6)**
- Setting a path whose value is defined at an anchor (`&name`) changes it at the anchor, and so everywhere
  the alias is used.
- Setting a path reached **through an alias** (`*name`) → exit 3; stderr mentions `--expand-alias`.
- `set --expand-alias PATH VALUE` replaces that alias with a block-style copy of the anchored node, puts the
  end-of-line comment `# expanded from *<name> by troupe` on the key's line (two spaces before `#`), and then
  sets the value in the copy. Nothing else changes; other aliases still refer to the anchor.
- Merge keys (`<<`) are never written.

See `brief.md` for the chosen approach, scope, boundaries and the open choices proposed at discovery.

## Introduction
This specification covers step 6, the contract section "Aliases", raised by tracker issue #2. Until now troupe has
refused every file that uses an anchor, an alias or a merge key (exit 4, config-foundation Requirement 3.9, decision
H). The reported corruption cannot happen on the current code, but no theatre that shares settings with anchors can
be read or changed. Step 6 changes that:
- **Reading**: anchors, aliases and merge keys are loaded the way YAML defines them. Every command sees the
  configuration with each alias replaced by the anchored value and each merge key's mappings merged in.
- **Writing at an anchor**: a `set` whose path is defined at an anchor changes the anchored node, so the change
  appears everywhere the anchor is used.
- **Writing through an alias**: a `set` whose path is reached through an alias is refused with exit 3, and stderr
  points to `--expand-alias`.
- **Expanding on request**: `set --expand-alias` gives the place its own copy of the anchored node, marks the copy
  with a comment, and then sets the value in the copy, leaving the anchor and every other alias as they were.
- **Merge keys**: troupe never writes a merge key.

Terms used below:
- **Anchor**: the property `&name` written on a value node. The node it is written on is the **anchored node**.
- **Alias**: a node `*name` that stands for the anchored node with that name most recently defined before it in the
  same YAML file.
- **Merge key**: a mapping key written as the plain scalar `<<`. Its value (an alias, an anchored or inline mapping,
  or a list of those) is merged into the mapping that holds it. Keys written in that mapping itself take precedence
  over merged keys. With a list, an earlier mapping takes precedence over a later one.
- **Defined at an anchor**: a value is defined at an anchor when it is the anchored node, or lies inside it, and the
  walk from the configuration root to it passes through no alias and no merge key.
- **Reached through an alias**: a value is reached through an alias when the walk from the configuration root to it
  passes through an alias node. The alias node itself counts: the value at `coder.settings` in the issue's file is
  reached through `*d`. A value that a mapping receives from a merge key is also reached through an alias (or
  through the merge key's inline mapping). Values written in the mapping itself are not.
- **The aliases on PATH**: the aliases that the walk from the root to PATH passes through, in walk order. For a PATH
  that does not exist yet, the walk ends at its deepest existing ancestor (config-set-writeback Requirement 3).
- **Expanded configuration**: the configuration with every alias replaced by its anchored value and every merge
  key's mappings merged in, after includes and overrides (config-foundation Requirement 4,
  config-include-overrides Requirement 1).

## Boundary Context
- **In scope**:
  - Loading anchors on scalars, mappings and lists, aliases to them, and merge keys, in the root file and in
    included files, at any depth. Also the refusals for the forms that cannot be loaded.
  - The expanded configuration as seen by `get`, `show`, `show --sources`, `history`, the reconcile and `watch`.
  - `set` at an anchor: changed values, new keys, list appends, collection replacements and removals.
  - The exit-3 refusal of writes reached through an alias or a merge key.
  - `set --expand-alias`: parsing, the copy, the comment, and the set in the copy.
  - The safety, read-back and history guarantees for these writes.
- **Out of scope**:
  - Writing a merge key in any form, or expanding a merge key's alias (Requirement 9).
  - Creating, renaming or removing anchors and aliases on request, and expanding every alias of an anchor at once.
  - A VALUE that contains an anchor or an alias: it stays a usage error (exit 2, config-set-writeback 1.3).
  - Printing anchors, aliases or merge keys in `show` output. `show` prints values, as in config-get-show.
- **Adjacent expectations**:
  - config-foundation keeps owning loading, includes, path addressing and the error format. Its Requirement 3.9
    (anchors, aliases and merge keys → exit 4) is superseded by Requirements 1 and 2 here, and gains a step-6 note.
  - config-set-writeback keeps owning every "How it is written" rule (quoting, styles, indentation, comments,
    placement, removals), read-back verification, atomic writes and the `set`/`remove` events. This spec adds
    *where* a write goes when anchors or aliases are involved, the refusal, and the expansion. config-set-writeback
    gains a step-6 note.
  - config-include-overrides keeps owning the placement of new keys below includes and `--in-include`. After an
    expansion, those rules apply to the copy as to any other written node.
  - config-get-show, config-file-sync and config-lineage are unchanged. They see the expanded configuration, and
    their output formats, reconcile rules and lineage do not change.
- **Choices made where the contract is silent** (conservative; recorded in `research.md`):
  1. *Merge keys are loaded* (Requirement 2). The step 6 contract names merge keys, so files with them are in
     contract from step 6. The only rule given for them is that troupe never writes them. This differs from the
     brief's proposed default (keep exit 4): refusing a whole file for a construct the contract now covers would
     block reading it, and loading it cannot change a byte. Any write that would need to change a merge key or a
     value reached through one is refused (Requirement 9).
  2. *A PATH naming the alias itself* (`set coder.settings {...}`) is reached through the alias and refused with
     exit 3 unless `--expand-alias` is given (5.1). Replacing the alias would harm no one else, but the literal
     reading is the safer one, and `--expand-alias` covers the need.
  3. *Several aliases on PATH* (an alias inside an anchored node that is itself aliased): `--expand-alias` expands
     each alias on PATH, outermost first, each with its own comment (8.1).
  4. *Anchors and aliases inside a copy*: a copy never repeats an anchor written inside the anchored node, so later
     aliases still refer to the original. An alias written inside the anchored node is copied as the same alias
     (7.5).
  5. *An alias in a block list item* (`- *d`): the comment goes on the item's line (8.3). *An alias inside a flow
     collection* (`{settings: *d}`, `[*d]`): a block copy cannot be written there, so `--expand-alias` exits 3 with
     no file changed (8.4).
  6. *The key's line already has an end-of-line comment*: `--expand-alias` exits 3 with no file changed (8.5).
     Replacing or merging the user's comment would lose or alter their text.
  7. *The copy's content*: values only, in block style at the file's indentation. Scalars and keys keep their
     source style where it can be written there. Comments inside the anchored node are not copied, and nothing at
     the anchor changes (7.2–7.4).
  8. *Replacing an anchored node* (`set defaults '{model: x}'`, or a scalar): the anchor stays on the new value, so
     every alias sees the new value (4.3).
  9. *`--expand-alias` when PATH is not reached through an alias*: accepted, and the set runs exactly as without it
     (6.3).
  10. *`--expand-alias` with a VALUE equal to the current value*: the alias is still expanded, because that is what
      was asked. No event is recorded, because no leaf value changes (8.6).
  11. *A collection replacement that would change values reached through aliases below PATH*: refused with exit 3
      (5.2). With `--expand-alias`, each such alias is expanded first, outermost first (8.2).
  12. *Removing an anchored node that a later alias still uses* (for example by a collection replacement that drops
      the key holding the anchor): exit 3 with no file changed (4.6). The file would no longer load.
  13. *Anchors and includes*: anchors on include strings and `$ref` mappings, and anchored nodes that contain
      includes, load and expand like any other node (1.5). A write at an anchor that would have to convert an
      anchored scalar-form include to mapping form (config-include-overrides Requirement 5) is refused with exit 3
      (4.7). Moving the anchor during that conversion is not covered by any written rule.
  14. *`--sources`*: a scalar reached through an alias or a merge key is labelled with the file in which its
      anchored node is written. Aliases are local to one YAML file, so this is the file that holds the alias (3.2).
  15. *Forms refused on load* (exit 4): an alias with no anchor before it in the same file, an alias inside its own
      anchored node (a recursive structure), an anchor on a mapping key or an alias used as a key, a merge key whose
      value is not a mapping or a list of mappings, and a merge key in the overrides of a mapping-form include
      (1.3, 1.4, 2.3). An anchor name defined twice is accepted. Each alias refers to the most recent definition before
      it, as YAML specifies (1.2).
  16. *Order of a mapping with a merge key*: the merged keys that the mapping does not write itself take the place
      of the `<<` entry, in the order of the merged mappings. The mapping's own keys keep their written order (2.2).
  17. *A set reached through an alias whose VALUE equals the current value*: still refused with exit 3 without
      `--expand-alias` (5.3). The contract's rule has no exception for no-op sets, and the user learns about the
      sharing either way.
- **Product owner review (2026-10-06)**: the requirements are approved as consistent with the step 6 "Aliases"
  section and the earlier contract sections in force.
  - *Confirmed as the contract's meaning*: writes at an anchor change the anchor (Requirement 4). A path reached
    through an alias, including the alias's own key (choice 2) and a no-op set (choice 17), exits 3 and mentions
    `--expand-alias` (Requirement 5). `--expand-alias` writes a block-style copy with the comment two spaces after
    the key, sets the value in the copy, and leaves the anchor and the other aliases unchanged (Requirement 7).
    Merge keys are never written (Requirement 9).
  - *Out of contract, our choice, accepted*: reading through merge keys (choice 1), several aliases on one PATH
    (choice 3), `--expand-alias` with no alias on PATH (choice 9), an alias inside a flow collection (choice 5), and
    a key line that already has a comment (choice 6), together with the other recorded choices.

## Requirements

### Requirement 1: Loading anchors and aliases
**Objective:** As a developer who shares settings between agents with YAML anchors, I want troupe to read my file, so that I can use troupe on theatres that use anchors.

#### Acceptance Criteria
1. When a YAML file of the configuration has an anchor on a scalar, mapping or list value and aliases to it, the troupe CLI shall load it, using the anchored node's value at the anchor and at the place of each alias.
2. When an anchor name is defined more than once in a file, the troupe CLI shall use for each alias the anchored node with that name most recently defined before the alias.
3. If a file contains an alias with no anchor of that name before it in the same file, including an anchor that is only defined in another file of the configuration, then the troupe CLI shall exit with code 4, write nothing to stdout, and write a stderr message that names the file and the alias.
4. If an alias lies inside its own anchored node, or an anchor is written on a mapping key, or an alias is used as a mapping key, then the troupe CLI shall exit with code 4, write nothing to stdout, and write a stderr message that names the file and, where known, the key path.
5. The troupe CLI shall load anchors and aliases in any YAML file of the configuration (the root file or an included file), at any depth, in block or flow style. This includes anchors on include strings and on mapping-form includes, and anchored nodes that contain includes. An alias of an include has the include's value.
6. The troupe CLI shall read files with anchors, aliases and merge keys without creating, modifying, renaming or deleting any file.
7. When a command is refused under 1.3, 1.4 or 2.3, the troupe CLI shall change no file and create or change no history state, as for any other configuration that cannot be loaded (config-foundation Requirement 3).

### Requirement 2: Loading merge keys
**Objective:** As a developer whose YAML uses merge keys, I want troupe to read them as YAML defines them, so that what troupe shows is what my YAML means.

#### Acceptance Criteria
1. When a mapping has a plain key `<<` whose value is a mapping, an alias of a mapping, or a list of those, the troupe CLI shall use as the mapping's value its own keys plus every key of the merged mappings that it does not write itself. Where several merged mappings in a list have the same key, the earlier one in the list is used.
2. The troupe CLI shall order a mapping with a merge key as its own keys in their written order, with the merged keys that it does not write itself placed where the `<<` entry is written, in the order of the merged mappings and of their keys.
3. If the value of a merge key is not a mapping, an alias of a mapping, or a list of those, or a merge key is written among the overrides of a mapping-form include, then the troupe CLI shall exit with code 4, write nothing to stdout, and write a stderr message that names the file and the key path of the merge key.
4. The troupe CLI shall not include a `<<` key in the loaded configuration, so that no path, leaf, history event or output line contains a `<<` segment produced by a merge key. A `<<` key written in a quoted style stays an ordinary key.

### Requirement 3: The expanded configuration in every command
**Objective:** As a developer, I want every command to show and record the configuration with aliases and merge keys resolved, so that I see what troupe uses.

#### Acceptance Criteria
1. When `troupe config get PATH` or `troupe config show [PATH]` addresses a value at, inside, or reached through an anchored node, an alias or a merge key, the troupe CLI shall print the value from the expanded configuration and print no anchor, alias or merge key.
2. When `troupe config show --sources` prints a scalar reached through an alias or a merge key, the troupe CLI shall name the file in which the scalar is written at its anchored node.
3. The troupe CLI shall import, reconcile and record history events for the leaves of the expanded configuration. Each alias place has its own leaf paths, so a hand edit at an anchor is recorded as the `set` or `remove` events of every leaf path it changes, at the anchor and at each alias place.
4. When a hand edit replaces an alias by an equal copy of its anchored node (or the reverse), the troupe CLI shall record no event, because the expanded configuration is unchanged.
5. While `troupe config watch` runs, the troupe CLI shall reconcile files with anchors, aliases and merge keys as it reconciles any other file.

### Requirement 4: Setting a value defined at an anchor
**Objective:** As a developer, I want a setting I change at an anchor to change everywhere the anchor is used, so that shared settings stay shared.

#### Acceptance Criteria
1. When `troupe config set PATH VALUE` resolves PATH to a value defined at an anchor, the troupe CLI shall change it in the anchored node under config-set-writeback's rules for changed values, and shall change no alias, so that the new value appears at the anchor and at every place where the anchor is used.
2. When `troupe config set` adds a key whose deepest existing ancestor is defined at an anchor, or appends an item to a list defined at an anchor, the troupe CLI shall add it inside the anchored node under config-set-writeback's and config-include-overrides' rules for new keys and list items.
3. When VALUE replaces the anchored node itself, the troupe CLI shall keep the anchor `&name` on the new value, written under config-set-writeback's rules, so that every alias refers to the new value.
4. When VALUE replaces a collection that contains an anchored node, the troupe CLI shall apply 4.1–4.3 to each changed, added or removed value inside the anchored node.
5. When a set at an anchor succeeds, the troupe CLI shall record one `set` or `remove` event for every leaf path whose value changed, at the anchor and at every place where the anchor is used (config-set-writeback Requirement 15).
6. If a set would remove an anchored node while an alias to it remains in the file, then the troupe CLI shall exit with code 3, write nothing to stdout, write a stderr message that names PATH, the file and the anchor, and change no file.
7. If a set at an anchor would have to convert an anchored scalar-form include to mapping form (config-include-overrides Requirement 5), then the troupe CLI shall exit with code 3, write nothing to stdout, write a stderr message that names PATH, the file and the anchor, and change no file.

### Requirement 5: Refusing writes reached through an alias
**Objective:** As a developer, I want troupe to refuse a change that would silently change other places through an alias, so that one agent's setting never changes another's by accident.

#### Acceptance Criteria
1. If `troupe config set PATH VALUE` is given without `--expand-alias` and PATH, or the deepest existing ancestor of a PATH that does not exist, is reached through an alias, then the troupe CLI shall exit with code 3, write nothing to stdout, write a stderr message that names the file, PATH and the alias `*name`, says that the value is shared through that alias, and mentions `--expand-alias`, and change no file.
2. If VALUE replaces a collection and the replacement would change, add or remove a value reached through an alias below PATH, then the troupe CLI shall refuse the set as in 5.1 without `--expand-alias`.
3. If PATH is reached through an alias and `--expand-alias` is not given, then the troupe CLI shall refuse the set under 5.1 even when VALUE equals the current value at PATH.
4. When a set is refused under 5.1 or 5.2, the troupe CLI shall record no event and change no history state other than the import or reconcile that `set` runs first (config-set-writeback 1.6, config-file-sync).

### Requirement 6: The `--expand-alias` option
**Objective:** As a developer, I want to ask explicitly for my own copy of a shared value, so that I can override it for one place.

#### Acceptance Criteria
1. The troupe CLI shall accept `--expand-alias` on `troupe config set`, without a value, before, between or after PATH and VALUE, together with `--reason` and `--in-include`.
2. If a value is attached to `--expand-alias` (`--expand-alias=TEXT`), then the troupe CLI shall exit with code 2, write nothing to stdout, write a stderr message describing the usage problem, and change no file and no history state.
3. When `--expand-alias` is given and PATH is not reached through an alias, the troupe CLI shall carry out the set exactly as without the option.
4. When `--expand-alias` is given and the set succeeds, the troupe CLI shall record its events and lineage as for any other `set`, with `--reason` recorded as usual (config-lineage).

### Requirement 7: The expanded copy
**Objective:** As a developer, I want the expansion written the way I would write it by hand and marked as troupe's, so that the diff shows exactly the copy and the change.

#### Acceptance Criteria
1. When `troupe config set --expand-alias PATH VALUE` expands an alias whose place is a key in a block mapping, the troupe CLI shall remove the alias from the key's line and write, two spaces after the key's `:`, the end-of-line comment `# expanded from *<name> by troupe`, where `<name>` is the alias's anchor name. For example `  settings: *d` becomes `  settings:  # expanded from *d by troupe`.
2. When the anchored node is a non-empty mapping or list, the troupe CLI shall write the copy as a block collection on the lines following the key's line, indented one step of the file's indentation deeper than the key, with every nested non-empty collection also in block style, and with the file's line breaks.
3. When the anchored node is a scalar or an empty collection, the troupe CLI shall write it on the key's line after `: `, followed by two spaces and the comment, for example `other: foo  # expanded from *n by troupe` or `settings: {}  # expanded from *d by troupe`. A literal or folded block scalar keeps its indicator on the key's line, followed by the comment, and its content on the following lines.
4. The troupe CLI shall write each key and scalar of the copy with the value and quoting style it has in the anchored node, and literal and folded block scalars as literal and folded with the same content. Where that style cannot be written at the copy's place, the value shall be written under config-set-writeback's rules for new values. Include strings and mapping-form includes in the anchored node are copied as written, so that the copy includes the same files.
5. The troupe CLI shall not repeat in the copy any anchor written inside the anchored node, and shall copy each alias written inside the anchored node as the same alias, so that every other alias in the file still refers to the node it referred to before.
6. The troupe CLI shall not copy comments written inside the anchored node, and shall change no byte of the anchored node, of any other alias, or of any other line apart from the key's line and the inserted copy.
7. After expanding, the troupe CLI shall set VALUE at PATH in the copy under the rules of config-set-writeback and config-include-overrides, as for a value written there, including the end-of-line comment rules for the key's line (config-set-writeback Requirements 11 and 12).

### Requirement 8: Expansion cases
**Objective:** As a developer, I want `--expand-alias` to behave predictably for nested aliases, lists and unusual layouts, so that it either does exactly what I asked or changes nothing.

#### Acceptance Criteria
1. When PATH is reached through more than one alias, the troupe CLI shall expand each alias on PATH, outermost first, each under Requirement 7 with its own comment, before setting VALUE.
2. When VALUE replaces a collection and the replacement would change, add or remove values reached through aliases below PATH, the troupe CLI shall also expand, under Requirement 7, each alias through which a value would change, outermost first. An alias whose values are all unchanged shall stay an alias.
3. When the alias is a block list item (`- *d`), the troupe CLI shall apply Requirement 7 with the item's line in place of the key's line, writing the comment two spaces after the `-`.
4. If an alias to be expanded lies inside a flow collection, then the troupe CLI shall exit with code 3, write nothing to stdout, write a stderr message that names the file, PATH and the alias and says that it cannot be expanded in a flow collection, and change no file.
5. If the line that would receive the comment already has an end-of-line comment, then the troupe CLI shall exit with code 3, write nothing to stdout, write a stderr message that names the file, PATH and the alias and says that the line already has a comment, and change no file.
6. When `--expand-alias` is given, PATH is reached through an alias and VALUE equals the current value at PATH, the troupe CLI shall still expand the aliases on PATH and record no event.

### Requirement 9: Merge keys are never written
**Objective:** As a developer, I want troupe to leave my merge keys alone, so that a `set` never rewrites YAML merges behind my back.

#### Acceptance Criteria
1. The troupe CLI shall never write a `<<` key, nor change, add to or remove from the value of a merge key, in any file.
2. If a set would change, add or remove a value that a mapping receives from a merge key (rather than a value written in the mapping itself), with or without `--expand-alias`, then the troupe CLI shall exit with code 3, write nothing to stdout, write a stderr message that names the file, PATH and the merge key, and says that merge keys are never written (mentioning that `--expand-alias` cannot expand them), and change no file.
3. When `troupe config set` changes a key written in a mapping that also has a merge key, or adds a new key to that mapping, the troupe CLI shall write it in that mapping under the usual rules and leave the merge key unchanged.

### Requirement 10: Safety of alias writes
**Objective:** As a developer, I want writes involving anchors and aliases held to the same guarantees as every other write, so that a `set` can never corrupt my files.

#### Acceptance Criteria
1. After a successful `troupe config set`, the troupe CLI shall have changed only the bytes Requirements 4, 7 and 8 and the earlier specs require, in the files they name, and no byte of any other file.
2. Before writing, the troupe CLI shall read the changed files back as configuration, including anchors, aliases, merge keys, includes and overrides. It shall exit with code 3 and change no file if the result is not the current expanded configuration with the value at PATH replaced by VALUE. For a write at an anchor, the intended configuration has the change at every place where the anchor is used. For an expansion, it has the change only at PATH.
3. The troupe CLI shall write the expansion and the set into each changed file together, atomically, and leave no extra file behind, as config-set-writeback specifies.
4. The troupe CLI shall record no event for an expansion itself, and the events of a set with `--expand-alias` shall be those of the leaf values it changed (config-set-writeback Requirement 15).
