"""Single-place names used by more than one module."""

from troupe.config import constants


def test_constants_hold_the_documented_names() -> None:
    assert constants.CONFIG_DIR_NAME == ".troupe"
    assert constants.CONFIG_FILE_NAMES == ("troupe.yml", "troupe.yaml")
    assert constants.INCLUDE_PREFIX == "file:"
    assert constants.YAML_INCLUDE_SUFFIXES == (".yml", ".yaml")
    assert constants.HISTORY_STORE_NAME == "history.sqlite"
    assert constants.PATH_SEPARATOR == "."
