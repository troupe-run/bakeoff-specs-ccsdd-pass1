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

YAML_INCLUDE_SUFFIXES: Final = (".yml", ".yaml")
"""Include targets with these suffixes (case-sensitive) are embedded as parsed YAML.

Any other target is embedded as its exact text.
"""

HISTORY_STORE_NAME: Final = "history.sqlite"
"""File name of the history store under the theatre's configuration directory."""

PATH_SEPARATOR: Final = "."
"""Separator between segments of a dotted configuration PATH."""

WATCH_POLL_SECONDS: Final = 0.2
"""Interval in seconds at which ``troupe config watch`` checks the watched files for changes."""

WATCHING_LINE_PREFIX: Final = "watching "
"""Start of the readiness line ``watching <dir>`` that ``troupe config watch`` prints once."""
