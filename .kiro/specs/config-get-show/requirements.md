# Requirements Document

## Project Description (Input)
### Problem
Developers can only read one scalar at a time: `troupe config show PATH` refuses mappings and lists (exit 5).
They cannot see a whole section, or the whole configuration, with its includes resolved. When a configuration
is split across files with `file:` includes, they also cannot tell which file a value comes from without
following the includes by hand.

### Current State
- config-foundation: `show PATH` (Requirement 6) prints one scalar through `show_value` (in
  `config/theatre.py`) and `format_show_output` (in `cli/render.py`). A mapping or list gives
  `WrongNodeKindError` (exit 5) naming PATH and the file. `resolve_path` returns any `ConfigNode`.
- The loaded `ConfigDocument` is already a fully expanded, source-mapped tree. Every `ConfigScalar` /
  `ConfigMapping` / `ConfigSequence` has an `origin` (`SourceFile` + `Span`). A text include is a
  `ConfigScalar` whose `origin.file` is the included file, with `include.kind == IncludeKind.TEXT`. Nodes inside a
  YAML include have their origin in the included file. So the provenance needed for `--sources` already exists.
- config-set-writeback has scalar rendering we can reuse: `writeback/scalar_render.py` (quote-style choice
  plain → single → double, with a parse probe so strings like `0.2` or `true` are never written plain, and key
  rendering) and `writeback/block_scalar.py` (`literal_fits`, `render_literal`, chomping). These live under
  `writeback/` and some raise `WriteRefusedError`.
- config-file-sync: every command except `history` reconciles first (`open_theatre(..., reconcile=True)`).
- CLI: subcommands are registered through `SUBCOMMAND_REGISTRARS` in `cli/main.py`. Handlers buffer their output
  and flush it only on success.
- Gap: no `get` subcommand, no YAML output of a subtree, and no source annotation.

### Desired Outcome
The step 3 contract section "`get` and `show`" holds. It replaces "show PATH (step 1 and 2)":
- `troupe config get PATH` prints a scalar exactly as step 1's `show` did (config-foundation 6.1–6.6, 6.8). A
  mapping or list gives exit 5, with a stderr message that names PATH and the file (as before) and mentions
  `config show`.
- `troupe config show [PATH]`: a scalar prints like `get`. A mapping or list is printed as YAML in block style,
  with includes resolved and text includes as literal block scalars. With PATH omitted, the whole configuration
  is printed.
- `troupe config show [PATH] --sources` prints the same YAML with an end-of-line comment `# from <file>` on
  every line that holds a scalar. `<file>` is the file the scalar comes from, relative to the theatre directory,
  with `/` separators. A text include's value comes from the included file, not from the file holding the
  `file:` directive.
- Both commands reconcile first (config-file-sync) and follow the step 1 error and exit-code conventions:
  missing PATH → 5, malformed PATH → 2, and on a non-zero exit stdout is empty.

### Contract: `get` and `show` (step 3, verbatim; replaces "show PATH (step 1 and 2)")
We are changing `show`. From now on:
- `troupe config get PATH` prints a single value — what `show` used to do.
- `troupe config show [PATH]` prints either a value or a whole section as YAML, and with `--sources` says
  which file each value comes from.

- `get PATH` prints a scalar as `show` did in step 1. A mapping or list → exit 5, and stderr mentions
  `config show`.
- `show [PATH]` prints a scalar like `get`; a mapping or list is printed as YAML (block style, includes
  resolved, text includes as literal blocks). `PATH` omitted → the whole configuration.
- `show [PATH] --sources` prints the same YAML with an end-of-line comment `# from <file>` on every line that
  holds a scalar, where `<file>` is the file the scalar comes from, relative to the theatre directory, with
  `/` separators. A text include's value comes from the included file.

Approach, scope, boundaries and open choices: see `brief.md`.

## Introduction
This specification covers contract step 3, "`get` and `show`". It replaces the step 1 and step 2 section "show
PATH". Reading the configuration splits into two commands:
- `troupe config get PATH` prints a single value, exactly as `show PATH` did in steps 1 and 2. A mapping or list
  is refused (exit 5), and the message points to `config show`.
- `troupe config show [PATH]` prints anything. A value prints as `get` prints it. A mapping or list, or the whole
  configuration when PATH is omitted, prints as block-style YAML with every include resolved and text includes
  shown as literal blocks. With `--sources`, every line that holds a value also says which file the value comes
  from.

Developers use `show` to read a whole section of a configuration split across many files, and `--sources` to find
which file to edit.

Terms used below:
- **The files**: the root configuration file (`troupe.yml` or `troupe.yaml`) together with every file it
  includes, directly or indirectly, through YAML and text includes, as loaded under the step 1 rules.
- **Scalar**: a string, boolean, null, integer or floating-point value. A text include is a string scalar whose
  value is the included file's exact text.
- **YAML output**: what `show` prints for a mapping or a list (Requirements 3 and 4), and what `show --sources`
  prints for a scalar (2.6).
- **Reads back**: parsing the YAML output as a YAML 1.2 document with the core schema, without troupe's include
  rules.
- **Source file of a scalar**: the file in which the scalar's value is written. For a text include it is the
  included text file, not the file that holds the `file:` string.

## Boundary Context
- **In scope**: the `get` subcommand; the `show` subcommand with an optional PATH and the `--sources` option;
  the YAML form of mappings, lists, the whole configuration and scalars; the source annotations; reconcile-first,
  error and exit-code behaviour of both commands.
- **Out of scope**: loading, includes, path rules, exit-code and message conventions (config-foundation, reused
  unchanged); reconcile (config-file-sync, reused unchanged); `set`, `history` and `watch` behaviour; keeping the
  user's formatting in `show` output (comments, quoting, flow style, blank lines and number spellings are not
  carried over, because the contract asks for block style with includes resolved); other output formats such as
  JSON; history in `show` output; anchors, aliases and merge keys (still refused, step 6); Windows.
- **Adjacent expectations**:
  - config-foundation: the configuration and its includes are loaded, PATH is parsed and resolved, and errors are
    reported exactly as in step 1. Each scalar's source file is the file the step 1 loader read it from.
  - config-file-sync: both `get` and `show` reconcile first. `history` alone does not.
- **Changes to existing specs** (step 3 replaces "show PATH (step 1 and 2)"):
  - config-foundation Requirement 6 ("Show a value") now describes `get PATH`. Criteria 6.1–6.6, 6.8 and 6.9
    apply to `get` unchanged. 6.7 gains "mentions `config show`" (1.2 here). `show` applied to a mapping or list
    no longer exits 5; it prints YAML (Requirement 2 here). 1.1 names `get`, `show` and `history`.
  - config-file-sync 1.1, 1.2 and 1.6: "`show`" now reads "`get` or `show`".
  - These notes are added to the existing specs in the same change as the behaviour (collaboration.md).
- **Choices made where the contract is silent** (conservative; to be recorded in `research.md`):
  1. `show PATH --sources` on a scalar prints the scalar in its YAML form with the source comment, not `get`'s raw
     form. This keeps the comment separate from the value, including for multi-line text (2.6).
  2. A literal block's source comment goes on its header line. Its content lines carry no comment, because text
     there would become part of the value (5.2).
  3. The YAML output must read back as exactly the same values (4.1). A string that a literal block cannot
     reproduce exactly, such as a text include with CRLF line endings, is printed double-quoted with escapes
     instead (4.5). The value is never altered to fit the requested style.
  4. Empty mappings and lists print as `{}` and `[]`, since block style has no empty form. An empty
     configuration prints `{}` (3.5).
  5. Layout: two spaces per nesting level; list items indented under their key; no document markers (3.2–3.4,
     3.7).
  6. The comment is one space, then `# from <file>`, with no column alignment (5.1).
  7. Source paths are taken lexically from the theatre directory, without resolving symbolic links. A file
     outside the theatre directory gets a leading `../` (5.4).
  8. `--sources` is accepted only by `show`. `get --sources` is a usage error (1.4).

## Requirements

### Requirement 1: Get a value
**Objective:** As a developer, I want `troupe config get PATH` to print one value exactly as `show PATH` used to, so that my scripts keep a simple single-value read.

#### Acceptance Criteria
1. When `troupe config get PATH` runs and PATH resolves to a scalar, the troupe CLI shall print the value exactly as config-foundation 6.1–6.6 specify (strings exactly, with a newline added unless the string already ends with one; `true`/`false`; `null`; integers in decimal; floats in Python `repr()` form; a text include as the included file's exact text) and exit with code 0.
2. If PATH given to `get` resolves to a mapping or a list, then the troupe CLI shall exit with code 5 and write a stderr message that names PATH and the file that holds it and mentions `config show` as the command that prints mappings and lists.
3. If PATH given to `get` is not found, then the troupe CLI shall exit with code 5 and write a stderr message that names PATH and the file holding the deepest part of PATH that exists.
4. If the `get` command line has no PATH, more than one PATH, or an unknown option (including `--sources`), then the troupe CLI shall exit with code 2 and write a stderr message describing the usage problem.
5. If the PATH given to `get` is empty or contains an empty segment, then the troupe CLI shall exit with code 2 and write a stderr message that names the offending PATH.

### Requirement 2: Show command line and scalar values
**Objective:** As a developer, I want `troupe config show [PATH]` to print whatever PATH names, so that one command serves values and whole sections alike.

#### Acceptance Criteria
1. When `troupe config show PATH` runs without `--sources` and PATH resolves to a scalar, the troupe CLI shall print exactly what `troupe config get PATH` prints for it and exit with code 0.
2. When `troupe config show PATH` runs and PATH resolves to a mapping or a list, the troupe CLI shall print that mapping or list as YAML output (Requirements 3 and 4) and exit with code 0.
3. When `troupe config show` runs without PATH, the troupe CLI shall print the whole configuration (the top-level mapping) as YAML output and exit with code 0.
4. If PATH given to `show` is not found, then the troupe CLI shall exit with code 5 and write a stderr message that names PATH and the file holding the deepest part of PATH that exists.
5. If the `show` command line has more than one PATH, an unknown option, or a PATH that is empty or contains an empty segment, then the troupe CLI shall exit with code 2 and write a stderr message describing the problem (naming the PATH when the PATH is malformed).
6. When `troupe config show PATH --sources` runs and PATH resolves to a scalar, the troupe CLI shall print the scalar in its YAML form (Requirement 4) as a document on its own, with the source comment of Requirement 5 on the line that holds the scalar (the header line for a literal block), and exit with code 0.
7. The troupe CLI shall accept `--sources` before or after PATH, and with PATH omitted.

### Requirement 3: YAML layout of mappings and lists
**Objective:** As a developer, I want a section printed as plain block-style YAML with every include resolved, so that I can read the effective configuration in one place.

#### Acceptance Criteria
1. The troupe CLI shall print every non-empty mapping and every non-empty list in the YAML output in block style, one entry or item per line start, and shall not use flow style (`{…}`, `[…]`) for any non-empty collection.
2. The troupe CLI shall print mapping entries in document order as `key: value`. When the value is a non-empty mapping or list, the entry line shall be `key:` and the nested content shall start on the following lines, indented two spaces more than the key.
3. The troupe CLI shall print list items in document order as `- item`, indented two spaces more than the key of the list that holds them (at column 0 when the printed node itself is a list). A mapping or list that is a list item shall start on the item's dash line, with its later entries or items aligned under its first.
4. When a value reached through a YAML include is printed, the troupe CLI shall print the included content in place of the include, as if it were written there, and shall not print the `file:` string. A text include shall be printed as its text (Requirement 4).
5. When a mapping or list in the YAML output is empty, the troupe CLI shall print it as `{}` or `[]` respectively. When the whole configuration has no entries, `show` shall print `{}`.
6. The troupe CLI shall not carry the source files' comments, quoting, flow style, blank lines or number spellings into the YAML output. Only the values and their order are kept.
7. The troupe CLI shall print no document start or end markers and no comments other than the source comments of Requirement 5. It shall end the output with a single newline and leave no trailing whitespace on any line, except inside literal block content where the value itself contains it.

### Requirement 4: Scalars in YAML output
**Objective:** As a developer, I want every value in the YAML output to mean exactly what it means in my configuration, so that I can trust what `show` prints and paste it back.

#### Acceptance Criteria
1. The troupe CLI shall print YAML output that reads back to exactly the printed node: the same keys (as text), in the same order, with the same value types, the same strings character for character, and the same numbers.
2. The troupe CLI shall print booleans as `true`/`false`, null as `null`, integers in decimal, finite floats in Python `repr()` form, and infinite and not-a-number floats as `.inf`, `-.inf` and `.nan`.
3. When a string contains no line break, the troupe CLI shall print it plain if the plain text reads back as that same string. Otherwise it shall print it single-quoted if single quotes can represent it, and otherwise double-quoted with escapes. For example, the strings `true`, `5`, `null`, an empty string and `a: b` are quoted.
4. When a string is a text include, or any other string that contains a line break, the troupe CLI shall print it as a literal block scalar (`|`). The block shall use the chomping indicator that reproduces the string's trailing line breaks (`|-` for none, `|` for exactly one, `|+` for more), an indentation indicator when the text's first line starts with a space, and content indented two spaces more than the line that holds the block's header.
5. If a string that criterion 4 would print as a literal block cannot be reproduced exactly by a literal block (for example, it contains a carriage return, as a text include with CRLF line endings does, or another character that a literal block cannot hold), then the troupe CLI shall print it double-quoted with escapes so that it still reads back exactly.
6. The troupe CLI shall print non-ASCII printable characters as UTF-8 text without escaping, and shall print non-printable characters only as escapes inside double-quoted strings.
7. The troupe CLI shall not wrap any scalar across lines, except for the lines of a literal block.
8. The troupe CLI shall print a mapping key plain if the plain text reads back as the same string key, and otherwise quoted under criterion 3.

### Requirement 5: Source annotations
**Objective:** As a developer, I want `show --sources` to tell me which file every value comes from, so that I know which file to edit.

#### Acceptance Criteria
1. Where `--sources` is given, the troupe CLI shall end every output line that holds a scalar with one space followed by `# from <file>`, where `<file>` is that scalar's source file. Such lines are a `key: value` line, a `- value` list item line, the line of a scalar printed on its own, and the header line of a literal block.
2. Where `--sources` is given, the troupe CLI shall add no comment to a line that holds no scalar (a `key:` line introducing a nested mapping or list, a line holding `{}` or `[]`) or to the content lines of a literal block.
3. Where `--sources` is given, the troupe CLI shall name as `<file>` the root configuration file for a scalar written there, the included YAML file for a scalar written in that file, and the included text file for a text include.
4. Where `--sources` is given, the troupe CLI shall write `<file>` relative to the theatre directory, with `/` separators, with no `.` segments and with `..` segments only at its start, taken from the paths as reached through the theatre directory and the include strings, without resolving symbolic links (for example `.troupe/troupe.yml`, `.troupe/agents/lead.yml`, `../shared/prompt.md`).
5. Where `--sources` is given and PATH resolves to a mapping or a list, or PATH is omitted, the troupe CLI shall print output that equals the output without `--sources` once the ` # from <file>` suffixes are removed.

### Requirement 6: Reading, reconciling and errors
**Objective:** As a developer, I want `get` and `show` to behave like every other troupe command, so that they are safe and predictable in scripts.

#### Acceptance Criteria
1. When `get` or `show` runs, the troupe CLI shall select the theatre and locate and load the configuration exactly as the other `troupe config` subcommands do.
2. When `get` or `show` runs against an imported theatre whose configuration loads, the troupe CLI shall reconcile (config-file-sync Requirement 2) before resolving PATH, and shall keep the events recorded by that reconcile when the command then exits with code 5.
3. When `get` or `show` runs against a theatre whose history has never been imported, the troupe CLI shall import it (config-foundation Requirement 7) and record no reconcile events in the same command.
4. The troupe CLI shall print values as they are in the files as read from disk by this command, not as recorded in the history.
5. The troupe CLI shall not create, modify, rename or delete any configuration file or included file when running `get` or `show`, and shall leave no file behind other than the history state under `<theatre>/.troupe/`.
6. If the configuration cannot be loaded, then the troupe CLI shall exit with code 4 as in step 1 and record no events.
7. If `get` or `show` exits with a non-zero code, then the troupe CLI shall write nothing to stdout and write a stderr message that follows the step 1 conventions (config-foundation Requirement 10).
8. When `get` or `show` finishes, whether it succeeds or fails, the troupe CLI shall close the history store before the command exits.
