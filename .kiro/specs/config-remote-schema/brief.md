# Brief: config-remote-schema

## Problem
Teams want one shared configuration for every theatre (step 7). They publish it at an `https://` URL and include
it from each theatre's `troupe.yml`. troupe must read that shared configuration, but it must never pretend it can
change it. A `set` that would have to change a value that comes from the URL has to be refused, and the message
has to tell the user which include line in their own file leads there.

Once `https://` strings are includes, some settings become ambiguous. A setting such as a webhook or API endpoint
holds a URL that is a value, not an include. A theatre can describe its configuration with a JSON Schema at
`.troupe/troupe.schema.json`. The schema marks URL-valued settings with `"format": "uri"`, and any other value that
must be taken literally can be tagged `!literal` in the file. The schema also lets troupe reject a configuration,
or a `set`, that does not match the theatre's declared shape.

## Current State
- config-foundation (`config/loader.py`): only `file:` strings are includes (`INCLUDE_PREFIX`, `_Builder._build`
  and `_ref_value`). Targets are local paths relative to the including file (`_include`, `read_include`), and
  `SourceFile` assumes a local `Path`. Every explicit tag is refused with exit 4 (`_refuse_properties`, decision I,
  Requirement 3.7). `http://` and `https://` strings are plain strings today. No test pins that.
- config-set-writeback: decision D says a `file:` VALUE is written as a plain string. It becomes an include, and an
  unloadable target gives exit 3. Six places rebuild include strings as `"file:" + spec`: `compare.py` (node_to_plain,
  `_include_text`), `shared.py` (three places) and `planner.py` (refusal messages). The planner changes a value
  inside an included YAML file directly in that file. Read-back verification reloads every file (`verify.py`).
- config-include-overrides: new keys under an include are written as overrides in the including file. An
  overridden value is changed in the override. A non-overridden value is changed in the file it comes from.
  `--in-include` puts new keys in the included file.
- config-file-sync: `watch` polls `ConfigDocument.files` and the root config candidates. It cannot poll a URL.
- config-get-show: `--sources` labels each scalar with its file relative to the theatre (`source_label`).
- There is no JSON Schema support, and no `jsonschema` dependency.

## Desired Outcome
The step 7 contract section "Remote includes and schema" holds:
- A string starting `https://` or `http://` is also an include. It works in the scalar form, and as the `$ref` of
  the mapping form. It is fetched when the configuration loads: as YAML if the URL path ends `.yml`/`.yaml`,
  otherwise as text. A failed fetch gives exit 4.
- Changing a value that comes from a remote include gives exit 3, and stderr contains the include string exactly as
  written in the local file. New keys under a remote include become local overrides (step 5).
- If `<dir>/.troupe/troupe.schema.json` (JSON Schema 2020-12) is present, it describes the configuration:
  - A string whose schema has `"format": "uri"` is a literal, not an include. That schema is found through
    `properties`, `items` and `additionalProperties`.
  - A string tagged `!literal` is a literal.
  - The loaded configuration must validate against the schema, else exit 4.
  - A `set` that would make the configuration invalid gives exit 3.
- `set PATH VALUE` where VALUE is a string that would be read as an include writes it tagged `!literal`, unless the
  schema marks PATH as `format: uri` or `--include` is given.
Every reader (`get`, `show`, `show --sources`, `history`, reconcile, `watch`) sees remote content, and never sees
literal strings expanded. All earlier `set` rules still hold: byte-surgical rendering, events and lineage, atomic
writes, read-back verification, overrides, `--in-include`, `--reason` and `--expand-alias`.

## Approach
Chosen: **the loader decides what an include is, with a pluggable fetcher and a schema-driven literal check. The
writer treats remote sources as read-only.**
- **Include recognition** lives in one place, `is_include_string` with the prefixes `file:`, `https://` and
  `http://`. The loader walks the tree with the current config path. A string is an include only if it is an include
  string, has no `!literal` tag, and the schema does not give its path `format: uri`. The schema lookup is a small
  walker over `properties` / `items` / `additionalProperties` (and nothing else, as the contract says). It walks the
  same path the loader is at.
- **Remote fetch**: the stdlib `urllib.request` with a timeout, so no HTTP dependency. Any failure gives exit 4,
  naming the including file, the key path and the URL: network error, HTTP status other than 2xx, a body that is
  not UTF-8, or invalid YAML. A remote source is a `SourceFile` with a URL identity and no local path. Its
  text is held in memory for the command. The fetcher is one function, so tests can serve real HTTP from a local
  `http.server` thread.
- **Schema**: the `jsonschema` library (Draft 2020-12 validator, MIT). The loader reads and checks the schema file.
  After the tree is built, the plain configuration is validated. A bad schema file or an invalid configuration gives
  exit 4, naming the schema file and the first failing key path.
- **Writer**:
  - When the physical target of an existing value lies in a remote source, the planner refuses with exit 3. The
    message names PATH and the include string as written in the local file: the nearest local include directive
    on the chain.
  - New keys below a remote include use the step 5 override rule. `--in-include` that would write into a remote
    source is refused the same way.
  - The intended configuration is validated against the schema before any write. If it is invalid, the set exits
    3.
  - VALUE strings that would be includes at their path are rendered with a `!literal ` prefix, unless `--include`
    is given or the schema gives their path `format: uri`.
- **`--include`**: a new no-value flag next to `--reason`, `--in-include` and `--expand-alias`.

Rejected alternatives:
- *httpx/requests for fetching*: a new dependency for a single GET. urllib covers TLS, redirects and timeouts.
- *Hand-written JSON Schema validator*: 2020-12 is large (`$ref`, `$defs`, combinators, `unevaluated*`). A
  partial validator would accept invalid configurations.
- *Caching remote content under `.troupe/`*: the contract says it is fetched when the configuration loads. A cache
  would add a second source of truth and staleness rules that nobody asked for.
- *Decide literals after building the tree*: an include would already have been fetched, and might have failed,
  before we knew it was a literal. The decision has to be made while walking.

## Scope
- **In**:
  - Remote include recognition, in the scalar form and as the `$ref` of the mapping form, including inside included
    YAML files.
  - Fetch, and YAML vs text by URL path suffix. Exit 4 on fetch failure.
  - Exit 3 for changes to remote-sourced values, with the include string in stderr. Overrides under remote includes
    (step 5 rules), including the conversion from scalar form to mapping form.
  - Schema discovery, reading and validation. Exit 4 for a malformed schema or an invalid configuration.
  - The literal check from `format: uri` and from `!literal`. `!literal` loading: decision I is narrowed for this
    one tag.
  - Exit 3 for a `set` that would make the configuration invalid.
  - VALUE strings written tagged `!literal` by default, and `set --include`.
  - `--sources` labels for remote scalars.
  - What `history`, reconcile and `watch` do with remote content.
  - Updating the step 1–6 tests that pin "explicit tags → exit 4" for `!literal`, and decision D.
- **Out**:
  - Authentication headers, proxies beyond what urllib does by default, caching, offline mode, and schemas fetched
    from URLs.
  - `$ref` to remote schemas: only the local schema file is read. Remote `$ref` inside it is not fetched; it fails
    as unresolvable (exit 4).
  - Other explicit tags, which stay exit 4.
  - Format assertion: `format` is an annotation, as in the 2020-12 default.
  - Windows, and concurrent writers.

## Boundary Candidates
- Include recognition and the literal decision (`is_include_string`, the schema walker, `!literal`): core/loader.
- The remote source fetcher (urllib, errors → exit 4): core/io.
- The schema store: locate, read and validate the schema file, and validate a plain configuration.
- The writer's remote-source refusal, and placement of new keys below remote includes.
- The writer's schema check of the intended configuration (exit 3).
- VALUE rendering with `!literal`, and the CLI `--include` flag.

## Out of Boundary
- Override placement rules and the conversion from scalar form to mapping form: config-include-overrides, unchanged.
- Rendering of scalars and collections: config-set-writeback, unchanged except for the `!literal ` prefix.
- Event shapes, lineage and reconcile mechanics: unchanged. Remote leaves are leaves like any other.

## Upstream / Downstream
- **Upstream**: config-foundation (loader, model, errors), config-set-writeback (planner, value, compare, verify),
  config-include-overrides (layers, placement), config-aliases (expanded tree, guard), config-get-show
  (`source_label`), config-file-sync (watch set).
- **Downstream**: none specified yet.

## Existing Spec Touchpoints
- **Extends**:
  - config-foundation: Requirement 4 gains remote includes, Requirement 3.7 and decision I gain the `!literal`
    exception, and the schema adds an exit-4 case.
  - config-set-writeback: decision D and 2.7–2.8 change, because include-like VALUE strings are now written
    `!literal` unless `--include` is given; `set` also accepts `--include`.
  - config-file-sync: `watch` and remote sources, and the schema file in the watch set.
  - config-get-show: the `--sources` label of a remote scalar.
- **Adjacent**: config-include-overrides (overrides of remote includes), config-aliases (an alias of a node that
  holds a remote include).

## Constraints
- Python 3.12+ and uv. New runtime dependency `jsonschema` (MIT, actively maintained). `mypy --strict` and ruff.
  Tests use real files and the real CLI, with byte-exact checks. Remote tests use a local HTTP server, never the
  internet.
- No file changes on any refusal. State only under `.troupe/`. No leftover files.
- Open choices to settle and record in requirements/research (proposed defaults, conservative):
  1. *`file:` includes inside remote YAML*: exit 4. There is no local directory to resolve them against. Remote
     includes inside remote YAML (absolute URLs) are allowed.
  2. *`!literal` on a scalar*: the value is the scalar's text as a string, which is YAML's rule for a tagged
     scalar. `!literal` on a mapping or a list: exit 4. Any other tag: still exit 4.
  3. *Schema lookup*: only `properties`, `items` (a schema, as in 2020-12) and `additionalProperties` (a schema)
     are followed. `true`/absent → no format. `$ref`, `allOf`, `patternProperties` and `prefixItems` are not
     followed for the literal decision. Validation still uses the full schema.
  4. *Validation input*: the expanded configuration. Includes are resolved, aliases are expanded and literals are
     strings. A text include is its text.
  5. *Remote values that are overridden* are changed in the override (step 5 rule 7.1). Only values whose physical
     place is remote are refused. Replacing a collection that has remote parts is refused when it would need to
     change or remove remote content. Replacing the include directive itself (the PATH whose value is the
     include) is the existing include-root rule: exit 3.
  6. *`--in-include` below a remote include*: exit 3 with the include string. It cannot write the remote file.
  7. *Which include string is reported*: the local include directive on the chain from the local file to the
     remote source, exactly as written in that file. For a remote include inside remote YAML, the outermost one
     that is written in a local file.
  8. *Nested VALUE strings* (inside a mapping or list VALUE): each include-like string is tagged `!literal` unless
     `--include` is given or the schema gives its own path `format: uri`. This applies the same rule to every
     string that would otherwise become an include without being asked.
  9. *`set` with `--include` and a remote VALUE*: written plain. Read-back fetches it, and a failed fetch gives exit
     3 with no file changed (decision D's rule for unloadable targets).
  10. *Changing an existing `!literal` value*: the new string replaces the whole node, tag included, and is tagged
      again only by the rule above.
  11. *`watch`*: polls local files and the schema file. URLs are not polled. Remote content is fetched again on
      every reload. A fetch failure during `watch` is a load error: nothing is recorded and watching continues
      (step 2).
  12. *`--sources`*: a remote scalar is labelled with its URL, as the include string is written.
  13. *Fetch details*: 10 s timeout. Redirects are followed (urllib default). The path suffix check ignores the
      query and the fragment and is case-sensitive, like the `file:` include suffix check.
  14. *Schema problems*: invalid JSON, a schema that is not valid 2020-12, or an unresolvable `$ref` gives exit 4,
      naming the schema file. Each command except `history` loads the configuration and so checks the schema;
      `history` checks it only when it imports.
