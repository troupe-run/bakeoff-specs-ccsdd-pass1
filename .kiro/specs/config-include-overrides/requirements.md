# Requirements Document

## Project Description (Input)
### Problem
Included YAML files are shared between projects (for example one `agents/coder.yaml` used by several
theatres). Today `troupe config set` writes a new key under an included section into the included file itself
(step 1 rule 2: "a new key is written into the file containing the deepest existing ancestor mapping"). One
project's `set` therefore silently changes every other project that shares the file. Developers have no way
to say "this theatre's coder is the shared coder plus `temperature: 0.3`" without copying the shared file.

### Current State
- config-foundation (`config/loader.py`): any string scalar starting with `file:` is an include. `.yml`/`.yaml`
  targets are embedded (`IncludeKind.YAML`), anything else is a text include. The source-mapped model
  (`config/model.py`: `SourceFile`, `Span`, `Origin`, `IncludeRef(kind, spec, target, directive)`,
  `ConfigScalar`/`ConfigMapping`/`ConfigSequence` with `origin` and `include`) assumes every collection's entries
  are written in one file. There is no `$ref` handling: `{$ref: file:x.yaml, model: big}` loads today as an
  ordinary mapping with a key `$ref` whose value is the embedded file.
- config-set-writeback (`config/writeback/`): `resolve_target` → `AddKeyTarget(mapping, missing)`; the planner's
  `add_entries` writes into `collection.origin.file`, so a new key under an included mapping lands in the included
  file (pinned by `tests/test_set_targets.py::test_new_key_below_yaml_include_is_added_in_the_included_file`).
  Replacing a YAML include root is refused with exit 3 (`_change_include`). `_replace_value` can already turn a
  scalar (including an include directive, via `FileLayout.inline_extent`) into a block mapping while keeping the
  key line's end-of-line comment. Read-back verification (`verify_patch`) reloads the patched files and compares
  `node_to_plain(root)` with the intended tree from `compare.splice`, where includes are `IncludeDirective`s.
- config-get-show / config-file-sync / config-lineage: `show --sources`, leaf flattening, reconcile and history
  work on the loaded tree by path and node origin; they need no change of their own if the loaded tree is the
  merged view with correct per-scalar origins and no `$ref` key.
- Gap: no mapping-form include, no deep merge, no override write target, no scalar → mapping-form conversion.

### Desired Outcome
The step 5 contract section "Overrides" holds (it replaces rule 2 under `set PATH VALUE`):
- An include may be written in **mapping form**: a mapping with a `$ref` key whose value is the include string.
  Its other keys are overrides; the value is the included content deep-merged with the overrides (mappings merge
  recursively; scalars and lists in the override replace).
- A new key whose deepest existing ancestor comes from an included file is written as an override in the file that
  contains the include reference. A scalar-form include is first converted to mapping form, at the file's
  indentation, keeping any end-of-line comment on the key's line:
  `coder: file:agents/coder.yaml  # c` → `coder:  # c` / `  $ref: file:agents/coder.yaml` / `  temperature: 0.3`.
- Appending a list item is not a new key: it is appended in the file that holds the list. A new key inside a list
  item is written in the file that holds the list (overrides cannot address list items).
- Overrides apply only to YAML includes; a mapping-form include of any other file with sibling keys → exit 4.
- Setting an existing value that is overridden changes the override; one that is not overridden is still changed in
  the file it comes from.
All other `set` rules (byte-surgical rendering, events, atomic writes, read-back verification) are unchanged, and
`get`/`show`/`--sources`/`history`/`watch` see the merged configuration.

### Approach
Chosen: **the loader builds the merged view, and every merged mapping keeps its layers.**
- The loader recognises a mapping with a `$ref` key whose value is an include string. Without siblings it is
  exactly a scalar-form include (YAML or text). With siblings it requires a YAML target whose content is a mapping,
  and produces a merged `ConfigMapping`: included entries in their order with overridden values replaced (merged
  recursively when both sides are mappings), then override-only keys in override order. `$ref` itself never
  appears in the merged view, so paths, leaves, history, `get` and `show` need no special casing. Each scalar
  keeps its own origin, so `--sources` names the including file for overridden values.
- Each merged mapping records its physical layers: the included mapping (base) and the override mapping as
  written in the including file (with the `$ref` directive's position). The writeback layer resolves "which file,
  which physical mapping" through these layers instead of assuming a collection lives in one file:
  - existing overridden value → the override entry; existing non-overridden value → the base file (unchanged rule);
  - new key: walk up from the deepest existing ancestor; if an include root is reached before a list item, write
    the key (with any missing intermediate mappings, mirroring the included nesting) as an override of that
    include, in the file holding its directive; otherwise the step 1 rule applies (the ancestor's own file);
  - a scalar-form directive is converted to mapping form by the existing scalar → block-mapping machinery, with
    `$ref:` as the first entry, keeping the directive text (and its quoting) verbatim.
- The intended tree used by read-back verification gains mapping-form include directives (directive + overrides),
  so verification still proves the files read back as exactly the intended configuration.

Rejected alternatives:
- *Keep the physical tree as the model and compute the merged view on demand*: every reader (`get`, `show`,
  leaves, reconcile, path walking) would need to merge, and they would drift. The merged view is what the
  contract defines as "the value".
- *A separate overlay structure keyed by path, outside the node model*: a second source of truth for provenance
  that the planner and layout code would have to keep in step with the tree.
- *Write new keys in the root file always*: contradicts the contract ("the file that contains the include
  reference"), and gives nested includes a different owner than their directive.

### Scope
- **In**: loading mapping-form includes (YAML and text, any depth, at the document root, in list items, inside
  included files, inside overrides); the deep merge; exit 4 for a non-YAML mapping-form include with siblings and
  the other refusals settled below; the new-key target rule (override in the including file, scalar-form
  conversion, list exceptions); changing overridden vs non-overridden existing values; collection replacement and
  removals across layers; read-back verification of overrides; `show --sources` provenance of overrides; history
  leaves of the merged view; updating step 1–4 tests that pin the old rule 2; step-5 notes in config-foundation
  (Requirement 4 Includes), config-set-writeback (rule 2 / Requirement 3) and config-get-show (provenance).
- **Out**: a syntax to delete an included key through an override; overriding list items; `$ref` to anything other
  than `file:` include strings; merge keys/anchors (still exit 4, until step 6); Windows; concurrent writers.

### Boundary Candidates
- Loader: `$ref` recognition, validation and the deep merge into a layered model (core/file layer).
- Model: layer provenance on merged mappings (narrow addition to `model.py`).
- Writeback target resolution: which physical mapping and file receive a change or a new key.
- Writeback planning: override insertion, scalar-form → mapping-form conversion, edits across layers.
- Read-back verification: intended tree with mapping-form directives.

### Out of Boundary
- Rendering rules (quoting, comments, indentation, block scalars) — reused unchanged from config-set-writeback.
- History event shapes, lineage, reconcile and watch mechanics — unchanged; they see the merged view.
- `show` output format — unchanged; only the content (merged) and source labels follow from the model.

### Upstream / Downstream
- **Upstream**: config-foundation (loader, model, errors), config-set-writeback (target, planner, layout,
  verify, compare), config-get-show (`render_yaml`, `source_label`), config-file-sync/config-lineage (unchanged
  consumers of leaves).
- **Downstream**: step 6 (anchors/aliases/merge keys) will interact with mapping-form includes and `$ref`.

### Existing Spec Touchpoints
- **Extends**: config-foundation Requirement 4 (Includes: mapping form, overrides, exit 4) — step-5 note.
  config-set-writeback Requirement 3 (new keys: target file now follows the override rule) and Requirement 2
  (existing values: overridden vs not) — step-5 notes; tests pinning the old rule are updated in this spec's tasks.
- **Adjacent**: config-get-show (`--sources` labels overrides with the including file — falls out of origins);
  config-file-sync, config-lineage (unchanged).

### Constraints
- Python 3.12+, uv, existing dependencies only, `mypy --strict`, ruff, real-file/real-CLI byte-exact tests.
- Byte-surgical: a `set` that adds an override changes only the including file, and only the bytes required
  (the conversion of a scalar-form directive plus the new entry). Included files stay byte-identical.
- No file changes on any refusal; read-back verification stays the final guard (exit 3).
- Open choices to settle and record in requirements/research (proposed defaults):
  1. *`$ref` value that is not an include string* (not a string, or not starting with `file:`): exit 4 naming the
     file and key path. `$ref` is now reserved; guessing it is an ordinary key could silently misread a typo.
  2. *Overrides on a YAML include whose content is not a mapping* (list, scalar, empty file): exit 4 — a mapping of
     overrides cannot be deep-merged into it.
  3. *Mapping-form include without siblings*: identical to the scalar form for every kind (YAML or text); a text
     target is allowed there (only "with sibling keys" is refused).
  4. *Order of the merged mapping*: included keys in their order (overridden ones keep their place), then
     override-only keys in override order.
  5. *Nested includes*: "the including file" is the file holding the directive of the nearest include above the
     deepest existing ancestor (the innermost one), even when that file is itself included.
  6. *Ancestor merged from both layers* (key present in included content and in the override): the new key goes
     into the override's mapping (it is in the including file).
  7. *A PATH segment `$ref` in `set`*: refused with exit 3 naming PATH — `$ref` is reserved for includes; `get`/
     `show` treat it as not found (exit 5) because it is not part of the merged value.
  8. *Replacing a mapping-form include root as a whole*: still refused with exit 3 when its value would change, as
     for scalar-form includes (rule 4); changing values inside it follows the per-key rules.
  9. *Removing a key during a collection replacement*: removed from every layer it is written in (override and/or
     included file) — "existing settings still change where they live".
  10. *VALUE containing a `$ref` mapping*: written as given, like a `file:` string (decision D): the read-back must
     be the mapping-form include it describes, else exit 3.

### Step 5 Contract, CR-A and section "Overrides" (authoritative, as supplied by the product owner)
**CR-A: keep shared includes shared.** Included files are shared between projects. Adding a new setting under an
included section must no longer modify the shared file: it should be added in the including file, as an override.
Existing settings still change where they live. Contract sections added: Overrides (replaces rule 2 under
"set PATH VALUE").

**Overrides (from step 5 — replaces rule 2 under `set`)**
- An include may be written in **mapping form**: a mapping with a `$ref` key whose value is the include
  string. Its other keys are **overrides**: the value is the included content deep-merged with the overrides
  (mappings merge recursively; scalars and lists in the override replace).
- A new key whose deepest existing ancestor comes from an included file is written as an override in the
  file that contains the include reference (the *including* file). A scalar-form include is first converted
  to mapping form, indented at the file's indentation, keeping any end-of-line comment on the key's line:
  `coder: file:agents/coder.yaml  # c` → `coder:  # c` / `  $ref: file:agents/coder.yaml` / `  temperature: 0.3`
- Appending a list item is not a new key: the item is appended in the file that holds the list. Likewise, a
  new key inside a list item is written in the file that holds the list, since overrides cannot address list
  items.
- Overrides apply only to YAML includes; a mapping-form include of any other file with sibling keys → exit 4.
- Setting an existing value that is overridden changes the override; one that is not overridden is still
  changed in the file it comes from.


### CR-B and section "In-include" (authoritative, as supplied by the product owner)
**CR-B: but let me put it in the shared file when I mean to.** Sometimes a new setting genuinely belongs in the
shared file. Give `set` an explicit way to do that. Contract sections added: In-include.

**In-include (from step 5, change request B)**
- `set --in-include PATH VALUE` writes a new key into the included file instead (the step 1 rule).

See `brief.md` for the chosen approach, scope, boundaries and the open choices to settle.

## Introduction
This specification covers the step 5 change request CR-A, "keep shared includes shared", and the contract section
"Overrides", which replaces rule 2 under `set PATH VALUE` (config-set-writeback Requirement 3.1–3.2). Included
YAML files are shared between projects. A theatre must be able to adjust an included section for itself without
changing the shared file, and `troupe config set` must do so on its own when it adds a new setting under an
included section.

Two things change:
- **Reading**: an include may be written in *mapping form*, a mapping with a `$ref` key whose value is the include
  string. Its other keys are *overrides*. The value at that place is the included content deep-merged with the
  overrides. Every command that reads the configuration sees this merged value.
- **Writing**: a new key below an included section is written as an override in the *including* file. Existing
  settings are still changed where they live: in the override when they are overridden, otherwise in the file they
  come from.
- **On request (CR-B)**: `set --in-include` writes a new key into the included file instead, by the step 1 rule
  (Requirement 10).

Terms used below:
- **Include string**: a string starting with `file:`, as defined by config-foundation Requirement 4.
- **Scalar-form include**: an include written as an include string at a value's place (step 1).
- **Mapping-form include**: a mapping written in a YAML file that has a key `$ref` whose value is an include string.
  Its other keys are its **overrides**. A mapping-form include with no other keys is **bare**.
- **Including file**: the file in which an include string (scalar form, or the value of `$ref`) is written.
- **Included content**: the value an include contributes as defined by config-foundation Requirement 4: the
  target's YAML content for a `.yml`/`.yaml` target, else the target file's exact text.
- **Deep merge**: the merge of the overrides into the included content. Where both the included content and the
  override hold a mapping under the same key, the two mappings are merged in the same way, recursively. Anywhere
  else, the override's value (a scalar, a list, or a mapping over a non-mapping) replaces the included value. Keys
  only in the included content are kept.
- **Overridden value**: a value in the merged configuration that is written in the overrides of a mapping-form
  include (at any depth below it) rather than in the included file.
- **Comes from an included file**: a value comes from an included file when it is written in the included file
  (not in an override). A mapping present both in the included content and in the overrides is written in both.
- **Deepest existing ancestor**: for a PATH that does not exist, the value at the longest prefix of PATH that
  exists (config-set-writeback Requirement 3).

## Boundary Context
- **In scope**:
  - Loading mapping-form includes wherever a value may be written, in the root file and in included files, inside
    lists, inside overrides and at the document root, for YAML and text includes, to any depth.
  - The deep merge, and the refusals for mapping-form includes that cannot be loaded.
  - The merged configuration as seen by `get`, `show`, `show --sources`, `history`, the reconcile and `watch`.
  - The `set` rules of the "Overrides" section: where a new key is written, the conversion of a scalar-form include
    to mapping form, list appends and keys inside list items, and changing overridden and non-overridden values.
- **Out of scope**:
  - A way to delete an included key through an override (the contract has none: an override can only add or
    replace).
  - Overriding a single list item, or merging lists.
  - `$ref` values other than include strings (they are refused, 2.2).
  - Anchors, aliases and merge keys (still exit 4, config-foundation decision H).
- **Adjacent expectations**:
  - config-foundation keeps owning the scalar-form include rules (Requirement 4), path addressing and the error
    format. Its Requirement 4 gains a step-5 note pointing here.
  - config-set-writeback keeps owning every "How it is written" rule (quoting, styles, indentation, comments,
    insertion points, removals), read-back verification, atomic writes and the `set`/`remove` events. Only its rule
    2 (Requirement 3.1–3.2: which file receives a new key) is replaced by Requirements 4 and 5 here, and its
    Requirement 2 (existing values) is refined by Requirement 7 here. Both gain step-5 notes.
  - config-get-show, config-file-sync and config-lineage are unchanged. They see the merged configuration. Their
    output formats, reconcile rules and lineage do not change.
- **Choices made where the contract is silent** (conservative; recorded in `research.md`):
  1. A `$ref` whose value is not an include string (not a string, or not starting with `file:`) is refused with
     exit 4 (2.2). `$ref` is now reserved for includes. Reading it as an ordinary key could hide a typo, and
     configurations with a `$ref` key changed meaning in step 5 anyway.
  2. Overrides on a YAML include whose content is not a mapping (a list, a scalar, or an empty file) are refused
     with exit 4 (2.3). A mapping of overrides cannot be merged into anything else, and replacing the included
     content by the overrides would silently drop it.
  3. A bare mapping-form include behaves exactly like the scalar form, for YAML and text targets (1.4). Only
     "with sibling keys" is refused for non-YAML targets.
  4. Order of a merged mapping: the included content's keys in their order (an overridden key keeps its place),
     then the keys only in the overrides, in the order they are written (1.3).
  5. With nested includes, the "including file" for a new key is the file that holds the include reference of the
     nearest include above the deepest existing ancestor, even when that file is itself included (4.2).
  6. A new key whose deepest existing ancestor is written both in the included file and in an override is written
     into the override (4.3).
  7. A PATH with a `$ref` segment: `get`/`show` do not find it (exit 5, it is not part of the merged value); `set`
     refuses it with exit 3 (8.1), because writing a `$ref` key would turn a mapping into an include or break an
     existing one.
  8. Replacing the whole value of a mapping-form include (PATH naming the include's place) is refused with exit 3
     when the value would change, as for a scalar-form include (config-set-writeback rule 4, 7.5). Values inside it
     are changed by their own paths.
  9. When a collection replacement removes a key below an include, the key is removed from every file it is written
     in: the override and/or the included file (7.4). "Existing settings still change where they live."
  10. VALUE may contain a mapping with a `$ref` key. It is written as given, under the usual rules, like a `file:`
      string (config-set-writeback 2.7, decision D). The configuration read back must contain the mapping-form
      include it describes, otherwise the set is refused (exit 3) (8.2).
  11. The conversion of a scalar-form include follows the contract's example exactly: the key's line keeps its
      bytes up to the `:` followed by the whitespace that preceded the end-of-line comment and the comment
      (`coder: file:agents/coder.yaml  # c` → `coder:  # c`). For this conversion the example takes precedence over
      config-set-writeback 11.5 (comment kept at its previous column), which still applies to every other
      scalar → collection replacement (5.2).

- **Choices for CR-B (In-include)**, made where the contract is silent (conservative; recorded in `research.md`):
  12. `--in-include` changes only where *new* keys are written, including the new keys of a collection
      replacement. Existing values, list appends and removals follow the same rules as without it (10.4).
  13. "The included file" is read as the step 1 rule says: the file containing the deepest existing ancestor
      mapping. With nested includes, that is the innermost included file. When that ancestor is written both in the
      included file and in an override, the included side is used. When it is written only in an override, the step
      1 rule gives the including file (10.1, 10.2).
  14. Where no include is involved, `--in-include` gives the same result as without it, and it is accepted rather
      than refused (10.5).
  15. `--in-include` takes no value. It may stand before, between or after PATH and VALUE, and it combines with
      `--reason`. A value given with it is a usage error (exit 2) (10.6).

- **Product owner review (2026-10-05)**: all of the choices above are approved.
  - *Confirmed as the contract's meaning*:
    - choice 11 (the conversion follows the Overrides example literally; the `set` end-of-line comment rule
      still applies to every other change);
    - choice 5 (the including file is the file holding the reference to the include that the deepest existing
      ancestor's content comes from, even when that file is itself included);
    - choice 9 (removals follow CR-A's "existing settings still change where they live" and the last Overrides
      bullet).
  - *Out of contract, our choice, accepted*:
    - choices 1 and 2 (exit 4);
    - choices 7 and 8 (exit 3, consistent with rule 4 under `set`).

- **Product owner review of CR-B (2026-10-05)**: Requirement 10 and choices 12–15 are approved.
  - *Confirmed as the contract's meaning*:
    - choice 12 (the flag changes only where a new key is written; existing values still change where they live);
    - choice 13 ("the included file" is the step 1 rule's file containing the deepest existing ancestor mapping,
      the innermost file for nested includes, and that rule applies when the ancestor is also, or only, in an
      override).
  - *Out of contract, our choice, accepted*: choices 14 and 15 (the flag is accepted with no include involved; its
    parsing, including exit 2 for `--in-include=TEXT`).

## Requirements

### Requirement 1: Mapping-form includes
**Objective:** As a developer, I want to include a shared YAML file and adjust some of its settings for my theatre, so that I can reuse shared settings without copying or changing them.

#### Acceptance Criteria
1. When a mapping written in a YAML file has a key `$ref` whose value is an include string, the troupe CLI shall treat the mapping as a mapping-form include of that string's target, resolved relative to the directory of the file containing the mapping under the rules of config-foundation Requirement 4.
2. When a mapping-form include of a `.yml`/`.yaml` target has overrides, the troupe CLI shall use as the value at that place the included content deep-merged with the overrides: where both sides hold a mapping under the same key the mappings are merged recursively, every other override value replaces the included value, and included keys that are not overridden are kept.
3. The troupe CLI shall order the keys of a merged mapping as the included content's keys in their written order, each overridden key at its included position, followed by the keys that exist only in the overrides in their written order.
4. When a mapping-form include is bare, the troupe CLI shall use the same value as the scalar-form include of the same string, for YAML and for text targets.
5. The troupe CLI shall not include the `$ref` key itself in the value of a mapping-form include, so that no path, leaf, history event or output line contains a `$ref` segment produced by an include.
6. The troupe CLI shall load mapping-form includes in any YAML file of the configuration (the root file or an included file), at the document root, as a list item, inside overrides and inside included content, in block or flow style, to any depth.
7. When an override value is itself an include (scalar or mapping form), the troupe CLI shall resolve it first and merge its value as the override value.
8. The troupe CLI shall read mapping-form includes without creating, modifying, renaming or deleting any file, and shall apply config-foundation Requirement 4.5–4.7 (missing target, cycle, unloadable target → exit 4) to the `$ref` include string as to any include string.

### Requirement 2: Mapping-form includes that cannot be loaded
**Objective:** As a developer, I want a clear error when a mapping-form include cannot mean anything, so that a mistake is never silently read as some other configuration.

#### Acceptance Criteria
1. If a mapping-form include's target is not a `.yml`/`.yaml` file and the mapping has at least one key other than `$ref`, then the troupe CLI shall exit with code 4, write nothing to stdout, and write a stderr message that names the including file, the key path of the mapping and the include target, and states that overrides apply only to YAML includes.
2. If a mapping written in a YAML file has a key `$ref` whose value is not a string starting with `file:`, then the troupe CLI shall exit with code 4, write nothing to stdout, and write a stderr message that names the file and the key path of the mapping and states that `$ref` must be an include string.
3. If a mapping-form include of a `.yml`/`.yaml` target has overrides and the target's content is not a mapping (a list, a scalar, or an empty file), then the troupe CLI shall exit with code 4, write nothing to stdout, and write a stderr message that names the including file, the key path and the included file, and states that overrides need a mapping.
4. When a command refuses a configuration under 2.1–2.3, the troupe CLI shall behave as for any other configuration that cannot be loaded (config-foundation Requirement 3): no file is changed and no history state is created or changed.

### Requirement 3: The merged configuration in every command
**Objective:** As a developer, I want every command to show and record the configuration as it is after overrides, so that what I read is what troupe uses.

#### Acceptance Criteria
1. When `troupe config get PATH` or `troupe config show [PATH]` addresses a value at or below a mapping-form include, the troupe CLI shall print the merged value.
2. When `troupe config show --sources` prints a scalar that is an overridden value, the troupe CLI shall name the including file in which the override is written; for a scalar that is not overridden it shall name the included file, as before.
3. The troupe CLI shall import, reconcile and record history events for the leaves of the merged configuration, so that a hand edit that adds, changes or removes an override is recorded as the set or remove events of the merged leaves it changes.
4. When a hand edit rewrites a scalar-form include as a bare mapping-form include of the same string (or the reverse), the troupe CLI shall record no event, because the merged configuration is unchanged.
5. While `troupe config watch` runs, the troupe CLI shall follow the targets of mapping-form includes as it follows the targets of scalar-form includes.

### Requirement 4: Where a new key is written
**Objective:** As a developer sharing included files between projects, I want `set` to add new settings below an included section to my own file, so that the shared file never changes when I add a setting.

#### Acceptance Criteria
1. When `troupe config set` adds a key whose deepest existing ancestor comes from an included file, the troupe CLI shall write the key, with any missing intermediate mappings, as an override in the including file of that include, and shall leave the included file byte-identical.
2. When the deepest existing ancestor lies below several nested includes, the troupe CLI shall write the override for the nearest include above it, in the file that holds that include's reference, even when that file is itself included.
3. When the deepest existing ancestor is written in an override (alone, or also in the included file), the troupe CLI shall write the new key into that override's mapping in the including file.
4. When the deepest existing ancestor is a mapping inside the included content that the overrides do not yet contain, the troupe CLI shall create in the overrides those mappings along the path from the include down to that ancestor that the overrides do not yet contain, holding the new key, so that the merged value keeps every included key and gains the new one.
5. When the include is already in mapping form, the troupe CLI shall add the new override under config-set-writeback's rules for new keys in that mapping (placement after the last entry and its trailing comments, sibling quoting style, block or flow style).
6. When the deepest existing ancestor does not come from an included file (it is written in the root file, or reached through no include), the troupe CLI shall add the key in the file containing that ancestor, as before.
7. The troupe CLI shall record one `set` event for each leaf created by the new key, as config-set-writeback Requirement 15 specifies, and no event for the conversion of an include to mapping form.

### Requirement 5: Turning a scalar-form include into a mapping-form include
**Objective:** As a developer, I want the first override of an include written the way I would write it by hand, so that the diff shows exactly the override and nothing else.

#### Acceptance Criteria
1. When a new key must be written as an override of a scalar-form include in a block mapping, the troupe CLI shall rewrite the include's place as a block mapping that starts on the next line with `$ref: ` followed by the include string exactly as it was written (same quoting style and bytes), followed by the new override entries, all indented one step of the file's indentation deeper than the key.
2. When the include string was followed by an end-of-line comment on the key's line, the troupe CLI shall keep the key's line up to and including the `:` followed by the whitespace that preceded the comment and the comment itself (for example `coder: file:agents/coder.yaml  # c` becomes `coder:  # c`); when there was no comment, the key's line shall end right after the `:`.
3. When the scalar-form include is a block list item, the troupe CLI shall write the mapping-form include as the list item's mapping (`$ref` entry first, then the overrides) at the list's item indentation, under the same rules config-set-writeback uses when a list item's scalar is replaced by a block mapping.
4. When the scalar-form include is inside a flow collection, the troupe CLI shall write the mapping-form include as a flow mapping in place of the include string, with `$ref` first, keeping the rest of the line under config-set-writeback's end-of-line comment rules.
5. The troupe CLI shall change no other bytes of the including file and no byte of the included file.

### Requirement 6: Lists
**Objective:** As a developer, I want list edits to stay with the list, so that lists are never split between files.

#### Acceptance Criteria
1. When `troupe config set` appends an item to a list (PATH ending in the index equal to the list's length), the troupe CLI shall append it in the file that holds the list, including a list written in an included file.
2. When `troupe config set` adds a key and, going up from its deepest existing ancestor, a list item is reached before any include (the ancestor is a list item that is not itself an include, or lies inside one), the troupe CLI shall write the key in the file that holds that list, under the rules for new keys of config-set-writeback.
3. When the list item is itself an include (the list holds the include reference), the troupe CLI shall write the key as an override of that include in the file that holds the list (Requirement 5.3 when it is in scalar form).

### Requirement 7: Changing existing values
**Objective:** As a developer, I want an existing setting changed where it is written, so that an override changes the override and a shared value changes where it is shared.

#### Acceptance Criteria
1. When PATH resolves to an overridden value, the troupe CLI shall change the override in the including file under config-set-writeback's rules for changed values, and leave the included file unchanged.
2. When PATH resolves to a value below a mapping-form include that is not overridden, the troupe CLI shall change it in the file it comes from, as config-set-writeback Requirement 2.6 specifies.
3. When VALUE replaces a mapping at or below a mapping-form include, the troupe CLI shall apply 7.1 and 7.2 to each changed entry, Requirement 4 to each added key, and 7.4 to each removed key.
4. When a collection replacement removes a key below an include, the troupe CLI shall remove it from every file in which it is written (the override, the included file, or both), under config-set-writeback's removal rules.
5. If PATH names the place of a mapping-form include and VALUE differs from its merged value, then the troupe CLI shall exit with code 3, name PATH and the included file, and change no file, as for a scalar-form include (config-set-writeback 2.5). "Differs" is judged as for a scalar-form include (step 1 rule 4, config-set-writeback 2.5): the place holds an include, and a VALUE that is not an include never equals it, so a mapping equal to the merged mapping still differs (exit 3).
6. When PATH names a text include in bare mapping form and VALUE is a string, the troupe CLI shall replace the target file's content as for a scalar-form text include (config-set-writeback 2.2–2.4).

### Requirement 8: `$ref` in PATH and VALUE
**Objective:** As a developer, I want `set` to refuse to write `$ref` keys by path and to handle `$ref` in a value predictably, so that an include is never created or broken by accident.

#### Acceptance Criteria
1. If a segment of PATH given to `troupe config set` is `$ref`, then the troupe CLI shall exit with code 3, write nothing to stdout, write a stderr message that names PATH and states that `$ref` is reserved for includes, and change no file.
2. When VALUE contains a mapping with a `$ref` key, the troupe CLI shall write it as any other mapping under config-set-writeback's rules, and shall accept the result only if the configuration read back contains, at that place, the value of the mapping-form include it describes; otherwise (for example a missing target, a non-YAML target with overrides, or a `$ref` that is not an include string) it shall exit with code 3, name PATH and the include target or file, and change no file.
3. When `troupe config get` or `troupe config show` is given a PATH with a `$ref` segment below a mapping-form include, the troupe CLI shall treat the path as not found (exit 5).

### Requirement 9: Safety of override writes
**Objective:** As a developer, I want override writes held to the same guarantees as every other write, so that a `set` can never corrupt my files.

#### Acceptance Criteria
1. After a successful `troupe config set`, the troupe CLI shall have changed only the bytes Requirements 4–8 and config-set-writeback require, in the files they name, and no byte of any other file.
2. Before writing, the troupe CLI shall read the changed files back as configuration, including mapping-form includes and the deep merge, and shall exit with code 3 and change no file if the result is not the current configuration with the value at PATH replaced by VALUE.
3. The troupe CLI shall write every changed file atomically and leave no extra file behind, as config-set-writeback specifies.

### Requirement 10: Writing a new key into the included file on request
**Objective:** As a developer, I want to say explicitly that a new setting belongs in the shared included file, so that I can extend a shared file from the shell when that is what I mean.

#### Acceptance Criteria
1. When `troupe config set --in-include PATH VALUE` adds a key, the troupe CLI shall write the key, with any missing intermediate mappings, into the file containing the deepest existing ancestor mapping of PATH (the step 1 rule, config-set-writeback 3.1–3.2), including an included file, instead of applying Requirements 4 and 5, and shall leave every other file byte-identical.
2. When `--in-include` is given and the deepest existing ancestor is a merged mapping written both in the included file and in an override, the troupe CLI shall add the key to the mapping written in the included file, and the merged value shall have the new key after the keys of the included side and before the keys that exist only in the overrides (Requirement 1.3).
3. When `--in-include` is given and VALUE replaces a mapping, the troupe CLI shall write each key that VALUE adds under 10.1 and 10.2.
4. When `--in-include` is given, the troupe CLI shall change existing values, append list items and remove keys exactly as it does without the option (Requirements 6.1 and 7).
5. When `--in-include` is given and the new key lies below no include, the troupe CLI shall write it exactly as without the option.
6. The troupe CLI shall accept `--in-include` without a value, before, between or after PATH and VALUE, together with `--reason`; if a value is attached to it (`--in-include=TEXT`), then the troupe CLI shall exit with code 2, write nothing to stdout, write a stderr message describing the usage problem, and change no file and no history state.
7. When `troupe config set --in-include` writes a new key, the troupe CLI shall record its events and lineage as for any other `set` (one `set` event per created leaf, config-lineage), and shall apply Requirement 9 (byte-surgical write, read-back verification, atomic write).
