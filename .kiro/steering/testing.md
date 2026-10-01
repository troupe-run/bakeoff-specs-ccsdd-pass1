# Testing Standards

## Core Rule

Every behaviour has a test that **fails when that behaviour breaks**. A test that passes whatever the code does
is worse than no test, because it gives false confidence.

## Writing Tests

- **The name states what it proves.** `test_set_value_leaves_comments_untouched`, not `test_set_1`.
- **The body checks exactly that.** Assert the specific outcome the name claims. Avoid incidental assertions
  that hide the point, and avoid assertions so loose they cannot fail (`assert result`, `assert x is not None`
  when the value matters).
- **Check that it can fail.** When you add a test, make sure it fails against broken or missing behaviour (for
  example, run it before the implementation, or break the code briefly).

## Real Shapes, Not Stand-ins

- Use **real files**: write real YAML into a temporary theatre (`tmp_path / ".troupe" / "troupe.yml"`) and run
  the code against it. Do not use hand-written dicts that stand in for parsed YAML.
- Use **real command output**: drive the CLI the way users do (`troupe config …`) and assert on its actual
  stdout, stderr and exit code.
- **Preservation tests compare bytes.** For "never reformat" behaviour, compare file contents before and after,
  and assert the diff is exactly the requested change. Fixtures should include comments, unusual ordering,
  quoting styles and blank lines.
- Cover both `troupe.yml` and `troupe.yaml` wherever the lookup matters.
- **Error tests assert the path.** Check that the message names the file (and key path, where relevant).

## Mocks

Prefer real collaborators. The configurations are small and the event store can run in-memory or in a temp
directory, so mocking is rarely needed. Mock only at real external boundaries, and never mock the thing under test.

## Done Means Verified

Before claiming something works, run the tests, type check and lint, and **show the output**:
`pytest`, `mypy --strict`, `ruff`. If something fails or was skipped, say so.

## Traceability

Tests map to spec requirements. When behaviour changes, update the spec, code and tests **in the same change**.

---
_Patterns for tests; specific cases live with each feature's tasks_
