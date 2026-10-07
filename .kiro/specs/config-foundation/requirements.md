# Requirements Document

## Project Description (Input)
### Problem
Developers who run troupe keep each theatre's configuration in hand-edited YAML (`.troupe/troupe.yml`),
often split across files with `file:` includes. They need to read a setting from the command line and see
how the configuration has changed. Errors must name the file or path at fault. There is no code yet.

### Current State
Greenfield repository: steering documents only, no package, no tests. The steering file `structure.md`
proposes a layout (`src/troupe/`, CLI → core → file layer) that the first design must confirm.

### Desired Outcome
- `uv run troupe config show PATH [--theatre DIR]` and `uv run troupe config history [PATH] [--theatre DIR]`
  work from the repository root, following the step 1 contract (General, Includes, show, history).
  *(Step 3 (spec config-get-show): the scalar command described here is now `troupe config get PATH`; the new
  `troupe config show [PATH] [--sources]` prints YAML and is owned by config-get-show.)*
- The configuration is loaded with recursive includes into a model that records, for every node, the file
  and source span it came from, its scalar style, and whether it came from an include (YAML or text).
  config-set-writeback builds on this model.
- An `eventsourcing`-based aggregate/projection persisted under `<theatre>/.troupe/`. The first command run
  against a theatre imports it as one `import` event per leaf (`old` null). Later commands do not reconcile
  hand edits. *(Superseded by step 2 (spec config-file-sync, requirements 1.1–1.3, 1.7): every command except
  `history` now reconciles hand edits into the history first.)*
- Exit codes and error messages behave as the contract says (2 usage, 4 load, 5 path), with empty stdout on
  any failure.

### Approach
ruamel.yaml (YAML 1.2 core schema) is used to compose nodes with positions and styles. A source-mapped tree
is built over all included files, and `eventsourcing` holds the per-leaf event log (see roadmap.md
Approach Decision). The CLI uses a standard argument parser, with usage errors mapped to exit 2.

### Scope
- **In**: project scaffold (pyproject, uv, `troupe` console script, mypy/ruff/pytest config); `--theatre`
  handling; locating `troupe.yml`/`troupe.yaml` (neither or both → 4); loading, including `file:` includes
  (`.yml`/`.yaml` embedded, other targets as exact text, relative to the including file, recursive; missing
  target or cycle → 4); dotted path addressing with digit segments as list indices; leaf flattening; scalar
  printing rules (strings exact plus `\n` unless already present, bool, null, int, float `repr`);
  `show PATH` (mapping/list → 5, missing → 5; *step 3: now `get PATH`, see spec config-get-show*); event
  store + projection + import; `history [PATH]` JSON
  Lines with `seq`, `op`, `path`, `old`, `new` and prefix filtering; central error types and exit-code
  mapping.
- **Out**: `set` and all write-back rules (config-set-writeback); anchors/aliases/merge keys; multi-document
  files; concurrency.
  *Note (step 6): anchors, aliases and merge keys are out only until step 6; they now load (spec config-aliases,
  requirements 1 and 2).*

### Boundary Candidates
- File location and loading (the only reader of the user's files)
- Source-mapped config model and path resolution
- Event store / aggregate / projection and import
- CLI layer (argument parsing, rendering, exit codes)

### Out of Boundary
- Any modification of the user's YAML or text files
- Rendering/quoting rules for written values
- Emitting `set`/`remove` events (owned by config-set-writeback via this spec's API)

### Upstream / Downstream
- **Upstream**: Python 3.12+, uv, ruamel.yaml, eventsourcing; steering documents.
- **Downstream**: config-set-writeback (uses the model, path resolution, event API, error types). Later
  contract steps (step 2 `show` changes, step 5 write-target change, step 6 anchors).

### Existing Spec Touchpoints
- **Extends**: none
- **Adjacent**: config-set-writeback (shared model and event API seam)

### Constraints
- State only under `<theatre>/.troupe/`. Never create or modify any other file. Read commands must not alter
  the user's config files.
- Open design decision to record: whether files or the projection are authoritative for `show`, and how
  the event store is persisted (e.g. eventsourcing SQLite under `.troupe/`).
- `mypy --strict`, ruff, tests on real files and through the real CLI. Cover both `.yml` and `.yaml`.

### Step 1 Contract (authoritative, as supplied by the product owner)
- **General**: Invocation `troupe config <subcommand> --theatre <dir> [arguments]`, run as `uv run troupe …` from repo root; `--theatre` defaults to cwd. Config file `<dir>/.troupe/troupe.yml` or `.yaml`; neither or both → exit 4. State only under `<dir>/.troupe/`; must not create or modify any other file except as write-back rules require. Paths: keys separated by `.`; a digit segment indexes a list (0-based). Keys containing `.` and digit-only mapping keys out of contract. VALUE on command line parsed as a single YAML 1.2 core-schema flow node. Printing a scalar: string printed exactly, followed by `\n` unless it already ends with `\n`; booleans `true`/`false`; null as `null`; ints decimal; floats as Python `repr()`. Exit codes: 0 success, 2 usage error, 3 write refused (no file changed), 4 configuration cannot be loaded, 5 path not found or wrong kind of node for the command. On non-zero exit stdout is empty and stderr names the offending path or file. Out of contract: TAB before end-of-line comment, multi-document files, anchors/aliases/merge keys, concurrent writers.
- **Includes**: a string value starting with `file:` is an include of the path after `file:`, relative to the directory of the file containing it. Target ending `.yml`/`.yaml`: content embedded as if written in place of the string. Any other target: value is the target file's exact text. Recursive. Missing target or cycle → exit 4.
- **show PATH**: prints the value at PATH if scalar (a text include is a string). Mapping or list → exit 5.
- **history [PATH]**: prints events as JSON Lines, oldest first; with PATH, only events whose path equals PATH or starts with `PATH.`. Each line at least `{"seq": int from 1, "op": "import"|"set"|"remove", "path": dotted, "old": json, "new": json}`. Events per leaf (scalar incl. text-include string; list items `list.0`, `list.1`...). First command run against a theatre imports it: one import event per leaf, old null. `history` also imports a never-imported theatre; importing is not reconciling. A set emits one set event per leaf changed/created and one remove event (new null) per leaf no longer existing; a no-op set emits nothing.
- `set PATH VALUE` belongs to the downstream spec config-set-writeback.

> **Note (step 2):** the contract above is kept verbatim. Its "importing is not reconciling" is superseded by
> step 2 (spec config-file-sync, requirements 1.1–1.3, 1.7) for every command except `history`, which still
> imports a never-imported theatre and never reconciles.
>
> **Note (step 3):** the contract's "show PATH" section now describes `get PATH` (spec config-get-show,
> requirement 1). The new `show [PATH] [--sources]` prints any node as YAML, so `show` applied to a mapping or
> list no longer exits 5 (config-get-show requirement 2).
>
> **Note (step 4):** each line of the contract's "history [PATH]" section now also has the members `actor`,
> `via`, `at`, `reason` and `change` after `new` (spec config-lineage, requirement 1). Import events carry
> `via` `"import"` and actor unknown, and the events of one import or one `set` share one `change`
> (config-lineage requirements 3 and 4).
>
> **Note (step 5):** the contract's "Includes" section also has a mapping form: a mapping with a key `$ref`
> whose value is an include string is an include of that string, and its other keys are overrides deep-merged
> over the included YAML content (spec config-include-overrides, requirement 1). A non-YAML target with
> overrides, a YAML target whose content is not a mapping with overrides, and a `$ref` that is not an include
> string are further exit-4 cases (config-include-overrides requirement 2).
>
> **Note (step 6):** the contract's "Out of contract" list no longer holds anchors, aliases and merge keys.
> They load, and every command works on the expanded configuration (spec config-aliases, requirements 1–3);
> `set` writes at an anchor or refuses a write reached through an alias or a merge key (config-aliases
> requirements 4, 5 and 9).

## Introduction
This specification covers the read side of the troupe configuration subsystem (contract step 1): the
`troupe config` command group with `show PATH` and `history [PATH]`, locating a theatre's configuration file,
loading it together with its `file:` includes, addressing values by dotted path, recording an event history of
the configuration (starting with a one-time import), and the exit-code and error-message conventions shared by
every `troupe config` subcommand. It also makes the loaded configuration available, with exact source
positions, to the downstream `set` command (spec config-set-writeback).
*(Step 3 (spec config-get-show): the scalar command `show PATH` is now `get PATH`; the new `show` prints YAML
and is owned by config-get-show.)*

## Boundary Context
- **In scope**: `troupe config show` (*step 3: now `troupe config get`; the new `show` is owned by spec
  config-get-show*) and `troupe config history`; `--theatre` handling; locating
  `troupe.yml`/`troupe.yaml`; loading with recursive `file:` includes; dotted path addressing; scalar printing;
  the configuration history (import, storage, listing, filtering); recording of change events on behalf of the
  `set` command; exit codes 2, 4 and 5 and their messages; the shared definition of exit code 3.
- **Out of scope**: `troupe config set`, VALUE parsing, every rule about how values are written back into files,
  deciding which leaves a `set` changed or removed (all owned by config-set-writeback); support for anchors,
  aliases and merge keys (they are refused, 3.9; *until step 6: they now load, see spec config-aliases,
  requirements 1 and 2*); multi-document files; TAB characters before end-of-line comments; keys containing `.`;
  digit-only mapping keys; concurrent invocations; Windows.
- **Adjacent expectations**: config-set-writeback relies on this feature to (a) load the configuration with the
  exact file and position of every value, (b) resolve paths with the same rules as `show`, (c) append `set` and
  `remove` events to the same history that `history` prints, and (d) report failures with the same exit codes
  and message conventions. Hand edits made after the import are not added to the history by this feature
  ("importing is not reconciling"). *(Superseded by step 2 (spec config-file-sync, requirements 1.1–1.3,
  1.7): every command except `history` reconciles hand edits into the history before its own work.)*
- **Choices made where the contract is silent** (conservative; rationale in `research.md`): a non-mapping
  top-level node and an unreadable history store are load failures (3.4, 7.8); an empty root file is an empty
  configuration (3.5); import order is document order and an empty configuration still counts as imported
  (7.1, 7.5).
- **Product owner decisions (2026-10-01)** (records in `research.md`, "Product Owner Decisions"):
  - A, confirmed by the product owner: the files are the truth. `show` (and the downstream `set`) always read the
    files as they are on disk; the history is only change history, so a hand edit shows in `show` but produces no
    history event (6.9, 7.3). *(The "produces no history event" part is superseded by step 2 (spec
    config-file-sync, requirements 1.1–1.3, 1.7) for every command except `history`; "files are the truth"
    stands.)*
  - E, confirmed with a condition: state files only under `<theatre>/.troupe/`, the history store is closed at the
    end of every command, and no file or directory is created anywhere else in the theatre (7.6, 7.9).
  - F, PO delegated (safest assumption kept): non-finite floats appear in history as the JSON strings `".inf"`,
    `"-.inf"`, `".nan"` (8.7).
  - G, PO delegated (safest assumption kept): malformed PATH syntax (empty PATH, empty segment, leading or
    trailing dot) is a usage error, exit 2 (1.5).
  - H, PO left to our choice: anchors, aliases and merge keys are load failures, exit 4 naming the file and the
    key path where known (3.9). *(Superseded by step 6 (spec config-aliases, requirements 1 and 2) for loading
    anchors, aliases and merge keys; the load refusals that remain are listed in the step 6 note on 3.9, and
    writes are governed by config-aliases.)*
  - I, PO delegated (safest assumption kept): duplicate keys, explicit tags and files that are not valid UTF-8 are
    load failures, exit 4 naming the file and the key path where known (3.2, 3.3, 3.7).

## Requirements

### Requirement 1: Command invocation and theatre selection
**Objective:** As a developer, I want to run `troupe config` subcommands from the repository root against any theatre directory, so that I can inspect a theatre's configuration without changing directory.

#### Acceptance Criteria
1. When a developer runs `uv run troupe config show PATH` or `uv run troupe config history [PATH]` from the repository root, the troupe CLI shall execute the requested subcommand.
   *Note (step 3): the read subcommands are now `get`, `show` and `history` (spec config-get-show, requirements 1 and 2), alongside `set` (config-set-writeback) and `watch` (config-file-sync).*
2. Where `--theatre DIR` is given, the troupe CLI shall use DIR as the theatre directory.
3. When `--theatre` is not given, the troupe CLI shall use the current working directory as the theatre directory.
4. If the command line is malformed (unknown subcommand, missing required argument, unknown option or surplus argument), then the troupe CLI shall exit with code 2, write a message describing the usage problem to stderr, and write nothing to stdout.
5. If a PATH argument is empty or contains an empty segment (for example `a..b`, `.a` or `a.`), then the troupe CLI shall exit with code 2 and write a stderr message that names the offending PATH.

### Requirement 2: Locating the configuration file
**Objective:** As a developer, I want troupe to find my theatre's configuration file unambiguously, so that it never reads the wrong file.

#### Acceptance Criteria
1. When exactly one of `<theatre>/.troupe/troupe.yml` and `<theatre>/.troupe/troupe.yaml` exists, the troupe CLI shall use that file as the root configuration file.
2. If neither `<theatre>/.troupe/troupe.yml` nor `<theatre>/.troupe/troupe.yaml` exists, then the troupe CLI shall exit with code 4 and write a stderr message that names the expected configuration file location.
3. If both `<theatre>/.troupe/troupe.yml` and `<theatre>/.troupe/troupe.yaml` exist, then the troupe CLI shall exit with code 4 and write a stderr message that names both files.
4. If the theatre directory does not exist or is not a directory, then the troupe CLI shall exit with code 4 and write a stderr message that names the expected configuration file location.

### Requirement 3: Loading YAML content
**Objective:** As a developer, I want my configuration read with standard YAML 1.2 meaning, so that values have the types I wrote.

#### Acceptance Criteria
1. The troupe CLI shall interpret every configuration file using the YAML 1.2 core schema (for example `yes` and `on` are strings, `true`/`false` are booleans, `null` and `~` are null, `0o17` is the integer 15).
2. If a configuration file cannot be read, is not valid UTF-8 or is not valid YAML, then the troupe CLI shall exit with code 4 and write a stderr message that names that file.
3. If a configuration file contains the same key twice in one mapping, then the troupe CLI shall exit with code 4 and write a stderr message that names the file and the duplicated key path.
4. If a configuration file's top-level node is neither a mapping nor empty, then the troupe CLI shall exit with code 4 and write a stderr message that names that file.
5. When the root configuration file is empty or contains only comments, the troupe CLI shall treat the configuration as an empty mapping with no values.
6. The troupe CLI shall read the configuration without creating, modifying, renaming or deleting the configuration file or any included file.
7. If a configuration file gives a value an explicit tag (for example `!!str 5` or `!custom x`), then the troupe CLI shall exit with code 4 and write a stderr message that names the file and the key path of the tagged value.
8. When a configuration value is written as a folded block scalar (`>`, with any chomping or indentation indicator), the troupe CLI shall use the value defined by YAML 1.2 line folding (for example `>\n  a\n  b\n` is `a b` followed by a newline) everywhere the value appears, including `show` output, history events and leaves, and shall not add any character that does not belong to the folded value.
9. If a configuration file uses an anchor (`&name`), an alias (`*name`) or a merge key (`<<`), then the troupe CLI shall exit with code 4 and write a stderr message that names the file and, where it is known, the key path at which the anchor, alias or merge key appears.
   *Note (step 6): anchors and aliases now load, and so do merge keys (spec config-aliases, requirements 1 and 2). Exit 4 under config-aliases remains for an anchor on a mapping key, an alias used as a mapping key, an alias inside its own anchored node, an alias with no anchor of that name before it in the same file (1.3, 1.4), a merge key whose value is not a mapping, an alias of a mapping or a list of those, a merge key among the overrides of a mapping-form include, and an include as a merge source (2.3).*

### Requirement 4: File includes
**Objective:** As a developer, I want to split my configuration across files with `file:` includes, so that large or shared settings can live in their own files.

*Note (step 5): includes also have a mapping form, `{$ref: "file:...", <overrides>}`, specified by spec config-include-overrides (requirements 1 and 2). The criteria below stand as worded for the include string, whether written as a scalar or as the value of `$ref`.*

#### Acceptance Criteria
1. When a string value starts with `file:`, the troupe CLI shall treat the rest of the string as the path of an include target, relative to the directory of the file that contains the string.
2. When an include target's name ends in `.yml` or `.yaml`, the troupe CLI shall use the target's YAML content in place of the include string, as if it had been written there.
   *Note (step 5): in a mapping-form include with overrides, the value at that place is the target's content deep-merged with the overrides (mappings merged recursively, every other override value replacing the included one, included key order kept and override-only keys appended), and no `$ref` key is part of the value; a bare `$ref` mapping has the same value as the scalar form (spec config-include-overrides, requirements 1.2–1.5).*
3. When an include target's name ends in anything other than `.yml` or `.yaml`, the troupe CLI shall use the target file's exact text, byte for byte including its line endings, as a string value in place of the include string.
4. When an included YAML file itself contains include strings, the troupe CLI shall resolve them in the same way, relative to the directory of the included file, to any depth.
5. If an include target does not exist or cannot be read, then the troupe CLI shall exit with code 4 and write a stderr message that names the missing target and the file containing the include.
6. If following YAML includes leads back to a file that is already being included on the same chain, then the troupe CLI shall exit with code 4 and write a stderr message that names the files forming the cycle.
7. If an included YAML file cannot be loaded for any reason listed in Requirement 3, then the troupe CLI shall exit with code 4 and write a stderr message that names the included file.
   *Note (step 5): 4.5–4.7 apply to the `$ref` include string as to any include string (spec config-include-overrides, requirement 1.8). Exit 4 is also given for overrides on a non-YAML target, for overrides on a YAML target whose content is not a mapping, and for a `$ref` value that is not an include string, with no file or history state changed (config-include-overrides requirements 2.1–2.4).*

### Requirement 5: Path addressing
**Objective:** As a developer, I want to address any value with a dotted path, so that I can name settings consistently across commands.

#### Acceptance Criteria
1. The troupe CLI shall interpret PATH as a sequence of segments separated by `.`, applied from the top-level mapping downward.
2. When a segment is applied to a mapping, the troupe CLI shall select the entry whose key equals the segment.
3. When a segment consisting only of digits is applied to a list, the troupe CLI shall select the list item at that 0-based index.
4. When a path passes through an included YAML file, the troupe CLI shall address the included content exactly as if it were written in place.
5. If a segment names no existing mapping key, a list index is out of range, a non-digit segment is applied to a list, or any segment is applied to a scalar value (including a text-include string), then the troupe CLI shall treat the path as not found.

### Requirement 6: Show a value
**Objective:** As a developer, I want `troupe config show PATH` to print one setting, so that I can read it from scripts and the shell.

*Note (step 3): this requirement now describes `troupe config get PATH` (spec config-get-show, requirement 1). Criteria 1–6, 8 and 9 apply to `get` unchanged; criterion 7 gains the `config show` hint. The new `troupe config show [PATH] [--sources]`, which prints any node as YAML, is owned by spec config-get-show.*

#### Acceptance Criteria
1. When PATH resolves to a string value, the troupe CLI shall print the string exactly, followed by a newline unless the string already ends with a newline, and exit with code 0.
2. When PATH resolves to a boolean value, the troupe CLI shall print `true` or `false` followed by a newline.
3. When PATH resolves to a null value, the troupe CLI shall print `null` followed by a newline.
4. When PATH resolves to an integer value, the troupe CLI shall print it in decimal followed by a newline.
5. When PATH resolves to a floating-point value, the troupe CLI shall print it in Python `repr()` form followed by a newline.
6. When PATH resolves to a text include, the troupe CLI shall print the included file's exact text as a string under criterion 1.
7. If PATH resolves to a mapping or a list, then the troupe CLI shall exit with code 5 and write a stderr message that names PATH and the file that holds it.
   *Note (step 3): this now applies to `get`, and the stderr message also mentions `config show` (spec config-get-show, requirement 1.2). `show` applied to a mapping or list prints YAML and exits 0 (config-get-show requirement 2).*
8. If PATH is not found, then the troupe CLI shall exit with code 5 and write a stderr message that names PATH and the file holding the deepest part of PATH that exists.
9. When a configuration file has been edited by hand since the history was imported, the troupe CLI shall show the value currently in the files as read from disk by this command, not the value recorded in the history, and shall record no history event for the hand edit.
   *Note (step 2): the "record no history event for the hand edit" part is superseded by spec config-file-sync (requirements 1.1–1.3, 1.7) for every command except `history`: `show` and `set` reconcile the hand edit into the history first; `show` still prints the value in the files as read from disk.*
   *Note (step 3): "`show`" in this criterion and its step 2 note now reads "`get` or `show`" (spec config-get-show, requirement 6.2).*

### Requirement 7: Configuration history import
**Objective:** As a developer, I want the first use of troupe on a theatre to record the configuration as it was found, so that later history has a starting point.

#### Acceptance Criteria
1. When any `troupe config` command first runs against a theatre whose history has never been imported and the configuration loads successfully, the troupe CLI shall record one `import` event per leaf of the configuration, with `old` null and `new` the leaf's value, in document order.
   *Note (step 4): every import event also carries import lineage: `via` `"import"`, actor `{"kind": "unknown", "id": null}`, `reason` null, and one `change` and one `at` shared by all events of the import, whichever command performed it and whatever `TROUPE_ACTOR` holds (spec config-lineage, requirements 3.1, 4.1 and 5.2).*
2. The troupe CLI shall treat as leaves every scalar value (including a text-include string) at its full dotted path, with list items addressed by index (`list.0`, `list.1`, …); empty mappings and empty lists contribute no leaves.
3. While a theatre's history has already been imported, the troupe CLI shall not record import events again, and shall not record events for differences between the files and the history (importing is not reconciling).
   *Note (step 2): the "shall not record events for differences between the files and the history" part is superseded by spec config-file-sync (requirements 1.1–1.3, 1.7) for every command except `history`; import events are still never recorded again, and `history` never reconciles.*
4. If the configuration cannot be loaded, then the troupe CLI shall not record any events and shall not create any history state.
5. When a theatre with no leaves is imported, the troupe CLI shall mark the theatre as imported with no events, so that later commands do not import it again.
6. The troupe CLI shall store all history state only under `<theatre>/.troupe/` and shall not create, modify, rename or delete any file or directory outside `<theatre>/.troupe/` (including in directories that hold included files), whether the command succeeds or fails.
7. If the import is interrupted, then the troupe CLI shall leave either the complete import or no import recorded, never a partial one.
8. If the stored history cannot be read, then the troupe CLI shall exit with code 4 and write a stderr message that names the history store location.
9. When a `troupe config` command that opened the history store finishes, whether it succeeds or fails, the troupe CLI shall close the history store before the command exits.

### Requirement 8: History listing
**Objective:** As a developer, I want `troupe config history [PATH]` to list configuration changes as JSON Lines, so that I can review and process them with standard tools.

#### Acceptance Criteria
1. When `troupe config history` runs, the troupe CLI shall print every recorded event, oldest first, one JSON object per line, and exit with code 0.
2. The troupe CLI shall include in each line at least the members `seq` (an integer, 1 for the first event and increasing by 1 per event), `op` (`import`, `set` or `remove`), `path` (dotted), `old` and `new` (the JSON form of the values).
   *Note (step 4): each line also has the members `actor`, `via`, `at`, `reason` and `change`, printed after `new` in that order; `seq`, `op`, `path`, `old` and `new` keep their order and values (spec config-lineage, requirement 1). Events recorded before step 4 are printed with the legacy lineage of config-lineage requirement 7.3.*
3. When PATH is given, the troupe CLI shall print only the events whose path equals PATH or starts with PATH followed by `.`, keeping their original `seq` numbers.
4. When PATH matches no events, the troupe CLI shall print nothing and exit with code 0.
5. When `troupe config history` runs against a theatre whose history has never been imported, the troupe CLI shall import it first (Requirement 7) and then print the import events.
6. The troupe CLI shall print each string, boolean, null, integer and finite floating-point value in `old` and `new` as the corresponding JSON value, with booleans as JSON `true`/`false` (never `1`/`0`).
7. When an `old` or `new` value is an infinite or not-a-number floating-point value, the troupe CLI shall print it as the JSON string of its YAML 1.2 canonical form (`".inf"`, `"-.inf"` or `".nan"`), so that every history line is valid JSON.

### Requirement 9: Recording changes for the set command
**Objective:** As the downstream `set` command (config-set-writeback), I want to append change events to the same history, so that `history` shows every change made through troupe.

#### Acceptance Criteria
1. When the set command reports a leaf as changed or created, the troupe CLI shall record one `set` event with the leaf's path, its previous value (`null` if newly created) as `old`, and its new value as `new`.
2. When the set command reports a leaf as no longer existing, the troupe CLI shall record one `remove` event with the leaf's path, its previous value as `old`, and `null` as `new`.
3. When the set command reports a group of changes from one invocation, the troupe CLI shall record all of them or none of them, with consecutive `seq` numbers in the order reported.
   *Note (step 4): such a group is one change: all its events share one `change` and one `at`, and carry the lineage of the caller (`via` `"cli"` with the command's actor for `set`, `via` `"file"` with actor unknown for a reconcile); an empty group records no change (spec config-lineage, requirements 3.2, 3.3, 4.1, 4.4 and 5.2).*
4. When the set command reports no changes, the troupe CLI shall record no events.

### Requirement 10: Error reporting and exit codes
**Objective:** As a developer, I want consistent exit codes and messages that name the file or path at fault, so that I can script troupe and fix problems quickly.

#### Acceptance Criteria
1. The troupe CLI shall exit with code 0 on success, 2 on a usage error, 3 when a write is refused, 4 when the configuration cannot be loaded, and 5 when a path is not found or names the wrong kind of node for the command.
2. If any `troupe config` command exits with a non-zero code, then the troupe CLI shall write nothing to stdout.
3. If any `troupe config` command exits with a non-zero code, then the troupe CLI shall write to stderr a message that names the offending file, the offending PATH, or both, in the form `<file>: <path>: <problem>` where both apply.
4. When an error concerns a value that comes from an included file, the troupe CLI shall name the included file in which that value is written.
5. The troupe CLI shall use the same exit code and message conventions for every `troupe config` subcommand, including subcommands provided by other specifications.
