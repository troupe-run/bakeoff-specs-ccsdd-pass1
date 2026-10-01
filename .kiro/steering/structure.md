# Project Structure

> No code exists yet. The layout below is a **proposal**, to be confirmed by the first design. Once code lands,
> update this file to describe what is actually there.

## Organization Philosophy

Small and layered, so that each stage's changing requirements touch as few places as possible:

- **CLI layer**: parses arguments, calls the core, renders output and errors. No business logic.
- **Core / domain**: configuration concepts, event-sourced aggregates and projections (`eventsourcing`).
- **File layer**: finds the theatre's config file and reads and writes YAML while preserving formatting.
  This is the only code that touches the user's file.

Dependencies point inward: CLI → core → nothing CLI-specific. The file layer sits behind a narrow interface, so
the preservation rules stay in one place.

## Directory Patterns (proposed)

### Package source
**Location**: `src/troupe/` (src layout, installed by uv)
**Purpose**: Application code. The `troupe` console script exposes the `config` command group.

### Tests
**Location**: `tests/`
**Purpose**: Tests that mirror the behaviours in the specs, with real YAML fixture files kept next to the tests
that use them (for example `tests/fixtures/`).

### Specs and steering
**Location**: `.kiro/specs/<feature>/`, `.kiro/steering/`
**Purpose**: Requirements, design, tasks and decisions for each feature, plus project-wide memory.

## Naming Conventions

- **Modules and packages**: `snake_case`
- **Classes** (including events and aggregates): `PascalCase`. Name events as past-tense facts.
- **Functions and variables**: `snake_case`
- **Constants for single-place decisions**: `UPPER_SNAKE_CASE` in one module (for example the config file names
  and the `.troupe` directory name)
- **Tests**: `test_<what_it_proves>`. The name states the behaviour being proved (see `testing.md`).

## Import Organization

```python
from troupe.config.locate import find_config_file   # absolute imports within the package
```
- Use absolute imports from `troupe`. Keep import sorting under ruff (`I` rules).

## Code Organization Principles

- **One decision, one place.** Constants, paths, message formats and policies each have a single named home.
- **The file layer owns the user's bytes.** Nothing else reads or writes the config file directly.
- **Errors carry the path.** Error types hold the file path (and the key path, where relevant) so that the CLI
  can always name them.

---
_Document patterns, not file trees. New files following patterns shouldn't require updates_
