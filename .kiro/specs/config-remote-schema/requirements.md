# Requirements Document

## Project Description (Input)
### Problem
Teams want one shared configuration for every theatre (step 7). They publish it at an `https://` URL and include it
from each theatre's `troupe.yml`. troupe must read that shared configuration, but it must never pretend it can
change it: a `set` that would have to change a value that comes from the URL is refused, and the message tells the
user which include line in their own file leads there.

Once `https://` strings are includes, URL-valued settings (webhooks, API endpoints) become ambiguous. A theatre can
describe its configuration with a JSON Schema at `.troupe/troupe.schema.json`. The schema marks URL-valued settings
with `"format": "uri"`, and any other value that must be taken literally can be tagged `!literal`. The schema also
lets troupe reject a configuration, or a `set`, that does not match the theatre's declared shape.

### Current State
- config-foundation (`config/loader.py`): only `file:` strings are includes (`INCLUDE_PREFIX`, `_Builder._build`,
  `_ref_value`). Targets are local paths relative to the including file (`_include`, `read_include`), and
  `SourceFile` assumes a local `Path`. Every explicit tag is refused with exit 4 (`_refuse_properties`, decision I,
  Requirement 3.7). `http://` and `https://` strings are plain strings today, and no test pins that.
- config-set-writeback: decision D says a `file:` VALUE is written as a plain string, becomes an include, and an
  unloadable target gives exit 3. Six places rebuild include strings as `"file:" + spec` (`compare.py`,
  `shared.py`, `planner.py`). Read-back verification reloads every file (`verify.py`).
- config-include-overrides: new keys under an include are written as overrides in the including file. An
  overridden value is changed in the override; a non-overridden value in the file it comes from. `--in-include`
  puts new keys in the included file.
- config-file-sync: `watch` polls `ConfigDocument.files` and the root config candidates. It cannot poll a URL.
- config-get-show: `--sources` labels each scalar with its file relative to the theatre (`source_label`).
- There is no JSON Schema support and no `jsonschema` dependency.

### Desired Outcome
The step 7 contract section "Remote includes and schema" holds (verbatim below). Every reader (`get`, `show`,
`show --sources`, `history`, reconcile, `watch`) sees remote content and never sees literal strings expanded. All
earlier `set` rules still hold: byte-surgical rendering, events and lineage, atomic writes, read-back verification,
overrides, `--in-include`, `--reason` and `--expand-alias`.

### Approach (from discovery)
The loader decides what an include is (one recognition function; a schema walker over `properties` / `items` /
`additionalProperties` and the `!literal` tag decide literals while walking), fetches remote sources with the
standard library (any failure → exit 4) into in-memory `SourceFile`s with a URL identity, and validates the loaded
configuration with a JSON Schema 2020-12 validator. The writer treats remote sources as read-only, checks the
intended configuration against the schema before writing, and renders include-like VALUE strings tagged `!literal`
unless `--include` is given. See `brief.md` for the rejected alternatives, scope, boundary candidates and the 14
open choices proposed at discovery.

### Step 7 contract, section "Remote includes and schema" (authoritative)
Includes can now point at https:// URLs (shared team configuration). They are read-only from troupe's point of view:
refuse changes to their values, and tell the user which include line to change instead.

**Remote includes and schema (from step 7)**
- A string starting `https://` or `http://` is also an include; it is fetched when the configuration loads
  (YAML if the URL path ends `.yml`/`.yaml`, otherwise text). A failed fetch → exit 4.
- Changing a value that comes from a remote include → exit 3; stderr contains the include string exactly as
  written in the local file. (New keys under a remote include become local overrides, per step 5.)
- `<dir>/.troupe/troupe.schema.json` (JSON Schema 2020-12), if present, describes the configuration. A string
  whose schema — found through `properties`, `items` and `additionalProperties` — has `"format": "uri"` is a
  literal, not an include. A string tagged `!literal` is a literal.
- The loaded configuration must validate against the schema, else exit 4. A `set` that would make it invalid
  → exit 3.
- `set PATH VALUE` where `VALUE` is a string that would be read as an include and the schema does not mark
  `PATH` as `format: uri` writes it tagged `!literal`, unless `--include` is given.

## Requirements
<!-- Will be generated in /kiro-spec-requirements phase -->
