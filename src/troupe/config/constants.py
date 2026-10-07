"""Single-place names used by more than one module.

Every name the configuration layer and the CLI agree on lives here, so that each decision is made in
exactly one place.
"""

from typing import Final

CONFIG_DIR_NAME: Final = ".troupe"
"""Directory under the theatre that holds the root configuration file and the history store."""

CONFIG_FILE_NAMES: Final = ("troupe.yml", "troupe.yaml")
"""Accepted root configuration file names; exactly one of them must exist."""

INCLUDE_PREFIX: Final = "file:"
"""Prefix of a string value that includes another file."""

INCLUDE_REF_KEY: Final = "$ref"
"""Reserved key of a mapping-form include ``{$ref: "file:...", <overrides>}``."""

MERGE_KEY: Final = "<<"
"""Text of a YAML merge key: a mapping key written plain with this text (config-aliases 2.4).

The loader merges such a key's value into its mapping, and no writer writes it plain as a key.
"""

YAML_INCLUDE_SUFFIXES: Final = (".yml", ".yaml")
"""Include targets with these suffixes (case-sensitive) are embedded as parsed YAML.

Any other target is embedded as its exact text.
"""

REMOTE_INCLUDE_PREFIXES: Final = ("https://", "http://")
"""Prefixes (case-sensitive) of a string value that includes remote content.

The whole string is the URL. A remote target is embedded as parsed YAML when its URL path, without
query and fragment, ends in one of :data:`YAML_INCLUDE_SUFFIXES`, and as its exact text otherwise.
"""

LITERAL_TAG: Final = "!literal"
"""The one explicit YAML tag the loader accepts: a scalar so tagged is a plain string, never an
include."""

SCHEMA_FILE_NAME: Final = "troupe.schema.json"
"""File name of the theatre's optional JSON Schema, next to the root configuration file."""

SCHEMA_DIALECT: Final = "https://json-schema.org/draft/2020-12/schema"
"""The only JSON Schema dialect accepted as the schema's ``$schema``."""

REMOTE_FETCH_TIMEOUT_SECONDS: Final = 10
"""Longest wait in seconds for a server answering a remote include fetch."""

HISTORY_STORE_NAME: Final = "history.sqlite"
"""File name of the history store under the theatre's configuration directory."""

PATH_SEPARATOR: Final = "."
"""Separator between segments of a dotted configuration PATH."""

WATCH_POLL_SECONDS: Final = 0.2
"""Interval in seconds at which ``troupe config watch`` checks the watched files for changes."""

WATCHING_LINE_PREFIX: Final = "watching "
"""Start of the readiness line ``watching <dir>`` that ``troupe config watch`` prints once."""
