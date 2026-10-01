# Implementation Plan

**Notation for byte-exact cases.** A case is written ``PATH, VALUE: before → after``. PATH is shown bare.
VALUE is the **exact argv string written as a Python literal** (for example `'"0.2"'` is the 5-character
argument `"0.2"`, and `'"one\\ntwo\\n"'` is the argument `"one\ntwo\n"` with backslash-n escapes that YAML
turns into line breaks). File contents are Python `str` literals, or `bytes` literals where line endings or a
BOM matter. Unless stated otherwise the file is `.troupe/troupe.yml`. "Unchanged" means every file of the
theatre is byte-identical to before and no file was added (history state under `.troupe/history.sqlite*`
excepted).

**External prerequisites.** All config-foundation tasks, which deliver every required upstream addition
**U1–U6** (design.md, "Required Upstream Additions": U1 fold-marker stripping in the loader, requirement 3.8 /
loader task 3.2; U2 `MappingEntry.key_style`; U3 the CLI shell module `troupe.cli.shell` with `Handler`,
`SubcommandRegistrar`, `TroupeArgumentParser`, `theatre_option_parent`, plus `SUBCOMMAND_REGISTRARS` in
`troupe.cli.main`; U4 pytest `pythonpath = ["tests"]`; U5 `LeafChange`, `INCLUDE_PREFIX`, `TroupeArgumentParser`
in Shared Seams; U6 empty root style `BLOCK` and documented span semantics). Task 2.6 keeps a guard test for U1.

- [ ] 1. Foundation: package skeleton, test support and value primitives
- [x] 1.1 Create the writeback package skeleton, the shared value types and the set-test support
  - Create the writeback package and its modules as typed placeholders following the design's File Structure Plan and import direction
  - Define the parsed-value types (scalar with spelling, mapping, list) in the value module so tasks 1.2 and 1.3 share them
  - Add the set-test support module `tests/set_support.py`: a subprocess runner for any `troupe config` argv plus a `set` convenience that runs `troupe config set PATH VALUE --theatre DIR` with VALUE passed as one argv element, an assertion of a file's exact bytes, and a snapshot of a theatre tree (relative paths and bytes, ignoring `.troupe/history.sqlite*`) so tests can assert "unchanged" and "no extra file"
  - Add a generic in-process runner that calls the foundation CLI `run` function with any `troupe config` argv and captured stdout/stderr (not specific to `set`), so validation tests can inject failures at the OS or SQLite boundary
  - Build on the foundation's theatre fixture and subprocess helper; the set-test support is a separate module of this spec and the foundation's shared test configuration is not modified; unit tests under `tests/writeback/` import `set_support` relying on the foundation's pytest setting `pythonpath = ["tests"]` (config-foundation task 1.1), so `pyproject.toml` is not modified here
  - Fix the traceability convention: every test written for this spec names the requirement IDs it proves in its docstring (for example `"""Req 12.1, 12.2: ..."""`)
  - Done when `uv run mypy --strict .`, `uv run ruff check .` and `uv run ruff format --check .` pass, and smoke tests prove a snapshot of an untouched fixture theatre equals itself and both runners return exit code 2, empty stdout and a usage message on stderr for `troupe config nosuchcommand` (a non-`set` invocation, since `set` is wired only in 4.5)
  - _Boundary: writeback package skeleton, tests/set_support.py_
  - _Requirements: 13.1, 16.1_

- [x] 1.2 Parse VALUE into a typed value tree
  - Parse VALUE as one YAML 1.2 core-schema flow node: plain scalars typed by the shared core-schema function with their written spelling kept for non-strings, quoted scalars as strings, flow mappings and flow lists recursively
  - Refuse with a usage error naming PATH: invalid YAML, empty or comment-only VALUE, several documents, any block node, tags, anchors, aliases, text that cannot be encoded as UTF-8; for duplicate, empty or non-scalar keys the error also names the offending key
  - Done when unit tests prove `'0.2'`→0.2, `'true'`→True, `'null'`→None, `'abc'`→"abc", `'"0.2"'`→"0.2", `'{a: 1}'`, `'[a, b]'`, `'"line one\\nline two\\n"'`→"line one\nline two\n", `'0x1F'` keeps spelling `0x1F`, each refusal raises the usage error naming PATH, and `'{a: 1, a: 2}'`, `'{"": 1}'` and `'{[x]: 1}'` name the offending key
  - _Boundary: ValueParser_
  - _Requirements: 1.2, 1.3, 1.4_

- [x] 1.3 (P) Provide plain configuration trees, include directives, typed equality and splicing
  - Convert loaded nodes into ordered plain trees with include roots as include directives carrying their `file:` text and expanded content; convert parsed values with `file:` strings as directives without content
  - Compare scalars by type and value (floats by `repr`), directives by text and (when both known) content, mappings with or without key order
  - Splice a value at a path: replace, create missing intermediate mappings, append at index equal to length; when a mapping replaces a mapping (at the path or at any depth below it) keep the remaining existing keys in their existing order and append new keys in VALUE order
  - Wherever the splice (at the path or at any depth of a merged mapping or list) places a string over an existing text-include directive, keep the directive text and set its content through a caller-supplied content function; the line-break conversion itself stays with target resolution (3.1), so this module imports nothing from it
  - Uses the parsed-value types from 1.1; independent of the parser in 1.2
  - Done when unit tests prove `1`, `1.0`, `True` and `"1"` are pairwise different, `nan` equals `nan`, `0.0` differs from `-0.0`, a contentless directive equals a loaded include with the same text and differs from one with another text or from a plain value, order-insensitive vs ordered mapping equality, the spliced trees for replace, new nested key and append, that splicing `{port: 5432, host: b, pool: 3}` over `{host: a, port: 5432, user: x}` gives keys in the order host, port, pool, and that splicing `{t: "new\n"}` over a mapping whose `t` is a text include gives a directive with the same text and the content returned by the supplied content function (equal to the current tree when that content equals the file's current text)
  - _Boundary: PlainTree_
  - _Depends: 1.1_
  - _Requirements: 2.8, 14.6, 15.3_

- [ ] 2. Text analysis and rendering primitives
- [x] 2.1 Analyse file text: lines, columns, line breaks, indentation step and list dash offset
  - Index lines and compute code-point columns, line content ends, blank and comment lines with their indentation
  - Choose the line break for an insertion: CRLF files CRLF, LF or break-less files LF, mixed files the break of the preceding line (LF if none)
  - Detect the indentation step (first block mapping nested under a block mapping key in that file, default 2) and the list dash offset (first block list under a mapping key, 0 allowed, default the step), using only nodes written in that file
  - Done when unit tests on real fixture texts prove step 2 and 4 detection, the default 2 for a flat file, dash offsets 0 and 2, the step ignoring nesting that lies in an included file, multibyte columns, and the CRLF/LF/mixed break choice
  - _Boundary: FileLayout_
  - _Requirements: 8.2, 8.3, 12.3, 13.2_

- [x] 2.2 Analyse values in text: comments, value slots, content ends, extents and insertion points
  - Find each line's end-of-line comment after the line's last token (keys, scalars incl. quoted text containing `#`, flow collections, block headers, indicators)
  - Locate value slots from the key's `:` or the item's `-` (never from an empty scalar's span), content ends (block scalars by chomping, collections by their last entry), inline extents and body lines
  - Compute the insertion point for new entries of a block collection: below trailing comment lines indented at least as far as the entries, above less-indented comments, above blank lines not followed by such a comment, end of file for an empty root, with a leading-break flag at a file end without a final break
  - Done when unit tests prove comment detection for `'a: "x # y"  # c\n'`, empty-null slots for `'a:\n'`, `'a: # c\n'` and `'a:   \n  # c\nb: 1\n'`, block scalar content ends for clip and keep, and the insertion points of the trailing-comment, less-indented-comment, blank-line, empty-root and no-final-break fixtures
  - _Boundary: FileLayout_
  - _Requirements: 11.1, 11.2, 11.6, 12.1_

- [x] 2.3 Apply edits to file text and keep end-of-line comments at their columns
  - Apply inline and whole-line edits in position order, refusing overlapping edits with a write-refused error
  - After inline edits, restore each affected end-of-line comment to its original column, with exactly one space when the new value reaches it, touching no other line
  - Done when unit tests prove `'a: 1    # c\n'` → `'a: 100  # c\n'`, `'a: 1000 # c\n'` → `'a: 1    # c\n'`, a value reaching the column gets one space, multibyte columns are counted in code points, line insertions never move comments, and overlapping edits are refused
  - _Boundary: EditApplier_
  - _Requirements: 12.1, 12.2, 12.3, 12.4, 14.9_

- [x] 2.4 (P) Decide scalar representability and style, and render quoted and plain scalars and keys
  - Decide plain representability with a syntactic check plus a parse probe in the exact context (block value, flow value, block key, flow key); single-quoted for one-line text without control characters other than TAB
  - Take existing styles from the model, never from span text: key styles from `MappingEntry.key_style`, value styles from `ConfigScalar.style`, narrowed to a quote style by `quote_style_of(style: ScalarStyle | None) -> QuoteStyle | None` (literal/folded, and `None` such as a text-include sibling's style, give no preferred quote style)
  - Choose styles: keep a representable existing style, else plain, single, double; start from a sibling style for new keys/values; non-strings plain with their VALUE spelling; strings starting with `file:` treated like any other string
  - Render double-quoted text escaping only `\`, `"` and control characters (named escapes where they exist, else `\xHH`), single-quoted text with `''`
  - Done when unit tests prove the representability table (`0.2`, `true`, `~`, `a: b`, ` x`, `a #b`, `x,y` in flow, `a\tb`), the escape table for every control character including `\N` for U+0085, literal `é` and U+2028, the fallback chain from each style, and `file:inc.yaml` rendered plain
  - _Boundary: ScalarRender_
  - _Depends: 1.1_
  - _Requirements: 2.7, 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 7.1, 7.2, 7.3_

- [x] 2.5 (P) Render literal blocks, chomping and greedy word wrapping
  - Decide whether a string fits a literal block, the required chomping (strip, clip, keep) and whether an existing chomping still fits
  - Render literal content at a given indentation and line break, empty lines as bare breaks, extra trailing breaks for keep
  - Wrap a paragraph greedily by word at a width, breaking only at single spaces, a longer word on its own line, no wrapping without a width
  - Done when unit tests prove `"one\ntwo\n"` → header `|`, `"one\ntwo"` → `|-`, `"one\n\n"` → `|+` with one extra empty line, CRLF content lines, the fit refusals (control character, U+2028, U+2029, first line starting with a space), and wrapping of `"Second para changed now and longer."` at 17 into three lines and `"aaaaaaaa bb"` at 5 into `aaaaaaaa` / `bb`
  - _Boundary: BlockScalar_
  - _Depends: 1.1_
  - _Requirements: 9.1, 9.2, 9.7, 10.2, 10.5_

- [x] 2.6 Rewrite existing literal and folded block scalars
  - Rewrite a literal block's content at its existing indentation, keeping its indicator, indentation indicator and header comment and changing chomping only when required
  - Rewrite a folded block paragraph by paragraph: align old and new paragraphs, keep bytes of unchanged paragraphs and their separators, re-wrap changed paragraphs at their counterpart's width and new ones at the block's widest line
  - Refuse a folded change that needs a more-indented line, and any change of a folded block that already has more-indented lines
  - Start with a guard test that loads `'t: >\n  a\n  b\n'` through the foundation loader and expects `'a b\n'` (U1); if it fails, stop and report to config-foundation instead of working around it
  - Done when the U1 guard passes and unit tests on real block texts produce the exact edits for a header-comment block, a chomping change, an unchanged-plus-changed folded value, an inserted paragraph, and the two folded refusals
  - _Boundary: BlockScalar_
  - _Depends: 2.2, 2.3, 2.5_
  - _Requirements: 9.4, 9.5, 10.1, 10.2, 10.3, 10.4, 10.6, 10.7_

- [x] 2.7 Render new collections and new entries in block and flow style
  - Render block mapping entries and list items at a column with nested mappings one step deeper, nested lists at the dash offset, list-item collections compact, multi-line strings as literal blocks, empty collections as `{}`/`[]`
  - Render flow entries and flow collections with `, ` and `: `, multi-line strings double-quoted
  - Apply sibling styles only to the first-level new key and value; everything nested uses the minimal style
  - Done when unit tests prove `{d: 1, e: [p, q]}` at step 4 with dash offset 4, the same at step 2 with dash offset 0, a compact list-item mapping, a flow rendering of a nested value, and minimal quoting inside a new collection under a double-quoted sibling
  - _Boundary: CollectionRender_
  - _Depends: 2.4, 2.5_
  - _Requirements: 7.4, 8.1, 8.4, 8.5, 8.6, 9.1, 9.2, 9.3_

- [ ] 3. Target resolution and patch planning
- [x] 3.1 Resolve write targets, path errors and the intended configuration
  - Map the path walk to replace, add-key and append targets; raise path-not-found errors naming PATH and the deepest node's file for missing paths below scalars and nulls, non-index segments on lists, indexes beyond the length, segments after an appended index, and digit-only keys to be created
  - Own the text-include content rule: VALUE exactly, with every line feed not already preceded by a carriage return written as CRLF when the target file uses CRLF; supply it to the splice from 1.3 so it applies at PATH and at any depth inside a replaced collection
  - Compute the intended configuration with include directives; at a text-include target (at PATH or nested) keep the directive and set its content through that rule
  - Done when unit tests on loaded fixtures prove each target kind, each exit 5 case with its named file (including a path ending inside an included file), CRLF conversion only for CRLF targets, that `p, '{t: "new\\n", n: 1}'` on `'p:\n  t: file:t.txt\n  n: 1\n'` with t.txt `b'old\r\n'` yields an intended `t` content of `"new\r\n"`, and that setting an include root to its own `file:` string yields an intended configuration equal to the current one
  - _Boundary: TargetResolver_
  - _Requirements: 2.1, 2.2, 2.3, 2.6, 2.8, 3.1, 3.2, 3.4, 3.5, 3.6, 4.1, 4.2, 4.3, 16.3_

- [x] 3.2 Plan in-place changes of existing values
  - Replace existing plain/quoted/empty scalars in their slot with a single-line value in the chosen style (multi-line strings double-quoted inside flow parents), and rewrite existing literal/folded blocks with string values that still fit them
  - Replace a text-include target's whole content with the text produced by the content rule of 3.1 (CRLF-converted for CRLF targets), wherever the slot change reaches it; refuse YAML include roots that would change (including to a different `file:` string) and non-string values for text includes
  - Collect edits per file and apply them into one file patch per changed file (multi-file plans are completed and tested in 3.6)
  - Done when planner tests on loaded fixtures produce the exact new texts for `a, 'yy': "a: 'x'  # c\n"` → `"a: 'yy' # c\n"`, a scalar change inside an included file, `a, '1': 'a:\n'` → `'a: 1\n'`, a multi-line string in a flow list, `a, 'file:inc.yaml': 'a: x\n'` → `'a: file:inc.yaml\n'`, a literal block rewrite, a text-include rewrite of a CRLF target (`prompt, '"new\\ntext\\n"'` with prompt.txt `b'old\r\n'` → `b'new\r\ntext\r\n'`), and refusals for an include root and a non-string text include
  - _Boundary: PatchPlanner_
  - _Requirements: 2.1, 2.4, 2.5, 2.6, 2.7, 6.1, 6.2, 9.3, 13.1_

- [x] 3.3 Plan new keys, intermediate mappings and list appends
  - Insert new block entries at the collection's insertion point and entries column (root column 0) with sibling styles, adding a leading break at a file end without one
  - Append new flow entries after the last entry on its line (after the opening bracket when empty), keeping trailing commas and closing brackets
  - Build the nested value for missing intermediate mappings and render it at the first missing key
  - Done when planner tests produce the exact texts for a new key under trailing comments, intermediates `a.p.q`, a key in an empty root, a block and a flow append, a flow append with a trailing comma, and a new entry in a file without a final line break
  - _Boundary: PatchPlanner_
  - _Requirements: 3.1, 3.2, 3.3, 4.1, 7.1, 7.2, 11.1, 11.3, 11.6_

- [x] 3.4 Plan replacements that change the kind or block form of a value
  - Replace a slot's inline extent and body lines: scalars or empty collections over block collections, block collections over scalars or other collections, literal blocks over plain or quoted scalars, plain non-strings over block scalars, double-quoted strings over block scalars that cannot hold them, flow renderings inside flow parents
  - Keep the key line through its end-of-line comment when a scalar becomes a block collection, removing the spaces after `:` when no comment follows; write list-item collections compactly unless the item line has a comment
  - Done when planner tests produce the exact texts for `a, '{x: 1}': 'a: 5  # note\nb: 1\n'` → `'a:    # note\n  x: 1\nb: 1\n'`; `a, '{x: 1}': 'a: 5\nb: 1\n'` → `'a:\n  x: 1\nb: 1\n'`; `a, '"one\\ntwo\\n"': 'a: x  # c\n'` → `'a: |  # c\n  one\n  two\n'`; `a, '5': 'a: |\n  t\nb: 1\n'` → `'a: 5\nb: 1\n'`; `a, '"  x\\ny\\n"': 'a: |\n  t\nb: 1\n'` → `'a: "  x\\ny\\n"\nb: 1\n'`; a block mapping replaced by `5` and by `{}`; a list replaced by a mapping
  - _Boundary: PatchPlanner_
  - _Requirements: 5.5, 5.6, 9.1, 9.2, 9.6, 9.7, 11.4, 11.5_

- [x] 3.5 Plan entry-wise merges and deletions
  - Merge mapping over mapping and list over list: unchanged entries untouched, differing entries through the slot change, new entries through one insertion, absent entries deleted
  - Delete block entries as whole lines through their last content line (refusing entries that do not start their line), flow entries with one separator on one line (refusing multi-line ranges), and re-render a flow collection whose entries are all replaced
  - Done when planner tests produce `db, '{host: b, port: 5432, pool: 3}': 'db:\n  host: a   # primary\n  # port comment\n  port: 5432\n  user: x\n'` → `'db:\n  host: b   # primary\n  # port comment\n  port: 5432\n  pool: 3\n'`; `l, '[a, z]': 'l:\n  - a  # first\n  - b\n  - c\n'` → `'l:\n  - a  # first\n  - z\n'`; `m, '{a: 1, c: 4}': 'm: {a: 1, b: 2, c: 3}\n'` → `'m: {a: 1, c: 4}\n'`; `m, '{b: 2}': 'm: {a: 1, b: 2}\n'` → `'m: {b: 2}\n'`; `m, '{x: 1}': 'm: {a: 1, b: 2}\n'` → `'m: {x: 1}\n'` (fixtures reused verbatim by 5.7); and a refusal for a compact first key `l.0, '{b: 2}': 'l:\n  - a: 1\n    b: 2\n'`
  - _Boundary: PatchPlanner_
  - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.7, 14.9_

- [x] 3.6 Plan includes inside replaced collections and multi-file patches
  - Inside a merged collection: refuse when a YAML include root would change, naming the path of that value (the nested path, not PATH) and the included file; remove only the directive entry when an included value is dropped; change a nested text include's content through the slot change of 3.2
  - Return one file patch per changed file, in the order the files appear in the loaded document (root file first), never two patches for the same file (product-owner decision B: a set that must change several files is carried out, not refused)
  - Done when planner tests prove `agents, '{coder: {model: z}, lead: x}'` on `'agents:\n  coder: file:coder.yaml\n  lead: x\n'` (coder.yaml `'model: y\n'`) is refused naming `agents.coder` and coder.yaml, `agents, '{lead: y}'` changes only the root file to `'agents:\n  lead: y\n'`, `p, '{t: "new\\n", n: 1}'` on `'p:\n  t: file:t.txt\n  n: 1\n'` with t.txt `b'old\r\n'` patches only t.txt to `b'new\r\n'`, and the two-file plan `p, '{t: new, n: 2}'` on the same root with t.txt `'old\n'` yields exactly two patches, in this order, with byte-exact new texts: troupe.yml `'p:\n  t: file:t.txt\n  n: 2\n'` and t.txt `'new'`
  - _Boundary: PatchPlanner_
  - _Requirements: 2.2, 2.3, 5.8, 5.9, 13.6_

- [ ] 4. Verification, effects and wiring
- [x] 4.1 (P) Verify a patch by re-loading the configuration in memory
  - Re-load with every current file overridden (original texts plus the patched texts of all patches at once); targets of newly written includes are read by the loader from disk
  - Compare the read-back with the intended configuration including key order and include directives; map load errors (including a missing target or cycle of a newly written include) and mismatches to write-refused errors naming the (first) patched file, PATH and, for load errors, the loader's message naming the target (refusal confirmed by the product owner, decision C)
  - Done when unit tests prove a correct patch returns the re-loaded document, two patches (troupe.yml and t.txt of the 3.6 two-file plan) verify together, a newly written include with an existing target verifies, and an unparsable patch, a value mismatch (a file included at two places), a missing include target and an include cycle each raise the write-refused error with PATH and (where applicable) the target in the message
  - _Boundary: WriteVerifier_
  - _Depends: 1.3, 3.1, 3.2_
  - _Requirements: 13.6, 14.1, 14.2, 14.8_

- [x] 4.2 (P) Derive leaf changes for the history
  - Diff the leaves of the documents before and after: set changes for new or changed leaves in new document order, then remove changes in old document order, with typed comparison
  - The before document is always the configuration loaded from disk for this command, never the history (product-owner decision A)
  - Done when unit tests on loaded fixture pairs prove the exact change list for a changed leaf, a created leaf with `old` null, removed leaves, a type change `1`→`"1"`, no changes between `{}` and `[]`, and the leaves of a newly embedded YAML include (set events) and of a new text include (one string leaf)
  - _Boundary: LeafDiff_
  - _Depends: 1.3_
  - _Requirements: 15.1, 15.2, 15.3, 15.5, 15.7_

- [x] 4.3 (P) Write user files atomically
  - Write through symlinks to the real file, via an exclusively created hidden temporary file in the same directory with the target's permission bits, flushed and synced, then renamed over the target; remove the temporary file on any failure and raise a write-refused error naming the file
  - Provide restore of a file's original text through the same path
  - Done when tests in a temporary directory prove the new bytes (with a BOM kept), the kept permission bits, the kept symlink, a directory listing identical to before apart from the target's content after success, a directory listing identical to before (no leftover temporary file) after a failing rename injected at the OS boundary and after an unwritable directory (product-owner decision E), and an unchanged target after each failure
  - _Boundary: AtomicWriter_
  - _Depends: 1.1_
  - _Requirements: 13.3, 13.4, 13.5, 14.3, 14.4, 14.5_

- [x] 4.4 Implement the set use case
  - Resolve on the document loaded from disk, return early for a no-op, plan all patches, verify them together, diff, write every patch atomically in order, then record the changes in one batch; nothing is written before every patch is verified
  - When a write fails, restore the already-written files in reverse order, record nothing and re-raise the write-refused error with `path=str(PATH)` added, keeping the failed file and the reason, so every exit 3 names PATH, the file and why
  - When recording fails, restore every written file in reverse order and re-raise the store error (exit 4)
  - When a restore itself fails, attempt the remaining restores, keep the original error and exit code (3 or 4) and name every file that could not be restored in its problem (the error's file stays the failed file or the store)
  - Tests call the use case directly with a theatre opened through the foundation session (the CLI is wired only in 4.5)
  - Done when tests on real theatres prove a no-op writes nothing and records nothing, a successful single-file set writes one file and records the diff, the two-file set of 3.6 writes both files byte-exactly and records both leaves, a refusal leaves files and history untouched, a failure of the second write injected at the OS boundary (the restore call succeeds) restores the first file byte-identically, raises a write-refused error naming PATH, t.txt and the reason and records no event, the same with the restore also failing names troupe.yml as not restored and keeps exit code 3, a failure injected at the SQLite boundary during recording leaves both files byte-identical to before, an additionally injected restore failure yields a message naming both the store and the file, and after every one of these outcomes the listing of each directory holding a written file equals the listing before (product-owner decision E)
  - _Boundary: SetCommand_
  - _Depends: 4.1, 4.2, 4.3_
  - _Requirements: 13.4, 13.6, 14.4, 14.5, 14.6, 14.7, 14.10, 15.4, 15.6, 16.2_

- [x] 4.5 Register and handle the `set` subcommand
  - Create the new module `src/troupe/cli/set_command.py`, importing `TroupeArgumentParser`, `Handler`, `SubcommandRegistrar` and `theatre_option_parent` from `troupe.cli.shell` and never from `troupe.cli.main` (import chain `troupe.cli.shell ← set_command ← troupe.cli.main`; the writeback core never imports `troupe.cli`), with `add_set_subparser(subparsers)`, which adds `set PATH VALUE` with the shared `--theatre` option and registers `set_defaults(handler=handle_set)`, and `handle_set(args: argparse.Namespace, out: io.BytesIO) -> None` matching the foundation's `Handler` protocol
  - `handle_set` parses PATH and VALUE first (`ConfigPath.parse(args.path)`, then `parse_value(args.value, path)`), then enters `with open_theatre(args.theatre) as theatre:` itself (so the history store is closed when the command ends, config-foundation 7.9), runs the use case inside the block and writes nothing to `out` on success
  - The only change to `src/troupe/cli/main.py` is one added entry, `add_set_subparser`, in the foundation's marked registration list `SUBCOMMAND_REGISTRARS`; the existing `show`/`history` parsing, dispatch and error mapping are reused unchanged
  - Done when `uv run troupe config set a 2 --theatre DIR` changes `'a: 1\n'` to `'a: 2\n'` with exit 0 and empty stdout from the repository root, the first set on a fresh theatre leaves the import events in the history, missing VALUE, a surplus argument, malformed PATH and an invalid VALUE exit 2 with empty stdout and create no `.troupe/history.sqlite*`, and an include-root refusal exits 3 with stderr `<file>: <PATH>: <reason>`
  - _Boundary: CliSet (src/troupe/cli/set_command.py, one SUBCOMMAND_REGISTRARS entry in src/troupe/cli/main.py)_
  - _Requirements: 1.1, 1.5, 1.6, 16.1, 16.2, 16.4_

- [ ] 5. End-to-end byte-exact validation through the real CLI
  - Each task writes only its own test module and uses only the shared set-test support (subprocess runner, or the in-process runner where a boundary failure must be injected); every test names its requirement IDs in its docstring; every success case asserts exit 0, empty stdout, the exact bytes of the changed file and that all other files are unchanged; every failure asserts the exit code, empty stdout, the stderr naming PATH and/or file, and that the theatre is unchanged
- [x] 5.1 (P) Validate invocation, VALUE parsing, import and error reporting
  - Contract examples on `'a: x\n'`: `a, '0.2'` → `'a: 0.2\n'`; `a, 'true'` → `'a: true\n'`; `a, 'null'` → `'a: null\n'`; `a, 'abc'` → `'a: abc\n'`; `a, '"0.2"'` → `"a: '0.2'\n"`; `a, '{a: 1}'` → `'a:\n  a: 1\n'`; `a, '[a, b]'` → `'a:\n  - a\n  - b\n'`; `a, '"line one\\nline two\\n"'` → `'a: |\n  line one\n  line two\n'`
  - Usage errors (exit 2, no history state created): VALUE `''`, `'# c'`, `'a: 1'`, `'- a'`, `'!!str 5'`, `'&x 1'`, `'{a: 1, a: 2}'` (stderr names key `a`), missing VALUE, a surplus argument, the unknown option `--bogus`, PATH `'a..b'`; a broken YAML file exits 4 naming it
  - Fresh-theatre import: a successful set, an exit 3 set (`agents.coder, '{model: x}'` on an include root) and an exit 5 set (`l.5, 'x'` on `'l: [a]\n'`) each leave `history` listing the import events, with no set event for the refused ones
  - Done when every case passes with the exact bytes or exit code and message described
  - _Boundary: tests for set CLI_
  - _Depends: 1.1, 4.5_
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 16.1, 16.4_

- [ ] 5.2 (P) Validate where the change is written, including new includes
  - Root `'model: gpt\nagents:\n  coder: file:agents/coder.yaml\n'` with `.troupe/agents/coder.yaml` `'model: small\ntemperature: 0.2\n'`: `agents.coder.model, 'large'` changes only coder.yaml to `'model: large\ntemperature: 0.2\n'`; `agents.coder.top_p, '0.9'` gives coder.yaml `'model: small\ntemperature: 0.2\ntop_p: 0.9\n'`; `agents.coder, '{model: x}'` and `agents.coder, 'file:agents/other.yaml'` exit 3 naming coder.yaml
  - Text include `'prompt: file:prompt.txt\n'`, prompt.txt `b'old\r\nline\r\n'`: `prompt, '"new\\ntext\\n"'` → prompt.txt `b'new\r\ntext\r\n'`; with prompt.txt `b'old\n'` the same set gives `b'new\ntext\n'`; `prompt, '5'` exits 3 naming prompt.txt
  - New keys: `a.y, '2': 'a:\n  x: 1\nb: 2\n'` → `'a:\n  x: 1\n  y: 2\nb: 2\n'`; `a.p.q, '1'` on the same → `'a:\n  x: 1\n  p:\n    q: 1\nb: 2\n'`; `a, '1': '# config\n'` → `'# config\na: 1\n'`; `a, '1': ''` → `'a: 1\n'`
  - New includes: `b, 'file:inc.yaml': 'a: 1\n'` with `.troupe/inc.yaml` `'k: 1\n'` → `'a: 1\nb: file:inc.yaml\n'` and inc.yaml unchanged; `b, 'file:notes.txt'` with notes.txt `'hi\n'` → `'a: 1\nb: file:notes.txt\n'`; `b, 'file:missing.yaml'` exits 3 naming `b` and `missing.yaml`; `b, 'file:loop.yaml'` with loop.yaml `'back: file:troupe.yml\n'` exits 3 naming the cycle files
  - Appends and exit 5: `l.2, 'c': 'l:\n  - a\n  - b\n'` → `'l:\n  - a\n  - b\n  - c\n'`; `l.3, 'c'`, `l.2.x, '1'`, `l.x, '1'`, `n.k, '1'` on `'n:\n'`, `s.k, '1'` on `'s: 5\n'` and `m.0, 'x'` on `'m:\n  k: v\n'` all exit 5 naming PATH and the file
  - Done when every case passes byte-exactly
  - _Boundary: tests for set targets_
  - _Depends: 1.1, 4.5_
  - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 4.1, 4.2, 4.3, 14.8, 16.3_

- [ ] 5.3 (P) Validate scalar quoting and sibling styles
  - Changed scalars: `a, 'y': "a: 'x'\n"` → `"a: 'y'\n"`; `a, 'é"q': 'a: "x"\n'` → `'a: "é\\"q"\n'`; `a, '"0.2"': 'a: x\n'` → `"a: '0.2'\n"`; `a, '5': "a: 'x'\n"` → `'a: 5\n'`; `a, '0x1F': 'a: x\n'` → `'a: 0x1F\n'`; `a, '"it\'s: here"': 'a: x\n'` → `"a: 'it''s: here'\n"`; `a, '"bell\\a"': 'a: x\n'` → `'a: "bell\\a"\n'`; `a, '"\\N"': 'a: "x"\n'` → `'a: "\\N"\n'`; `l.0, 'x,y': 'l: [a, b]\n'` → `"l: ['x,y', b]\n"`
  - Sibling styles: `c, '3': '"a": 1\n'` → `'"a": 1\n"c": 3\n'`; `b, 'y': 'a: "x"\n'` → `'a: "x"\nb: "y"\n'`; `l.1, 'b': "l:\n  - 'a'\n"` → `"l:\n  - 'a'\n  - 'b'\n"`; `b, '"q"': 'a:\n  x: 1\n'` → `'a:\n  x: 1\nb: q\n'`; `c, '{k: "v"}': 'a: "x"\n'` → `'a: "x"\nc:\n  k: v\n'`
  - Done when every case passes byte-exactly
  - _Boundary: tests for set quoting_
  - _Depends: 1.1, 4.5_
  - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 7.1, 7.2, 7.3, 7.4_

- [ ] 5.4 (P) Validate new collections, indentation and placement
  - Indentation: `c, '{d: 1, e: [p, q]}': 'a:\n    x: 1\nb: 2\n'` → `'a:\n    x: 1\nb: 2\nc:\n    d: 1\n    e:\n        - p\n        - q\n'`; `n, '[x, y]': 'l:\n- a\nm: 1\n'` → `'l:\n- a\nm: 1\nn:\n- x\n- y\n'`; `b, '{c: 1}': 'a: 1\n'` → `'a: 1\nb:\n  c: 1\n'`; `l.1, '{n: 1, m: 2}': 'l:\n  - a\n'` → `'l:\n  - a\n  - n: 1\n    m: 2\n'`
  - Placement: `a.y, '2': 'a:\n  x: 1\n  # end of a\n\n# about b\nb: 2\n'` → `'a:\n  x: 1\n  # end of a\n  y: 2\n\n# about b\nb: 2\n'`; `a.y, '2': 'a:\n  x: 1\n# about b\nb: 2\n'` → `'a:\n  x: 1\n  y: 2\n# about b\nb: 2\n'`; `b, '2': 'a: 1\n# tail\n'` → `'a: 1\n# tail\nb: 2\n'`
  - Flow: `a.y, '[1, 2]': 'a: {x: 1}  # c\n'` → `'a: {x: 1, y: [1, 2]} # c\n'`; `l.2, 'c': 'l: [a, b,]\n'` → `'l: [a, b, c,]\n'`; `m.k, 'v': 'm: {}\n'` → `'m: {k: v}\n'`; `m.b, '"l1\\nl2\\n"': 'm: {a: 1}\n'` → `'m: {a: 1, b: "l1\\nl2\\n"}\n'`
  - Scalar to block collection: `a, '{x: 1}': 'a: 5  # note\nb: 1\n'` → `'a:    # note\n  x: 1\nb: 1\n'`; `a, '{x: 1}': 'a: 5\nb: 1\n'` → `'a:\n  x: 1\nb: 1\n'`; `a, '[x]': 'a:\nb: 1\n'` → `'a:\n  - x\nb: 1\n'`
  - Done when every case passes byte-exactly
  - _Boundary: tests for set collections_
  - _Depends: 1.1, 4.5_
  - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 11.1, 11.2, 11.3, 11.4, 11.5_

- [ ] 5.5 (P) Validate literal and folded block scalars
  - Literal: `a, '"one\\ntwo\\n"': 'a: x\n'` → `'a: |\n  one\n  two\n'`; `a, '"one\\ntwo"'` on the same fixture → `'a: |-\n  one\n  two\n'`; `a, '"one\\n\\n"'` on the same fixture → `'a: |+\n  one\n\n'`; `b, '"l1\\nl2"': 'a: 1\n'` → `'a: 1\nb: |-\n  l1\n  l2\n'`; `a, '"new\\nlines\\n"': 'a: |  # hdr\n  old\nb: 1\n'` → `'a: |  # hdr\n  new\n  lines\nb: 1\n'`; `a, '"new"': 'a: |\n  old\n'` → `'a: |-\n  new\n'`; `a, '"x\\ny"': 'a: |-\n    deep\n'` → `'a: |-\n    x\n    y\n'`; `a, '5': 'a: |\n  t\nb: 1\n'` → `'a: 5\nb: 1\n'`; `a, '"  indented\\nx\\n"': 'a: x\n'` → `'a: "  indented\\nx\\n"\n'`; `a, '"  x\\ny\\n"': 'a: |\n  t\nb: 1\n'` → `'a: "  x\\ny\\n"\nb: 1\n'`
  - Folded fixture F = `'text: >\n  The quick brown fox\n  jumps over.\n\n  Second para here.\nnext: 1\n'`: `text, '"The quick brown fox jumps over.\\nSecond para changed now and longer.\\n"'` → `'text: >\n  The quick brown fox\n  jumps over.\n\n  Second para\n  changed now and\n  longer.\nnext: 1\n'`; `text, '"The quick brown fox jumps over.\\nSecond para here.\\nExtra paragraph text that wraps.\\n"'` → `'text: >\n  The quick brown fox\n  jumps over.\n\n  Second para here.\n\n  Extra paragraph\n  text that wraps.\nnext: 1\n'`; `text, '"The quick brown fox jumps over.\\n  indented\\n"'` exits 3; `t, '"x\\n"'` on `'t: >\n  a\n    more\n  b\n'` exits 3
  - Done when every case passes byte-exactly
  - _Boundary: tests for set block scalars_
  - _Depends: 1.1, 4.5_
  - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7, 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7_

- [ ] 5.6 (P) Validate end-of-line comment columns
  - `a, '100': 'a: 1    # c\nb: 2\n'` → `'a: 100  # c\nb: 2\n'`; `a, '1234567'` on the same → `'a: 1234567 # c\nb: 2\n'`; `a, '1': 'a: 1000 # c\n'` → `'a: 1    # c\n'`; `name, 'ééé': 'name: é   # c\n'` → `'name: ééé # c\n'`; `a, 'z': 'a: "x # y"  # c\n'` → `'a: "z"      # c\n'`; `a, '22': 'a: 1   # x\nb: 2   # y\n'` → `'a: 22  # x\nb: 2   # y\n'`
  - Done when every case passes byte-exactly
  - _Boundary: tests for set comments_
  - _Depends: 1.1, 4.5_
  - _Requirements: 12.1, 12.2, 12.3, 12.4_

- [ ] 5.7 (P) Validate merges, deletions and kind changes
  - The five merge fixtures of task 3.5 through the CLI, byte-exactly, plus `db, '{port: 5432, host: b, pool: 3}'` on the first fixture giving the same bytes as `db, '{host: b, port: 5432, pool: 3}'`, and `p, '{t: "new\\n", n: 1}'` on `'p:\n  t: file:t.txt\n  n: 1\n'` with t.txt `'old\n'` changing only t.txt to `'new\n'`, and the same set with t.txt `b'old\r\n'` changing only t.txt to `b'new\r\n'`
  - Two-file set (product-owner decision B): `p, '{t: new, n: 2}'` on the same root with t.txt `'old\n'` exits 0, changes troupe.yml to `'p:\n  t: file:t.txt\n  n: 2\n'` and t.txt to `'new'` byte-exactly, leaves every other file unchanged, and `history` then shows `set p.t` (old `"old\n"`, new `"new"`) and `set p.n` (old 1, new 2) with consecutive seq
  - Kind changes: `db, '5': 'db:\n  host: a\n  # trailing\nnext: 1\n'` → `'db: 5\n  # trailing\nnext: 1\n'`; `db, '{}': 'db:\n  host: a\nnext: 1\n'` → `'db: {}\nnext: 1\n'`; `x, '{k: v}': 'x:\n  - a\n'` → `'x:\n  k: v\n'`
  - Refusals and includes (each exit 3 asserts stderr names PATH, the file and the reason): `l.0, '{b: 2}': 'l:\n  - a: 1\n    b: 2\n'` exits 3; on root `'agents:\n  coder: file:coder.yaml\n  lead: x\n'` with coder.yaml `'model: y\n'`, `agents, '{coder: {model: z}, lead: x}'` exits 3 naming `agents.coder` and coder.yaml, and `agents, '{lead: y}'` → `'agents:\n  lead: y\n'` with coder.yaml unchanged
  - Done when every case passes byte-exactly
  - _Boundary: tests for set replacements_
  - _Depends: 1.1, 4.5_
  - _Requirements: 2.2, 2.3, 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8, 5.9, 13.6, 14.9, 15.1, 15.5, 16.2_

- [ ] 5.8 (P) Validate byte preservation, line endings, encoding and file identity
  - `b.d, '"x\\ny\\n"': b'a: 1\r\nb:\r\n  c: 2\r\n'` → `b'a: 1\r\nb:\r\n  c: 2\r\n  d: |\r\n    x\r\n    y\r\n'`; `c, '3': b'a: 1\r\nb: 2\n'` → `b'a: 1\r\nb: 2\nc: 3\n'`; `c, '3': 'a: 1\nb: 2'` → `'a: 1\nb: 2\nc: 3'`; `a, '2': b'\xef\xbb\xbfa: 1\n'` → `b'\xef\xbb\xbfa: 2\n'`
  - Rich fixture `'# Theatre\n\nname: "Main stage"   # display\nagents:\n  coder:\n    model: \'gpt\'\n    tools: [read, write]\n  # reviewer next\n  reviewer: {model: x}\nzeta: café\n'`: `agents.coder.model, 'claude'` yields exactly the fixture with `'gpt'` replaced by `'claude'`; the same set on a `troupe.yaml` theatre gives the same bytes
  - A symlinked `troupe.yml` stays a symlink with the change in its target; permission bits `0o640` are kept; untouched include files keep their bytes
  - Done when every case passes byte-exactly and the tree snapshot shows no added, removed or renamed file
  - _Boundary: tests for set preservation_
  - _Depends: 1.1, 4.5_
  - _Requirements: 13.1, 13.2, 13.3, 13.4, 13.5_

- [ ] 5.9 (P) Validate write safety, no-ops and refusals
  - No-ops (exit 0, files and history unchanged): `a, '1.00': 'a: 1.0\n'`; `agents.coder, 'file:agents/coder.yaml'` on root `'agents:\n  coder: file:agents/coder.yaml\n'` with `.troupe/agents/coder.yaml` `'model: small\n'`; `p, '{t: "old\\n"}'` on `'p:\n  t: file:t.txt\n'` with t.txt `'old\n'`
  - Refusals (files and history unchanged; confirmed by the product owner, decision C): `x.k, '2'` on `'x: file:shared.yaml\ny: file:shared.yaml\n'` with shared.yaml `'k: 1\n'` exits 3 naming shared.yaml; `b, 'file:missing.yaml'` exits 3 naming the target
  - Write failure: an include target in a read-only directory exits 3 naming it, unchanged, with no temporary file left (skipped when running as root)
  - Multi-file rollback (product-owner decision B), with the in-process runner from 1.1: the two-file set `p, '{t: new, n: 2}'` on `'p:\n  t: file:t.txt\n  n: 1\n'` with t.txt `'old\n'`, with the second `os.replace` call failing at the OS boundary (the restore succeeds), exits 3 naming PATH and t.txt, leaves troupe.yml and t.txt byte-identical to before, leaves no temporary file, and `history` shows no `set` event
  - No extra file (product-owner decision E): for a successful set, the verification refusal, the write failure and the multi-file rollback above, the listing of every directory holding a file the set wrote or would write is identical to before apart from the written files' contents
  - Done when every case passes, each exit 3 stderr names PATH, the file concerned and the reason, and each refusal or failure leaves the theatre snapshot and the history output identical to before
  - _Boundary: tests for set safety_
  - _Depends: 1.1, 4.5_
  - _Requirements: 2.8, 13.6, 14.2, 14.3, 14.4, 14.5, 14.6, 14.7, 14.8, 16.2_

- [ ] 5.10 (P) Validate the history of sets
  - First set on a fresh theatre: `history` lists the import events, then the set event; a new key records `old` null; the mapping merge fixture of 3.5 records `set db.host`, `set db.pool`, then `remove db.user` with consecutive `seq`
  - Typed changes `a, '"1"'` then `a, '1.0'` on `'a: 1\n'` each record a set event with JSON-typed values; `x, '[]': 'x: {}\n'` changes the file and records nothing; a no-op and a refused set record nothing
  - New includes: `b, 'file:inc.yaml'` on `'b: x\n'` with inc.yaml `'k: 1\nl: [p]\n'` records `set b.k` (old null, new 1), `set b.l.0` (new "p"), then `remove b` (old "x"); `c, 'file:notes.txt'` with notes.txt `'hi\n'` records one `set c` with new `"hi\n"`
  - Files are the truth (product-owner decision A): on `'a: 1\n'`, a first command imports; then hand-edit the file to `'a: 5\n'`; `history` shows no event for the hand edit; `a, '7'` then records exactly one `set a` event with `old` 5 (the hand-edited value, not the imported 1) and `new` 7
  - Using the in-process runner from 1.1, a store failure injected at the SQLite boundary while recording (the recorder's insert raises) restores the file byte-identically, exits 4 naming `history.sqlite`, leaves stdout empty and leaves the directory listing unchanged (no temporary file, product-owner decision E)
  - Done when each JSON line is parsed and asserted for seq, op, path, old and new
  - _Boundary: tests for set history_
  - _Depends: 1.1, 4.5_
  - _Requirements: 15.1, 15.2, 15.3, 15.4, 15.5, 15.6, 15.7_

- [ ] 5.11 Run the full quality gate and check traceability
  - Run `uv run pytest`, `uv run mypy --strict .`, `uv run ruff check .` and `uv run ruff format --check .` and show the output
  - Check that every requirement ID of this spec is named by at least one test (docstring or marker) and every set test maps to a requirement
  - Done when all four commands pass and the traceability check lists no missing requirement
  - _Boundary: whole spec (verification only; fixes go back to the owning task)_
  - _Requirements: 13.1, 14.7, 16.2_

## Implementation Notes
- (from config-foundation validation) In `src/troupe/cli/set_command.py`, quote the `argparse._SubParsersAction[TroupeArgumentParser]` annotation (or use `from __future__ import annotations`): unquoted, it raises `TypeError` at import on Python 3.12 and would break the whole CLI (see `src/troupe/cli/main.py` for the quoted form).
- (from config-foundation 5.3) argparse treats PATH/VALUE arguments starting with `-` such as `-1e3`, `-.inf`, `-x` as options (exit 2); `-1`/`-0.5` pass. `set` must accept such VALUEs (e.g. register PATH/VALUE so leading-dash values are taken as positionals) and test them through the CLI. `--theatre` is accepted only after the subcommand.
- (from config-foundation) Test helpers: `tests/support.py` (`make_theatre`, `run_cli` fixtures in `tests/conftest.py`); run tests via `uv run pytest`. Never restore files with `git checkout` during mutation testing; re-write the original text.
- 1.2: `parse_value` keeps the source spelling of non-string plain scalars (`0x1F`); an empty value (`{a: }`) is `NewScalar(None, "")`, so renderers must handle an empty spelling. A double-quoted `"\ud800"` escape yields a lone-surrogate str; the renderer (2.4) must escape it or refuse it, because it cannot be written as UTF-8.
- 1.3: `IncludeDirective` has an extra `kind: IncludeKind | None` field (`compare=False`; `None` when the directive comes from VALUE), used to tell a text include from a YAML include with string content. `splice` raises `ValueError` for paths that target resolution refuses, so 3.1 must make sure exit-5 paths never reach it. Mapping over mapping = merge (keys absent from VALUE are removed; existing order kept; new keys appended).
- 2.2: `_block_scalar_end` takes the content indentation from the smallest non-blank line and ignores an explicit indentation indicator (`|2`). A whitespace-only line that is content under `|2` can be left out of `body_lines`; fix this in 2.6 if it matters there. Explicit `? k` keys make `entry_slot` raise `ValueError`, which the planner must turn into exit 3.
- 2.4: Multi-line strings come out of `render_scalar` double-quoted; block-context callers must use a literal block (BlockScalar). A `<<` key is never plain (the loader refuses plain `<<` as a merge key). Lone surrogates make the renderer raise `WriteRefusedError` naming PATH. U+FFFE/U+FFFF are escaped. Keys are compared as text, so verification must catch typed-duplicate clashes such as a new plain `true` beside `True`.
- 2.5: `render_literal` raises `ValueError` when `literal_fits` is false, so callers must check first and fall back to double-quoted. A first line starting with a space is refused (no indentation indicator, per design). A lone `"\n"` renders as `|+` (`required_chomping` is KEEP). `wrap_paragraph` never breaks at a space next to a TAB or another space.
- 2.6: KNOWN DEVIATION TO REVISIT: following req 10.7, any change to a folded value whose OLD text has more-indented lines is refused (exit 3). The contract only refuses a change that would PUT a more-indented line in. `rewrite_block` raises `ValueError` for a value no block can hold; 3.2/3.4 must check `literal_fits` first and fall back to double quotes. layout `_block_scalar_end` now honours `|N` indicators.
- 2.7: `render_block_value` has extra keyword-only args: `compact` (default True; pass False for a list-item collection whose `-` line has an end-of-line comment) and `preferred` (the first-level sibling quote style). Compact list-item collections put further entries at dash+2 (req 8.4). Content inside new collections uses minimal quoting (7.4).
- 3.1: `resolve_target` returns Replace/AddKey/Append targets. Include roots and non-string-over-text-include are NOT refused there; the planner (3.2) must refuse them (exit 3). A different `file:` string set over a text include replaces the directive in the intended tree, and 3.2 decides whether to refuse it. Text-include targets are looked up by `id()` of the directive objects that `splice` passes through.
- 3.2: The planner already writes a multi-line string over a plain/quoted scalar as a literal block, so 3.4 must reuse that rather than reimplement it. Still refused (exit 3) until 3.4: a block scalar set to a string it cannot hold (req 9.7 needs a double-quoted fallback), non-strings over block scalars, and kind changes. A different `file:` string over a text include is refused (exit 3, conservative). `plan_patch` refuses an empty plan, so SetCommand must detect no-ops (`plain_equal`) BEFORE planning (14.6).
- 3.3: `add_entries` takes a list of entries (for 3.5 merges); each new entry copies the LAST existing entry's style. A flow entry added after an empty flow value uses `" , "` so the previous key does not absorb the comma.
- 3.5: Merges pass the top-level PATH to nested `change_slot` refusals; 3.6 must name the nested path for YAML include roots (5.8). Deleting a compact first key on a `-` line is refused (req 5.7). OPEN: after the old last entry is deleted, new keys copy that deleted entry's style (7.1 is ambiguous here); revisit in validation.
- 4.4: The use case is `set_value(theatre, path, value) -> SetOutcome`, with the CLI writing nothing on success. `write_patches(patches, path)` takes PATH. Some test docstrings cite requirement IDs loosely (e.g. `test_old_values_come_from_the_files_on_disk_not_the_history` proves 15.1/15.2/15.5); fix them in 5.11.
- 4.5: On `set`, any single-dash token other than `-h` is positional (`-1e3`, `-.inf`, `-x`, `-hello`), implemented by overriding argparse's private `_parse_optional` (it only ever returns None early, so it works with every 3.12–3.14 return shape). A VALUE spelled like a long option (`--theatre`) must come after `--`. This broadens design.md's "VALUE starting with `-` needs `--` unless numeric" note. `test_cli_usage.py` registration assertion now includes `add_set_subparser`.
