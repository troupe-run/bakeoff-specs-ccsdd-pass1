# Research & Design Decisions: config-set-writeback

## Summary
- **Feature**: `config-set-writeback`
- **Discovery Scope**: Complex extension of a greenfield system (full discovery). The code of config-foundation
  does not exist yet; its approved design (`.kiro/specs/config-foundation/design.md`, "Shared Seams") is the
  integration contract.
- **Key Findings**:
  - ruamel.yaml 0.19.1's round-trip composer gives exact spans for non-empty scalars, quoted keys and flow
    collections, but **zero-length (empty) null scalars have unreliable positions** (at the colon, at the
    start of the next line, or at the next key). The writer must locate empty values from the key's `:` or the
    item's `-`, never from the empty scalar's span.
  - The round-trip composer stores **U+0007 fold markers inside folded (`>`) scalar values** (`'a\x07 b\n'` for
    `>\n  a\n  b\n`). U+0007 can never occur in YAML source (the reader rejects it), so stripping it is exact.
    This was raised as **required upstream addition U1** (see design.md) and has since been added to
    config-foundation (requirement 3.8). Upstream additions U2–U6 (key styles, CLI shell module, pytest
    pythonpath, Shared Seams entries, empty root style and span-semantics wording) are likewise delivered in
    config-foundation.
  - Block scalar spans run past the value: for clip/strip chomping they include trailing blank lines that are
    not part of the value; for keep chomping those lines are content. The writer computes a "content end"
    itself.

## Research Log

### Upstream seams consumed
- **Context**: The writer must not redefine anything config-foundation owns.
- **Sources Consulted**: config-foundation `design.md` (Shared Seams, Components), `requirements.md`,
  `research.md`, `tasks.md` `_Boundary:_` lines.
- **Findings**:
  - Model: `SourceFile(path, real_path, text, line_ending)`, `Span` (character indices), `Origin`, `IncludeRef`
    (`kind`, `spec`, `target`, `directive`), `ScalarStyle`, `CollectionStyle`, `ConfigScalar`, `ConfigMapping`
    (`entries` of `MappingEntry(key, key_origin, value)`), `ConfigSequence`, `ConfigDocument(root_file, root,
    files)`. Block collection spans are composer-reported (not tight); scalar spans exact.
  - `load_config(config_file, overrides)` with overrides keyed by `real_path` is the write-verify seam.
  - `walk_path` reports `nodes`, `consumed` and `WalkStop`; `flatten_leaves` yields `Leaf(path, value, node)` in
    document order.
  - `HistoryStore.record_changes(changes)` appends one batch in one transaction; `SetChange`/`RemoveChange` are
    the change records; `open_theatre` guarantees the import before the command.
  - Errors: `UsageError` (2), `WriteRefusedError` (3, "raised only by config-set-writeback"), `ConfigLoadError`
    (4), `PathNotFoundError`/`WrongNodeKindError` (5); `format_message` is the single message format.
  - CLI: subcommands register through the marked list `SUBCOMMAND_REGISTRARS` in `troupe/cli/main.py`; each
    registrar (`SubcommandRegistrar`) adds a subparser to a `TroupeArgumentParser` and sets `handler=` to a
    function of the `Handler` protocol `Callable[[argparse.Namespace, io.BytesIO], None]`, which `run`
    dispatches to. `TroupeArgumentParser`, `Handler`, `SubcommandRegistrar` and `theatre_option_parent` live in
    the leaf module `troupe/cli/shell.py` (imports nothing from other `troupe.cli` modules), so
    `set_command.py` imports them from there and `main.py` imports `set_command` without a cycle.
  - Further Shared Seams names used (delivered in config-foundation as U2–U6): `INCLUDE_PREFIX`
    (`troupe.config.constants`), `TroupeArgumentParser`, `LeafChange`, `MappingEntry.key_style: ScalarStyle`,
    the pytest setting `pythonpath = ["tests"]` (config-foundation task 1.1), empty root style `BLOCK`, and the
    span-semantics wording (block spans may overrun trailing blanks; empty-null spans unreliable).
- **Implications**: The writer imports only these names. U1 (folded-scalar fold markers) was found as a gap and is
  listed as a required upstream addition in design.md instead of being worked around (U2–U6 were added during cross-spec review); the unreliable position
  of empty null scalars is now documented upstream (U6) and needs no code change. The `set`
  CLI code lives in a new module `troupe/cli/set_command.py` (`add_set_subparser`, `handle_set`), importing
  the shell names from `troupe/cli/shell.py`; the only edit to `troupe/cli/main.py` is one added entry in
  `SUBCOMMAND_REGISTRARS`.

### ruamel.yaml positions relevant to writing (local probes, ruamel.yaml 0.19.1, Python 3.12.14)
- **Context**: Every rule patches text at positions derived from the model.
- **Sources Consulted**: scratch scripts composing small documents and printing `start_mark.index`,
  `end_mark.index`, `style`, `flow_style` (not committed).
- **Findings**:
  - `a:\nb: 1` → the null value of `a` has span (3, 3) (start of next line); `a: # c` → (2, 2) (right after the
    colon); `a:   \n  # c\nb: 1` → (12, 12) (at the next key). `- \n- x` → item 0 at (4, 4).
  - Block sequence spans start at the first `-`; item spans start at the item content. Block mapping spans start
    at the first key. Both extend over trailing blank lines and comments.
  - `k: |\n  a\n  b\n\n\nz: 1` → literal span `'|\n  a\n  b\n\n\n'` (includes the two trailing blank lines although
    the value is `'a\nb\n'`). `| # hc` header comments are inside the span.
  - Quoted keys have spans including their quotes (`'"q k"'`). The writer does not derive a key's quoting
    style from this text: it uses the foundation's `MappingEntry.key_style` (and `ConfigScalar.style` for
    values).
  - Flow collections: spans are exact; a trailing comma (`[a, b,]`) is accepted and lies before `]`.
- **Implications**: The writer derives (a) the "value slot" of an empty value from the key's `:`/item's `-`,
  (b) the tight end of a collection from its last descendant, (c) the content end of a block scalar from its
  lines and chomping.

### What the parser accepts (representability)
- **Findings**:
  - `x: a\tb` (plain with TAB) → ScannerError; `'a\tb'` single-quoted is accepted.
  - `"a\x85b"` and plain `a\x85b` → value `'a b'` (ruamel folds U+0085 as a line break); `"\N"` → U+0085.
  - Literal blocks containing U+0085 or U+2028 → ParserError; TAB and trailing spaces inside literal content are
    fine (`'a\tb\n\tc\n'`, `'a  \n# b\n'`).
  - `|\n   a\n  b` (first line more indented than later lines) → ParserError; `|2` with the same content works.
  - `-foo`, `?foo`, `:foo`, `a:b`, `---a` are plain strings in block context; `[a, -b, ?c]` turns `?c` into a
    mapping in flow context.
  - Folded values: `>\n  a\n  b\n\n  c d\n` → `'a b\nc d\n'` once fold markers are removed.
- **Implications**: Plain representability is decided by a syntactic pre-check plus a parse probe in the exact
  context (block value, flow item, mapping key); C1 controls (including U+0085) are always escaped in double
  quotes; literal blocks are only used for strings free of control characters other than TAB/LF and of
  U+2028/U+2029, whose first non-empty line does not start with a space.

### VALUE as a flow node
- **Findings**: `compose()` of VALUE text returns a `ScalarNode` with style `None`/`'`/`"`, a flow
  `MappingNode`/`SequenceNode` (`flow_style=True`), a block node (`a: 1`, `- a` → `flow_style=False`; `|\n x`
  → style `|`), or `None` for empty/whitespace/comment-only text. `!!str 5` and `&a x` compose silently (the tag
  or anchor must be detected from the text, as the loader does); `*a` raises ComposerError; duplicate keys are
  not rejected by the composer.
- **Implications**: VALUE parsing reuses the same techniques as the loader (span text starting with `!`/`&`,
  own duplicate-key check) and the shared `resolve_plain_scalar`.

## Architecture Pattern Evaluation

| Option | Description | Strengths | Risks / Limitations | Notes |
|--------|-------------|-----------|---------------------|-------|
| Text patch engine over the source-mapped model, verify by re-load | Compute character edits on original text, apply, re-load with overrides, compare | Byte-surgical by construction; parser is the final judge | Many small rules; each must be exact | **Selected** (roadmap decision) |
| ruamel round-trip load → modify → dump | Library re-emits the document | Little code | Re-formats untouched parts; rejected in roadmap | Rejected |
| Token/CST-level editor (own YAML CST) | Full fidelity editing | Most control | Very large; duplicates the loader | Rejected (size, duplication) |
| Whole-subtree re-render on collection replacement | Replace the old collection text by a rendering of VALUE | Simpler planner | Loses comments and quoting of unchanged entries; violates "only required bytes" | Rejected in favour of entry-wise merge |

## Design Decisions

### Decision: Patch, re-load, compare, then write
- **Context**: Contract: byte-surgical; exit 3 leaves files unchanged; brief: verify before writing.
- **Alternatives Considered**: 1) trust the renderer; 2) verify by re-parsing only the changed file in
  isolation; 3) verify by re-loading the whole configuration with in-memory overrides.
- **Selected Approach**: 3. All files of the loaded document are passed as overrides (changed text for the
  changed file, original text for the others), so verification never re-reads the disk and checks includes,
  duplicate keys and typing exactly as `show` would. The loaded configuration (as a plain tree, ordered, typed)
  must equal the intended tree, and the set of files read must be unchanged.
- **Rationale**: The loader is the single definition of meaning; any renderer mistake, diamond include or
  unforeseen layout becomes exit 3 instead of a corrupted file.
- **Trade-offs**: A few legitimate edge cases are refused (exit 3) rather than handled.
- **Follow-up**: Every rule test also asserts that verification passes (exit 0); safety tests force mismatches.

### Decision: Collection over collection = entry-wise merge
- **Context**: Contract defines writing new keys/items and changing scalars, not replacing collections, but
  requires that only required bytes change and that removed leaves emit `remove` events.
- **Alternatives Considered**: whole re-render of the collection; refuse (exit 3); entry-wise merge.
- **Selected Approach**: Mapping over mapping: keep unchanged entries, change differing ones (recursively),
  append new keys, delete absent keys. List over list: same by index, append/delete at the end. Kind changes,
  empty targets and scalar↔collection are whole-value replacements.
- **Rationale**: Leaves comments, quoting and layout of unchanged entries intact; history semantics (by path)
  match the edits.
- **Trade-offs**: Introduces deletion rules (whole lines in block style, one entry plus one separator in flow
  style) not described in the contract; layouts where a deletion would cut a line in two are refused (exit 3).
- **Follow-up**: Revisit if the product owner prefers whole replacement.

### Decision: Atomic write with a temporary file in the target's own directory
- **Context**: Files must be written atomically; state only under `<theatre>/.troupe/`; no other file may be
  created. Include targets may lie outside `.troupe/` (e.g. `file:../shared.yaml`).
- **Alternatives Considered**:
  1. Temporary file in `.troupe/`, then `os.replace` into place — not atomic (and fails with `EXDEV`) when the
     target is on another file system.
  2. In-place overwrite (`open(..., "w")`) — no extra file, but a crash leaves a truncated user file.
  3. Temporary file next to the target (same directory), `fsync`, `os.replace`, removed in a `finally` block on
     any failure.
- **Selected Approach**: 3, with an exclusive-create name `.<name>.troupe-tmp-<random>` (hidden, recognisable),
  the target's permission bits copied before the rename, and the symlink resolved first (`real_path`) so the
  link is kept.
- **Rationale**: Atomicity protects the user's data, which outranks a transient file that never outlives the
  command (Product Principle 2). The "no other file" rule is read as an end-state rule; the temp file is never
  left behind on any handled path.
- **Trade-offs**: A hard kill between create and rename can leave the hidden temp file; documented risk.
- **Follow-up**: Tests list the directory before/after for success and injected failures.

### Decision: One file per set; history after the file; compensate on history failure
- **Context**: Several files cannot be replaced atomically together; file write and event append cannot share
  one transaction.
- **Selected Approach**: A plan that would change more than one file is refused (exit 3). The file is written
  first, then events are recorded; if recording fails, the original text is written back atomically and the
  command exits 4 naming the history store.
- **Alternatives Considered**: record first, then write (a failed write would leave events for a change that
  never happened, and events cannot be deleted).
- **Rationale**: Files are authoritative (config-foundation decision); a set either happens completely or not.
- **Follow-up**: Store failure injected at the external SQLite boundary in a safety test.

### Decision: `file:` strings in VALUE create includes, judged by the read-back (revised)
- **Context**: A first draft refused any VALUE string starting with `file:` (exit 3). The coordinator pointed
  out that the contract defines such strings as includes and does not forbid setting them, so refusing narrows
  scope quietly (collaboration.md).
- **Alternatives Considered**: 1) refuse (exit 3) — rejected, narrows scope; 2) write the string and treat the
  read-back value as a plain string — impossible, the loader expands it; 3) write the string as a normal scalar
  and let the in-memory read-back decide.
- **Selected Approach**: 3. The string is rendered under the ordinary quoting rules. The plain-tree comparison
  represents every include root as `IncludeDirective(text, content)`; a directive written by this set has
  `content=None` and matches whatever its target loads as. If the read-back fails (target missing or unreadable,
  cycle, target fails to load) the set exits 3 naming PATH and the target, with no file changed. On success the
  leaf diff runs over the read-back, so an embedded YAML include contributes its leaves as `set` events and a
  text include one string leaf. A YAML include root is still never *replaced* (rule 4), including by a
  different `file:` string; writing the same string again is a no-op.
- **Rationale**: Covers the whole contract while keeping Product Principle 2: the user's files can only change
  when the resulting configuration loads.
- **Trade-offs**: The verifier reads the new target from disk (read-only); the set never creates the target.
- **Follow-up**: CLI tests for a YAML target, a text target, a missing target and a cycle; history test for
  the embedded leaves.

### Decision: Contract gaps resolved conservatively

| Gap | Choice | Why | Revisit when |
|-----|--------|-----|--------------|
| Output on success | Nothing on stdout | Contract defines no output | PO wants a confirmation |
| Empty / comment-only VALUE | Exit 2 | Not a flow node a user would type deliberately; avoids silent null | PO wants empty = null |
| Block node, tag, anchor, alias, multi-document VALUE | Exit 2 | Contract: single flow node; tags/anchors out of contract | Step 6 |
| Duplicate, empty or non-scalar key in VALUE | Exit 2 | Ambiguous or unaddressable | — |
| Spelling of non-string scalars | VALUE's own text (`0x1F`, `~`, `1e3`) | It is what the user wrote and re-types identically | PO wants canonical forms |
| No-op vs refusal order | No-op wins (exit 0) | "A set that changes nothing … should not modify files"; nothing to refuse | — |
| PATH below a null (`key:`) | Exit 5 | Null is not a mapping (rule 2 needs an ancestor mapping) | PO wants null treated as empty mapping |
| Digit segment that would create a mapping key, or index below a missing list, or `list.<len>.x` | Exit 5 | Digit keys out of contract; rule 3 only for PATH ending in the index | — |
| VALUE string starting with `file:` written to YAML | Written as a normal string; the read-back decides: missing target, cycle or unloadable target → exit 3, otherwise success with the include's leaves recorded | The contract defines such strings as includes and does not forbid setting them; refusing would narrow scope. Verification keeps it safe | PO wants targets created by `set` |
| Change touching two files (text include inside a replaced collection) | Exit 3 | Cannot be atomic | — |
| Same file included twice and edited | Exit 3 (verification) | The edit would change other paths too | — |
| Mixed-line-ending file | Inserted breaks copy the line they follow (LF if none) | Matches local context; CRLF-only rule is about CRLF files | — |
| Text include with mixed/LF/no line ending | VALUE written exactly | Contract converts only for CRLF targets | — |
| File without final line break | Insert break before the new entry; still no final break | Minimal byte change | — |
| Empty root file | New key appended at end of file with a final line break | No last entry to anchor to | — |
| Indentation step | First block mapping nested under a block mapping key in that file; default 2 | Deterministic, follows the user's own nesting | Files with inconsistent nesting |
| Block list dash offset | First block list under a mapping key in that file (0 allowed); default = step | Keeps indentless lists indentless | — |
| Collections as list items | Compact (`- a: 1` / `- - a`), continuation two columns right of `-` | Conventional; independent of step | — |
| New entry separators | `key: value`, `- value`, flow `, ` and `: ` | Minimal and conventional | PO wants sibling spacing copied |
| Sibling for entries inside a newly written collection | None (minimal style) | "Preceding sibling" read as an existing entry | PO reads it as the previous new entry |
| Block scalar sibling | Not a "quoting style" → minimal | Contract speaks of quoting styles | — |
| Multi-line string replacing a scalar inside a flow collection | Double-quoted | A literal block cannot appear in flow context; mirrors the new-key rule | — |
| String that a literal block cannot hold | Double-quoted | End of the contract's fallback chain | — |
| Scalar → block collection, key line | Scalar removed; comment keeps its column; without comment the spaces after `:` go too | Combines "keeps its bytes up to and including its comment" with the column rule | PO clarifies |
| List item scalar → block collection | Compact when the item line has no comment, otherwise collection starts on the next line | Keeps the comment on its line | — |
| Removing block entries | Whole lines through the last content line; interior comment lines go, trailing comment lines stay | Interior comments belong to the removed value | — |
| Removing flow entries | Entry plus one separator, only within one line; otherwise exit 3 | Avoids deleting comments in multi-line flow text | — |
| Folded value with existing more-indented lines | Exit 3 when changed | Paragraph mapping is ambiguous | — |
| Folded paragraph correspondence | Longest-common-subsequence on paragraph texts; changed paragraphs paired in order within each gap | Unchanged paragraphs keep bytes even after insertions | — |
| Folded width when the old value has no content | No wrapping | No width to copy | — |
| Event order | Sets in new document order, then removes in old document order | Deterministic; readers see new state first | — |
| VALUE starting with `-` | Must follow `--` unless it is a negative number (argparse) | Standard CLI behaviour | — |

## Synthesis Outcomes
- **Generalization**: All writes reduce to three primitives on original text — replace a span, insert text at a
  position, delete a line range — plus one line-level fix-up (comment column). Every rule (scalar change, new
  key, append, merge, kind change, block scalar rewrite) is a planner case producing these edits. "Where a value
  lives" is captured once as a *value slot* (parent kind, owner column, key/dash position, inline extent, body
  lines) used by every case. Plain-tree comparison serves no-op detection, merge decisions, verification and
  leaf diffing (via `flatten_leaves`).
- **Build vs. Adopt**: Adopt ruamel.yaml compose (via the foundation loader and for VALUE/representability
  probes), `difflib.SequenceMatcher` (stdlib) for folded paragraph alignment, `os.replace`/`tempfile`-style
  exclusive create for atomic writes, `argparse` via the foundation parser. Build the renderers and planner:
  no library renders YAML fragments byte-faithfully into existing text.
- **Simplification**: No undo/transaction framework (one file per set, one compensation path); no generic
  "YAML emitter" (renderers produce only what the rules need); no line-ending abstraction beyond one function
  choosing the break for an insertion point; folded and literal rendering share chomping and indentation code.

## Risks & Mitigations
- Renderer or planner mistake corrupting a file — verification by full re-load before any write (exit 3).
- ruamel internals (marks, fold markers) change — pinned `<0.20` upstream; probes encoded as unit tests here.
- Rule interactions (comment column with flow appends, CRLF with literal blocks) — byte-exact CLI tests per
  rule on fixtures with comments, CRLF, multibyte characters and odd quoting.
- Transient temp file left by a hard kill — hidden recognisable name; documented.
- Upstream addition not delivered (U1–U6 are delivered in config-foundation) — task 2.6 keeps a failing guard test for U1 and stops.

## References
- YAML 1.2.2 specification: 7.3 flow scalar styles (escapes), 8.1 block scalar headers (chomping,
  indentation indicator), 8.1.3 folded style, 10.3.2 core schema — https://yaml.org/spec/1.2.2/
- ruamel.yaml documentation — https://yaml.dev/doc/ruamel.yaml/
- Python `os.replace`, `difflib.SequenceMatcher` — https://docs.python.org/3.12/library/
- Consulted skills: none beyond the kiro spec skills (no design/accessibility/UX skill applies to a CLI
  text-patching feature).
