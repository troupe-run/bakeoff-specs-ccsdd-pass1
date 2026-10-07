# Requirements Document

## Project Description (Input)
### Problem
Teams want one shared configuration for every theatre (step 7). They publish it at an `https://` URL and include it
from each theatre's `troupe.yml`. troupe must read that shared configuration, but it must never pretend it can
change it: a `set` that would have to change a value that comes from the URL is refused, and the message tells the
user which include line in their own file leads there.

Once `https://` strings are includes, URL-valued settings (webhooks, API endpoints) become ambiguous. A theatre can
describe its configuration with a JSON Schema at `.troupe/troupe.schema.json`. The schema marks URL-valued settings
with `"format": "uri"`, and any other value that must be taken literally can be tagged `!literal`. The schema also
lets troupe reject a configuration, or a `set`, that does not match the theatre's declared shape.

### Current State
- config-foundation (`config/loader.py`): only `file:` strings are includes (`INCLUDE_PREFIX`, `_Builder._build`,
  `_ref_value`). Targets are local paths relative to the including file (`_include`, `read_include`), and
  `SourceFile` assumes a local `Path`. Every explicit tag is refused with exit 4 (`_refuse_properties`, decision I,
  Requirement 3.7). `http://` and `https://` strings are plain strings today, and no test pins that.
- config-set-writeback: decision D says a `file:` VALUE is written as a plain string, becomes an include, and an
  unloadable target gives exit 3. Six places rebuild include strings as `"file:" + spec` (`compare.py`,
  `shared.py`, `planner.py`). Read-back verification reloads every file (`verify.py`).
- config-include-overrides: new keys under an include are written as overrides in the including file. An
  overridden value is changed in the override; a non-overridden value in the file it comes from. `--in-include`
  puts new keys in the included file.
- config-file-sync: `watch` polls `ConfigDocument.files` and the root config candidates. It cannot poll a URL.
- config-get-show: `--sources` labels each scalar with its file relative to the theatre (`source_label`).
- There is no JSON Schema support and no `jsonschema` dependency.

### Desired Outcome
The step 7 contract section "Remote includes and schema" holds (verbatim below). Every reader (`get`, `show`,
`show --sources`, `history`, reconcile, `watch`) sees remote content and never sees literal strings expanded. All
earlier `set` rules still hold: byte-surgical rendering, events and lineage, atomic writes, read-back verification,
overrides, `--in-include`, `--reason` and `--expand-alias`.

### Approach (from discovery)
The loader decides what an include is (one recognition function; a schema walker over `properties` / `items` /
`additionalProperties` and the `!literal` tag decide literals while walking), fetches remote sources with the
standard library (any failure → exit 4) into in-memory `SourceFile`s with a URL identity, and validates the loaded
configuration with a JSON Schema 2020-12 validator. The writer treats remote sources as read-only, checks the
intended configuration against the schema before writing, and renders include-like VALUE strings tagged `!literal`
unless `--include` is given. See `brief.md` for the rejected alternatives, scope, boundary candidates and the 14
open choices proposed at discovery.

### Step 7 contract, section "Remote includes and schema" (authoritative)
Includes can now point at https:// URLs (shared team configuration). They are read-only from troupe's point of view:
refuse changes to their values, and tell the user which include line to change instead.

**Remote includes and schema (from step 7)**
- A string starting `https://` or `http://` is also an include; it is fetched when the configuration loads
  (YAML if the URL path ends `.yml`/`.yaml`, otherwise text). A failed fetch → exit 4.
- Changing a value that comes from a remote include → exit 3; stderr contains the include string exactly as
  written in the local file. (New keys under a remote include become local overrides, per step 5.)
- `<dir>/.troupe/troupe.schema.json` (JSON Schema 2020-12), if present, describes the configuration. A string
  whose schema — found through `properties`, `items` and `additionalProperties` — has `"format": "uri"` is a
  literal, not an include. A string tagged `!literal` is a literal.
- The loaded configuration must validate against the schema, else exit 4. A `set` that would make it invalid
  → exit 3.
- `set PATH VALUE` where `VALUE` is a string that would be read as an include and the schema does not mark
  `PATH` as `format: uri` writes it tagged `!literal`, unless `--include` is given.

## Introduction
This specification covers step 7, the contract section "Remote includes and schema". Until now only `file:` strings
were includes, every explicit tag was refused (config-foundation 3.7, decision I), and nothing described the shape
of a configuration. Step 7 adds:
- **Remote includes**: `https://` and `http://` strings are includes, fetched each time the configuration loads.
  Every command reads their content. A failed fetch is a load failure (exit 4).
- **Read-only remote content**: a `set` that would change anything written in remote content is refused (exit 3),
  and stderr quotes the include line of the user's own file. New keys below a remote include become local overrides
  (step 5).
- **Literals**: a string tagged `!literal`, or whose schema has `"format": "uri"`, is a plain string, never an include.
- **A schema**: `.troupe/troupe.schema.json` (JSON Schema 2020-12), when present, must accept the loaded configuration
  (else exit 4) and the configuration a `set` would produce (else exit 3).
- **Writing include-like VALUEs**: a VALUE string that would be read as an include is written tagged `!literal`,
  unless `set --include` is given or the schema marks its path `format: uri`.

Terms used below:
- **Include string**: a string scalar value (in any quoting style, never a mapping key) that starts with `file:`,
  `https://` or `http://` (case-sensitive). A **remote include** is one starting with `https://` or `http://`; its
  **URL** is the whole string.
- **Remote content**: the content fetched for a remote include, and everything written in it (at any depth, including
  through further remote includes in it).
- **Literal**: an include string that is read as a string, not as an include (Requirement 4).
- **Include-like string**: a string that would be read as an include if written plain at its path: an include string
  for which the schema does not give that path `format: uri`.
- **The schema**: the file `troupe.schema.json` in the theatre's `.troupe` directory, next to the root configuration
  file.
- **A string's own path**: the dotted path of that string in the configuration; for a string nested in VALUE, PATH
  followed by the keys and list indices leading to it.
- **Expanded configuration**: the configuration as every reader sees it: includes (local and remote) replaced by their
  content, overrides merged (config-include-overrides), aliases and merge keys expanded (config-aliases), literals as
  strings and a text include as its text.

## Boundary Context
- **In scope**:
  - Recognising, fetching and reading remote includes in the scalar form and as `$ref` of the mapping form, in the
    root file, in local included files and in remote YAML content. The exit-4 cases of a failed fetch.
  - What `get`, `show`, `show --sources`, `history`, the reconcile and `watch` do with remote content.
  - `!literal` on load, and literals from the schema's `format: uri`.
  - Reading the schema, validating the loaded configuration, and the exit-4 cases for a schema that cannot be used.
  - The exit-3 refusals of a `set` that would change remote content or make the configuration invalid.
  - Writing include-like VALUE strings tagged `!literal`, and the `set --include` option.
  - Updating the step 1–6 tests that pin "explicit tags → exit 4" for `!literal`, and decision D.
- **Out of scope**: authentication, request headers, proxies beyond the platform default, caching or offline use of
  remote content, schemas fetched from URLs or remote `$ref`s inside the schema, any tag other than `!literal`,
  asserting `format` during validation, `file:` includes inside remote content, Windows, concurrent writers.
- **Adjacent expectations**:
  - config-foundation keeps owning loading, path addressing and the error format. Requirement 4 gains remote includes,
    and Requirement 3.7 and decision I gain the `!literal` exception (step-7 notes).
  - config-set-writeback keeps every "how it is written" rule. Decision D and 2.7–2.8 change: an include-like VALUE
    string is written tagged `!literal` unless `--include` is given (step-7 note).
  - config-include-overrides keeps owning override placement and `--in-include`. They apply unchanged below remote
    includes; only writes into remote content are refused.
  - config-aliases keeps owning anchors, aliases and `--expand-alias`; an expansion inside remote content is refused.
  - config-get-show keeps its output formats; only the `--sources` label of a remote scalar is new (step-7 note).
  - config-file-sync keeps the reconcile and `watch` mechanics; the watch set gains the schema file (step-7 note).
- **Choices made where the contract is silent** (conservative; decision records in `research.md`). Choices 1–14
  are the brief's proposals; 15–22 were found while writing these requirements.
  1. *`file:` includes inside remote YAML content*: exit 4 (1.4). There is no local directory to resolve them against.
     Remote includes inside remote content are allowed (1.3).
  2. *`!literal`*: on a scalar, the value is the scalar's text as a string, in any style and without typing (4.1). On
     a mapping or a list, exit 4. Every other tag still exits 4 (4.2).
  3. *Schema lookup for `format: uri`*: only `properties`, `items` and `additionalProperties` are followed; `true`,
     an absent keyword or any other form gives no format (4.4, 4.5). Validation still uses the whole schema.
  4. *Validation input*: the expanded configuration (5.1).
  5. *Remote values that are overridden* are changed in the override (6.3). Only changes to what is physically
     written in remote content are refused, including a collection replacement that would change or remove remote
     content (6.1). Replacing a remote include root follows the existing include-root rule (6.5).
  6. *`--in-include` below a remote include*: exit 3 with the include string (6.1).
  7. *The include string reported*: the include directive written in a local file on the way to the remote content
     (the outermost one, 6.2), exactly as written in that file, with its line number (6.1).
  8. *Nested VALUE strings*: each include-like string in a flow mapping or list VALUE is tagged too (8.1).
  9. *`set --include` with a remote VALUE*: written plain; reading back fetches it, and a failed fetch exits 3 with no
     file changed (9.4), as decision D does for unloadable local targets.
  10. *Changing an existing `!literal` value*: VALUE replaces the whole scalar, tag included, and is tagged again only
      by 8.1 (8.4).
  11. *`watch`*: watches local files and the schema file, never polls a URL, and fetches remote content again on every
      reload. A failed fetch during `watch` is a load error: nothing is recorded and watching continues (3.4, 3.5).
  12. *`--sources`*: a scalar from remote content is labelled with the URL of the remote include holding it (3.2).
  13. *Fetching*: at most 10 seconds of waiting for the server, redirects followed, YAML or text decided by the URL
      path's suffix with the query and fragment ignored, case-sensitive like the `file:` suffix check (1.2, 1.6).
  14. *Schema problems*: invalid JSON, a schema that is not valid 2020-12, or a `$ref` that cannot be resolved exits 4
      naming the schema file (5.3). **Differs from the brief**, which proposed that `history` checks the schema only
      when it imports: every command, `history` included, loads the configuration today (config-foundation,
      config-file-sync), so `history` also fetches remote includes and checks the schema on every run (3.6).
  15. *`$schema`*: a schema whose `$schema` names a dialect other than 2020-12 exits 4 (5.3). Reading a draft-07
      schema with 2020-12 rules could accept configurations its author meant to refuse.
  16. *A `$ref` mapping in VALUE without `--include`*: exit 3 mentioning `--include` (8.6). Its `$ref` string is an
      include by construction; tagging it would write a mapping that no longer loads.
  17. *Tags in VALUE*: still a usage error (exit 2), `!literal` included (8.7). troupe decides the tag itself.
  18. *Tagged strings stay on one line*: plain or quoted by the usual style rules, double-quoted when they contain a
      line break, never a block scalar (8.3).
  19. *One fetch per command*: the read-back of a `set` and alias-expansion reloads reuse the content fetched when the
      command loaded the configuration; only an include the set newly writes is fetched (10.3).
  20. *A string written as a text include's content* is text, not YAML, and is never tagged (8.8).
  21. *`!literal` before an anchor* (`!literal &a x`): exit 4 (4.2). `&a !literal x` loads. A replacement could not
      remove the tag and keep the anchor written after it.
  22. *One schema failure reported*: when several parts of the configuration fail, stderr reports one, the
      validator's most relevant failure (5.2, 7.1).
- **Fast-track approval (2026-10-07)**: the product owner was not available. The brief's open-choice defaults are
  taken as the decisions (choice 14 differs, for the reason given), the requirements are approved under `-y`, and
  every choice is recorded in `research.md` with what would make us revisit it.

## Requirements

### Requirement 1: Remote includes
**Objective:** As a developer whose team shares one configuration at a URL, I want troupe to include it from my theatre, so that every theatre uses the shared settings.

#### Acceptance Criteria
1. When a YAML file of the configuration holds a string scalar value that starts with `https://` or `http://` and is not a literal (Requirement 4), the troupe CLI shall fetch that URL when the configuration loads and use the fetched content in place of the string. This holds in the scalar form and as the `$ref` value of a mapping-form include, with overrides merged as for a local include (config-include-overrides Requirement 1).
2. When the path of the URL, without its query and fragment, ends with `.yml` or `.yaml` (case-sensitive), the troupe CLI shall read the content as YAML under the rules for a local YAML include, including nested includes, mapping forms, anchors and an empty content read as null. Otherwise it shall use the content's exact text as a string.
3. When remote YAML content holds a remote include, the troupe CLI shall fetch and include it too, at any depth.
4. If remote YAML content holds a `file:` include, then the troupe CLI shall exit with code 4, write nothing to stdout, and write a stderr message that names the URL of that content and the key path and says that local includes are not allowed in remote content.
5. If a remote YAML include leads back to a URL already on its include chain, then the troupe CLI shall exit with code 4 and write a stderr message that lists the cycle, as for local includes (config-foundation 4.6).
6. The troupe CLI shall fetch each URL at most once per load, follow redirects, wait at most 10 seconds for the server, and create, change or delete no file for a remote include.

### Requirement 2: Failed fetches
**Objective:** As a developer, I want a remote include that cannot be read to stop the command and say why, so that troupe never works with half a configuration.

#### Acceptance Criteria
1. If fetching a remote include fails, because the server cannot be reached, does not answer within 10 seconds, answers with a status other than 2xx after redirects, or sends a body that is not valid UTF-8, then the troupe CLI shall exit with code 4, write nothing to stdout, and write a stderr message that names the file holding the include string, the key path and the URL, and gives the reason.
2. If the content of a remote YAML include is not loadable YAML under config-foundation Requirement 3, then the troupe CLI shall exit with code 4, write nothing to stdout, and write a stderr message that names the URL (and the key path where known) and the problem.
3. When a command fails under 2.1 or 2.2, the troupe CLI shall change no file and create or change no history state, as for any configuration that cannot be loaded (config-foundation Requirement 3).

### Requirement 3: Remote content in every command
**Objective:** As a developer, I want every command to show and record the shared configuration as part of mine, so that I see what troupe uses.

#### Acceptance Criteria
1. When `troupe config get PATH` or `troupe config show [PATH]` addresses a value in remote content, the troupe CLI shall print the fetched value under config-get-show's rules; a remote text include prints its text.
2. When `troupe config show --sources` prints a scalar written in remote content, the troupe CLI shall label it with the URL of the remote include whose content holds it, exactly as that URL is written.
3. The troupe CLI shall import, reconcile and record history events for the leaves of remote content like any other leaves, so that a change of remote content seen by a later command is recorded as reconcile events (config-file-sync Requirement 2).
4. While `troupe config watch` runs, the troupe CLI shall watch the local files of the configuration and the schema file (present or absent), shall not poll any URL, and shall fetch remote content again whenever it reloads the configuration.
5. If a fetch fails while `troupe config watch` runs, then the troupe CLI shall report the load failure, record nothing, and keep watching, as for any other load failure (config-file-sync Requirement 5).
6. The troupe CLI shall load the configuration, fetching remote includes and checking the schema, for every command that loads it today, `history` included.

### Requirement 4: Literal strings
**Objective:** As a developer whose settings hold URLs, I want to say which strings are values and not includes, so that a webhook URL is never fetched and embedded.

#### Acceptance Criteria
1. When a scalar is tagged `!literal`, the troupe CLI shall load it as a string whose value is the scalar's text, in any scalar style and without plain-scalar typing (an empty `!literal` scalar is the empty string), shall never read it as an include, and shall print no tag in `get` or `show`.
2. If `!literal` is written on a mapping or a list, or before an anchor on the same node (`!literal &a`), or any other explicit tag is written, then the troupe CLI shall exit with code 4, write nothing to stdout, and write a stderr message that names the file, the key path and the tag (config-foundation 3.7).
3. If the `$ref` value of a mapping-form include is a `!literal` string, then the troupe CLI shall exit with code 4, naming the file and key path, because a `$ref` value must be an include string (config-include-overrides 2.2).
4. Where the schema is present, when the subschema that the schema gives a string's own path has `"format": "uri"`, the troupe CLI shall load that string as a literal.
5. The troupe CLI shall find the subschema of a path by starting at the schema's root and, for each segment in turn, taking the `properties` entry named by the segment, else for a digit-only segment the `items` subschema, else the `additionalProperties` subschema. When none of these is an object subschema, the path has no format. `$ref`, `allOf`, `anyOf`, `oneOf`, `patternProperties` and `prefixItems` are not followed.
6. The troupe CLI shall never fetch or read as an include a literal, in any command.

### Requirement 5: The schema
**Objective:** As a developer, I want troupe to check my configuration against the schema I wrote, so that a configuration of the wrong shape is caught before it is used.

#### Acceptance Criteria
1. Where the schema file exists, the troupe CLI shall read it as a JSON Schema 2020-12 document and validate the expanded configuration against it whenever the configuration loads.
2. If the expanded configuration does not validate, then the troupe CLI shall exit with code 4, write nothing to stdout, and write a stderr message that names the schema file, the key path of the reported failure (none for the root) and the validator's description of it.
3. If the schema file cannot be read, is not valid UTF-8 or JSON, is not a valid JSON Schema 2020-12 document, has a `$schema` naming another dialect, or has a `$ref` that cannot be resolved when validation meets it (a remote `$ref` is never fetched), then the troupe CLI shall exit with code 4, write nothing to stdout, and write a stderr message that names the schema file and the problem.
4. Where the schema file does not exist, the troupe CLI shall validate nothing and find no literal by schema.
5. The troupe CLI shall treat `format` as an annotation during validation, so that a string that does not look like a URI still validates against `"format": "uri"`.
6. When a command fails under 5.2 or 5.3, the troupe CLI shall change no file and create or change no history state, and shall never create, change or delete the schema file.

### Requirement 6: Remote content is read-only
**Objective:** As a developer, I want troupe to refuse changing shared values and tell me which include line leads there, so that I change the shared configuration where it lives.

#### Acceptance Criteria
1. If a `troupe config set` would change, add or remove anything written in remote content, whether an existing value written there, a collection replacement that would change or remove part of it, a new key that `--in-include` would put there, or an alias expansion there, then the troupe CLI shall exit with code 3, write nothing to stdout, write a stderr message that names the local file holding the include directive, PATH, the include string exactly as written in that file and its line number, and change no file.
2. When remote content is reached through several remote includes, the troupe CLI shall report under 6.1 the outermost include directive that is written in a local file.
3. When a value from remote content is overridden in a local file (config-include-overrides), the troupe CLI shall change it in the override under the existing rules.
4. When `troupe config set` without `--in-include` adds a key below a remote include, the troupe CLI shall write it as a local override under the step 5 placement rules, converting a scalar-form include to mapping form with its include string unchanged (config-include-overrides Requirements 4 and 5).
5. When VALUE would replace the whole value of a remote include (its include root), the troupe CLI shall apply the existing include-root rules (config-set-writeback 2.4, 2.5), exiting 3 with a stderr message that contains the include string.
6. When a set is refused under 6.1 or 6.5, the troupe CLI shall record no event and change no history state other than the import or reconcile that `set` runs first.

### Requirement 7: Sets must keep the configuration valid
**Objective:** As a developer, I want troupe to refuse a change that would break my schema, so that my configuration always matches its declared shape.

#### Acceptance Criteria
1. If the configuration that a `troupe config set` would produce, as read back before writing (config-set-writeback 13.6), does not validate against the schema, then the troupe CLI shall exit with code 3, write nothing to stdout, write a stderr message that names the schema file, PATH, the key path of the reported failure and the validator's description, and change no file.
2. When a set is refused under 7.1, the troupe CLI shall record no event and change no history state other than the import or reconcile that `set` runs first.

### Requirement 8: Writing include-like VALUE strings
**Objective:** As a developer, I want a URL I set to stay a value unless I ask for an include, so that a `set` never makes troupe fetch something by accident.

#### Acceptance Criteria
1. When `--include` is not given and VALUE is an include-like string, or a flow mapping or list VALUE holds include-like strings (as values, not keys), the troupe CLI shall write each of them tagged: `!literal`, one space, then the string as config-set-writeback writes it. For example `set hook https://x` writes `hook: !literal https://x`, and `set a '{b: https://x}'` writes `b: !literal https://x` under `a` (in block or flow style by config-set-writeback's rules, e.g. `m: {b: !literal https://x}` when an existing flow mapping `m` is replaced).
2. When the schema gives a VALUE string's own path `format: uri`, the troupe CLI shall write that string without a tag.
3. The troupe CLI shall write a tagged string on its line in plain, single-quoted or double-quoted style under config-set-writeback's style rules, double-quoted when it holds a line break, and never as a literal or folded block scalar.
4. When VALUE replaces an existing `!literal` string, the troupe CLI shall replace the whole scalar, tag included, and tag the new string only under 8.1. An anchor written before the tag stays.
5. When the new value equals the current value at PATH (for example a `!literal` string with the same text), the troupe CLI shall treat the set as a no-op (config-set-writeback 14.6).
6. If VALUE holds a mapping with a `$ref` key and `--include` is not given, then the troupe CLI shall exit with code 3, write nothing to stdout, write a stderr message that names PATH, says that a `$ref` mapping is an include and mentions `--include`, and change no file.
7. If VALUE holds an explicit tag, `!literal` included, then the troupe CLI shall exit with code 2 as before (config-set-writeback 1.3).
8. When VALUE becomes the new content of a text include (config-set-writeback 2.2), the troupe CLI shall write the string as text, untagged.

### Requirement 9: The `--include` option
**Objective:** As a developer, I want to say explicitly that a string I set is an include, so that I can add a local or remote include with `set`.

#### Acceptance Criteria
1. The troupe CLI shall accept `--include` on `troupe config set`, without a value, before, between or after PATH and VALUE, together with `--reason`, `--in-include` and `--expand-alias`.
2. If a value is attached to `--include` (`--include=TEXT`), then the troupe CLI shall exit with code 2, write nothing to stdout, write a stderr message describing the usage problem, and change no file and no history state.
3. When `--include` is given, the troupe CLI shall write every include string of VALUE without a tag, so that it loads as an include, under config-set-writeback 2.7 and 2.8 and config-include-overrides 8.2.
4. If `--include` is given and a remote include written by the set cannot be fetched or loaded when the set is read back (2.1, 2.2), then the troupe CLI shall exit with code 3, write nothing to stdout, write a stderr message that names PATH and the URL, and change no file.
5. When `--include` is given and VALUE holds no include string, the troupe CLI shall carry out the set exactly as without the option.
6. When a set with `--include` succeeds, the troupe CLI shall record its events and lineage as for any other `set` (config-set-writeback Requirement 15, config-lineage).

### Requirement 10: Safety and compatibility
**Objective:** As a developer, I want step 7 to keep every earlier guarantee, so that a `set` can never corrupt my files.

#### Acceptance Criteria
1. After a successful `troupe config set`, the troupe CLI shall have changed only the bytes the earlier specs and Requirement 8 require, written atomically, verified by read-back and recorded as events, and shall have created no file outside `.troupe/` and left none behind.
2. The troupe CLI shall load, print and write a configuration that has no remote include, no `!literal` tag and no schema file exactly as before step 7, except that an include-like VALUE string is written under Requirement 8.
3. While one `set` runs, the troupe CLI shall reuse for its read-back and alias-expansion reloads the content fetched when the configuration was loaded, and shall fetch only remote includes that the set newly writes.
4. The troupe CLI shall access the network only to fetch remote includes, with plain requests that carry no credentials, and shall store no remote content on disk.
