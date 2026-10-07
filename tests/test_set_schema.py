"""Sets that the schema rejects (config-remote-schema Requirement 7).

The read-back of a set (config-set-writeback 13.6) loads the patched configuration, which validates
it against ``.troupe/troupe.schema.json``. A violation found there is a refusal (exit 3) naming the
schema file, PATH, the failing key path (none at the root) and the validator's description, with
no file changed and no event recorded beyond the import or reconcile ``set`` runs first. Every test
runs the real console script.

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

import json
from pathlib import Path

import pytest

from set_support import run_config, run_set, snapshot_tree
from support import MakeTheatre, step1_members
from troupe.config.constants import CONFIG_DIR_NAME, SCHEMA_FILE_NAME
from troupe.config.errors import ExitCode

CONFIG_NAMES = ["troupe.yml", "troupe.yaml"]
DIALECT = "https://json-schema.org/draft/2020-12/schema"

SCHEMA = json.dumps(
    {
        "$schema": DIALECT,
        "type": "object",
        "maxProperties": 3,
        "properties": {
            "port": {"type": "integer", "maximum": 10},
            "a": {
                "type": "object",
                "properties": {"tags": {"type": "array", "items": {"type": "string"}}},
            },
        },
    }
)
CONFIG = "port: 1\na:\n  tags: [x]\nname: n\n"


def _history(theatre: Path) -> list[dict[str, object]]:
    result = run_config("history", "--theatre", str(theatre), cwd=theatre)
    assert (result.exit_code, result.stderr) == (0, ""), result.stderr
    return [step1_members(line) for line in result.stdout.splitlines()]


@pytest.mark.parametrize("config_name", CONFIG_NAMES)
@pytest.mark.parametrize(
    ("path", "value", "failing"),
    [
        pytest.param("port", "99", "port: 99 is greater than the maximum of 10", id="value"),
        pytest.param("a.tags", "[x, 1]", "a.tags.1: 1 is not of type 'string'", id="nested"),
        pytest.param(
            "extra",
            "1",
            "{'port': 1, 'a': {'tags': ['x']}, 'name': 'n', 'extra': 1} has too many properties",
            id="root",
        ),
    ],
)
def test_set_making_the_configuration_invalid_is_refused(
    make_theatre: MakeTheatre, config_name: str, path: str, value: str, failing: str
) -> None:
    """Req 7.1: a set whose read-back does not validate exits 3 with nothing on stdout, stderr
    naming the schema file, PATH, the failing key path (none at the root) and the validator's
    description, and no file changed."""
    theatre = make_theatre(
        CONFIG,
        config_name=config_name,
        files={f"{CONFIG_DIR_NAME}/{SCHEMA_FILE_NAME}": SCHEMA},
    )
    before = snapshot_tree(theatre)
    schema = theatre / CONFIG_DIR_NAME / SCHEMA_FILE_NAME

    result = run_set(theatre, path, value)

    assert result.exit_code == ExitCode.WRITE_REFUSED, result.stderr
    assert result.stdout == b""
    assert result.stderr == (
        f"{schema}: {path}: the set would make the configuration invalid: {failing}\n"
    )
    assert snapshot_tree(theatre) == before


@pytest.mark.parametrize("config_name", CONFIG_NAMES)
def test_refused_set_records_only_the_reconcile_it_runs_first(
    make_theatre: MakeTheatre, config_name: str
) -> None:
    """Req 7.2: a set refused under 7.1 records no event; only the reconcile of a hand edit that
    ``set`` runs first is recorded, and a later valid set is recorded as usual."""
    theatre = make_theatre(
        CONFIG,
        config_name=config_name,
        files={f"{CONFIG_DIR_NAME}/{SCHEMA_FILE_NAME}": SCHEMA},
    )
    imported = _history(theatre)
    assert [event["op"] for event in imported] == ["import", "import", "import"]
    config = theatre / CONFIG_DIR_NAME / config_name
    config.write_text(CONFIG.replace("port: 1", "port: 2"))

    refused = run_set(theatre, "port", "11")

    assert refused.exit_code == ExitCode.WRITE_REFUSED, refused.stderr
    assert config.read_text() == CONFIG.replace("port: 1", "port: 2")
    events = _history(theatre)
    assert events[: len(imported)] == imported
    assert [(e["op"], e["path"], e["old"], e["new"]) for e in events[len(imported) :]] == [
        ("set", "port", 1, 2)
    ]

    accepted = run_set(theatre, "port", "10")

    assert accepted.exit_code == ExitCode.OK, accepted.stderr
    assert [(e["op"], e["path"], e["old"], e["new"]) for e in _history(theatre)[-2:]] == [
        ("set", "port", 1, 2),
        ("set", "port", 2, 10),
    ]
