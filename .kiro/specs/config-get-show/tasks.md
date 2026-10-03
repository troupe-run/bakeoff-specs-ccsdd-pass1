# Implementation Plan

**Conventions.** Every test written for this spec names in its docstring the requirement IDs it proves (for
example `"""Req 4.4: ..."""`), as in the earlier specs. Tests use real theatres on disk and the real CLI
(`testing.md`). Unit tests of the YAML output use nodes loaded from real files, never hand-built dicts or nodes.
"Reads back" means: the output written as a theatre's root file and loaded with the project's own loader gives
the same leaf paths, order and typed values (design, Testing Strategy). Each task ends with `uv run pytest -n
auto`, `uv run mypy --strict .`, `uv run ruff check .` and `uv run ruff format --check .` passing.

**External prerequisites.** config-foundation, config-set-writeback and config-file-sync are complete (all their
tasks `[x]`). No new runtime dependency is added.

- [x] 1. Foundation: shared YAML scalar presentation
- [x] 1.1 Move the write-neutral scalar presentation rules from write-back into a core module
  - Move the quoting contexts, quote styles, representability check, minimal style choice, quoting and escaping (made public) and the plain-scalar probes, plus the chomping kinds, required chomping and the "characters a literal block can hold" check, into the new core presentation module
  - Leave the refusals (lone surrogates, the first-line-starts-with-a-space rule of the write-back literal check), the literal renderer and the block rewriting in write-back, importing the moved definitions; keep every name other write-back modules already import available from its old place
  - Pure move: no write-back output byte changes
  - Done when the whole existing suite (including the byte-exact `set` tests) passes unchanged, a new test proves the names imported from the write-back modules are the very objects defined in the core module, the core import-boundary test still passes (the new module imports neither write-back nor CLI), and mypy and ruff pass
  - The rules listed under the requirements are preserved here and proven for `show` in 1.2 and 2.3
  - _Boundary: YamlStyle_
  - _Requirements: 4.3, 4.6, 4.7_

- [x] 1.2 Add the document-root quoting context
  - Add a context for a scalar printed alone as a whole document; plain is allowed only when the text also reads back as the same string at the document root
  - Write-back does not use the new context
  - _Boundary: YamlStyle_
  - Done when tests prove `--- a`, `...`, `%x`, `true`, `5` and an empty string are not plain in that context while `hello` and `café` are, and that every chosen style reads back as the original string
  - _Requirements: 2.6, 4.3_

- [x] 2. Core: read use cases, source labels and the YAML emitter
- [x] 2.1 (P) Turn the scalar read into `get`'s use case and add the node lookup
  - Rename the scalar read use case for `get`; its refusal of a mapping or list keeps exit 5 and the `<file>: <path>: <problem>` shape and adds a hint to print it with `troupe config show <PATH>`
  - Add the lookup of the node at an optional PATH: the top-level mapping when PATH is omitted, otherwise the resolved node (missing PATH → the existing not-found error naming the deepest existing file)
  - Update the existing callers of the old name in the same change: the step 1 `show` handler in the CLI entry module, `tests/test_theatre.py` and `tests/test_theatre_sync.py`. The step 1 `show` keeps working on scalars; until 3.2 replaces it, its mapping/list refusal carries the new hint pointing at `config show` (harmless and expected)
  - Done when core tests on real theatres prove scalars are returned unchanged, mappings and lists are refused with exit 5 naming the file and PATH and containing `config show`, a missing PATH names the deepest existing file, the omitted PATH yields the root mapping, values come from the files as read even after a hand edit, and the suite passes
  - _Boundary: TheatreReads_
  - _Requirements: 1.1, 1.2, 1.3, 2.3, 2.4, 6.4_

- [x] 2.2 (P) Provide the source label of a file
  - Compute a file's label relative to the theatre directory as given, lexically (normalising `.` and `..` without resolving symbolic links), with `/` separators and `..` only at the start
  - Done when tests prove the labels `.troupe/troupe.yml`, `.troupe/agents/lead.yml` for a nested include, a `sub/../x.yml` include spec normalised, `../shared/p.md` for a file outside the theatre, identical labels for a relative and an absolute theatre argument, and a symlinked directory kept unresolved
  - _Boundary: SourceLabel_
  - _Requirements: 5.4_

- [x] 2.3 (P) Render mappings, lists and one-line scalars as block YAML
  - Handles a printed mapping or list (and the whole configuration); a scalar printed alone at the document root is added in 2.5
  - Lay out mappings and lists in block style: `key: value` in document order, nested collections on the following lines two spaces deeper, list items indented under their key (column 0 for a printed list), collections inside list items in compact form on the dash line, `{}`/`[]` for empty collections and `{}` for an empty configuration
  - Print booleans, null, integers, finite floats in `repr()` form and `.inf`/`-.inf`/`.nan`; one-line strings plain when they read back as the same string, otherwise single- then double-quoted; never wrap; non-ASCII printable characters unescaped
  - Quote keys that would not read back as the same string key (including `true`, `null`, `1.5`, `<<`, an empty key)
  - No document markers, a single final newline, no trailing spaces; the source's comments, quoting, flow style and number spellings are not carried over
  - Done when unit tests on configurations loaded from real files match exact expected bytes for nested mappings, lists, lists of mappings, lists of lists, empty collections and the empty root, prove flow style becomes block, `0o17` prints `15`, `~` prints `null`, comments vanish, and the scalar and key quoting cases above
  - _Boundary: YamlOutput_
  - _Requirements: 3.1, 3.2, 3.3, 3.5, 3.6, 3.7, 4.2, 4.3, 4.6, 4.7, 4.8_

- [x] 2.4 Render text includes and multi-line strings as literal blocks
  - Every text include is a literal block whatever its text; any other string with a line break is a literal block when a literal block can hold its characters, otherwise double-quoted with escapes
  - Header: `|`, plus the indentation indicator `2` when the first line that is not empty starts with a space, plus the chomping that reproduces the trailing line breaks (`|-`, `|`, `|+`)
  - Content two spaces past the column of the key or dash owning the header (including `- k: |`), empty lines bare, every other character unchanged, so a CRLF text include reaches the output with its `\r\n`
  - Done when unit tests on real files with text includes prove the exact bytes for `|`, `|-`, `|+`, `|2`, nested and list-item indentation, a single-line include without a final newline, an empty include, a CRLF include kept literal with `\r\n` content lines, and a non-include string containing `\r` printed double-quoted; and when every LF case reads back exactly
  - _Boundary: YamlOutput_
  - _Requirements: 3.4, 4.1, 4.4, 4.5, 4.6_

- [x] 2.5 Annotate scalar lines with their source file
  - Every line that holds a scalar (a `key: value` line, a `- value` line, a scalar alone, a literal block's header) optionally ends with ` # from <label>` of the scalar's source file: the root file, the YAML include it is written in, or the included text file for a text include
  - Lines holding no scalar (`key:` before a nested collection, `{}`/`[]` lines, literal content lines) never get a comment
  - Print a scalar alone (for `show PATH --sources`) as a one-document YAML scalar using the document-root context, a literal block for a text include with the comment on its header
  - Done when unit tests prove exactly which lines carry comments for a configuration with a root file, a nested YAML include and a text include; that removing every ` # from …` suffix gives the unlabelled output byte for byte; and the exact bytes for a scalar alone (`5 # from …`, `'true' # from …`, a root literal block)
  - Sequential after 2.3 and 2.4 (same emitter)
  - _Boundary: YamlOutput_
  - _Requirements: 2.6, 5.1, 5.2, 5.3, 5.5_

- [x] 3. Integration: the `get` and `show` commands
- [x] 3.1 Add the `get` command
  - `get PATH` (PATH required; any other option, including `--sources`, is a usage error) prints a scalar exactly as step 1's `show` and refuses mappings and lists with the `config show` hint
  - Parse PATH before opening the theatre, open it the default way (import a never-imported theatre, otherwise reconcile first), buffer the output and close the store at the end; never write a file
  - Put it in the new read-command module and register it in the entry module; the step 1 `show` stays as it is for now
  - Update the tests that assert the exact registrar list (`tests/test_cli_usage.py`, `tests/test_cli_set.py`) to include `get` in the same change, placing it first so the order is already the design's final `get, show, history, set, watch`
  - Done when `uv run troupe config get PATH` prints scalars on a sample theatre, `get agents` exits 5 naming the file, `agents` and `config show`, `get` with no PATH and `get a --sources` exit 2, the read-command module imports nothing from write-back, and the suite passes
  - _Depends: 2.1_
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 6.1, 6.2, 6.3, 6.5, 6.7, 6.8_

- [x] 3.2 Replace the step 1 `show` with the new `show` and retarget the tests that relied on it
  - `show [PATH] [--sources]` (flag before or after PATH, PATH optional) prints a scalar without `--sources` exactly like `get`, and otherwise the YAML output (labelled with source files when `--sources` is given), with the same opening, buffering and no-write rules as `get`
  - Remove the step 1 `show` handler and registrar from the entry module and register the new `show` from the read-command module
  - In the same change, retarget every existing test that relies on step 1 `show` being scalar-only, so the suite stays green. Mapping/list → 5 and "missing PATH → 2" cases move to `get` in place (or, where the case is about `show`, change to a surplus PATH or the new exit 0). Known cases:
    - `tests/test_cli_usage.py`: "show missing PATH", `test_missing_argument_message_names_it`, `test_mapping_target_exits_5`, the registrar list and handler identity
    - `tests/test_cli_streaming.py`: the handler identity
    - `tests/test_cli_set.py`: the registrar list
    - `tests/test_e2e_reconcile.py`: the `missing-path` usage case
    - `tests/test_e2e_safety.py`: `("show", "team.members")` → 5
    - `tests/test_e2e_show.py`: its mapping and list cases
    - plus any other test found expecting `show` to exit 2 without PATH or 5 on a mapping or list
  - Done when `uv run troupe config show agents` exits 0 with block YAML, `show` with no PATH prints the whole configuration, `show a --sources` and `show --sources a` print the same bytes, a scalar prints the same bytes as `get`, and the full suite passes
  - _Depends: 2.2, 2.5_
  - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 6.1, 6.2, 6.3, 6.5, 6.7, 6.8_

- [x] 3.3 Align docstrings and the earlier specs with step 3
  - Add step 3 notes to config-foundation's requirements (1.1 names `get`, `show`, `history`; Requirement 6 now describes `get`, and 6.7 gains the `config show` hint) and config-file-sync's (1.1, 1.2, 1.6 read "`get` or `show`"), and update module and test docstrings that describe `show PATH` as a scalar-only command
  - Done when a search for `show PATH` and `show_value` in `src/`, `tests/` and those two requirement files finds only text that is correct for step 3, and the suite passes
  - _Requirements: 1.2, 6.2_

- [ ] 4. Validation: end to end through the real CLI
- [ ] 4.1 (P) Validate `get` end to end
  - Every step 1 scalar printing case (strings with and without a final newline, booleans, null, integers, floats, a folded block, a text include), mapping and list → 5 with empty stdout and stderr naming the file, PATH and `config show`, missing PATH → 5, no PATH / two PATHs / `--sources` → 2, `a..b` → 2
  - This task owns the new `get` e2e module; the cases 3.2 retargeted in place stay where they are
  - Done when the new e2e test module passes and fails if the hint or any exit code is broken
  - _Boundary: e2e tests for get_
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 6.7_

- [ ] 4.2 (P) Validate `show` output and command behaviour end to end
  - A scalar gives the same bytes as `get`; a mapping section mixing a YAML include and a text include, the whole configuration, a list, and an empty file give exact expected bytes; missing PATH → 5; surplus PATH, unknown option, `a.` → 2
  - Reconcile first (a hand edit before `show` or `get` appears in `history` as a `set`, also when the command then exits 5), a never-imported theatre is imported with no reconcile events, configuration files are byte-identical afterwards and no new file remains outside `.troupe/` state, a missing config file → 4 with no events, both `troupe.yml` and `troupe.yaml`, `TROUPE_THEATRE` honoured, a following command succeeds (store and lock released)
  - Done when the new e2e test module passes
  - _Boundary: e2e tests for show_
  - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 3.4, 3.5, 4.4, 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8_

- [ ] 4.3 (P) Validate that the YAML output reads back exactly
  - For fixtures covering every scalar kind, quoting edge cases (strings that look like other types, `: `, ` #`, leading/trailing spaces, quotes, control characters, non-ASCII), awkward keys, nested collections, YAML includes and LF text includes (leading spaces, no final newline, several final newlines), run `show` and reload its output as a fresh theatre
  - Lives in its own test module, separate from 4.2's, so 4.2 and 4.3 can run concurrently (a small refinement of the design's test file list)
  - Done when leaf paths, order and typed values of the reloaded output equal the original's for every fixture
  - _Boundary: read-back tests_
  - _Requirements: 3.4, 4.1, 4.2, 4.3, 4.4, 4.8_

- [ ] 4.4 (P) Validate `--sources` end to end
  - The whole configuration with a root file, a YAML include in a subdirectory, a text include (labelled with the included file, comment on the `|` header) and an include outside the theatre directory (`../…`) gives exact expected bytes; `--sources` before and after PATH gives the same bytes; a scalar with `--sources` and a text include scalar with `--sources`; a CRLF text include emitted literal with `\r\n` content lines and the comment on the header
  - Done when the new e2e test module passes
  - _Boundary: e2e tests for sources_
  - _Requirements: 2.6, 2.7, 4.5, 5.1, 5.2, 5.3, 5.4, 5.5_

- [ ] 4.5 Run the full quality gate and check traceability
  - Check every requirement ID 1.1–6.8 is named in at least one test docstring and every new test names an existing ID; tick the step 3 items in the roadmap
  - Done when the traceability check finds no gap, and `uv run pytest -n auto`, `uv run mypy --strict .`, `uv run ruff check .` and `uv run ruff format --check .` pass with their output shown
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 4.8, 5.1, 5.2, 5.3, 5.4, 5.5, 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8_

## Implementation Notes
- 2.3: Under requirement 4.3's minimal chain, a string containing a TAB is single-quoted (`'x<TAB>y'`), not double-quoted. Double quotes are only for characters single quotes cannot hold.
- 2.3 (review): `yaml_style` (shared with `set`) writes a string containing U+FEFF plain or single-quoted. Our loader and ruamel read it back exactly, but strict YAML 1.2 excludes the BOM from `nb-char`. This is pre-existing write-back behaviour; changing it would change `set`'s bytes, which this spec must not do. Revisit in write-back if a strict parser rejects it.
- 3.2: The full suite found one more test relying on step 1's scalar-only `show`, beyond the known list: `tests/test_e2e_includes.py` (an included collection → 5). It was retargeted to `get`, and now also asserts that `show` exits 0.
