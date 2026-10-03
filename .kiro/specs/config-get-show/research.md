# Research & Design Decisions: config-get-show

## Summary
- **Feature**: `config-get-show`
- **Discovery Scope**: Extension (light discovery). We add a read command and an output renderer to an existing
  CLI. No new dependency.
- **Key Findings**:
  - The loaded `ConfigDocument` already gives every scalar's source file (`ConfigScalar.origin.file`; for a text
    include this is the included file). `--sources` needs no loader change.
  - The quoting and literal-block rules `show` needs already exist in `writeback/scalar_render.py` and
    `writeback/block_scalar.py`. However, `tests/test_core_boundary.py` forbids core modules
    (`troupe/config/*.py`) from importing `troupe.config.writeback`, and those writeback functions raise
    `WriteRefusedError`. The write-neutral part must move to a core module that both write-back and `show` import.
  - A literal block's indentation indicator is relative to the column of the key or dash that owns the block,
    not to the leading spaces of the header line (verified with ruamel.yaml 0.19.1, below). A CR inside literal
    content reads back as a line feed, confirming that a CRLF text include cannot read back exactly (requirement
    4.5's exception).

## Research Log

### Where `show` lives today and what can be reused
- **Context**: Requirement 1 moves step 1's `show PATH` behaviour to `get`. Requirements 3 and 4 need a YAML
  emitter.
- **Sources Consulted**: `src/troupe/cli/main.py` (`handle_show`, `add_show_subparser`,
  `SUBCOMMAND_REGISTRARS`), `src/troupe/cli/render.py` (`format_show_output`), `src/troupe/config/theatre.py`
  (`show_value`), `src/troupe/config/resolve.py` (`resolve_path`, `walk_path`; the empty path `ROOT` resolves to
  the root mapping), `src/troupe/config/model.py`, `src/troupe/cli/shell.py`, `src/troupe/cli/set_command.py`,
  `tests/test_core_boundary.py`.
- **Findings**:
  - `show_value` raises `WrongNodeKindError("is a mapping, not a value", file=…, path=…)`, which gives exit 5 and
    the `<file>: <path>: <problem>` message. Only the problem text needs the `config show` hint.
  - Subcommands are registered through `SUBCOMMAND_REGISTRARS`. Each spec since step 1 adds its own module
    (`set_command.py`, `watch_command.py`). `main.py` still holds `show` and `history`.
  - `open_theatre(directory)` reconciles by default. `history` alone passes `reconcile=False`. So `get` and
    `show` meet 6.2/6.3 simply by opening the theatre the default way.
  - `Theatre.directory` is the theatre directory as given (`--theatre`, `$TROUPE_THEATRE` or cwd).
    `SourceFile.path` is the path as opened: the theatre argument joined with `.troupe/…` and the include specs,
    not resolved.
- **Implications**: `get` is a rename of today's use case plus a message change. `show` adds one core lookup
  (node at an optional PATH) and a CLI renderer. No loader, store or reconcile change.

### Reusable scalar rendering (write-back)
- **Sources Consulted**: `writeback/scalar_render.py` (`Context`, `QuoteStyle`, `can_represent`,
  `choose_style`, private `_render`/`_escape`, plain-probe machinery, `render_scalar`, `render_key`),
  `writeback/block_scalar.py` (`Chomping`, `required_chomping`, `_UNFIT`, `literal_fits`, `render_literal`).
- **Findings**:
  - `choose_style(text, context, preferred=None)` runs the minimal chain plain → single → double. Plain is
    accepted only after a parse probe, and for values only if the core schema still types the text as a string.
    That is exactly 4.3.
  - Key contexts compare text only (the loader treats keys textually). So a plain `true` key is acceptable for
    write-back, but a YAML 1.2 core reader would read it as the boolean `true`, which violates 4.8 ("reads back as
    the same string key").
  - The probes cover a mapping value and a list item (`k: …`, `- …`). They do not cover a scalar alone at the
    document root, where text such as `--- a` or `...` would be read as a document marker.
  - `render_scalar`/`render_key` refuse lone surrogates with `WriteRefusedError` (exit 3). `show` must never
    refuse. The double-quoted escaper (`_escape`) already writes surrogates as `\uXXXX`.
  - `literal_fits` = "no character from `_UNFIT`" + "first non-empty line does not start with a space". `show`
    handles the second case with an indentation indicator instead (4.4), so it needs only the character check.
- **Implications**: move the write-neutral pieces into a core module (`troupe/config/yaml_style.py`): contexts,
  quote styles, representability, style choice, quoting/escaping, chomping and the literal character check. Add
  a document-root context. write-back keeps its refusals and its byte-exact block rewriting, importing the moved
  names (Decision 1).

### Literal block indentation and read-back (experiment)
- **Context**: 4.4 needs an indentation indicator when the text's first line starts with a space. 4.1 needs
  read-back to be exact.
- **Sources Consulted**: experiment with `ruamel.yaml` 0.19.1 (`YAML(typ="safe", pure=True)`) in the project
  environment.
- **Findings** (input → value read):
  - `k: |2\n    a\n  b\n` → `'  a\nb\n'`; `a:\n  k: |2\n      a\n    b\n` → `'  a\nb\n'`.
  - `- |2\n    a\n  b\n` → `['  a\nb\n']`; `k:\n  - |2\n      a\n    b\n` → `{'k': ['  a\nb\n']}`.
  - `- k: |2\n      a\n    b\n` → `[{'k': '  a\nb\n'}]`. The content is 2 past the **key** column (2), not past
    the line's leading spaces (0).
  - Root `|2\n    a\n  b\n` → `'  a\nb\n'` (content at column 2).
  - `k: |\n  a\r\n  b\r\n` → `{'k': 'a\nb\n'}`. CR is read as part of a line break, so the value changes.
  - `k: |- # from x\n  a\n` → `{'k': 'a'}`. A comment after the header is valid.
  - `k: |2\n     \n  a\n` → `{'k': '   \na\n'}`. A leading spaces-only line is kept with an explicit indicator.
  - `- - a\n  - b\n- - c\n` → `[['a','b'],['c']]`. The compact nested sequence works.
- **Implications**: content indent = (column of the key or dash owning the header) + 2, and the indicator,
  when present, is always `2`. This is how the design reads 4.4's "two spaces more than the line that holds the
  block's header": for `- k: |` the line's own column (the dash) would leave the content outside the mapping
  and break 4.1. The indicator is needed whenever the first line that is not empty starts with a space (a
  spaces-only line counts as not empty), which also covers leading empty lines before an indented line.
  The YAML spec places the document root at indentation −1, so a strict parser could read a root `|2` block as
  indented 1. Only `show PATH --sources` on a scalar prints a root literal block, so this is recorded as a risk.

### Reading back in tests
- **Findings**: ruamel's resolver deviates from the 1.2 core schema (`1_0` → int, `2001-12-14` → date). The
  project's loader (`load_config`) applies the exact core schema with `resolve_plain_scalar`. It refuses
  anchors and tags, which `show` never prints, and applies include rules only to strings starting with `file:`.
- **Implications**: read-back tests write the YAML output into a temporary theatre and load it with
  `load_config`, then compare leaves with `scalar_equal` and keys and order with `flatten_leaves`. Fixtures
  avoid text-include contents starting with `file:`. A root scalar document is checked against exact expected
  bytes, since the loader requires a top-level mapping.

## Architecture Pattern Evaluation

| Option | Description | Strengths | Risks / Limitations | Notes |
|--------|-------------|-----------|---------------------|-------|
| Own line-based emitter over `ConfigNode` (chosen) | Walk the source-mapped tree; each output line carries the scalar it holds (or none) | Exact control of which line gets `# from`; scalar typing shared with our loader and writer; no second tree | We own block-layout correctness | Read-back tests guard it |
| ruamel dump + comment insertion by matching lines | Dump plain data, then insert comments | Little code | Fragile matching (repeated keys and values, quoting); ruamel's float forms and resolver differ from ours | Rejected (brief) |
| ruamel `CommentedMap` + `yaml_add_eol_comment` | Build ruamel objects with comments | Library handles layout | ruamel decides comment columns, widths and quoting; needs a second tree; tests would test ruamel | Rejected (brief) |
| Print source spans | Copy original text | Keeps formatting | Does not resolve includes, not block style; contradicts 3.1, 3.4, 3.6 | Rejected |

## Design Decisions

### Decision 1: Move write-neutral YAML scalar presentation to a core module
- **Context**: `show` needs the plain/single/double choice, escaping, chomping and the literal character check.
  Core modules may not import write-back, and write-back's renderers refuse surrogates (exit 3).
- **Alternatives Considered**:
  1. The CLI emitter imports `troupe.config.writeback.scalar_render` directly and makes `_render` public:
     less movement, but `show` would depend on the `set` package, and the refusal and quoting rules would remain
     tied to writing.
  2. Duplicate the quoting logic in the emitter: two homes for one decision (violates "one decision, one place").
  3. Move the write-neutral part to `troupe/config/yaml_style.py` (chosen).
- **Selected Approach**: `yaml_style.py` owns `Context` (plus a new `DOCUMENT` context for a scalar alone at
  the root), `QuoteStyle`, `quote_style_of`, `can_represent`, `choose_style`, `quote(text, style)` (formerly
  `_render`) with its escaper and plain probes, plus `Chomping`, `required_chomping` and
  `literal_characters_fit(text)` (formerly `_UNFIT`). `writeback/scalar_render.py` and
  `writeback/block_scalar.py` import these and re-export the names other write-back modules already use, so no
  other write-back module changes. Refusals (`_refuse_surrogates`, `literal_fits`' first-line rule,
  `render_literal`, `rewrite_block`) stay in write-back.
- **Rationale**: one home for "how a string is written in YAML", usable by both a writer and a viewer. The
  dependency direction stays CLI → core and writeback → core.
- **Trade-offs**: touches write-back files (a pure move). The full existing write-back suite (byte-exact) must
  still pass unchanged.
- **Follow-up**: `tests/test_core_boundary.py` keeps passing (the new module imports only core and ruamel).

### Decision 2: The emitter lives in the CLI layer and produces annotated lines
- **Context**: steering `structure.md` says the CLI renders output, and `format_show_output` is already in
  `cli/render.py`.
- **Selected Approach**: `troupe/cli/yaml_output.py` exposes `render_yaml(node, *, label)`. Internally it builds a
  sequence of lines, each with an optional `SourceFile` (the scalar the line holds). With `label` given, every
  line that has a source gets ` # from <label(source)>`. Literal content lines, `key:` lines, `{}`/`[]` lines
  and `- ` lines that open a nested collection have no source.
- **Rationale**: 5.5 ("equals the output without `--sources` once suffixes are removed") holds by construction,
  because the same lines are produced either way.
- **Trade-offs**: none significant. The emitter is pure and unit-testable.

### Decision 3: Layout rules
- Two spaces per level. Mapping entries `key: value`. A non-empty collection value starts on the next line,
  indented 2 more than the key. List items `- ` are indented 2 more than the key holding the list (column 0 for
  a printed list). A collection that is a list item starts on the dash line (compact form: `- a: 1` / `  b: 2`;
  `- - a`). Empty collections are `{}` / `[]`. No `---`/`...`. Output ends with one `\n`. Lines are joined with
  `\n`.
- **Rationale**: requirements 3.1–3.7. The compact form is valid YAML 1.2 (verified) and the usual hand-written
  style.

### Decision 4: Scalars
- Non-strings: `true`/`false` (bool checked before int), `null`, `str(int)`, `repr(float)` for finite floats
  (always matches the core float pattern, e.g. `1e+16`, `-0.0`), and `.inf`/`-.inf`/`.nan`.
- One-line strings: `choose_style(text, BLOCK_VALUE, None)`. For a scalar alone at the root (`show PATH
  --sources`): `choose_style(text, DOCUMENT, None)`.
- Strings with a line feed (any text include, and multi-line strings): a literal block. The chomping comes from
  `required_chomping`. The indentation indicator `2` is added when the first line that is not empty starts with
  a space. Content lines are the text split on `\n` (a `\r` stays at the end of its line, so CRLF reaches stdout
  unchanged). An empty line is written as a bare line break, and other lines are prefixed with the content
  indent.
  - A non-include multi-line string with a character that fails `literal_characters_fit` is written
    double-quoted instead (4.5). A text include is always a literal block, even then (4.4, 4.5; PO decision on
    the requirements, choice 3).
  - A text include **without** a line feed (e.g. `hello` with no final newline) is still a literal block: `|-`
    followed by one content line.
- Keys: `choose_style(key, BLOCK_KEY, None)`, except that a key whose plain form the core schema would read as
  a non-string (`true`, `null`, `1.5`) is not written plain. The chain starts at single-quoted (4.8).
- **Rationale**: 4.1–4.8. `show` never refuses: surrogates are escaped by double quoting, since plain and
  single-quoted cannot represent them.

### Decision 5: Source labels
- `label = Path(os.path.relpath(os.path.abspath(file.path), os.path.abspath(theatre.directory))).as_posix()`:
  lexical (`abspath` normalises `.` and `..` without touching symbolic links), relative to the theatre directory
  as given, with `..` only at the start (5.4). It lives in `cli/render.py` as `source_label(file, theatre_dir)`.
- **Alternatives**: `real_path` (resolved symlinks) was rejected because users recognise the paths they wrote
  (same convention as error messages, `errors.py` docstring).

### Decision 6: `get` and `show` commands in their own CLI module
- `troupe/cli/get_show_command.py` holds `add_get_subparser`, `add_show_subparser`, `handle_get` and
  `handle_show`, following the one-module-per-spec registrar pattern (`set_command.py`, `watch_command.py`).
  `main.py` drops `handle_show`/`add_show_subparser` and registers `get` and `show` in `SUBCOMMAND_REGISTRARS`.
- Core: `theatre.show_value` is renamed `get_value`. Its refusal problem text becomes
  `is a mapping, not a value; print it with `troupe config show <PATH>`` (1.2). A new
  `node_at(theatre, path | None)` returns the root mapping for `None` and otherwise `resolve_path`.
- **Rationale**: a buffered handler keeps the "stdout only on success" rule (6.7). `get --sources` is an unknown
  option, so argparse raises the usage error (exit 2, 1.4).

## Synthesis Outcomes
- **Generalization**: `show`'s four outputs (scalar raw, scalar with source, mapping or list, whole
  configuration) are one operation: "render a node, optionally labelled". The raw scalar form is the
  exception, reusing `format_show_output`. The whole configuration is the node at the empty path.
- **Build vs. adopt**: adopting ruamel's emitter was rejected (see table). We build a ~200-line emitter on
  existing, tested scalar rules. No new dependency.
- **Simplification**: no new error type (exit 5 and 2 reuse `WrongNodeKindError`, `PathNotFoundError`,
  `UsageError`). No configurable indentation, comment column or output format. No separate "source map"
  structure: the line model carries the `SourceFile` directly.

## Risks & Mitigations
- Moving scalar rendering could change `set`'s bytes. Mitigation: a pure move with names re-exported; the
  existing write-back suite must pass unchanged; it is a separate task with its own commit.
- A root literal block with an indentation indicator (`show PATH --sources` on a text include whose first line
  starts with a space) may be read with a different indentation by strict YAML parsers (root at −1).
  Mitigation: documented. Our read-back tests use the project's reader. Revisit if a user reports it.
- Text includes that a literal block cannot reproduce (CR, control characters) produce output that does not
  read back exactly, or is not valid YAML. This is accepted by requirement 4.5 (PO decision). Tests pin the
  exact bytes.
- Implicit keys longer than 1024 characters are not valid YAML. Accepted (out of contract, the same as
  write-back). Configurations are small.

## References
- YAML 1.2.2 specification, sections 8.1.1 (block scalar headers, indentation indicator), 8.2 (block
  collections, compact forms), 10.3.2 (core schema).
- `tests/test_core_boundary.py`: import boundary of core modules.
- config-set-writeback design: scalar quoting (requirements 6.x) and literal blocks (9.x).
