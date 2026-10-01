# Research & Design Decisions: config-foundation

## Summary
- **Feature**: `config-foundation`
- **Discovery Scope**: New Feature (greenfield; full discovery)
- **Key Findings**:
  - ruamel.yaml 0.19.1 `compose()` gives every node a `start_mark.index` / `end_mark.index` that are **character
    (code point) indices into the decoded text**, plus scalar style (`None` plain, `'`, `"`, `|`, `>`) and
    `flow_style` for collections. This is enough to build a source-mapped tree without ever re-dumping.
  - ruamel's round-trip resolver is **not a strict YAML 1.2 core-schema resolver**: `1_0` resolves to int 10 and
    `2001-12-14` to a timestamp, both of which are strings under the 1.2 core schema. The model therefore types
    plain scalars with its own core-schema function and uses ruamel only for structure, positions and style.
  - ruamel's round-trip composer inserts U+0007 fold markers into folded (`>`) scalar values; the loader strips
    them for folded style only (verified exact against the pure safe loader).
  - eventsourcing 9.5.5 with `PERSISTENCE_MODULE=eventsourcing.sqlite` passed through the `env` constructor
    argument persists to a single SQLite file (plus `-wal`/`-shm` siblings), saves all pending events of one
    aggregate in one transaction, and round-trips `str`, `int`, `float` (including `inf`), `bool` and `None`
    unchanged. Explicit `env` overrides `os.environ`.

## Research Log

### ruamel.yaml composition, marks and styles
- **Context**: The loader must record file, span, style and block/flow for every node (shared seam with
  config-set-writeback).
- **Sources Consulted**: Local probes against ruamel.yaml 0.19.1 on Python 3.12.14 (`/tmp` scratch project,
  not committed); ruamel.yaml source (`composer.py`, `nodes.py`).
- **Findings**:
  - `YAML().compose(text)` returns `MappingNode` / `SequenceNode` / `ScalarNode` or `None` for an empty or
    comment-only document.
  - Indices are character indices: in `é: café\r\n` the value starts at index 3 after a one-character key.
  - Scalar span text includes quotes and, for block scalars, the header and all content lines
    (`'|\r\n  hi\r\n  there\r\n'`). A block scalar's span may also extend over trailing blank lines (e.g.
    `'|\n  a\n  b\n\n\n'` for the value `'a\nb\n'`), so only plain and quoted scalar spans are exact. The span of
    an empty (zero-length) null value is not a reliable position. Flow collection spans are exact
    (`'[1, yes, 0.1, null, ~, 1e3]'`).
  - A node with properties starts its span at the property: `'!!str 5'`, `'&x 1'`, `'!foo [x]'`. Because `!`
    and `&` cannot start a plain scalar or a flow collection, "span text starts with `!` or `&`" reliably detects
    an explicit tag or anchor.
  - Constructed values from ruamel normalise CRLF to LF inside block scalars; the original text is still
    available through the span.
  - Multi-document input raises `ComposerError` ("expected a single document"); syntax errors raise
    `MarkedYAMLError` subclasses carrying line/column.
  - Duplicate keys are **not** rejected by `compose()`; the loader must check them itself.
  - Complex keys (`? [a]`) compose to a `SequenceNode` key.
  - A leading BOM is accepted by the composer.
- **Implications**: Spans are stored as character indices into `SourceFile.text`; files are decoded with
  `newline=''` semantics (no newline translation) so indices map onto the exact on-disk text. Typing of plain
  scalars is done by the model, not by ruamel's constructor.

### YAML 1.2 core schema
- **Context**: Contract requires YAML 1.2 core-schema typing for loaded values (and, downstream, for VALUE).
- **Sources Consulted**: YAML 1.2.2 specification, section 10.3.2 (Core Schema tag resolution).
- **Findings**: Plain scalars resolve as: null `null|Null|NULL|~|` (empty); bool
  `true|True|TRUE|false|False|FALSE`; int `[-+]?[0-9]+`, `0o[0-7]+`, `0x[0-9a-fA-F]+`; float
  `[-+]?(\.[0-9]+|[0-9]+(\.[0-9]*)?)([eE][-+]?[0-9]+)?`, `[-+]?(\.inf|\.Inf|\.INF)`, `\.nan|\.NaN|\.NAN`;
  anything else is a string. Quoted and block scalars are always strings.
- **Implications**: One function `resolve_plain_scalar` in `troupe.config.scalars` is the single home of
  core-schema typing, shared with config-set-writeback's VALUE parsing.

### eventsourcing persistence
- **Context**: History must persist under `<theatre>/.troupe/` with the `eventsourcing` library (fixed
  constraint) and import atomically.
- **Sources Consulted**: Local probes against eventsourcing 9.5.5; source of `application.py`
  (`construct_env`) and `sqlite.py`.
- **Findings**:
  - `Application(env={...})` merges `type(self).env`, then `os.environ`, then the explicit `env` argument (last
    wins). `Environment.get` also honours application-name-prefixed keys.
  - SQLite recorder opens the database lazily on construction and creates tables (`CREATE_TABLE` default true),
    and uses WAL, producing `<db>-wal` and `<db>-shm` next to the database file.
  - `Application.save(aggregate)` writes all pending events in one `BEGIN … COMMIT` transaction.
  - An aggregate with a fixed ID (`create_id` returning a constant UUID) can be fetched with
    `repository.get(id)`; a missing aggregate raises `AggregateNotFoundError`.
  - Event `originator_version` starts at 1 for the created event and increases by exactly 1 per event.
  - The package ships `py.typed` (as does ruamel.yaml), so `mypy --strict` can check usage.
- **Implications**: One aggregate per theatre with a fixed ID; `seq = originator_version - 1` gives a gapless
  sequence starting at 1; import and each `set` batch are single `save` calls and therefore atomic.

### Folded scalars carry U+0007 fold markers (requested upstream by config-set-writeback, U1)
- **Context**: config-set-writeback found that composed folded (`>`) scalar values contain `\x07`.
- **Sources Consulted**: Local probe against ruamel.yaml 0.19.1 (`/tmp` scratch project, outside the repo),
  comparing `YAML().compose(...)` node values with `YAML(typ="safe", pure=True).load(...)`.
- **Findings**:
  - `>\n  a\n  b\n` composes to `'a\x07 b\n'`; the true YAML value is `'a b\n'`.
  - `>-\n  x\n\n  y\n  z\n` composes to `'x\x07\ny\x07 z'`; true value `'x\ny z'`.
  - With a more-indented line, `>\n  a\n    more\n  b\n` composes to `'a\x07\n  more\x07\nb\n'`; true value
    `'a\n  more\nb\n'`.
  - In every probed case, deleting all U+0007 from the composed folded value gives exactly the pure safe loader's
    value. Literal (`|`), quoted and plain multi-line scalars carry no markers.
  - U+0007 cannot appear literally in YAML source (it is not a printable YAML character), and folded scalars have
    no escape sequences, so every U+0007 in a folded value is a marker. A double-quoted `"\a"` legitimately
    produces U+0007, so the stripping must be limited to folded style.
- **Implications**: The loader removes U+0007 from the value of every folded scalar (and only folded scalars)
  when building `ConfigScalar.value`. The model's values are therefore the true YAML values for show, history,
  leaves and config-set-writeback's diffs. The span and source text are not affected.

### Python I/O details
- **Context**: Text includes must be byte-exact; output must be exact.
- **Findings**: `open()` in text mode translates `\r\n` to `\n` by default; reading bytes and decoding UTF-8
  strictly preserves CRLF. Writing via `sys.stdout.buffer` avoids locale encoding and newline translation.
- **Implications**: The loader reads bytes and decodes strictly; the CLI writes encoded bytes.

## Architecture Pattern Evaluation

| Option | Description | Strengths | Risks / Limitations | Notes |
|--------|-------------|-----------|---------------------|-------|
| Layered (CLI → service → domain/file layer) | Steering's proposed shape; file layer is the only reader of user files | Matches steering; narrow seams for the downstream spec | Some indirection for a small tool | Selected |
| Hexagonal with ports for file system and store | Abstract FS and store behind interfaces | Swappable infra | Speculative; tests use real files anyway | Rejected (simplification) |
| Projection-authoritative (event store is the source of truth, files are a view) | `show` reads projected state | Pure event sourcing | Wrong after hand edits; violates "the user's file is theirs" | Rejected, see decision below |

## Design Decisions

### Decision: Files are authoritative for current values; the event store is authoritative for history only
- **Status**: Confirmed by product owner (2026-10-01, decision A).
- **Context**: Open decision from roadmap/brief: does `show` read the files or the projection? Contract:
  "importing is not reconciling" (history is not updated for hand edits), and `show PATH` "prints the value at
  PATH" of the configuration, which is the hand-edited file set.
- **Alternatives Considered**:
  1. `show` reads the projection (leaf values derived from events).
  2. `show` reads the files; the event log only answers `history`.
  3. Hybrid: read the projection, fall back to files on mismatch.
- **Selected Approach**: Option 2. Every command loads the files; `show` resolves the path in the freshly
  loaded source-mapped model. The event log is only read by `history` (and only appended to by import and by
  `set`). There is no "current value" projection; the history projection is the ordered list of history records.
- **Rationale**: After a hand edit, the projection would print a value that is no longer in the user's file,
  which is wrong under the contract and misleading for a developer who just edited the file. The files are
  what troupe and the downstream `set` act on; showing anything else could cause a developer to make wrong
  decisions (Product Principle 2). The "importing is not reconciling" rule constrains history only.
- **Trade-offs**: History and `show` can disagree after hand edits; this is the contract's intent. A
  current-value projection is not built (no consumer needs it).
- **Follow-up**: Test: import, hand-edit file, `show` prints the new value and `history` has no new events
  (tasks 5.1, 6.3, 6.4). Revisit if a later contract step makes the event store authoritative.

### Decision: Event store persistence
- **Selected Approach**: eventsourcing `Application` with the SQLite persistence module, database at
  `<theatre>/.troupe/history.sqlite` (constant `HISTORY_STORE_NAME`), configured only via the explicit `env`
  argument. One aggregate `ConfigHistory` per theatre with a fixed UUID.
- **Alternatives Considered**: POPO (in-memory) store with a JSON dump file (not durable without custom
  persistence code, re-implements what the library does); one aggregate per leaf path (no global order, `seq`
  would need a separate counter and cross-aggregate transactions).
- **Rationale**: One aggregate gives one total order, gapless versions and single-transaction batches.
- **Trade-offs**: The history grows in one stream; configurations are small (no performance target).
- **Follow-up**: Revisit if concurrent writers become in contract (would need optimistic-concurrency
  handling; the library already raises on version conflicts).

### Decision: `seq` numbering
- **Selected Approach**: `seq = originator_version - 1`. The aggregate's created event (`HistoryStarted`) is
  version 1 and is not a history line; it records that the theatre has been imported.
- **Rationale**: Gapless and from 1 by construction; no counter to keep consistent.

### Decision: When the import happens
- **Selected Approach**: Every command first locates and loads the configuration; only after a successful
  load is the store opened and, if no `ConfigHistory` aggregate exists, one `LeafImported` per leaf is saved
  together with `HistoryStarted` in one transaction. A later exit 5 from `show` does not undo the import.
- **Alternatives Considered**: `history` skipping the load when already imported (would print history even
  when the config is broken).
- **Rationale**: "First command run against a theatre imports it" is satisfied uniformly; neither/both config
  files is always exit 4 as the General section says; no state is created when loading fails.
- **Follow-up**: Revisit if the product owner wants `history` to work on a broken configuration.

### Decision: Contract gaps resolved conservatively
| Gap | Choice | Why | Revisit when |
|-----|--------|-----|--------------|
| Malformed PATH (`""`, `a..b`, `.a`, `a.`) | Exit 2 (usage) — PO delegated: safest assumption (G) | The argument is malformed, not "not found" | PO prefers 5 |
| Duplicate keys | Exit 4 naming file and key path — PO delegated: safest assumption (I) | Ambiguous meaning; writes could hit the wrong key | Never likely |
| Top-level node not a mapping | Exit 4 | Paths start at a mapping; writes need a mapping root | A step addresses list roots |
| Empty / comment-only root file | Empty configuration, zero leaves | It is a valid YAML document with no settings | — |
| Empty YAML include target | Null scalar in place of the include | "As if written in place": `key:` with nothing is null | — |
| Explicit tags (`!!str 5`, `!foo x`) | Exit 4 naming file and key path — PO delegated: safest assumption (I) | Not described by the contract; guessing a type risks wrong writes | A contract step adds tags |
| Anchors / aliases / merge keys | Exit 4 naming file and key path where known (anchor by the `&` property, merge key by a plain `<<` key, undefined alias by the composer error) — PO: our choice (H) | Out of contract; refusing protects the byte-surgical writer | Step 6 |
| Complex (non-scalar) mapping keys | Exit 4 | Cannot be addressed by a dotted path | — |
| Mapping keys | The key scalar's text as written (`"a b"` → `a b`, `true` → `true`) | Paths are textual | — |
| Include suffix match | Case-sensitive `.yml` / `.yaml` | Contract text; avoids guessing | — |
| Non-UTF-8 file or text-include target | Exit 4 naming the file — PO delegated: safest assumption (I) | Cannot represent exactly as a string | Binary includes requested |
| Unreadable/corrupt history store | Exit 4 naming `<theatre>/.troupe/history.sqlite` | Closest contract meaning; refuse rather than reset history | — |
| Non-finite floats in history JSON | PO delegated: safest assumption (F). JSON string of the YAML 1.2 canonical form: `".inf"`, `"-.inf"`, `".nan"`; lines are encoded with `allow_nan=False` so invalid JSON can never be emitted | Contract requires JSON Lines; Python's `Infinity`/`NaN` tokens are not JSON. `null` was rejected because it cannot be told apart from a real null and loses the value. Accepted cost: in history only, `.inf` the float and `".inf"` the string look the same. `show` still prints `repr()` (`inf`, `nan`) | PO prefers another encoding |
| JSON escaping | `json.dumps` defaults (ASCII-escaped) | Always valid regardless of terminal encoding | — |
| Import order | Document order (depth-first, as written, includes expanded in place) | Deterministic, matches what a reader sees | — |

## Synthesis Outcomes
- **Generalization**: Path resolution for `show` and the downstream `set` is one walk (`walk_path`) that
  reports the deepest node reached and why it stopped; `resolve_path` (show) is a thin strict wrapper. Leaf
  flattening serves both import and the downstream leaf diff. The history projection (`HistoryStore.records`)
  serves both full and prefix listing; the prefix filter is a pure function.
- **Build vs. Adopt**: Adopt ruamel.yaml for parsing/positions (rejecting PyYAML and a hand-written parser,
  per roadmap); adopt eventsourcing for persistence and ordering (required). Build the core-schema resolver (a
  dozen regular expressions) because ruamel's resolver deviates from 1.2 core. Adopt `argparse` for the CLI
  (standard library; usage errors routed to exit 2).
- **Simplification**: No current-value projection (no consumer). No file-system or store abstraction ports
  (tests use real files and a real SQLite store in `tmp_path`). Error hierarchy kept to one class per exit code
  plus two path-error subclasses that callers distinguish.

## Risks & Mitigations
- ruamel internals (marks, `style`, `flow_style`) are not a formal public API — pin `ruamel.yaml>=0.19.1,<0.20`
  and cover spans/styles with tests on real fixtures.
- Block-collection end marks may extend over trailing blank lines/comments — documented in the model contract
  as "collection spans are composer-reported, not tight"; only scalar spans are exact.
- `os.environ` keys such as `COMPRESSOR_TOPIC`, `CIPHER_TOPIC` or `HISTORYAPP_*` could alter the store
  configuration — the store passes explicit values for the persistence keys and the topics it relies on;
  residual risk accepted (developer-controlled environment).
- SQLite `-wal`/`-shm` side files appear under `.troupe/` while the store is open — allowed (state only under
  `.troupe/`); the store is closed at the end of every command (7.9), after which SQLite removes them.

## Product Owner Decisions (2026-10-01)
Answers to the open questions of this spec. Each record states the decision, the alternatives, why, what would
make us revisit it, and its status.

### A. Files are the truth; the history is change history only
- **Status**: Confirmed by product owner.
- **Decision**: `show` (and the downstream `set`) always read the configuration files as they are on disk for the
  current command. The event store is change history only: a hand edit shows in `show` but produces no history
  event; config-set-writeback takes `old` from the configuration loaded from disk, not from the history.
- **Alternatives**: `show` reads a projection of the events; hybrid (projection with a fallback to the files).
- **Why**: After a hand edit a projection would print a value that is no longer in the user's file; "importing is
  not reconciling" constrains history only.
- **Revisit when**: a later contract step makes the event store authoritative or asks for reconciliation of hand
  edits.
- **Spec impact**: 6.9 (wording sharpened), 7.3; tests in tasks 5.1, 6.3 (new hand-edit case) and 6.4.

### E. State only under `.troupe/`; store closed; nothing created elsewhere
- **Status**: Confirmed by product owner, with a condition (no extra file may be left once any command finishes).
- **Decision**: The only state files are `<theatre>/.troupe/history.sqlite*`. The history store is closed at the end
  of every command, on success and on failure (`Theatre` context manager, `HistoryStore.close`). No file or
  directory is created, modified, renamed or deleted anywhere else in the theatre, including directories of
  included files.
- **Alternatives**: leave closing to process exit (SQLite side files and open connections linger, behaviour depends
  on interpreter shutdown); keep state elsewhere (e.g. a user cache directory) — rejected by the contract.
- **Why**: The contract allows state only under `.troupe/`; explicit closing makes the end state deterministic and
  testable (no `-wal`/`-shm` after a command).
- **Revisit when**: concurrent writers come into contract (connection lifetime and locking would need a design) or
  state is moved out of the theatre.
- **Spec impact**: 7.6 (sharpened), new 7.9; design Store/Theatre/CliMain; tasks 4.3, 5.1, 5.3, 6.5.

### F. Non-finite floats in history as `".inf"`, `"-.inf"`, `".nan"`
- **Status**: PO delegated: safest assumption (kept).
- **Decision**: In history JSON lines, infinite and NaN values are the JSON strings of their YAML 1.2 canonical
  forms; lines are encoded with `allow_nan=False`.
- **Alternatives**: bare `Infinity`/`NaN` tokens (not JSON); `null` (loses the value and cannot be told apart
  from a real null); an object wrapper such as `{"float": "inf"}` (changes the shape of `old`/`new`).
- **Why**: The contract requires JSON Lines; YAML's canonical spellings are unambiguous to a human reader.
  Accepted cost: in history only, the float `.inf` and the string `".inf"` look the same.
- **Revisit when**: the product owner or a consumer of the history needs to tell those apart.
- **Spec impact**: 8.7 (unchanged).

### G. Malformed PATH is a usage error
- **Status**: PO delegated: safest assumption (kept).
- **Decision**: An empty PATH or a PATH with an empty segment (`a..b`, `.a`, `a.`) exits 2 naming PATH, before
  the theatre is opened (no state, no file touched).
- **Alternatives**: exit 5 (path not found); treat empty segments as empty-string keys.
- **Why**: The path grammar has no empty keys; a usage error prevents `set` from ever creating empty-string keys.
- **Revisit when**: keys containing `.` or empty keys come into contract.
- **Spec impact**: 1.5 (unchanged).

### H. Anchors, aliases and merge keys are load failures
- **Status**: PO left to our choice; we chose the conservative refusal.
- **Decision**: A file with an anchor, an alias or a merge key (`<<`) exits 4 naming the file and, where known, the
  key path.
- **Alternatives**: expand them like a YAML loader would (then `set` could write through an alias and change
  values elsewhere); ignore them (wrong values).
- **Why**: Out of contract until step 6; surgical writes through aliases could change values at other paths.
- **Revisit when**: contract step 6.
- **Spec impact**: new 3.9; design Loader refusals; tasks 3.2, 6.1.

### I. Duplicate keys, explicit tags and non-UTF-8 files are load failures
- **Status**: PO delegated: safest assumption (kept).
- **Decision**: Each exits 4 naming the file (and the key path where known).
- **Alternatives**: last key wins (PyYAML behaviour); honour tags; decode with replacement characters.
- **Why**: Duplicate keys make the value ambiguous (YAML 1.2 forbids them); tags could change typing in ways the
  byte-surgical writer cannot preserve; non-UTF-8 bytes cannot be patched safely. Refusing to load cannot corrupt
  data.
- **Revisit when**: a contract step adds tags or non-UTF-8 encodings.
- **Spec impact**: 3.2 (UTF-8 made explicit), 3.3, 3.7; tasks 3.2, 6.1.

Decisions B, C and D concern `set` only and are recorded in config-set-writeback's `research.md`.

## References
- YAML 1.2.2 specification, 10.3.2 Tag Resolution (Core Schema) — https://yaml.org/spec/1.2.2/#1032-tag-resolution
- ruamel.yaml documentation — https://yaml.dev/doc/ruamel.yaml/
- eventsourcing 9 documentation (Application, Aggregate, SQLite module) — https://eventsourcing.readthedocs.io/
