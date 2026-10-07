# Implementation Plan

**Conventions.** Every test written for this spec names the requirement IDs it proves in its docstring (for example
`"""Req 6.1: ..."""`), as in the earlier specs. Tests use real theatres on disk, the real CLI and real HTTP from a
local server, never the internet (`testing.md`). Preservation tests compare whole files byte for byte, and error
tests assert the file, the key path and, for remote cases, the URL. Each task ends with `uv run pytest -n auto`,
`uv run mypy --strict .`, `uv run ruff check .` and `uv run ruff format --check .` passing. Existing tests that pin
behaviour a task changes are updated in that same task.

**Where this spec's tests live.** Every test written for this spec goes in one of these new files. Existing test files
only lose, adjust or gain rows that pin a changed rule. This keeps the traceability check of task 5.1 exact.
- `tests/test_includes.py` (1.1, 1.2), `tests/test_schema.py` (1.3), `tests/remote_support.py` (fixture, 1.4),
  `tests/test_fetch.py` (1.4)
- `tests/test_loader_literals.py` (2.1), `tests/test_loader_schema.py` (2.2, 2.3), `tests/test_loader_remote.py` (2.4)
- `tests/writeback/test_value_marks.py` (3.1, 3.2), `tests/writeback/test_remote_guard.py` (3.3)
- `tests/test_e2e_remote.py` (2.5, 4.1), `tests/test_set_remote.py` (2.4, 3.4, 4.2), `tests/test_set_schema.py`
  (3.4, 4.3), `tests/test_set_literals.py` (3.5, 4.4)

**External prerequisites.** config-foundation, config-set-writeback, config-file-sync, config-get-show,
config-lineage, config-include-overrides and config-aliases are complete. Task 1.1 adds the only new dependencies:
`jsonschema` (runtime) and `types-jsonschema` (dev).

**Safety window.** From task 2.4 the loader reads remote sources, but the writer refuses writes into them only from
task 3.3. In between, `set` refuses (exit 3, no file changed) every configuration that holds a remote include, and
`write_atomically` refuses any remote source. Task 2.4 adds the temporary refusal, pinned by a test in
`tests/test_set_remote.py`, and task 3.3 removes both together. Between tasks 3.1 and 3.5,
VALUE strings that were never marked keep today's `file:` rule, so decision D holds until 3.5 changes it together with
its tests. Between tasks 2.1 and 3.2, replacing a `!literal`
scalar can keep its tag. The read-back still refuses any result that differs from VALUE, so no wrong value can be
written.

**Task-graph review (independent, two passes).** The first pass found ordering, typing and boundary issues
(repaired: the schema task made sequential, task 2.3 added for identity keys, recognition switched on only in 2.4,
(P) removed from 3.1 and 3.3). The second pass found one remaining issue, the undefined "unmarked VALUE" state
between 3.1 and 3.5. The fix it prescribed was applied verbatim (3.1, 3.5). The skill allows no third pass, so that
fix has not been re-reviewed.

**Fast-track approval (2026-10-07).** The product owner was not available. The plan is approved under `-y`
together with the design decisions it depends on (research.md Decisions 1–7, choices 1–22).

- [ ] 1. Foundation: dependencies, recognition, model, fetcher and schema
- [x] 1.1 Add the dependencies, the step 7 constants and the include recognition module
  - Add `jsonschema` (runtime, `>=4.23,<5`) and `types-jsonschema` (dev) with uv, so the lock file changes and `mypy --strict` sees typed jsonschema.
  - One home for the step 7 names: the remote include prefixes, the `!literal` tag, the schema file name, the 2020-12 dialect URI and the 10-second fetch timeout.
  - The recognition module answers three questions: is a value an include string (`file:`, `https://`, `http://`, case-sensitive, strings only), is it remote, and is its target YAML (for `file:`, the name's suffix as today; for a URL, the suffix of its path, ignoring the query and fragment).
  - Done when unit tests prove each prefix and its case sensitivity, the YAML/text decision for URLs with queries, fragments and upper-case suffixes, and that `file:` suffix rules are unchanged
  - _Requirements: 1.1, 1.2_

- [x] 1.2 Give the model remote identity, literal properties, include strings and a schema source
  - A source file can carry a URL, and has an identity (`key`, which is the URL or the real path) and a display name (`shown`, which is the URL or the path as opened). A scalar can record its `!literal` property span. An include reference gives its whole include string. A document can carry the schema it was checked against. All new fields default to "none", so existing constructions are unchanged.
  - Errors accept a URL as their file. A schema-violation error keeps its failing key path and detail.
  - The six places that rebuild `"file:" + spec` (compare, shared, planner) use the include reference's string instead, with no change in behaviour.
  - Done when unit tests (in `tests/test_includes.py`) show `key`, `shown` and the include string for local and remote references, and an error message that names a URL, and the full existing suite still passes
  - _Requirements: 10.2_

- [x] 1.3 Read the schema, find `format: uri`, and validate a configuration
  - Reading returns nothing when the schema file is absent. It is a load error naming the schema file when the file is not a regular file, cannot be read, is not UTF-8 or JSON, is not an object or boolean, has a `$schema` other than 2020-12, or is not a valid 2020-12 schema.
  - The walker follows `properties`, then `items` for a digit-only segment, then `additionalProperties`, each only when it is an object schema. A path has the format only when the final subschema's `format` is exactly `uri`.
  - Validation turns a node tree into its plain JSON value. It uses the 2020-12 validator without format assertion and reports the best-matching failure as a schema-violation error naming the schema file and the dotted key path (none at the root). An unresolvable `$ref`, local or remote, is a load error naming the schema file, and nothing is retrieved.
  - Done when unit tests prove each read refusal, the walker over the three keywords and its stops (`true`, `$ref`, `allOf`, `patternProperties`), the failing key path and message, `format` not asserted, and the unresolvable `$ref` cases
  - _Requirements: 4.4, 4.5, 5.1, 5.2, 5.3, 5.4, 5.5_

- [x] 1.4 (P) Fetch remote sources, with a real local HTTP server for tests
  - The fetcher does one GET with the timeout from 1.1, follows redirects, and returns the body bytes. Any failure (connection, timeout, a final status outside 2xx) is a fetch error with a short reason. It sends no credentials and writes nothing to disk.
  - The test fixture runs a threaded HTTP server on 127.0.0.1 with a mutable route table (status, body, headers), redirects, a delayed route and a request log. It sets `NO_PROXY` for the CLI subprocess and stops the server afterwards. It is registered in `tests/conftest.py`.
  - Done when tests against the fixture prove a 200 body, 404 and 500 reasons, a followed 301, a refused connection, a timeout with a short timeout, and exactly one request per call
  - _Boundary: Fetch_
  - _Depends: 1.1_
  - _Requirements: 1.6, 2.1, 10.4_

- [ ] 2. Core: literals, the schema and remote includes in the loader
- [x] 2.1 Load `!literal` scalars and narrow the explicit-tag refusal
  - A scalar tagged exactly `!literal` loads as its text, in any style and without plain-scalar typing (empty is `""`). It records the tag's span, and its value span excludes the tag, as for anchors (`&a !literal x` loads). It is never an include. `!literal` on a mapping or a list, `!literal` written before an anchor, any other tag, and a `$ref` value tagged `!literal` stay load errors (exit 4) naming the file, the key path and the tag.
  - Existing tests change in this task: in `tests/test_loader.py` (`REFUSALS`, ~:455), `tests/test_loader_aliases.py` (`TAGGED_ANCHORS`, ~:316) and `tests/test_e2e_locate_load.py` (~:210), every other tag stays refused. Docstrings that say "every explicit tag" are narrowed, and `!literal` acceptance rows move to the new file.
  - Done when loader tests show `!literal` values for plain, quoted, block and empty scalars, an alias of a literal, spans without the tag, `get` printing no tag, and each refusal with its message
  - _Requirements: 4.1, 4.2, 4.3, 4.6_

- [x] 2.2 Read and enforce the schema when the configuration loads
  - Loading reads the schema next to the root configuration file before building. Each include string's path is checked with the walker, so a `format: uri` string stays a string at any depth, also inside included files. After building, the expanded configuration is validated. The document carries the schema.
  - This task adds the literal and schema checks on top of the existing `file:` test; remote prefixes are recognised only from task 2.4. The included-content helper used by writers takes the path and the schema, and its caller in the shared-file rebuild passes them, so new content is decided like loaded content.
  - An invalid configuration, or a schema that cannot be used, is a load error (exit 4) naming the schema file. No history state is created, and the schema file is never written.
  - Done when loader tests prove literals by schema in the root and in an included file, validation of includes, aliases, merge keys and text includes as their expanded values, the exit-4 cases with their messages, and an unchanged load without a schema file
  - _Requirements: 4.4, 4.6, 5.1, 5.2, 5.3, 5.4, 5.6_

- [x] 2.3 Key loading overrides and written texts by source identity
  - Integration task, no behaviour change: the overrides accepted by loading, the texts of the setter, read-back, alias-expansion and shared-file rebuild, and the planner's layouts are keyed by source identity (`key`), so `mypy --strict` accepts URL keys. The included-content helper takes the whole include string instead of the text after `file:`.
  - Done when the full existing suite, mypy and ruff pass unchanged, and a loader test (in `tests/test_loader_schema.py`) shows that overrides keyed by identity replace a file's text as before
  - _Boundary: Loader, Setter, Verify, Expand, Shared, Planner_
  - _Requirements: 10.2, 10.3_

- [x] 2.4 Load remote includes, with a temporary refusal of writes
  - The loader recognises remote prefixes from here on. A remote include in scalar or `$ref` form is fetched once per load (cached by URL, or read from the overrides by URL when given), decoded strictly as UTF-8, and read as YAML or text by the rule of 1.1. It becomes a source with that URL, listed with the files.
  - Remote YAML may hold remote includes, and a URL cycle is refused. A `file:` include in remote content is refused naming the URL and key path.
  - A fetch or decoding failure is a load error naming the including file (by display name), the key path and the URL. Every loader and compose error names sources by display name.
  - Also in this task, so that no write can reach a remote source: `set` refuses (exit 3, no file changed) any configuration with a remote source until task 3.3, and `write_atomically` refuses a remote source. Both are pinned in `tests/test_set_remote.py`.
  - Done when loader tests against the local server prove YAML and text, mapping form with overrides, remote in remote, the refusals, one request per URL across a diamond, no request when overrides hold the URL, and each failure message
  - _Boundary: Loader, compose; Setter and Atomic for the temporary refusal only_
  - _Depends: 1.4_
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 2.1, 2.2, 2.3, 10.3_

- [x] 2.5 Show remote sources by URL and watch the schema file
  - Path-not-found and wrong-kind errors name sources by display name. `--sources` labels a scalar from remote content with its URL.
  - The watch set holds the local files of the last load, both root candidates and the schema file path. Baselines hold the loaded bytes of local files and the schema. A URL is never polled, and each reload fetches again.
  - Done when tests prove the `--sources` label of a remote scalar, a watcher that reloads (and re-fetches) on a schema-file edit and never puts a URL in its watch set, and a watcher that reports a failing fetch and keeps running with nothing recorded
  - _Requirements: 2.2, 3.1, 3.2, 3.4, 3.5_

- [ ] 3. File layer: marks, rendering, the remote guard and the read-back
- [x] 3.1 Mark include strings in VALUE
  - Each string in VALUE (scalar, or nested in flow mappings and lists, never keys) that is an include string is marked by its own path: no mark when the schema gives that path `format: uri`, otherwise an include with `--include` and a literal without.
  - A mapping with a `$ref` key without `--include` is refused (exit 3) naming PATH and mentioning `--include`.
  - The plain tree of VALUE makes include directives from include marks only, so a literal or schema-literal string is a plain value, and a literal equal to a loaded `!literal` string is a no-op. Until task 3.5 makes `set` mark every VALUE, a scalar may also be unmarked (a temporary state, e.g. `mark: ScalarMark | None = None`, where `None` means "not marked yet") and an unmarked string keeps today's `file:` prefix rule, so the existing suite stays green. `NONE` keeps its design meaning (a plain value, also a schema literal).
  - Done when unit tests on loaded documents with and without a schema prove the marks for scalars, nested strings and keys, the `$ref` refusal, and the plain trees and no-op comparisons that follow
  - _Boundary: ValueMarks, Compare_
  - _Depends: 1.3, 2.2_
  - _Requirements: 8.1, 8.2, 8.5, 8.6, 9.3, 9.5_

- [x] 3.2 Write tagged strings and replace existing `!literal` scalars whole
  - A literal-marked string is written as `!literal`, one space, and the string in the style chosen as before (double-quoted when it has a line break), in block values, flow values and list items. It never takes a block-scalar path.
  - The inline extent of a `!literal` scalar starts at its tag, so a replacement removes the tag and keeps an anchor written before it. An existing `!literal` block scalar is replaced as a whole, and the tag counts as a token for comment placement.
  - A string written as a text include's content is written as text, untagged. An alias expansion copies a `!literal` scalar with its tag (config-aliases 7.4).
  - Done when planner and expander tests prove `key: !literal https://x`, `{a: !literal https://x}`, `- !literal file:x`, a string with a line break, replacing a plain and a block `!literal` scalar (with and without an anchor and an end-of-line comment), text-include content, and the copy of an anchored `!literal` scalar
  - _Requirements: 8.3, 8.4, 8.8, 10.2_

- [x] 3.3 Refuse every write into remote content, naming the local include line
  - The remote guard finds the outermost include directive written in a local file that leads to a remote source, preferring the one on PATH's walk. The refusal (exit 3) names that file, PATH, the directive's text exactly as written (quotes included) and its line.
  - The planner raises it whenever it would take the layout of a remote source or replace a remote file's whole text. The alias expander raises it for an alias in remote content. The include-root refusal names the include string.
  - The temporary refusals of task 2.4 are removed together with their pinned test, and `write_atomically` keeps refusing a remote source only as an internal backstop.
  - Done when unit tests prove the directive lookup (scalar and `$ref` form, quoted, remote in remote, several places) and that changed values, removals across layers, `--in-include` placements, text-include rewrites and alias expansions into remote content are refused, while overridden values and new keys below a remote include are planned in the local file
  - _Boundary: RemoteGuard, Planner, Expand, Atomic, Setter_
  - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6_

- [ ] 3.4 Read back by source identity and refuse sets the schema rejects
  - Read-back and the alias-expansion reloads override every loaded source by identity (keyed since 2.3), so remote content is reused and only includes the set newly writes are fetched.
  - A schema violation found by the read-back becomes a refusal (exit 3) naming the schema file, PATH, the failing key path and the detail. A new remote include that cannot be fetched keeps the existing read-back refusal, which names PATH and the URL.
  - Done when tests prove that a set below a remote include sends no second request, that a set making the configuration invalid is refused with that message and nothing recorded, and that an unfetchable new include is refused naming the URL
  - _Boundary: Verify, Setter_
  - _Depends: 3.3_
  - _Requirements: 7.1, 7.2, 9.4, 10.3_

- [ ] 3.5 Mark VALUE in `set`, add `--include`, and update the decision D tests
  - `set` marks VALUE before target resolution, with the schema of the loaded document and the include choice, so marks reach the alias guard, the no-op check and planning. The temporary unmarked state of task 3.1 and the `file:` prefix fallback in the plain tree are removed: every scalar reaching compare or the planner is marked, and the default mark is `NONE`, as in the design. `--include` is a flag that takes no value, may stand anywhere among PATH and VALUE, and combines with `--reason`, `--in-include` and `--expand-alias`. `--include=TEXT` is a usage error (exit 2).
  - Decision D changes in this task. The tests that pin "a `file:` VALUE is written plain" pass `--include` (or `include=True`), and new rows pin the tagged form: `tests/test_set_targets.py` (~:202–231), `tests/test_set_history.py` (~:200, 220), `tests/test_set_overrides.py` (~:918, 1398, and every other include-like VALUE, including `$ref` VALUEs), `tests/test_e2e_reconcile.py` (~:160), and the other set tests a search for include-like VALUEs finds (for example `test_set_in_include.py`, `test_set_safety.py`, `test_e2e_aliases.py` and `writeback/test_setter.py`).
  - Done when CLI tests show the flag's positions and combinations, the exit-2 case, `set b file:inc.yaml` writing `b: !literal file:inc.yaml` and `set --include b file:inc.yaml` writing the include, and the full suite passing with the updated tests
  - _Depends: 3.1, 3.2, 3.4_
  - _Requirements: 8.1, 8.7, 9.1, 9.2, 9.3, 9.5, 9.6, 10.2_

- [ ] 4. Validation: end-to-end through the CLI and a local server
- [ ] 4.1 (P) Validate reading remote content, literals and the schema end to end
  - `get`, `show` and `show --sources` over remote YAML and text includes, both forms, remote in remote, for `troupe.yml` and `troupe.yaml`. `!literal` and `format: uri` strings print as strings, and the server log shows no request for them.
  - Fetch failures (404, refused connection, bad UTF-8, bad YAML, `file:` in remote content, a cycle) and schema failures exit 4, name the file, key path and URL or the schema file, and create no history state.
  - `history` imports and records remote leaves. A remote change is reconciled by the next command, and `history` fails on an unreachable URL. `watch` re-fetches on a local edit and survives a failing fetch.
  - Done when these e2e tests pass in `tests/test_e2e_remote.py`
  - _Boundary: test_e2e_remote_
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 2.1, 2.2, 2.3, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 4.1, 4.2, 4.4, 4.6, 5.2, 5.3, 5.6_

- [ ] 4.2 (P) Validate the read-only rules for remote content end to end
  - Setting a remote value, replacing a collection that holds remote values, `--in-include` below a remote include, replacing a remote include root, an alias in remote content, and remote in remote each exit 3. stderr holds the include string as written in the local file and its line, every file is byte-identical, and no event is recorded.
  - An overridden remote value changes in the local override. A new key below a scalar-form remote include converts it to mapping form with the URL unchanged. Each change is recorded as events, with `--reason`.
  - Done when these e2e tests pass in `tests/test_set_remote.py`
  - _Boundary: test_set_remote_
  - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 10.1_

- [ ] 4.3 (P) Validate schema checks of `set` end to end
  - A set that breaks the schema (type, required key, `additionalProperties: false`) exits 3, names the schema file, PATH and the failing key path, and changes no file and records no event. A valid set is written byte-exactly. A set in a theatre whose configuration is already invalid exits 4.
  - Done when these e2e tests pass in `tests/test_set_schema.py`
  - _Boundary: test_set_schema_
  - _Requirements: 7.1, 7.2, 5.2, 10.1_

- [ ] 4.4 (P) Validate writing include-like VALUEs and `--include` end to end
  - `set hook https://x` writes `hook: !literal https://x`. Nested flow VALUEs are tagged per string, and a schema `format: uri` path is written plain. An existing `!literal` is replaced with its tag, and an equal VALUE is a no-op. `--expand-alias` over an alias of a `!literal` anchor keeps the tag in the copy. A VALUE with a tag exits 2, and a `$ref` VALUE without `--include` exits 3.
  - `--include` with a local and with a remote VALUE writes and reads back the include (one `set` event per leaf). An unreachable remote VALUE exits 3 with no file changed, and `--include` without an include string behaves as without it.
  - Done when these e2e tests pass in `tests/test_set_literals.py`, comparing whole files byte for byte
  - _Boundary: test_set_literals_
  - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8, 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 10.2_

- [ ] 5. Spec notes and traceability
- [ ] 5.1 Point earlier specs at step 7 and check traceability
  - Step-7 notes: config-foundation Requirement 4 (remote includes), 3.7 and decision I (`!literal`), and the schema exit-4 case. config-set-writeback decision D and 2.7–2.8 (`!literal` unless `--include`), and the set flags. config-file-sync (the watch set gains the schema file; URLs are re-fetched, not polled). config-get-show (`--sources` label of a remote scalar). Tick the step 7 checkboxes in `.kiro/steering/roadmap.md`.
  - Done when every requirement ID of this spec appears in a test docstring in the files listed above, no test there names an ID that does not exist, and the full suite, mypy and ruff pass
  - _Requirements: 10.1, 10.2_

## Implementation Notes
- 1.3: jsonschema 4.26 fetches remote `$ref`s unless a registry is passed; always validate through `schema.validate()` (empty `referencing.Registry()`).
- 2.3: `shared.written_location` and `layout.py` (two `real_path` comparisons) still use `real_path`; task 3.3 must switch them to `SourceFile.key` (a URL as `Path` collapses `//` and `.` segments) and update test annotations of `WrittenLocation`.
- 3.2: unit tests that reload include-like VALUEs should use hosts that never leave the machine (`.invalid` or the `remote_server` fixture), so a tagging regression fails locally instead of fetching; existing `https://x` fixtures are to be swept before final validation.
