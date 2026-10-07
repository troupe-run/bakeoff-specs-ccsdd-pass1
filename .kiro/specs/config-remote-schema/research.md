# Research & Design Decisions: config-remote-schema

## Summary
- **Feature**: `config-remote-schema` (step 7, contract section "Remote includes and schema")
- **Discovery Scope**: Extension (light discovery: loader, model and writeback integration points, plus checks of
  ruamel.yaml's handling of tags, the `jsonschema` library and `urllib`)
- **Key Findings**:
  - Include recognition is two prefix tests in the loader (`_Builder._build`, `_ref_value`), and the builder already
    knows the full configuration path of every node it builds, including inside included files (`builder.build(
    composed.root, path)`). So the literal decision (`!literal`, schema `format: uri`) can be made right there,
    before anything is read or fetched.
  - Every edit the planner makes to a file goes through `_Planner._layout(file)` (four call sites, all for editing)
    or `_Planner._replace_file` (text-include rewrites). These two places are an exact choke point for "never write
    remote content". The alias expander builds its own layout (`expand.py`), and `write_atomically` is the final
    backstop.
  - The read-back (`verify_patch`) and the alias expansion loop reload through `load_config(config_file,
    overrides)`. If overrides are keyed by source identity, remote text is reused and nothing is fetched twice. If
    the loader validates the schema, the read-back checks the intended configuration against the schema for free.

## Research Log

### Loader, model and readers
- **Context**: where remote sources and literals enter, and who assumes a local `Path`.
- **Sources Consulted**: `config/loader.py`, `model.py`, `compose.py`, `resolve.py`, `theatre.py`, `watch.py`,
  `cli/render.py`, `cli/main.py`.
- **Findings**:
  - `SourceFile.real_path` is the identity for the read cache, cycles, overrides, written locations
    (`shared.written_location`), planner layouts and setter texts (39 uses outside the loader). `SourceFile.path`
    is the display form in messages (`file=…path`, ~60 uses) and in `source_label`.
  - `history` opens the theatre with `open_theatre(reconcile=False)`, which still calls `load_config`. Every command
    loads the configuration today, so a `history` that skips the schema check is not how the code works (see
    choice 14).
  - `watch` builds its watch set from `source.path` of `document.files` and its baseline from the decoded texts.
- **Implications**: a remote source keeps the `SourceFile` type with a new `url`; identity and display go through two
  properties (`key`, `shown`) so the sites that may see a remote source are changed explicitly. `watch` filters
  remote sources out of the watch set and adds the schema file.

### How ruamel.yaml 0.19.1 composes `!literal`
- **Context**: decision I refuses tags from the composed node's own text and from tokens next to anchors.
- **Sources Consulted**: experiments in this repository (`uv run python`, `YAML(typ="rt").compose`).
- **Findings**:
  - `a: !literal https://x` gives a `ScalarNode` with `tag == "!literal"`, `ctag.handle == "!"`, value
    `https://x`, and a span that starts at the tag.
  - `a: &x !literal https://x`: the span starts at `&x`, and the tag follows the anchor.
    `a: !literal &x https://x`: the span starts at `&x`, and the tag lies before the span.
  - `a: !literal` gives the value `''`. `a: !literal |` keeps the block style, and `a: !literal [1]` gives a
    `SequenceNode` with the tag.
- **Implications**: the loader can accept exactly `!literal` on scalars, record its span, and exclude it from the
  value span, as config-aliases Decision 3 does for anchors. The tag-before-anchor order is refused (choice 21).

### jsonschema and referencing
- **Context**: validating against JSON Schema 2020-12 without writing a validator.
- **Sources Consulted**: jsonschema 4.26.0 installed in a scratch environment (`uv run --with jsonschema`); the
  jsonschema documentation (validators, `best_match`, referencing).
- **Findings**:
  - `Draft202012Validator.check_schema` raises `SchemaError` for an invalid schema. `iter_errors` yields every
    failure with `absolute_path`. `best_match` picks the most relevant one. `format` is an annotation unless a
    `FormatChecker` is passed.
  - A `$ref` to `https://…` is **not** retrieved by default. Validation raises
    `jsonschema.exceptions._WrappedReferencingError`, which is a `referencing.exceptions.Unresolvable`. A missing
    local `#/$defs/x` raises the same. Both are raised lazily, when validation meets the `$ref`.
  - jsonschema ships no `py.typed`, but `referencing` does. The typeshed stubs `types-jsonschema` cover jsonschema.
- **Implications**: runtime dependency `jsonschema`, dev dependency `types-jsonschema`. Catch `Unresolvable` from
  the typed `referencing` package. "Unresolvable `$ref`" means met during validation (5.3).

### Fetching with urllib
- **Context**: one GET with a timeout, redirects, status and decoding rules, and tests without the internet.
- **Findings**: `urllib.request.urlopen(url, timeout=…)` follows redirects (`HTTPRedirectHandler`), raises
  `HTTPError` for 4xx/5xx and `URLError` for connection errors, and raises `TimeoutError` (`socket.timeout`) on a
  slow server. It honours `*_proxy` environment variables, so tests must set `NO_PROXY` for 127.0.0.1. The CLI tests
  run the console script as a subprocess (`tests/support.py`), so a server thread in the test process is reachable.
- **Implications**: `fetch.py` holds the one call and maps every failure to `FetchError(reason)`. Tests serve real
  HTTP from `ThreadingHTTPServer`, as the brief asks, and never mock the fetcher.

### Writeback integration
- **Context**: the six `"file:" + spec` sites, VALUE include detection, and the include-root rule.
- **Findings**: `compare.node_to_plain` and `shared._Rebuild.plain` build `IncludeDirective` texts.
  `compare._include_text` treats a VALUE `file:` string as an include. `shared._Loading.content` strips the prefix
  to call `load_include`, and `_is_yaml` uses the text's suffix. `planner._change_include` builds the directive for
  the include-root refusal. `render_scalar` is the one renderer of VALUE scalars in every context. Block-scalar
  paths are taken for multi-line strings (9.1, 9.2) and for in-place rewrites (9.4, 9.5).
- **Implications**: an `IncludeRef.string` property removes the rebuilds. A mark on `NewScalar` (decided once in
  the setter) replaces prefix tests on VALUE. The tag prefix lives in `render_scalar`, and tagged strings avoid the
  block-scalar paths (choice 18).

### Consulted skills and rules
- kiro-spec-init, kiro-spec-requirements (EARS format, review gate), kiro-spec-design (light discovery, synthesis,
  review gate, design principles) and kiro-spec-tasks rules, as in the earlier specs.

## Architecture Pattern Evaluation

| Option | Description | Strengths | Risks / Limitations | Notes |
|--------|-------------|-----------|---------------------|-------|
| Decide while walking; remote as `SourceFile` (chosen) | Loader asks tag and schema at each string; remote sources join `document.files` with `url` | One recognition place; readers unchanged; read-back reuses texts and checks the schema | `SourceFile.path` consumers must use `shown` | Matches brief and roadmap |
| Separate remote node kind | Readers resolve remote content themselves | Physical distinction explicit | Every reader changes and they drift | Rejected (as in steps 5, 6) |
| Decide literals after building | Build everything, then un-include literals | Simple walker | Literals would already be fetched, and a failed fetch would fail a valid configuration | Rejected (brief) |
| Remote guard before planning | Predict from PATH which files a set edits | Early message | Cannot see collection replacements, replication or placements | Rejected; the layout choke point sees every edit |

## Synthesis
- **Generalization**: "is this string an include?" is one question asked by the loader (with tag and schema) and by
  the writer (for VALUE with the `--include` flag and schema). Both use `is_include_string` and `uri_format_at`, so
  the two always agree. Remote and local includes share every loader rule except where the target comes from.
- **Build vs adopt**: adopt `jsonschema` for validation (2020-12 is large; a partial validator would accept invalid
  configurations) and stdlib `urllib` for fetching (a new HTTP dependency for one GET is not justified). Build the
  `format: uri` walker, because the contract restricts it to three keywords, which no library exposes.
- **Simplification**: no cache, no fetcher abstraction or protocol (one function, real HTTP in tests), no separate
  schema-check step in `set` (the read-back already loads with the schema), no `!literal` block scalars on write.

## Design Decisions

### Decision 1: A remote source is a `SourceFile` with `url`, identified by `key` and named by `shown`
- **Context**: 1.1, 3.2, 6.1, 10.3. Layers, origins, leaves and written locations all hang off `SourceFile`.
- **Alternatives Considered**:
  1. `path: Path | None` for remote sources. mypy would find every use, but about 100 sites would change for one
     case.
  2. A separate `RemoteSource` type in `Origin.file`. Same ripple, and every consumer would need a union.
  3. A synthetic `Path` identity with no display rule. URLs would print as `https:/host/x.yml`.
- **Selected Approach**: `url: str | None = None`, with `key` (URL or real path) for identity and `shown` (URL or
  path) for messages. `path` and `real_path` of a remote source are a `Path(url)` stand-in that is never opened.
  `write_atomically` refuses a remote source as a backstop.
- **Rationale**: the smallest change that keeps every reader working and makes identity explicit.
- **Trade-offs**: mypy cannot catch a site that still shows `path`. Mitigated by task-level sweeps (`file=` in the
  loader, compose, resolve, theatre and render) and by tests that assert URLs in messages.

### Decision 2: The literal decision is made while walking, by one predicate
- **Context**: 4.1–4.6, 1.1. The contract makes a string's include-ness depend on its tag and its path's schema.
- **Selected Approach**: `_Builder._is_include(scalar, path)` = no `!literal`, an include string, and no
  `format: uri` at `path`. `_ref_value` ignores the schema (a `$ref` value is an include by construction) but
  refuses `!literal`.
- **Follow-up**: values a mapping receives through a merge key are decided where they are written (their own path),
  not at the merged path. Revisit if users rely on schema literals through `<<`.

### Decision 3: `!literal` is a recorded property excluded from the value span
- **Context**: 4.1, 8.4, config-aliases 7.4.
- **Alternatives Considered**: keep the tag inside the scalar span. Then a replacement drops the tag for free, but
  block-scalar header parsing, `quote_style_of`-adjacent code and the alias copier would all see `!literal` where
  they expect the value.
- **Selected Approach**: `ConfigScalar.literal: Span`. The value span excludes the tag, as for anchors. The layout
  starts the inline extent at the tag, so a replacement removes it while an anchor before it stays. The copier
  writes the tag again.

### Decision 4: The schema check of a `set` is the read-back
- **Context**: 7.1. The brief asked for validation "before any write".
- **Alternatives Considered**: validate `plan.intended` in the setter. But a new include's content is not known in the
  intended tree (`IncludeDirective(content=None)`), so this check would be incomplete or need a second loader.
- **Selected Approach**: the loader validates every load. `verify_patch` re-words a `SchemaViolationError` as the
  exit-3 refusal naming the schema file, PATH and the failing key path. The check is complete, sees fetched content,
  and still runs before any write.

### Decision 5: One remote write guard at the planner's layout choke point
- **Context**: 6.1, 6.2. Edits into remote content can come from changed values, collection replacements, removals
  across layers, replication to shared places, `--in-include` and text-include rewrites.
- **Selected Approach**: `_Planner._layout` and `_replace_file` raise `remote_refusal` for a remote source. The
  expander does the same for alias tokens, and `write_atomically` refuses as a backstop. The refusal finds the
  outermost local include directive leading to the source, preferring PATH's walk, and quotes its source text and
  line.
- **Rationale**: it sees every edit exactly. Nothing is refused that would not have been written.

### Decision 6: VALUE strings carry a mark decided once
- **Context**: 8.1–8.6, 9.3, 9.5. Rendering, the plain tree and no-op detection must agree on what a VALUE string is.
- **Selected Approach**: `NewScalar.mark` (`NONE`, `INCLUDE`, `LITERAL`), set by `mark_include_strings` in the
  setter before target resolution. `compare.new_to_plain` makes directives from `INCLUDE` only, and `render_scalar`
  prefixes `LITERAL`.
- **Alternatives Considered**: decide at render time from the path. That would be repeated in the plain tree and the
  renderer, and they would drift.

### Decision 7: The fetcher is one function, tested over real HTTP
- **Context**: 1.6, 2.1, 10.4, testing.md ("mock only at real external boundaries").
- **Selected Approach**: `fetch_remote(url, timeout)` in `config/fetch.py`. Tests run `RemoteServer`
  (`ThreadingHTTPServer` on 127.0.0.1 in a thread) and drive the real CLI against it. Only the timeout unit test
  passes a short timeout.

## Choices where the contract is silent (decision records)
All 22 choices are listed in `requirements.md` ("Choices made where the contract is silent"). The product owner was
not available. The brief's defaults (1–14) are taken as decisions, except where 14 notes otherwise, and 15–22 are
ours. Each record gives the decision, the alternatives, why, and when to revisit.
1. **`file:` in remote content → exit 4; remote in remote allowed** (1.3, 1.4). *Alternatives*: resolve relative to
   the URL (a second fetch scheme the contract does not define), or relative to the theatre (surprising). *Why*: no
   local directory to resolve against. *Revisit*: if teams need relative includes inside shared files.
2. **`!literal` is the scalar's text; collections and other tags exit 4** (4.1, 4.2). *Alternatives*: type plain
   literals (`!literal 5` → 5). *Why*: YAML's rule for a tagged scalar. *Revisit*: never expected.
3. **Schema walker: `properties`, `items`, `additionalProperties` only** (4.4, 4.5). For each segment the walker
   tries `properties`, then `items` for a digit-only segment, then `additionalProperties`. It does not know the
   node's kind, and digit-only mapping keys are out of contract (resolve.py). *Alternatives*: follow `$ref` and
   combinators (the contract names exactly three keywords). *Revisit*: if schemas use `$defs` for URL types.
4. **Validation input is the expanded configuration** (5.1). *Alternatives*: the root file alone (would miss
   included content). *Why*: it is the configuration every command uses.
5. **Overridden remote values change in the override; physical remote edits are refused** (6.1, 6.3, 6.5).
   *Alternatives*: refuse every PATH below a remote include (would block step 5 overrides the contract keeps).
6. **`--in-include` below a remote include → exit 3** (6.1). *Why*: it would write the remote file.
7. **Report the outermost local directive, as written, with its line** (6.1, 6.2). The line number is added
   because the contract's intro says "tell the user which include line to change". *Alternatives*: the innermost
   directive (in remote content the user cannot edit).
8. **Nested VALUE strings are tagged too** (8.1). *Why*: the same accidental include can hide in a flow mapping.
9. **`--include` remote VALUE: written plain, fetched on read-back, failure → 3** (9.4). *Why*: decision D's rule
   for unloadable targets.
10. **Replacing an existing `!literal` replaces its tag** (8.4). *Alternatives*: keep the tag. That would silently
    keep a literal the user may have meant to turn into an include with `--include`.
11. **`watch` polls local files and the schema; re-fetches on reload** (3.4, 3.5). *Revisit*: if remote changes
    should be noticed without a local change (would need URL polling).
12. **`--sources` labels remote scalars with their URL** (3.2).
13. **Fetch details: 10 s, redirects, suffix from the URL path, case-sensitive** (1.2, 1.6); prefixes are
    case-sensitive too, like `file:`. *Revisit*: if servers serve YAML under other suffixes (a Content-Type rule).
14. **Every command, `history` included, fetches and checks the schema** (3.6). **Differs from the brief**, which
    proposed that `history` checks the schema only when it imports. *Why*: `history` loads the configuration
    today (`open_theatre(reconcile=False)` still calls `load_config`), so a load failure already stops it. A
    schema-free `history` would need a second, partial load path, which is more change and a second meaning of
    "the configuration loads". *Revisit*: if users need `history` while a shared URL is down. The step 1–2 rule
    would then change for every load failure.
15. **A `$schema` naming another dialect → exit 4** (5.3). *Alternatives*: ignore `$schema`, or pick the
    validator from it. *Why*: draft-07 `items` arrays mean something else in 2020-12, so validating them silently
    could accept configurations the author meant to refuse. *Revisit*: if older schemas must be supported.
16. **`$ref` mapping in VALUE without `--include` → exit 3** (8.6). *Alternatives*: tag the `$ref` string (the
    read-back would then fail with an obscure message), or treat `$ref` as an implicit `--include`. *Why*: a clear,
    conservative refusal that names the flag.
17. **Tags in VALUE stay exit 2, `!literal` included** (8.7). *Why*: troupe decides tagging. Accepting user tags
    would add a second way to say the same thing, plus tag validation. *Revisit*: if users want to force
    `!literal` where the schema says `format: uri`.
18. **Tagged strings stay on one line and are never block scalars** (8.3). *Why*: keeps the tag in one renderer.
    Include-like strings with line breaks are not realistic. *Revisit*: never expected.
19. **One fetch per command** (10.3). The read-back and expansion reloads reuse the fetched text. *Why*: verifying
    against a second, possibly different response would refuse correct writes at random.
20. **Text include content is never tagged** (8.8). *Why*: the string is written to a text file, not to YAML.
21. **`!literal &a x` → exit 4; `&a !literal x` loads** (4.2). *Why*: a replacement must remove the tag but keep
    the anchor, which is only possible when the anchor comes first. *Revisit*: if the other order shows up in
    real files.
22. **One schema failure is reported (`best_match`)** (5.2, 7.1). *Alternatives*: list every failure (long,
    unordered output). *Revisit*: if users ask for all failures at once.

## Risks & Mitigations
- **Missed `path` → `shown` site prints a mangled URL**: the tasks sweep each module, and e2e tests assert the URL in
  the messages of every remote failure and refusal.
- **Network flakiness in tests**: the server runs on 127.0.0.1 with `NO_PROXY` set. Only the timeout unit test waits,
  and it uses a short timeout.
- **`watch` retries a failing fetch on every poll** (choice 11, the step 2 retry): each retry may wait up to 10 s.
  Accepted for now, because `watch` stays responsive to stop requests between ticks. Revisit with backoff if users
  include slow URLs.
- **Large or hostile remote content**: no size limit and no auth (out of contract). Remote content cannot write or
  read local files (choice 1). Revisit if theatres include untrusted URLs.
- **Behaviour change for existing users**: `http(s)://` values become includes, and `file:` VALUEs become literals.
  Documented in the design's Migration Strategy and in the step-7 notes of the earlier specs.

## References
- JSON Schema 2020-12 Core and Validation: https://json-schema.org/draft/2020-12
- jsonschema documentation (validators, `best_match`, referencing): https://python-jsonschema.readthedocs.io/
- Python `urllib.request` and `http.server`: https://docs.python.org/3/library/urllib.request.html,
  https://docs.python.org/3/library/http.server.html
- YAML 1.2.2 §6.9.1 (node tags): https://yaml.org/spec/1.2.2/
- config-aliases `research.md` Decision 3 (property spans), config-include-overrides choice 18 (written locations)
