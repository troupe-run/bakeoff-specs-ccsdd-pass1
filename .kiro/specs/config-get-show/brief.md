# Brief: config-get-show

## Problem
Developers can only read one scalar at a time: `troupe config show PATH` refuses mappings and lists (exit 5).
They cannot see a whole section, or the whole configuration, with its includes resolved. When a configuration
is split across files with `file:` includes, they also cannot tell which file a value comes from without
following the includes by hand.

## Current State
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

## Desired Outcome
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

## Approach
Chosen: **our own small block-style emitter over the source-mapped tree, reusing the existing scalar
renderers.**
- A pure function walks a `ConfigNode` (`ConfigMapping` / `ConfigSequence` / `ConfigScalar`) and produces
  output lines. Each line records the scalar it holds, if any. `--sources` then appends ` # from <file>` to
  those lines. Because the comment is attached when the line is built, there is no text post-processing and
  no guessing which output line belongs to which value.
- Scalars are rendered with the writeback quote-style rules (`choose_style` / `render_scalar` / `render_key`
  with no preferred style). A string comes out plain when it reads back as the same string, otherwise quoted.
  Text includes, and other multi-line strings that `literal_fits`, come out as literal blocks with the right
  chomping indicator. Other scalars come out as YAML 1.2 core forms (`true`, `null`, decimal integers, and
  floats including `.inf`, `-.inf`, `.nan`). The rule throughout: **the printed YAML must load back to the
  same configuration.**
- The parts of `scalar_render` / `block_scalar` that both writeback and show need are reused or moved to a
  shared, write-neutral module. Show must never raise a "write refused" error. The design decides where the
  shared code lives, and keeps one decision in one place.
- `get` = today's `show_value` + `format_show_output`, with the exit-5 message extended to mention
  `config show`. The new `show` resolves the node: a scalar goes to `format_show_output`, anything else to the
  emitter.

Rejected alternatives:
- *ruamel.yaml dump of plain data, then insert comments by matching output lines to values*: we would not
  control how lines map to values, line wrapping or quoting. Strings that look like other types, and keys
  repeated at different depths, make the matching fragile.
- *ruamel.yaml `CommentedMap` with `yaml_add_eol_comment` and `LiteralScalarString`*: possible, but ruamel
  decides comment columns, line width, float forms and quoting in ways that differ from our loader's YAML 1.2
  core typing. We would be testing ruamel's emitter instead of our contract. We would also need a second
  representation of the tree.
- *Printing the source text spans*: raw text keeps comments and flow style and does not resolve includes,
  which contradicts "block style, includes resolved".

## Scope
- **In**: the `get PATH` subcommand (taking over config-foundation Requirement 6, with the exit-5 message
  mentioning `config show`); `show [PATH]` with an optional PATH; the block-style YAML emitter (mappings,
  sequences, nested and empty collections, keys needing quotes, scalar typing that round-trips, text includes and
  multi-line strings as literal blocks); `--sources` with paths relative to the theatre directory; reconcile-first
  for both commands; updating step-1/2 tests and docstrings that use `show PATH` for scalar reads where the
  contract now means `get` (scalar `show` still works the same, so most stay valid).
- **Out**: changing `set`, `history` or `watch` behaviour; preserving the user's original formatting, comments,
  flow style or quoting in `show` output (the contract asks for block style, includes resolved); JSON or other
  output formats; showing history alongside values; anchors/aliases (step 6); Windows.

## Boundary Candidates
- Read-side use cases: `get_value` (scalar or exit 5 with the `config show` hint) and `show_node`
  (resolve PATH, or use the root when PATH is omitted).
- YAML emitter: `ConfigNode` → lines, each with optional provenance. Pure, with no I/O.
- Scalar/key/literal rendering shared with writeback (a write-neutral home for reused code).
- Source labelling: `SourceFile.path` → theatre-relative POSIX string.
- CLI: `get` and `show` registrars and the `--sources` flag; `show`'s output stays buffered.

## Out of Boundary
- Loading, includes, path resolution and error types (config-foundation; reused unchanged, apart from the
  wording of the exit-5 message).
- Reconcile and lock semantics (config-file-sync; reused as is).
- Write-back rendering rules (config-set-writeback). They may only be *moved* to a shared module, not changed.

## Upstream / Downstream
- **Upstream**: config-foundation (`open_theatre`, `resolve_path`, `ConfigDocument`, the model's
  `Origin`/`IncludeRef`, `WrongNodeKindError`/`PathNotFoundError`, `format_show_output`); config-set-writeback
  (`scalar_render`, `block_scalar`); config-file-sync (reconcile-first opening).
- **Downstream**: step 6 (anchors/aliases) may need to say how shared nodes are shown and sourced. Later steps
  that change write targets may rely on `--sources` to explain where a `set` will write.

## Existing Spec Touchpoints
- **Extends**: config-foundation. Requirement 1.1 and the Introduction/Requirement 6 name `show PATH`. Requirement 6
  becomes the `get PATH` behaviour (6.7 adds "mentions `config show`"), and the new `show` is owned by
  config-get-show. Add a step-3 note in the same style as the step-2 notes. config-file-sync: the list of
  commands that reconcile first (`show`, `set`, `watch`) gains `get`; `show` stays in it.
- **Adjacent**: config-set-writeback (shared scalar rendering; its byte-exact tests must still pass unchanged
  after any move).
- Tests likely to touch: `tests/test_e2e_show.py`, `tests/test_render.py`, `tests/test_theatre.py`
  (`show_value`), `tests/test_cli_usage.py` (subcommand list and usage), `tests/test_core_boundary.py`.

## Constraints
- Python 3.12+, uv, `mypy --strict`, ruff, real-file and real-CLI tests (steering `testing.md`). No new
  dependencies.
- Output is UTF-8 bytes through the buffered handler. Nothing reaches stdout on failure.
- `show`/`get` create no files outside `.troupe/` and leave no extra file behind (PO decision E).
- **Round-trip rule** (conservative default): loading `show`'s YAML output with the project's loader gives the
  same values as the configuration at PATH (same types, same strings byte for byte, same key order).
- Open choices to settle and record in requirements/research (proposed defaults):
  1. *`show PATH --sources` when PATH is a scalar*: print the scalar as a one-line YAML document (or a literal
     block) with the `# from <file>` comment, so the comment is never confused with the value. Alternative:
     ignore `--sources` for scalars. Proposed: annotate, because the contract promises a source for "every line
     that holds a scalar".
  2. *Literal blocks and comments*: a comment cannot sit inside block content, so the comment goes on the
     header line (`key: |  # from …` is valid YAML). Content lines carry no comment.
  3. *A text include that a literal block cannot represent exactly* (for example a `\r`, a non-printable
     character or a leading-space first line that needs an indentation indicator): use an indentation indicator
     where that suffices. Otherwise fall back to a double-quoted scalar, which keeps the value exact. Never
     alter the value.
  4. *Other multi-line strings* (not includes): a literal block when `literal_fits`, otherwise double-quoted.
  5. *Empty mapping or list*: printed as `{}` / `[]`. This is the one flow form in the output, since block
     style has no empty form. It holds no scalar, so it gets no comment. Empty root: `{}`.
  6. *Indentation*: 2 spaces per level. Sequence items are indented under their key (`key:\n  - a`). A mapping
     inside a sequence item starts on the dash line (`- a: 1\n  b: 2`). No `---` marker. The output ends with a
     newline.
  7. *Comment spacing*: one space before `#` (`value # from .troupe/troupe.yml`). No column alignment.
  8. *Source label*: `SourceFile.path` (the path as opened, not the symlink-resolved path) made relative to the
     theatre directory as `os.path.relpath` would, with `/` separators. An include outside the theatre directory
     gives a `../` path. For the configuration file itself the label is `.troupe/troupe.yml`.
  9. *Keys*: rendered with `render_key`, quoted only when needed, in document order. Key lines that hold a
     mapping or list get no comment. A scalar key-value line gets the value's source.
  10. *exit-5 message for `get`*: keep the step-1 shape naming PATH and the file, and add a hint such as
      "use `troupe config show <PATH>`".
