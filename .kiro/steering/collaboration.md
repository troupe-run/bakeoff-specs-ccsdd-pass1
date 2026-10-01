# Collaboration & Traceability

## Working with the Product Owner

- The **product owner** decides **what** to build. Do not invent requirements.
- **Look before asking.** Check the repository, the contract and the documents provided for the stage, and
  `.kiro/specs/`.
- **Ask well.** When a question remains, give: what you found (with references), the options, and **your
  recommendation** with its reason. Do not just state the problem.
- **Open choices.** Where the contract leaves a choice open, make it, then **say what you chose and why**. Record
  it in the spec (see below).
- **Do not narrow scope quietly.** Deliver the whole of what was asked. If part of it cannot be done, say so
  plainly and explain why.

## Conservative Defaults

When unsure, choose the option that **cannot lose or corrupt a user's data**, and state the choice. For example:
refuse and explain rather than guess; leave the file untouched rather than "fix" it.

## Recording Decisions

- Record significant decisions with their **reasons** where the next person will find them:
  - per feature: `.kiro/specs/<feature>/research.md` and `design.md`
  - project-wide: the relevant `.kiro/steering/` file
- A decision record says what was decided, the alternatives considered, why, and what would make us revisit it.

## Keeping Spec, Code and Tests in Agreement

- When behaviour changes, update the **requirements/design, code and tests in the same change**.
- A requirement with no test, or a test with no requirement, is drift. Fix it or flag it.

## Commits

- Commit in **small, meaningful steps**.
- Commit messages say **why**, not just what.
- Do not commit code that fails tests, `mypy --strict` or ruff.

## Building in Stages

- Build for the **current** stage only. Do not add speculative hooks for stages that have not been specified.
- Keep the code easy to change: single-place decisions, narrow interfaces, behaviour pinned down by tests.

---
_How we work; applies to every spec and implementation task_
