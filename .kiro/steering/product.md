# Product Overview

troupe runs teams of AI agents. This repository is troupe's **configuration subsystem**: a command-line tool
(`troupe config …`) over YAML configuration files that people edit by hand and review as diffs.

A *theatre* is a directory whose configuration lives in `<theatre>/.troupe/troupe.yml` (or `troupe.yaml`).

## Core Capabilities

The concrete capabilities are set stage by stage in `.kiro/specs/`. Steering does not list them. The lasting shape is:

- Read and inspect a theatre's configuration from the command line.
- Change configuration on request, touching only what was asked.
- Record configuration changes as events, using an event store and a projection.

## Users

- **Developers.** They write and review the YAML by hand and read it in diffs.
- They care more about their files **staying exactly as they wrote them** than about speed.
- They expect errors to tell them **which path or file** is at fault.

## Product Principles

1. **The user's file is theirs.** Never reformat, reorder or rewrite anything the user did not ask to change.
   This includes comments, key order, quoting, indentation, blank lines and line endings. The diff after a
   command should contain only the requested change.
2. **Cannot lose or corrupt data.** When unsure, take the conservative choice that cannot lose or corrupt a
   user's data, and say what you chose.
3. **Errors name the path or file involved.** Messages like "invalid value" are not acceptable.
   "`<theatre>/.troupe/troupe.yml`: `<key path>`: …" is the expected shape.
4. **Built in stages.** Requirements will change. Build for the current stage, in a way that is easy to change.
   Do not build ahead for imagined future stages.

## Scope

- **Platforms**: macOS and Linux. Windows is out of scope; do not add Windows-specific handling.
- **Performance**: no targets. Configurations are small, so prefer clarity over optimisation.

## Where Requirements Come From

- The **product owner** decides what to build. Do not invent requirements.
- The contract and documents provided for each stage, plus `.kiro/specs/`, are the source of truth for scope.
- See `collaboration.md` for how to raise questions and record open choices.

---
_Focus on purpose and principles; per-stage features live in specs_
