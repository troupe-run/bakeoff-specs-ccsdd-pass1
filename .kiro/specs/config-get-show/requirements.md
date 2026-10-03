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

## Requirements
<!-- Will be generated in /kiro-spec-requirements phase -->


