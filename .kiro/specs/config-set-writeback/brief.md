# Brief: config-set-writeback

## Problem
Developers review every change to their configuration as a diff. `troupe config set` must change a
setting in whichever file holds it, and change **only the bytes of that value**. Comments, quoting, layout,
indentation, blank lines and line endings elsewhere must stay exactly as they were.

## Current State
config-foundation provides loading with includes into a source-mapped model, path resolution, the event
store/projection with import, and error/exit-code conventions. Nothing writes to the user's files yet.

## Desired Outcome
`uv run troupe config set PATH VALUE [--theatre DIR]` follows the step 1 contract section `set PATH VALUE`:
- VALUE is parsed as a single YAML 1.2 core-schema flow node.
- Target resolution: an existing value is changed in the file that contains it. A text-include target's
  content becomes VALUE exactly, with CRLF preserved if the target used CRLF; a non-string VALUE → 3.
  A new key goes into the file that holds the deepest existing ancestor mapping, and missing intermediate
  mappings are created there. An index equal to a list's length appends; a larger index → 5. Replacing a
  value that wholly comes from an include → 3.
- Rendering rules: quoting-style retention or fallback; new keys and values copy the sibling's style;
  minimal double-quote escaping; block vs flow collections at the file's indentation; literal blocks for
  multi-line strings; block scalar indicator and chomping kept; folded paragraph re-wrapping; a
  more-indented line in a folded value → 3; new entries placed after trailing comments; flow-collection
  appends on the last entry's line; scalar → block collection keeps the key line through its comment;
  end-of-line comment column preservation, counted in code points.
- Events: one `set` per leaf changed or created, one `remove` per leaf that no longer exists, nothing for
  a no-op.
- Files are written atomically. On any refusal or error, no file changes.

## Approach
A text-patch engine works on the original file contents, using spans and styles from the foundation's
source-mapped model. Each rule is implemented as a small, separately tested function. The whole document is
never re-dumped. After patching, the result is re-parsed to check that the new configuration equals the
intended one. If it does not, the write is refused (exit 3) rather than risk corrupting the file.

## Scope
- **In**: VALUE parsing; target resolution rules 1–4; all "How it is written" rules; leaf diffing and
  set/remove event emission; atomic writes; byte-exact preservation tests.
- **Out**: loading/includes/path semantics (config-foundation); step 5's change to the new-key target rule;
  anchors/aliases; TAB-before-comment files; concurrent writers.

## Boundary Candidates
- VALUE parsing (CLI string → typed node)
- Write-target resolution (which file, which span, insert vs replace vs append)
- Scalar rendering (quoting style choice, escaping, block/folded scalars)
- Collection rendering and insertion (block/flow, indentation, trailing comments)
- Line-level fix-ups (end-of-line comment column, CRLF)
- Event diffing (old vs new leaves)

## Out of Boundary
- Defining the source-mapped model, the event types or the error types (consumed from config-foundation)
- `show` and `history` output

## Upstream / Downstream
- **Upstream**: config-foundation (model, path resolution, event API, errors), ruamel.yaml.
- **Downstream**: later contract steps (step 5 changes new-key placement; step 6 anchors).

## Existing Spec Touchpoints
- **Extends**: none
- **Adjacent**: config-foundation

## Constraints
- Byte-surgical: after a successful `set`, only the required bytes differ in any file.
- Conservative: when in doubt, refuse with exit 3 and leave files untouched.
- ruamel marks give character indices into decoded text. Patch at character level and keep the file's
  encoding and line endings.
- `mypy --strict`, ruff, byte-comparison tests on real fixtures driven through the CLI.
