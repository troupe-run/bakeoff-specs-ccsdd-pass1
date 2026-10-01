# Requirements Document

## Project Description (Input)
### Problem
Developers review every change to their configuration as a diff. `troupe config set` must change a
setting in whichever file holds it, and change **only the bytes of that value**. Comments, quoting, layout,
indentation, blank lines and line endings elsewhere must stay exactly as they were.

### Current State
config-foundation provides loading with includes into a source-mapped model, path resolution, the event
store/projection with import, and error/exit-code conventions. Nothing writes to the user's files yet.

### Desired Outcome
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

### Approach
A text-patch engine works on the original file contents, using spans and styles from the foundation's
source-mapped model. Each rule is implemented as a small, separately tested function. The whole document is
never re-dumped. After patching, the result is re-parsed to check that the new configuration equals the
intended one. If it does not, the write is refused (exit 3) rather than risk corrupting the file.

### Scope
- **In**: VALUE parsing; target resolution rules 1–4; all "How it is written" rules; leaf diffing and
  set/remove event emission; atomic writes; byte-exact preservation tests.
- **Out**: loading/includes/path semantics (config-foundation); step 5's change to the new-key target rule;
  anchors/aliases; TAB-before-comment files; concurrent writers.

### Constraints
- Byte-surgical: after a successful `set`, only the required bytes differ in any file.
- Conservative: when in doubt, refuse with exit 3 and leave files untouched.
- Patch at character level and keep the file's encoding and line endings.
- `mypy --strict`, ruff, byte-comparison tests on real fixtures driven through the CLI.

### Step 1 Contract, section `set PATH VALUE` (authoritative, as supplied by the product owner)
- **General**: Invocation `troupe config set PATH VALUE --theatre <dir>`. VALUE parsed as a single YAML 1.2 (core schema) flow node: `0.2` float, `true` bool, `null` null, `abc` and `"0.2"` strings, `{a: 1}` mapping, `[a, b]` list, `"line one\nline two\n"` multi-line string. Exit codes: 0 ok, 2 usage, 3 write refused (no file changed), 4 cannot load, 5 path not found / wrong kind of node. Non-zero exit: stdout empty, stderr names path or file. State only under `<dir>/.troupe/`. Out of contract: TAB before EOL comment, multi-document, anchors/aliases/merge keys, concurrent writers.
- **set PATH VALUE**: Updates the configuration and writes the change back to the files, byte-surgically: after a successful set, the only bytes that differ in any file are those this section requires to change.
- **Where the change is written**:
  1. An existing value is changed in the file that contains it. For a text include, the target file's content becomes VALUE exactly, except that if the target file used CRLF line endings, the new content uses CRLF line endings too (VALUE must then be a string, else exit 3).
  2. A new key is written into the file containing the deepest existing ancestor mapping of PATH; missing intermediate mappings are created there.
  3. PATH ending in the index equal to a list's length appends to that list. A larger index → exit 5.
  4. Replacing the value at a path whose whole value comes from an include (e.g. `agents.coder` when it is `file:agents/coder.yaml`) → exit 3.
- **How it is written**:
  - A changed scalar keeps its quoting style if that style can represent the new value; otherwise plain if possible, else single-quoted, else double-quoted.
  - A new key, and a new string value, copy the quoting style of the preceding sibling key or value; with no sibling, plain (or the minimal quoting the text needs).
  - A double-quoted scalar escapes only what YAML requires (`\\`, `\"` and control characters, as `\t`, `\n`, …); every other character is written literally.
  - A new collection value is written in block style at the file's indentation (default 2), unless its parent is a flow collection, in which case it is written in flow style.
  - A multi-line string replacing a plain or quoted scalar is written as a literal block (`|`, with `|-` when the value has no trailing newline). A block scalar keeps its indicator and chomping unless the value's trailing newlines require a different chomping.
  - A new key whose value is a multi-line string is written as a literal block in the same way, unless its parent is a flow collection, where it is written as a double-quoted flow scalar.
  - Folded (`>`) values: paragraphs whose text is unchanged keep their bytes. A changed paragraph is re-wrapped, greedily by word, at the width of the longest content line of that paragraph before the change (a single word longer than the width takes a line of its own). A paragraph with no counterpart in the old value is wrapped at the width of the value's longest old content line. A change that would put a more-indented line into a folded value → exit 3.
  - New entries and trailing comments: a new list item or mapping key goes after the collection's last entry and below any comment lines that follow it before the next less-indented line. A comment line indented less than a collection's entries ends that collection.
  - In a flow collection, a new entry is appended after the last entry, on that entry's line.
  - When a scalar is replaced by a block collection, the key's line keeps its bytes up to and including its end-of-line comment.
  - End-of-line comments: when a changed value's length changes, the comment on that line is moved back to its previous column; if the new value reaches that column, exactly one space separates value and comment. No other line changes. Columns count Unicode code points.
- **history**: A set emits one `set` event per leaf whose value it changes or creates, and one `remove` event (new null) per leaf that no longer exists. A set that changes nothing emits nothing (and should not modify files). The first command run against a theatre imports it (import events); set must also trigger import first if never imported.

## Introduction
This specification covers the write side of the troupe configuration subsystem (contract step 1, section
`set PATH VALUE`): the `troupe config set PATH VALUE` command, which changes one setting of a theatre's
configuration and writes that change back into the hand-edited YAML (or text-include) file that holds it,
touching only the bytes the change requires. It also records the change in the configuration history that
`troupe config history` prints. Loading, includes, path addressing, the history store, the import and the
shared error conventions are provided by config-foundation and are used unchanged.

## Boundary Context
- **In scope**: the `set` subcommand and its VALUE argument; deciding which file and which part of it changes;
  every rule for how changed and new values, keys, list items and collections are written; preservation of all
  other bytes, line endings and encoding; refusing unsafe writes (exit 3) and missing paths (exit 5); atomic
  writing; deciding which leaves a set changed, created or removed and recording the matching `set`/`remove`
  events.
- **Out of scope**: how the configuration is located, loaded and included, how PATH is parsed and resolved,
  how the history is stored, imported and printed, `show` and `history` output (all config-foundation);
  step 5's change to where new keys are written; anchors, aliases and merge keys; multi-document files; TAB
  characters before end-of-line comments; keys containing `.`; digit-only mapping keys; concurrent
  invocations; Windows.
- **Adjacent expectations**: `set` relies on config-foundation to load the configuration with the exact file,
  position and style of every value, to resolve PATH with the same rules as `show`, to import a never-imported
  theatre before the command runs, to append `set`/`remove` events to the same history `history` prints (all
  or none per invocation), and to map errors to exit codes and messages in the shared format.
- **Choices made where the contract is silent** (conservative; rationale in `research.md`; the items marked
  with a letter were put to the product owner on 2026-10-01, see "Product Owner Decisions" in `research.md`):
  - `set` prints nothing on success (1.1).
  - An empty or comment-only VALUE, a VALUE containing a block-style node, a tag, an anchor or an alias, and
    VALUE mappings with duplicate, empty or non-scalar keys are usage errors (1.3, 1.4).
  - Non-string scalars are written in the spelling given in VALUE (`0x1F` stays `0x1F`) (6.3).
  - A set whose VALUE equals the current value is a no-op (exit 0) even where the same set with a different
    VALUE would be refused (14.6).
  - A null value (`key:` with nothing) is not a mapping: a PATH below it is not found (3.4).
  - A PATH that would need a digit-only mapping key, or an index below a missing list, is not found (3.6, 4.3).
  - Setting a mapping over a mapping, or a list over a list, changes only the entries that differ (5.1–5.3).
  - D, PO delegated (safest assumption kept): a VALUE string that starts with `file:` is written as a plain
    string and therefore becomes an include when the configuration is read; if its target is missing, forms a
    cycle or cannot be loaded, the set exits 3 and no file changes (2.7, 2.8, 14.8, 15.7).
  - B, PO delegated (changed): a set that must change more than one file is carried out, not refused. All file
    contents are built and verified in memory before any file is written; if a write fails, the files already
    written are restored and the set exits 3 (13.6, 14.5, 14.10).
  - C, confirmed by the product owner: a set that would change the configuration anywhere other than at PATH
    (for example a file included at two places), or whose in-memory read-back is not the intended
    configuration, is refused with exit 3 and no file changed (14.1, 14.2).
  - A, confirmed by the product owner: files are the truth. `set` reads the files as they are on disk; a value
    edited by hand since the import is the `old` value of the next `set` event, and the hand edit itself is
    not recorded (15.1, 15.2).
  - E, confirmed with a condition: files are written through a temporary file that is renamed over the target,
    and no extra file is left behind once the command finishes, whether it succeeds or fails (14.3, 14.4).
  - Set events are recorded in the new configuration's document order, followed by remove events in the
    previous configuration's document order (15.5).
  - If the history cannot be recorded after the files were written, the files are restored and the command
    exits 4 (15.6).

## Requirements

### Requirement 1: Command invocation and VALUE parsing
**Objective:** As a developer, I want to run `troupe config set PATH VALUE` with VALUE written as YAML, so that I can change a setting to a value of any type from the shell.

#### Acceptance Criteria
1. When a developer runs `uv run troupe config set PATH VALUE [--theatre DIR]` from the repository root and the set succeeds, the troupe CLI shall change the configuration value at PATH to VALUE, write nothing to stdout and exit with code 0.
2. The troupe CLI shall interpret VALUE as a single YAML 1.2 core-schema flow node, so that `0.2` is a float, `true` a boolean, `null` null, `abc` and `"0.2"` strings, `{a: 1}` a mapping, `[a, b]` a list and `"line one\nline two\n"` the two-line string `line one⏎line two⏎`.
3. If VALUE is not valid YAML, is empty or contains only whitespace or comments, contains more than one document, contains a block-style node (block mapping, block list, literal or folded scalar), or uses a tag, an anchor or an alias, then the troupe CLI shall exit with code 2 and write a stderr message that names PATH and states that VALUE is not a single flow node.
4. If a mapping inside VALUE has two equal keys, an empty key or a key that is not a scalar, then the troupe CLI shall exit with code 2 and write a stderr message that names PATH and the offending key.
5. If the `set` command line is malformed (missing PATH or VALUE, surplus argument, unknown option) or PATH is malformed, then the troupe CLI shall exit with code 2 under the same rules as the other `troupe config` subcommands.
6. When a set runs against a theatre whose history has never been imported and the configuration loads, the troupe CLI shall import the theatre before applying the set, even if the set is then refused or the path is not found.

### Requirement 2: Changing an existing value
**Objective:** As a developer, I want an existing setting changed where it is written, so that the diff shows the change in the file I expect.

#### Acceptance Criteria
1. When PATH resolves to an existing value, the troupe CLI shall write the change into the file that contains that value, including a file reached through YAML includes, and shall leave the including files unchanged.
2. When PATH resolves to a text include and VALUE is a string, the troupe CLI shall replace the text-include target file's whole content with VALUE exactly and leave the file containing the `file:` directive unchanged.
3. While the text-include target file uses CRLF for every line break, when its content is replaced, the troupe CLI shall write each line break of VALUE that is not already CRLF as CRLF.
4. If PATH resolves to a text include and VALUE is not a string, then the troupe CLI shall exit with code 3, name PATH and the text-include target file, and change no file.
5. If PATH resolves to a value whose whole value comes from a YAML include (for example `agents.coder` when it is `file:agents/coder.yaml`) and VALUE differs from it, then the troupe CLI shall exit with code 3, name PATH and the included file, and change no file.
6. When PATH resolves to a value inside a YAML-included file that is not itself the include's whole value, the troupe CLI shall change it inside the included file under the same rules as any other existing value.
7. When VALUE is, or contains, a string starting with `file:` that is written into a YAML file, the troupe CLI shall write it as a string scalar under the same rules as any other string, so that the configuration read back contains the include's content at that place.
8. When a string starting with `file:` is set at a place that already holds an include written with exactly that string, the troupe CLI shall treat that place as unchanged.

### Requirement 3: Adding new keys
**Objective:** As a developer, I want to add a setting that does not exist yet, so that I can extend the configuration from the shell.

#### Acceptance Criteria
1. When PATH does not exist and the deepest existing value along PATH is a mapping, the troupe CLI shall add the missing key to that mapping in the file that contains that mapping.
2. When more than one segment of PATH is missing below that mapping, the troupe CLI shall create the missing intermediate mappings in the same file, nested under the new key, with VALUE at the last segment.
3. When the root configuration file is empty or contains only comments, the troupe CLI shall add the new key to the root configuration file.
4. If the deepest existing value along PATH is a scalar (including null and a text include), then the troupe CLI shall exit with code 5 and write a stderr message that names PATH and the file holding that scalar.
5. If a non-digit segment of PATH is applied to an existing list, then the troupe CLI shall exit with code 5 and write a stderr message that names PATH and the file holding the list.
6. If adding PATH would require creating a mapping key that consists only of digits, then the troupe CLI shall exit with code 5 and write a stderr message that names PATH and the file holding the deepest existing value.

### Requirement 4: Appending to lists
**Objective:** As a developer, I want to append an item to a list by addressing the index after its last item, so that I can grow lists without rewriting them.

#### Acceptance Criteria
1. When the last segment of PATH is an index equal to the length of an existing list, the troupe CLI shall append VALUE as a new last item of that list in the file that contains the list.
2. If a segment of PATH is an index greater than the length of the list it is applied to, then the troupe CLI shall exit with code 5 and write a stderr message that names PATH and the file holding the list.
3. If an index equal to a list's length is followed by further PATH segments, then the troupe CLI shall exit with code 5 and write a stderr message that names PATH and the file holding the list.

### Requirement 5: Replacing collections and changing the kind of a value
**Objective:** As a developer, I want to set a whole mapping or list, or turn a scalar into a collection and back, so that I can restructure settings with one command and still get a minimal diff.

#### Acceptance Criteria
1. When VALUE is a non-empty mapping and the existing value at PATH is a mapping, the troupe CLI shall leave the entries whose values are unchanged byte-identical, change the entries whose values differ under the rules for changed values, add the keys that are new under the rules for new keys, remove the entries whose keys are absent from VALUE, and keep the existing order of the remaining keys.
2. When VALUE is a non-empty list and the existing value at PATH is a list, the troupe CLI shall compare items by index, leave unchanged items byte-identical, change differing items, append surplus new items and remove surplus old items.
3. When removing a mapping entry or list item written in block style, the troupe CLI shall remove the lines from the entry's first line through its last content line, including comment lines inside the entry, and shall keep comment lines and blank lines that follow the entry's last content line.
4. When removing an entry of a flow collection, the troupe CLI shall remove the entry together with one adjoining comma separator and the space after it, keeping the rest of the collection's text.
5. When a scalar (or a flow collection whose parent is a block collection) replaces a block collection, the troupe CLI shall write the scalar on the key's line (or the list item's line), remove the collection's lines through its last content line, and keep the comment lines that follow its last content line.
6. When VALUE is an empty mapping or empty list and differs from the existing value, the troupe CLI shall write it as `{}` or `[]` in place of the existing value.
7. If a removal would have to delete part of a line that also holds content that remains (for example the first key of a mapping written on a list item's `-` line), then the troupe CLI shall exit with code 3 and change no file.
8. If, while a collection is replaced, a value inside it whose whole value comes from a YAML include would change, then the troupe CLI shall exit with code 3, name the path of that value and the included file, and change no file.
9. When a removed entry's value comes from an include, the troupe CLI shall remove only the entry from the including file and leave the included file unchanged.

### Requirement 6: Quoting style of changed scalars
**Objective:** As a developer, I want a changed value to keep the quoting I chose, so that the diff shows only the new value.

#### Acceptance Criteria
1. When a scalar written plain, single-quoted or double-quoted is changed to a string that this style can represent, the troupe CLI shall write the new value in the same style.
2. When the existing style cannot represent the new value, the troupe CLI shall write the value plain if possible, otherwise single-quoted, otherwise double-quoted.
3. The troupe CLI shall write integers, floats, booleans and null plain, in the spelling given in VALUE (for example `0.2`, `0x1F`, `true`, `~`).
4. The troupe CLI shall treat a string as representable plain only if it is on one line, has no leading or trailing whitespace, contains no control characters, does not read back as null, a boolean or a number, and reads back unchanged as a plain scalar in its position (inside a flow collection the characters `,[]{}` are not allowed).
5. The troupe CLI shall treat a string as representable single-quoted only if it is on one line and contains no control character other than TAB, and shall write each `'` inside it as `''`.
6. When writing a double-quoted scalar, the troupe CLI shall escape `\` as `\\`, `"` as `\"` and each control character (U+0000–U+001F, U+007F–U+009F) as `\0`, `\a`, `\b`, `\t`, `\n`, `\v`, `\f`, `\r`, `\e` or `\N` where such an escape exists and as `\xHH` otherwise, and shall write every other character literally, including non-ASCII characters.

### Requirement 7: Style of new keys and new values
**Objective:** As a developer, I want new entries to look like their neighbours, so that the file stays consistent.

#### Acceptance Criteria
1. When a new key is added to a mapping that already has entries, the troupe CLI shall write the key in the quoting style of the mapping's last remaining key (the last key that stays in the written file; a key the same set removes does not count) if that style can represent it, and otherwise in the minimal style (plain if possible, otherwise single-quoted, otherwise double-quoted).
2. When a new string value is written for a new key or a new list item and the preceding sibling (the last remaining entry's value or the last remaining item) is a plain, single-quoted or double-quoted scalar, the troupe CLI shall write the string in that style if the style can represent it, and otherwise in the minimal style.
3. When a new key or new string value has no preceding sibling, or the preceding sibling is a collection or a literal or folded block scalar, the troupe CLI shall write it in the minimal style.
4. The troupe CLI shall write every key and string inside a newly written collection, including created intermediate mappings, in the minimal style.

### Requirement 8: Writing new collections
**Objective:** As a developer, I want new mappings and lists written in the layout my file already uses, so that they read naturally.

#### Acceptance Criteria
1. When a new mapping or list value is written whose parent is a block collection, the troupe CLI shall write it in block style, indenting each nested mapping by the file's indentation step.
2. The troupe CLI shall take a file's indentation step from the first block mapping in that file that is nested directly as the value of a block mapping entry (the column of its keys minus the column of the parent key), and shall use 2 when the file has no such mapping.
3. When a new block list is written as the value of a mapping key, the troupe CLI shall place its `-` indicators at the same offset from the key as the first block list written under a mapping key in that file (0 for a file that writes lists flush with their key), and at the indentation step when the file has no such list.
4. When a mapping or list is written as an item of a block list, the troupe CLI shall start it on the item's `-` line after `- ` and align its further entries two columns right of the `-`.
5. When a new mapping or list value is written whose parent is a flow collection, the troupe CLI shall write it in flow style, as `{k: v, k2: v2}` or `[a, b]`.
6. The troupe CLI shall write an empty mapping or empty list as `{}` or `[]` in either style.

### Requirement 9: Multi-line strings and block scalars
**Objective:** As a developer, I want multi-line text written as readable blocks, so that prompts and notes stay easy to review.

#### Acceptance Criteria
1. When a string containing a line break replaces a plain or quoted scalar whose parent is a block collection, or is written for a new key or new list item whose parent is a block collection, the troupe CLI shall write it as a literal block: `|` when the value ends with exactly one line break, `|-` when it ends with none, and `|+` when it ends with more than one.
2. The troupe CLI shall indent the content lines of a newly written literal block by the file's indentation step relative to the column of its key (or of its list item's `-`), and shall write empty content lines as bare line breaks.
3. When a string containing a line break is written as a new key's value, a new list item, or a changed scalar whose parent is a flow collection, the troupe CLI shall write it as a double-quoted scalar.
4. When a literal or folded block scalar is changed to another string, the troupe CLI shall keep its indicator, any indentation indicator and its chomping indicator, and shall change the chomping indicator only when the new value's trailing line breaks require it (strip when there is none, clip when there is exactly one, keep when there are more).
5. When a block scalar is changed to another string, the troupe CLI shall write the new content lines at the block's existing content indentation and keep the header line's other bytes, including its comment.
6. When a block scalar is changed to a value that is not a string, the troupe CLI shall write the value plain in place of the block indicator and remove the block's content lines.
7. If a string cannot be written as a literal block (it contains a control character other than TAB and line feed, or its first non-empty line starts with a space), then the troupe CLI shall write it double-quoted instead.

### Requirement 10: Folded block scalars
**Objective:** As a developer, I want edits to folded text to re-wrap only the paragraphs that changed, so that unchanged paragraphs produce no diff.

#### Acceptance Criteria
1. When a folded (`>`) value is changed, the troupe CLI shall keep the bytes of every paragraph whose text is unchanged.
2. When a paragraph's text changes, the troupe CLI shall re-wrap it greedily by word at the width of the longest content line of the corresponding paragraph before the change, placing a single word longer than the width on a line of its own.
3. When a paragraph has no counterpart in the old value, the troupe CLI shall wrap it at the width of the longest content line of the old value.
4. The troupe CLI shall match unchanged paragraphs between the old and new value in order, and shall treat changed paragraphs lying between the same matched paragraphs as counterparts of the old paragraphs there, pairing them in order.
5. The troupe CLI shall measure widths in Unicode code points of the content after the block's indentation, and shall break lines only at single spaces between words.
6. If the new value would require a more-indented line in the folded block (a line that starts with a space or TAB) that is not an unchanged run of the old value's more-indented lines, then the troupe CLI shall exit with code 3 and change no file.
7. If a change would put a more-indented line into a folded value, then the troupe CLI shall exit with code 3 and change no file; a change to a folded value that already contains more-indented lines is otherwise written, and each run of old more-indented lines that the new value keeps unchanged and in order keeps its bytes and the line breaks around it.

### Requirement 11: Placement of new entries
**Objective:** As a developer, I want new entries placed where I would put them by hand, so that comments stay attached to what they describe.

#### Acceptance Criteria
1. When a new key or list item is added to a block collection, the troupe CLI shall insert it after the collection's last entry and below the comment lines that follow that entry, up to the next line that is not blank and is not a comment indented at least as far as the collection's entries.
2. While scanning for the insertion point, the troupe CLI shall treat a comment line indented less than the collection's entries as the end of the collection, and shall insert before blank lines that are not followed by a further qualifying comment line.
3. When a new entry is added to a flow collection, the troupe CLI shall insert it after the last entry, on that entry's line, separated by `, `, and keep any trailing comma and the closing bracket after it.
4. When a scalar is replaced by a block collection, the troupe CLI shall keep the key's line (or list item's line) unchanged up to and including its end-of-line comment except that the scalar itself is removed, and shall start the collection on the following line.
5. When the scalar removed from a line was followed by an end-of-line comment, the troupe CLI shall keep that comment at its previous column; when no comment followed, the troupe CLI shall also remove the spaces between the key's `:` (or the `-`) and the removed scalar.
6. When a new entry is added after the last line of a file that does not end with a line break, the troupe CLI shall insert a line break before the new entry and leave the file still ending without a line break.

### Requirement 12: End-of-line comments
**Objective:** As a developer, I want end-of-line comments to stay aligned when a value's length changes, so that aligned comment columns survive edits.

#### Acceptance Criteria
1. When a changed value's length changes on a line that has an end-of-line comment, the troupe CLI shall move the comment back to its previous column.
2. If the new value reaches or passes the comment's previous column, then the troupe CLI shall separate value and comment by exactly one space.
3. The troupe CLI shall count columns in Unicode code points.
4. The troupe CLI shall not change any other line because of a comment adjustment.

### Requirement 13: Byte-surgical preservation
**Objective:** As a developer, I want a set to change only the bytes it must, so that the diff shows exactly the requested change.

#### Acceptance Criteria
1. When a set succeeds, the troupe CLI shall leave every byte of every file unchanged except the bytes that Requirements 2 to 12 require to change.
2. The troupe CLI shall write every line break it inserts with the file's line-ending convention: CRLF in a file that uses CRLF, LF in a file that uses LF or has no line break, and in a file with mixed line endings the line ending of the line after which it inserts (LF if that line has none).
3. The troupe CLI shall keep each written file in UTF-8 and keep a byte order mark at its start if one was present.
4. The troupe CLI shall leave files that the set does not change byte-identical, and shall create, remove or rename no file other than the written files themselves and the history state under `<theatre>/.troupe/`.
5. When the change is written into a file reached through a symbolic link, the troupe CLI shall write the link's target and keep the link, and shall keep the written file's permissions.
6. When a set requires changing more than one file (for example replacing a mapping that contains a text include and an ordinary value), the troupe CLI shall change each of those files under the same rules as a single file, and shall build the changed contents of all of them and verify them together (14.1) before writing any file.

### Requirement 14: Write safety
**Objective:** As a developer, I want troupe to refuse a change rather than risk corrupting my files, so that my configuration can never be left damaged.

#### Acceptance Criteria
1. Before writing, the troupe CLI shall read the changed file contents back as configuration and confirm that the result equals the current configuration with the value at PATH replaced by VALUE, where each string written from VALUE that starts with `file:` must have been read back as an include written with exactly that string.
2. If the changed contents cannot be read back, or the configuration read back differs from the intended configuration anywhere (for example because the changed file is included at more than one place), then the troupe CLI shall exit with code 3, name PATH and the file, and change no file.
3. The troupe CLI shall write each file atomically, so that at every moment the file holds either its complete previous content or its complete new content.
4. The troupe CLI shall leave no temporary or other extra file behind once the set finishes, whether it succeeds, is refused, fails to write a file or fails to record the history, so that the listing of every directory that holds a written file is unchanged apart from the written files' contents.
5. If a file cannot be written, then the troupe CLI shall restore every file already written by this set to its previous bytes, exit with code 3, name PATH and the file that could not be written, and record no `set` or `remove` event.
6. When VALUE equals the current value at PATH, the troupe CLI shall exit with code 0, modify no file and record no event, before any check that would otherwise refuse the set.
7. If a set exits with code 2, 3, 4 or 5, then the troupe CLI shall leave every user file unchanged (files already written are restored; a file whose restore fails is named under 14.10) and record no `set` or `remove` event.
8. If an include written by the set cannot be read back (its target is missing or unreadable, it leads back into its own include chain, or the target cannot be loaded), then the troupe CLI shall exit with code 3, write a stderr message that names PATH and the include target, and change no file.
9. If the change cannot be written in a way that satisfies Requirements 5 to 12 for the layout found in the file, then the troupe CLI shall exit with code 3, name PATH and the file, and change no file.
10. If restoring a file after a failed write (14.5) or a failed history recording (15.6) itself fails, then the troupe CLI shall keep the exit code of the original failure and write a stderr message that also names every file that could not be restored.

### Requirement 15: History of a set
**Objective:** As a developer, I want every successful set recorded leaf by leaf, so that `troupe config history` shows what changed.

#### Acceptance Criteria
1. When a set succeeds, the troupe CLI shall record one `set` event per leaf whose value it changed or created, with the leaf's previous value as `old` (null for a created leaf) and its new value as `new`, where the previous value is the value in the configuration loaded from disk for this command (including any hand edit made since the import), never a value taken from the history.
2. When a set succeeds, the troupe CLI shall record one `remove` event per leaf that no longer exists, with its previous value, taken from the configuration loaded from disk for this command, as `old` and null as `new`.
3. The troupe CLI shall compare leaves by dotted path and by typed value, so that `1`, `1.0`, `true` and `"1"` are different values and a leaf whose type changes counts as changed.
4. When a set changes no leaf (including a no-op set and a change between an empty mapping and an empty list), the troupe CLI shall record no event.
5. The troupe CLI shall record the events of one set together, with consecutive `seq` numbers: first the `set` events in the new configuration's document order, then the `remove` events in the previous configuration's document order.
6. If the events cannot be recorded after the files were written, then the troupe CLI shall restore every file written by this set to its previous content, exit with code 4 and write a stderr message that names the history store location.
7. When a set writes a new include, the troupe CLI shall derive its events from the configuration as read back, so that each leaf of an embedded YAML include is recorded as a created or changed leaf and a text include is recorded as one string leaf.

### Requirement 16: Error reporting for set
**Objective:** As a developer, I want set failures to tell me which path and file are involved, so that I can fix the problem quickly.

#### Acceptance Criteria
1. If `set` exits with a non-zero code, then the troupe CLI shall write nothing to stdout and write to stderr a message in the shared `<file>: <path>: <problem>` form, naming the offending file, PATH, or both.
2. If a set is refused with code 3, then the troupe CLI shall name PATH and the file that would have been changed (or that the refusal concerns) and state why the write was refused.
3. If PATH is not found or names the wrong kind of node, then the troupe CLI shall exit with code 5 and name PATH and the file holding the deepest existing part of PATH.
4. If the configuration cannot be loaded, then the troupe CLI shall exit with code 4 under the same rules as the other `troupe config` subcommands and change no file.
