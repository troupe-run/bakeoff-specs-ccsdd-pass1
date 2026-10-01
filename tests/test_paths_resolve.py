"""Dotted PATH parsing, formatting and history prefix matching (Requirements 1.5, 5.1, 5.3, 8.3)."""

import pytest

from troupe.config.errors import ExitCode, UsageError
from troupe.config.paths import ROOT, ConfigPath, is_index_segment, path_has_prefix


@pytest.mark.parametrize(
    ("text", "segments"),
    [
        ("a", ("a",)),
        ("a.b", ("a", "b")),
        ("agents.0.name", ("agents", "0", "name")),
        ("a b.c-d", ("a b", "c-d")),
        ("é.ü", ("é", "ü")),
    ],
)
def test_parse_splits_on_dots(text: str, segments: tuple[str, ...]) -> None:
    path = ConfigPath.parse(text)
    assert path.segments == segments
    assert str(path) == text


@pytest.mark.parametrize("text", ["a..b", ".a", "a.", ".", "..", "a.b."])
def test_parse_rejects_empty_path_or_segment_naming_path(text: str) -> None:
    with pytest.raises(UsageError) as info:
        ConfigPath.parse(text)
    error = info.value
    assert error.exit_code is ExitCode.USAGE
    assert error.path == text
    assert error.file is None
    assert text in str(error)


def test_parse_rejects_empty_path_naming_it_explicitly() -> None:
    with pytest.raises(UsageError) as info:
        ConfigPath.parse("")
    error = info.value
    assert error.exit_code is ExitCode.USAGE
    assert error.path is None
    assert error.file is None
    message = str(error)
    assert "''" in message
    assert "PATH" in message
    assert not message.startswith(":")


@pytest.mark.parametrize(
    ("segment", "expected"),
    [
        ("0", True),
        ("12", True),
        ("007", True),
        ("", False),
        ("-1", False),
        ("+1", False),
        ("1a", False),
        ("a", False),
        (" 1", False),
        ("1.0", False),
        ("\u0661", False),  # ARABIC-INDIC DIGIT ONE
        ("²", False),
    ],
)
def test_is_index_segment(segment: str, expected: bool) -> None:
    assert is_index_segment(segment) is expected


def test_child_builds_paths_from_keys_and_indices() -> None:
    path = ROOT.child("agents").child(0).child("name")
    assert path.segments == ("agents", "0", "name")
    assert str(path) == "agents.0.name"
    assert path == ConfigPath.parse("agents.0.name")


def test_root_is_empty_and_child_does_not_mutate() -> None:
    assert ROOT.segments == ()
    assert str(ROOT) == ""
    parent = ConfigPath.parse("a")
    child = parent.child("b")
    assert parent.segments == ("a",)
    assert child.segments == ("a", "b")


def test_config_path_is_hashable_value() -> None:
    assert {ConfigPath.parse("a.b"), ConfigPath.parse("a.b")} == {ConfigPath(("a", "b"))}


@pytest.mark.parametrize(
    ("path", "prefix", "expected"),
    [
        ("a", "a", True),
        ("a.b", "a", True),
        ("a.b.c", "a", True),
        ("a.b.c", "a.b", True),
        ("ab", "a", False),
        ("ab.c", "a", False),
        ("a", "a.b", False),
        ("b.a", "a", False),
        ("list.10", "list.1", False),
        ("list.1", "list.1", True),
    ],
)
def test_path_has_prefix(path: str, prefix: str, expected: bool) -> None:
    assert path_has_prefix(path, prefix) is expected
