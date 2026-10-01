"""Locate the root configuration file of a theatre (Requirements 2.1-2.4).

Only checks existence: nothing is created, opened or modified here.
"""

from pathlib import Path

from troupe.config.constants import CONFIG_DIR_NAME, CONFIG_FILE_NAMES
from troupe.config.errors import ConfigLoadError


def find_config_file(theatre_dir: Path) -> Path:
    """Return ``<theatre_dir>/.troupe/<name>`` for the single existing regular config file.

    Raises :class:`ConfigLoadError` naming the expected ``troupe.yml`` when no config file exists
    (also when ``theatre_dir`` is missing or not a directory), and naming both files when both
    ``troupe.yml`` and ``troupe.yaml`` exist.
    """
    config_dir = theatre_dir / CONFIG_DIR_NAME
    candidates = [config_dir / name for name in CONFIG_FILE_NAMES]
    existing = [candidate for candidate in candidates if candidate.is_file()]
    if not existing:
        expected = " or ".join(CONFIG_FILE_NAMES)
        raise ConfigLoadError(
            f"no configuration file (expected {expected})",
            file=candidates[0],
        )
    if len(existing) > 1:
        raise ConfigLoadError(
            f"both {existing[0].name} and {existing[1]} exist",
            file=existing[0],
        )
    return existing[0]
