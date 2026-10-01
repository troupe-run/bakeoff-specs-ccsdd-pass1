"""Error types, the exit-code mapping and the single message format (10.1, 10.3, 10.4, 10.5)."""

from pathlib import Path

import pytest

from troupe.config.errors import (
    ConfigLoadError,
    ExitCode,
    PathError,
    PathNotFoundError,
    TroupeError,
    UsageError,
    WriteRefusedError,
    WrongNodeKindError,
    format_message,
)


def test_exit_code_values_match_the_documented_mapping() -> None:
    assert {code.name: int(code) for code in ExitCode} == {
        "OK": 0,
        "USAGE": 2,
        "WRITE_REFUSED": 3,
        "LOAD": 4,
        "PATH": 5,
    }


@pytest.mark.parametrize(
    ("error_class", "expected_code"),
    [
        (UsageError, 2),
        (WriteRefusedError, 3),
        (ConfigLoadError, 4),
        (PathError, 5),
        (PathNotFoundError, 5),
        (WrongNodeKindError, 5),
    ],
)
def test_each_error_class_reports_its_exit_code(
    error_class: type[TroupeError], expected_code: int
) -> None:
    error = error_class("boom", file=Path("t/.troupe/troupe.yml"), path="a.b")

    assert isinstance(error, TroupeError)
    assert error.exit_code == expected_code
    assert type(error).exit_code == expected_code


def test_path_error_kinds_are_path_errors() -> None:
    assert issubclass(PathNotFoundError, PathError)
    assert issubclass(WrongNodeKindError, PathError)
    assert not issubclass(PathNotFoundError, WrongNodeKindError)
    assert not issubclass(WrongNodeKindError, PathNotFoundError)


def test_error_classes_are_distinct_from_each_other() -> None:
    top_level = [UsageError, WriteRefusedError, ConfigLoadError, PathError]
    for cls in top_level:
        for other in top_level:
            if cls is not other:
                assert not issubclass(cls, other)


def test_error_carries_file_path_and_problem() -> None:
    error = PathNotFoundError(
        "no such key", file=Path("theatre/.troupe/agents.yaml"), path="agents.bob"
    )

    assert error.file == Path("theatre/.troupe/agents.yaml")
    assert error.path == "agents.bob"
    assert error.problem == "no such key"


def test_error_file_and_path_default_to_none() -> None:
    error = UsageError("unknown subcommand")

    assert error.file is None
    assert error.path is None
    assert error.problem == "unknown subcommand"


def test_message_names_file_then_path_then_problem() -> None:
    message = format_message(
        file=Path("theatre/.troupe/troupe.yml"), path="agents.0.name", problem="not found"
    )

    assert message == "theatre/.troupe/troupe.yml: agents.0.name: not found"


def test_message_with_file_only() -> None:
    message = format_message(file=Path("theatre/.troupe/troupe.yaml"), path=None, problem="bad")

    assert message == "theatre/.troupe/troupe.yaml: bad"


def test_message_with_path_only() -> None:
    message = format_message(file=None, path="a..b", problem="empty segment in PATH")

    assert message == "a..b: empty segment in PATH"


def test_message_with_problem_only() -> None:
    assert format_message(file=None, path=None, problem="unknown option") == "unknown option"


def test_message_shows_the_file_as_opened_not_resolved() -> None:
    included = Path("theatre/.troupe/../shared/agents.yaml")

    message = format_message(file=included, path="bob", problem="not found")

    assert message == "theatre/.troupe/../shared/agents.yaml: bob: not found"


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            WrongNodeKindError(
                "is a mapping, not a scalar",
                file=Path("t/.troupe/../shared/agents.yaml"),
                path="agents",
            ),
            "t/.troupe/../shared/agents.yaml: agents: is a mapping, not a scalar",
        ),
        (
            ConfigLoadError("duplicate key", file=Path("t/.troupe/troupe.yml")),
            "t/.troupe/troupe.yml: duplicate key",
        ),
        (UsageError("malformed PATH", path=".a"), ".a: malformed PATH"),
        (WriteRefusedError("refused"), "refused"),
    ],
)
def test_str_of_an_error_is_the_formatted_message(error: TroupeError, expected: str) -> None:
    assert str(error) == expected
    assert str(error) == format_message(file=error.file, path=error.path, problem=error.problem)


def test_errors_are_raisable_and_catchable_as_troupe_error() -> None:
    with pytest.raises(TroupeError) as caught:
        raise ConfigLoadError("missing", file=Path("t/.troupe/troupe.yml"))

    assert caught.value.exit_code == ExitCode.LOAD
