"""Locating the root configuration file of a theatre (Requirements 2.1, 2.2, 2.3, 2.4)."""

from pathlib import Path

import pytest

from support import MakeTheatre
from troupe.config.errors import ConfigLoadError, ExitCode
from troupe.config.locate import find_config_file


def _snapshot(root: Path) -> list[Path]:
    return sorted(root.rglob("*"))


@pytest.mark.parametrize("name", ["troupe.yml", "troupe.yaml"])
def test_returns_the_single_existing_config_file(make_theatre: MakeTheatre, name: str) -> None:
    theatre = make_theatre("a: 1\n", config_name=name)
    assert find_config_file(theatre) == theatre / ".troupe" / name


def test_neither_file_names_expected_location(make_theatre: MakeTheatre) -> None:
    theatre = make_theatre(files={".troupe/other.yml": "a: 1\n"})
    before = _snapshot(theatre)
    with pytest.raises(ConfigLoadError) as info:
        find_config_file(theatre)
    error = info.value
    assert error.exit_code is ExitCode.LOAD
    assert error.file == theatre / ".troupe" / "troupe.yml"
    assert str(theatre / ".troupe" / "troupe.yml") in str(error)
    assert "troupe.yaml" in str(error)
    assert _snapshot(theatre) == before


def test_theatre_without_config_dir_is_not_modified(make_theatre: MakeTheatre) -> None:
    theatre = make_theatre()
    with pytest.raises(ConfigLoadError) as info:
        find_config_file(theatre)
    assert info.value.file == theatre / ".troupe" / "troupe.yml"
    assert _snapshot(theatre) == []


def test_both_files_names_both(make_theatre: MakeTheatre) -> None:
    theatre = make_theatre("a: 1\n", files={".troupe/troupe.yaml": "a: 2\n"})
    with pytest.raises(ConfigLoadError) as info:
        find_config_file(theatre)
    error = info.value
    assert error.exit_code is ExitCode.LOAD
    message = str(error)
    assert str(theatre / ".troupe" / "troupe.yml") in message
    assert str(theatre / ".troupe" / "troupe.yaml") in message


def test_missing_theatre_directory_names_expected_location(tmp_path: Path) -> None:
    theatre = tmp_path / "absent"
    with pytest.raises(ConfigLoadError) as info:
        find_config_file(theatre)
    error = info.value
    assert error.exit_code is ExitCode.LOAD
    assert error.file == theatre / ".troupe" / "troupe.yml"
    assert str(theatre / ".troupe" / "troupe.yml") in str(error)
    assert not theatre.exists()


def test_theatre_that_is_a_file_names_expected_location(tmp_path: Path) -> None:
    theatre = tmp_path / "plain-file"
    theatre.write_text("not a directory\n", encoding="utf-8")
    with pytest.raises(ConfigLoadError) as info:
        find_config_file(theatre)
    assert info.value.file == theatre / ".troupe" / "troupe.yml"
    assert theatre.read_text(encoding="utf-8") == "not a directory\n"


def test_directory_named_like_config_file_is_not_a_config_file(make_theatre: MakeTheatre) -> None:
    theatre = make_theatre("a: 1\n", config_name="troupe.yaml")
    (theatre / ".troupe" / "troupe.yml").mkdir()
    assert find_config_file(theatre) == theatre / ".troupe" / "troupe.yaml"
