# Technology Stack

## Architecture

A Python CLI over hand-edited YAML files, with an event-sourced core built on the `eventsourcing` library.
Changes are recorded as events in an event store, and a projection derives state from them.

How the YAML file, the event store and the projection relate to one another (which one is authoritative, and when
each is written) is a **design decision for the specs**. It is not settled here. Whatever is chosen must
keep Product Principle 1: the user's file changes only where they asked.

## Fixed Constraints (non-negotiable)

- **Language**: Python **3.12+**
- **Project / environment manager**: **uv**
- **Entry point**: the CLI must run as `uv run troupe config …` **from the repository root**
- **Event store and projection**: the **`eventsourcing`** library
- **Config location**: `<theatre>/.troupe/troupe.yml`, or `.yaml`
- **Platforms**: macOS and Linux only

## Key Libraries

- `eventsourcing`: event store and projection (required).
- **YAML handling must preserve formatting.** Use a round-trip-capable approach that keeps comments, key order,
  quoting and layout. A load-then-dump with a plain YAML library does not meet the product principles.
  The library choice belongs in the design, recorded with its rationale. Test it against real files and check
  that the round trip is byte-identical where nothing changed.

## Development Standards

### Type Safety
- Every module is fully typed and passes **`mypy --strict`**.
- No untyped escapes (`Any`, `# type: ignore`) unless a third-party library forces it. When one is unavoidable,
  keep it narrow and add a comment explaining why.

### Code Quality
- **ruff** clean (lint and format) on all source and tests.

### Testing
- Every behaviour has a test that **fails when that behaviour breaks**. See `testing.md` for the full rules.

## Development Environment

### Required Tools
- Python 3.12+, uv

### Common Commands
```bash
uv run troupe config …        # run the CLI (from repo root)
uv run pytest -n auto        # tests (parallel; plain `uv run pytest` also works)
uv run mypy --strict .        # type check
uv run ruff check . && uv run ruff format --check .   # lint / format
```
_(The exact commands are fixed once the project scaffold exists. Keep this block in step with them.)_

## Key Technical Decisions

- **Each decision lives in one named place.** For example: the config file names and their lookup order, the
  `.troupe` directory name, error message formatting, event names. Changing one later should mean editing one
  place, not searching the codebase.
- **Conservative by default.** If a technical choice could lose or corrupt user data, pick the option that cannot.
  Record the choice and why. Example: write files atomically; never leave a partially written config.
- Record significant decisions and their reasons in the feature's `research.md` / `design.md`. See
  `collaboration.md`.

---
_Document standards and patterns, not every dependency_
