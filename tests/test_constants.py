"""Single-place names used by more than one module."""

from troupe.config import constants


def test_constants_hold_the_documented_names() -> None:
    assert constants.CONFIG_DIR_NAME == ".troupe"
    assert constants.CONFIG_FILE_NAMES == ("troupe.yml", "troupe.yaml")
    assert constants.INCLUDE_PREFIX == "file:"
    assert constants.YAML_INCLUDE_SUFFIXES == (".yml", ".yaml")
    assert constants.HISTORY_STORE_NAME == "history.sqlite"
    assert constants.PATH_SEPARATOR == "."


def test_watch_constants_hold_the_poll_interval_and_the_readiness_line_prefix() -> None:
    """Req 4.3, 5.1: watch polls every 0.2 s and its readiness line starts with ``watching ``."""
    assert constants.WATCH_POLL_SECONDS == 0.2
    assert constants.WATCHING_LINE_PREFIX == "watching "
