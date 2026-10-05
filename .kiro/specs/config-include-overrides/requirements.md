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


## Requirements
<!-- Will be generated in /kiro-spec-requirements phase -->


