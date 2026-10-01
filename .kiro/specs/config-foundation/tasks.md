# Implementation Plan

- [ ] 1. Foundation: project scaffold and shared conventions
- [x] 1.1 Set up the uv project, console script and quality tooling
  - Create the src-layout package with the CLI and config sub-packages, typed-package marker and a minimal `config` command group entry point so the console script exists
  - Declare runtime dependencies (ruamel.yaml pinned below 0.20, eventsourcing 9.5.5 or later below 10) and a dev group with pytest, mypy and ruff; configure mypy strict mode, ruff lint and format rules and pytest test paths, and set `[tool.pytest.ini_options] pythonpath = ["tests"]` so helper modules in `tests/` are importable from test subdirectories (e.g. `tests/writeback/`)
  - Add test helpers: a fixture that writes real files into a temporary theatre (`.troupe/troupe.yml` or `.yaml`, plus arbitrary include files) and a helper that runs the real `troupe` console script as a subprocess and returns exit code, stdout bytes and stderr text
  - Add an ignore file for the virtual environment and tool caches
  - Add a smoke test in the CLI usage test module that builds a `.yml` theatre with the fixture and uses the subprocess helper to assert `troupe config --help` exits 0, so both helpers are exercised
  - Done when `uv run troupe config --help` exits 0 from the repository root, the smoke test passes, and `uv run pytest`, `uv run mypy --strict .`, `uv run ruff check .` and `uv run ruff format --check .` all pass on the skeleton
  - _Boundary: Scaffold (pyproject.toml, src/troupe/__init__.py, tests/conftest.py)_
  - _Requirements: 1.1_

- [x] 1.2 Define single-place constants, error types and the exit-code mapping
  - Record the `.troupe` directory name, both config file names, the `file:` include prefix, the YAML include suffixes, the history store file name and the path separator in one constants module
  - Provide the exit-code enumeration (0, 2, 3, 4, 5), the base error carrying file, key path and problem, one error class per non-zero code (usage, write refused, load, path) and the two path-error kinds (not found, wrong node kind)
  - Provide the single message formatter producing `<file>: <path>: <problem>` and its file-only, path-only and problem-only forms
  - Done when unit tests prove each error class reports its exit code and that messages name the file and path in the documented shape
  - _Boundary: Constants, Errors (src/troupe/config/constants.py, src/troupe/config/errors.py)_
  - _Requirements: 10.1, 10.3, 10.4, 10.5_

- [ ] 2. Core value and path primitives
- [x] 2.1 (P) Implement YAML 1.2 core-schema typing of plain scalars
  - Classify written plain scalar text as null, boolean, integer (decimal, `0o` octal, `0x` hex), float (including infinities and NaN forms) or string, exactly per the core schema, returning plain built-in values
  - Done when a table-driven test passes for at least `yes`, `on`, `True`, `FALSE`, `~`, empty, `null`, `0o17`, `0x1F`, `1_0`, `2001-12-14`, `1e3`, `.inf`, `-.inf`, `.NaN`, `-.nan`, `012`, `+1`, and fails if any entry is typed differently
  - _Boundary: Scalars_
  - _Requirements: 3.1_

- [x] 2.2 (P) Implement dotted path parsing, formatting and history prefix matching
  - Parse PATH into segments, rejecting an empty PATH or any empty segment with a usage error that names PATH
  - Recognise digit-only segments as list indices; format paths back to dotted text; build child paths from keys and indices
  - Provide the textual prefix test used by `history`: equal to PATH or starting with PATH followed by `.`
  - Done when tests prove `a..b`, `.a`, `a.` and empty raise usage errors naming PATH, and that prefix `a` matches `a` and `a.b` but not `ab`
  - _Boundary: Paths_
  - _Requirements: 1.5, 5.1, 5.3, 8.3_

- [x] 2.3 Define the source-mapped configuration model
  - Define immutable node types for scalars, mappings (with key text, key origin and key style per entry) and sequences, each with origin (file and character span), style (scalar style or block/flow), and optional include provenance (kind YAML or text, spec text, target file, directive origin)
  - Define the source file record (display path, real path, exact text, line ending: none, LF, CRLF or mixed) and the document record (root file, root mapping, all files read)
  - Document span semantics in the module (character indices, exact plain/quoted scalar spans, block scalar spans that start at the indicator and may extend over trailing blank lines, unreliable spans for empty null values, composer-reported block collection spans) as stated in design.md
  - Done when the model module type-checks under strict mode and a small test constructs a document by hand and reads each field back
  - Depends on 2.1 for the scalar value type, so it is not parallel with 2.1
  - _Boundary: Model (src/troupe/config/model.py)_
  - _Requirements: 4.2, 4.3, 6.6_

- [ ] 3. File layer: locating and loading the user's files
- [x] 3.1 (P) Locate the root configuration file
  - Return the single existing `troupe.yml` or `troupe.yaml` under `<theatre>/.troupe/`; raise a load error naming the expected file when neither exists or the theatre directory is missing or not a directory; raise a load error naming both files when both exist
  - Never create directories or files while checking
  - Done when tests on real temporary theatres cover `.yml` only, `.yaml` only, neither, both and a missing theatre directory, asserting the returned path or the named files in the error
  - _Boundary: Locate_
  - _Requirements: 2.1, 2.2, 2.3, 2.4_

- [x] 3.2 Load a single YAML file into the source-mapped model
  - Read bytes read-only, decode UTF-8 strictly without newline translation, detect the line ending, compose one document and build nodes with exact spans, scalar styles, block/flow styles and core-schema typing for plain scalars
  - Treat an empty or comment-only root file as an empty mapping with block style and span (0, 0)
  - Record each mapping entry's key style from the key node's style
  - Refuse with a load error naming the file (and key path where one exists): unreadable or non-UTF-8 file, YAML syntax error or multiple documents, duplicate keys, explicit tags, anchors, aliases (a defined alias is caught by its anchor, an undefined one by the composer error), plain `<<` merge keys, non-scalar keys, a top-level node that is not a mapping (product-owner decisions H and I)
  - Accept caller-supplied in-memory text for a file (overrides keyed by real path) instead of reading it from disk
  - Uses the dotted path type from 2.2 to name key paths in load errors
  - Remove the U+0007 fold markers that the round-trip composer inserts into folded (`>`) scalar values, for folded style only, so the model holds the true folded value
  - Done when tests on real fixtures with comments, CRLF, multibyte characters and every scalar style show that each plain and quoted scalar's span text equals the written source exactly, each block scalar's span starts at its `|`/`>` indicator and covers all its content lines (trailing blank lines may be included, so the end is not asserted exactly), value styles, key styles (plain, single- and double-quoted keys) and line endings are reported correctly, an empty root file yields an empty block-style root mapping with span (0, 0), each refusal raises a load error naming the file, folded values (`>`, `>-`, with a more-indented line) equal their YAML folded text with no U+0007 while a double-quoted `"\a"` keeps its U+0007, and the fixture files are byte-identical afterwards
  - _Boundary: Loader (src/troupe/config/loader.py)_
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9_

- [ ] 3.3 Resolve `file:` includes recursively
  - Detect string values starting with `file:`, resolve the target relative to the directory of the containing file, embed `.yml`/`.yaml` targets as parsed content and use any other target's exact text as a string, recording include provenance on the produced node
  - Recurse into included YAML files to any depth; treat an empty YAML target as null
  - Refuse with a load error when a target is missing or unreadable (naming the target and the including file), when YAML includes form a cycle on the current chain (naming the files in the cycle), and when an included file fails any single-file check (naming the included file)
  - Done when tests prove embedding, byte-exact text (including CRLF) for text includes, nested includes relative to the included file's own directory, a diamond (same file included twice, no cycle) loads, and the missing-target and cycle errors name the right files
  - _Boundary: Loader (src/troupe/config/loader.py)_
  - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7_

- [ ] 4. Path resolution and configuration history
- [ ] 4.1 (P) Implement path walking, resolution and leaf flattening
  - Walk a parsed path from the root, reporting the nodes reached, the number of segments consumed and why it stopped (complete, missing key, index out of range, non-index segment on a list, segment applied to a scalar)
  - Resolve a path strictly for reading, raising a not-found path error that names the file of the deepest existing node and PATH
  - Flatten the configuration into leaves in document order (every scalar, including text includes and nulls; list items by index; empty collections contribute nothing), with includes transparent
  - Done when tests on loaded real fixtures cover each stop reason, the file named for paths ending inside an included file, and the exact leaf order for a config with nested includes and lists
  - _Boundary: Resolve_
  - _Depends: 3.3_
  - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 6.8, 7.2_

- [ ] 4.2 (P) Implement the event-sourced history aggregate and its event types
  - Define the single history aggregate with a fixed identity and the past-tense events for history start, leaf imported, leaf value set and leaf removed
  - Define the writer-facing change records for set and remove and the history record (seq, op, path, old, new)
  - Provide the single pure mapping from a stored history event to a history record (seq = event version minus one, op name, old/new with nulls for import and remove); the store in 4.3 reuses it and does not re-implement it
  - Done when tests prove that a new aggregate followed by import, set and remove events yields versions that map to records with seq 1, 2, 3, the right op names, and `old` null for imports and `new` null for removals, and that the history-start event maps to no record
  - _Boundary: HistoryAggregate_
  - _Depends: 2.2_
  - _Requirements: 7.1, 7.3, 7.5, 9.1, 9.2_

- [ ] 4.3 Persist the history under the theatre's `.troupe/` directory and import once
  - Open an eventsourcing application backed by SQLite at `<theatre>/.troupe/history.sqlite`, configured only through explicit settings so the ambient environment cannot redirect or transform it
  - Import when no history exists: history start plus one leaf-imported event per leaf in order, saved in a single transaction; an empty configuration still counts as imported; an existing history records nothing (no reconciliation)
  - Project the stored events into history records, oldest first, by applying the event-to-record mapping from 4.2
  - Map any store failure while opening, importing or reading to a load error naming the store location
  - Provide an idempotent close that closes the eventsourcing application and its SQLite connections and never masks an error already being raised
  - Done when tests with a real store in a temporary theatre prove a second import records nothing, the records survive reopening, only `history.sqlite*` files appear under `.troupe/`, a corrupt store file yields a load error naming it, a failure injected at the SQLite boundary during import leaves no partial import, and after `close()` (called twice) no `history.sqlite-wal` or `history.sqlite-shm` remains
  - _Boundary: Store (src/troupe/config/store.py)_
  - _Depends: 4.1, 4.2_
  - _Requirements: 7.1, 7.3, 7.5, 7.6, 7.7, 7.8, 7.9, 8.1, 8.2_

- [ ] 4.4 Provide the change-recording API for the set command
  - Append one leaf-value-set event per set change and one leaf-removed event per remove change, in the given order, in one transaction; an empty change list records nothing
  - Done when tests against a real store prove consecutive seq numbers following the import, correct old/new values (including `old` null for a created leaf), no events for an empty batch, and no events at all when the batch save fails
  - _Boundary: Store (src/troupe/config/store.py)_
  - _Requirements: 9.1, 9.2, 9.3, 9.4_

- [ ] 5. Application session and CLI
- [ ] 5.1 Implement the theatre session use cases
  - Open a theatre in the fixed order locate, load (from disk), open store, ensure import, so that no state is created when locating or loading fails; if the import fails, close the store before re-raising
  - Make the theatre session a context manager whose exit closes the history store without suppressing an exception
  - Show a value by resolving PATH in the loaded files only (never the store), raising the wrong-node-kind path error naming the node's file and PATH for mappings and lists (files are the truth, product-owner decision A)
  - List history records, optionally filtered by the textual PATH prefix, keeping original seq numbers
  - Done when tests on real theatres prove a failed load leaves no `history.sqlite*`, a hand edit after import is reflected by show while history is unchanged, filtering keeps original seq values, and leaving the `with` block (normally and by an exception) closes the store
  - _Boundary: Theatre (src/troupe/config/theatre.py)_
  - _Requirements: 6.7, 6.9, 7.4, 7.9, 8.3, 8.4, 8.5_

- [ ] 5.2 (P) Implement output rendering for show and history
  - Render all output as UTF-8 bytes. Format scalars for show: strings exactly with a newline appended only when missing, `true`/`false`, `null`, decimal integers, floats via `repr()`; booleans checked before integers
  - Format one JSON line per history record with members seq, op, path, old, new in that order, booleans as JSON booleans, and infinite or NaN values as the strings `".inf"`, `"-.inf"`, `".nan"`, encoding so that invalid JSON can never be emitted
  - Done when unit tests pass for every scalar type, strings with and without a trailing newline, a CRLF-terminated string, and JSON lines whose parsed values equal the record (including `true` not `1`, and `".inf"`, `"-.inf"`, `".nan"` for non-finite floats, each line accepted by a strict JSON parser)
  - _Boundary: CliRender_
  - _Depends: 4.2_
  - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 8.2, 8.6, 8.7_

- [ ] 5.3 Wire the CLI: parsing, dispatch, error mapping and buffered output
  - Build the `troupe config` parser with `show PATH` and `history [PATH]`, a shared `--theatre DIR` option defaulting to the current directory, and a parser whose errors raise the usage error
  - Create the leaf module `src/troupe/cli/shell.py` holding `TroupeArgumentParser`, the `Handler` protocol, `SubcommandRegistrar` and `theatre_option_parent`; it imports nothing from other `troupe.cli` modules. `main.py` and any subcommand module import these names from `shell.py` (direction `shell ← subcommand modules ← main`), never shell-level names from `main.py`
  - Define the handler protocol (a callable taking the parsed arguments and an in-memory bytes buffer), register the show and history subparsers through the `SUBCOMMAND_REGISTRARS` tuple (the clearly marked registration point that config-set-writeback extends with one entry), each setting its handler via `set_defaults(handler=...)`
  - `run` parses, calls the handler with an `io.BytesIO` buffer and copies the buffer to stdout only on success; on any troupe error write the formatted message to stderr, nothing to stdout, and exit with the error's code. `run` never opens the theatre: each handler validates its own arguments (e.g. PATH) and then enters `with open_theatre(...) as theatre:` itself, so usage errors create no state and the store is closed when the handler ends
  - Owns the CLI usage test module: covers every 1.4 case (unknown subcommand, missing required argument, unknown option, surplus argument) and `--theatre` defaulting to the current directory
  - Done when `shell.py` imports no other `troupe.cli` module, `uv run troupe config show PATH --theatre DIR` and `uv run troupe config history --theatre DIR` work from the repository root on a sample theatre, each 1.4 case exits 2 with empty stdout and a usage message on stderr, and a malformed PATH creates no `history.sqlite*`
  - _Boundary: CliMain (src/troupe/cli/shell.py, src/troupe/cli/main.py)_
  - _Depends: 5.1, 5.2_
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 7.9, 10.1, 10.2, 10.3, 10.5_

- [ ] 6. End-to-end validation through the real CLI
  - Tasks 6.1–6.4 each write only their own test module (locate/loader, includes, show, history) and only use the shared helpers from 1.1; any new helper stays in that task's own module, never in the shared conftest
- [ ] 6.1 (P) Validate locating and loading errors end to end
  - Through the console script, cover `.yml` and `.yaml` theatres, neither, both, a missing theatre directory, invalid YAML, a file with non-UTF-8 bytes, duplicate keys, explicit tags, an anchor (`a: &x 1`), an alias (`a: &x 1\nb: *x`), a merge key (`<<: {a: 1}`), a non-mapping root and an empty root file
  - Done when each case asserts the exit code, empty stdout on failure and a stderr message naming the file (and key path where relevant)
  - _Boundary: tests for locate and loader_
  - _Requirements: 1.2, 2.1, 2.2, 2.3, 2.4, 3.2, 3.3, 3.4, 3.5, 3.7, 3.9, 10.2, 10.3_

- [ ] 6.2 (P) Validate includes end to end
  - Through the console script, show values from YAML includes, nested includes in other directories and text includes (byte-exact, including CRLF and a missing final newline), and trigger missing-target and cycle errors
  - Done when outputs match byte for byte, errors exit 4 naming the target, including file or cycle files, and path errors inside included files name the included file
  - _Boundary: tests for includes_
  - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 6.6, 10.4_

- [ ] 6.3 (P) Validate show output and path errors end to end
  - Through the console script, show strings (plain, quoted, literal, folded, already ending in a newline), booleans, null, integers (including octal and hex), floats (including `1e3` and `.inf`), list items by index, and core-schema strings such as `yes`
  - Cover a mapping or list target, a missing key, an out-of-range index, a non-digit segment on a list, a segment below a scalar and malformed PATH syntax (in the show test module, complementing the usage tests owned by 5.3)
  - Show a folded (`>`) value and assert stdout is the folded text with no `\x07` byte
  - Files are the truth (product-owner decision A): run `show a` on `'a: 1\n'` (imports), capture `history` output, hand-edit the file to `'a: 2\n'`, then `show a` prints `2\n` and a second `history` output is byte-identical to the first (no event for the hand edit)
  - Done when stdout bytes match the printing rules exactly, the hand-edit case passes, and every error case asserts its exit code (5 or 2), empty stdout and a stderr message naming PATH and the file
  - _Boundary: tests for show_
  - _Requirements: 1.5, 3.1, 3.8, 5.1, 5.2, 5.3, 5.4, 5.5, 6.1, 6.2, 6.3, 6.4, 6.5, 6.7, 6.8, 6.9, 7.3_

- [ ] 6.4 (P) Validate history and import behaviour end to end
  - Through the console script, prove the first `show` imports and a later `history` lists one import line per leaf with seq from 1 and `old` null; the first `history` on a fresh theatre imports; a second command imports nothing; a hand edit adds no events while `show` reflects it; prefix filtering includes `PATH` and `PATH.` descendants only; a non-matching PATH prints nothing with exit 0
  - Append set and remove changes through the recording API and prove `history` shows them with consecutive seq numbers
  - Prove the import event for a folded (`>`) leaf has `new` equal to the folded text with no `\x07`, and that `.inf`, `-.inf` and `.nan` leaves appear as `".inf"`, `"-.inf"`, `".nan"`
  - Done when each JSON line is parsed by a strict JSON parser (rejecting `Infinity`/`NaN` tokens) and asserted for seq, op, path, old and new, and boolean leaves appear as JSON booleans
  - _Boundary: tests for history_
  - _Requirements: 3.8, 6.9, 7.1, 7.2, 7.3, 7.5, 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 9.1, 9.2, 9.3_

- [ ] 6.5 Validate read-only safety and state location
  - Hash every file in a theatre with includes (including an include target outside `.troupe/`) before and after `show` and `history` runs, and list the theatre tree afterwards
  - Product-owner decision E: on a theatre whose includes live in a directory outside `.troupe/` (e.g. `file:../shared/agents.yaml`), list every file and directory of the theatre before and after each of these commands: first `show` (import), `history`, a second `show`, a usage error (malformed PATH), a path error (exit 5) and a load failure (exit 4, broken include); after each one the only new entries anywhere in the theatre are under `.troupe/` and named `history.sqlite*`, no entry appears in any include directory, and `.troupe/` holds no `history.sqlite-wal` or `history.sqlite-shm` (the store was closed)
  - Prove a run that fails to load creates no history state, and a run with a corrupt history store exits 4 naming the store
  - Done when the hashes are identical, the tree listings show no new entry outside `.troupe/` after any command, and the full gate (`uv run pytest`, `uv run mypy --strict .`, `uv run ruff check .`, `uv run ruff format --check .`) passes with its output shown
  - Runs last because it includes the full quality gate over all tests, so it is not parallel with 6.1–6.4
  - _Boundary: tests for read-only safety_
  - _Requirements: 3.6, 7.4, 7.6, 7.8, 7.9, 10.2_

## Implementation Notes
- 1.1: Test helpers live in `tests/support.py` (typed `make_theatre`/`run_cli` fixtures in `tests/conftest.py`); the subprocess runner uses the console script next to `sys.executable`, so run tests via `uv run pytest`. Ruff excludes `.kiro`/`.claude` (otherwise `ruff format` rewrites code blocks in spec markdown).
- 3.2: A `---`-only or `~` root is refused as a non-mapping root (exit 4); only empty/comment-only/whitespace-only/BOM-only files are the empty mapping. ruamel rejects `a:\tb` (tab after colon) → exit 4. Duplicate keys compare key text and typed plain values. Folded U+0007 stripping applies to folded style only.
