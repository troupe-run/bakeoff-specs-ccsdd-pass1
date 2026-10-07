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


# ---- task 4.3: end to end with a schema using local `$defs`/`$ref` ----

AGENT_SCHEMA = json.dumps(
    {
        "$schema": DIALECT,
        "$defs": {
            "agent": {
                "type": "object",
                "required": ["model"],
                "additionalProperties": False,
                "properties": {
                    "model": {"enum": ["small", "big"]},
                    "max_tokens": {"type": "integer"},
                    "temperature": {"type": "number"},
                },
            }
        },
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "name": {"type": "string"},
            "defaults": {"$ref": "#/$defs/agent"},
            "agents": {"type": "object", "additionalProperties": {"$ref": "#/$defs/agent"}},
        },
    },
    indent=2,
)
"""Every agent (``defaults`` and each entry of ``agents``) is checked through a local ``$ref``."""

TEAM = """\
# team settings
name: demo

defaults: &d
  model: small   # shared
agents:
  coder: file:agents/coder.yaml  # c
  lead: *d
"""
"""A root file with a comment, a blank line, an anchor used by an alias and a scalar include."""

CODER_FILE = f"{CONFIG_DIR_NAME}/agents/coder.yaml"
CODER = "# coder\nmodel: small\nmax_tokens: 100\n"
BAD_CODER_FILE = f"{CONFIG_DIR_NAME}/agents/bad.yaml"
BAD_CODER = "model: huge\n"
"""An included file that is not referenced yet and whose content the schema rejects."""


def _team(make_theatre: MakeTheatre, config_name: str, config: str = TEAM) -> Path:
    return make_theatre(
        config,
        config_name=config_name,
        files={
            f"{CONFIG_DIR_NAME}/{SCHEMA_FILE_NAME}": AGENT_SCHEMA,
            CODER_FILE: CODER,
            BAD_CODER_FILE: BAD_CODER,
        },
    )


@pytest.mark.parametrize("config_name", CONFIG_NAMES)
@pytest.mark.parametrize(
    ("args", "failing"),
    [
        pytest.param(("name", "5"), "name: 5 is not of type 'string'", id="type"),
        pytest.param(
            ("agents.coder.model", "huge"),
            "agents.coder.model: 'huge' is not one of ['small', 'big']",
            id="enum-in-included-file",
        ),
        pytest.param(
            ("agents.coder.max_tokens", "many"),
            "agents.coder.max_tokens: 'many' is not of type 'integer'",
            id="type-in-included-file",
        ),
        pytest.param(
            ("agents.extra", "{max_tokens: 1}"),
            "agents.extra: 'model' is a required property",
            id="required",
        ),
        pytest.param(
            ("extra", "1"),
            "Additional properties are not allowed ('extra' was unexpected)",
            id="additional-properties-at-the-root",
        ),
        pytest.param(
            ("agents.coder.top_p", "0.9"),
            "agents.coder: Additional properties are not allowed ('top_p' was unexpected)",
            id="override-below-an-include",
        ),
        pytest.param(
            ("agents.reviewer", "file:agents/bad.yaml", "--include"),
            "agents.reviewer.model: 'huge' is not one of ['small', 'big']",
            id="new-include-with-invalid-content",
        ),
        pytest.param(
            ("agents.lead.model", "huge", "--expand-alias"),
            "agents.lead.model: 'huge' is not one of ['small', 'big']",
            id="expand-alias",
        ),
    ],
)
def test_set_breaking_a_ref_schema_is_refused_changing_nothing(
    make_theatre: MakeTheatre, config_name: str, args: tuple[str, ...], failing: str
) -> None:
    """Req 7.1, 7.2, 5.1: through a schema whose agents are checked by a local ``$defs``/``$ref``,
    a set that breaks a type, an enum, a required key or ``additionalProperties: false`` (at the
    root, in an included file, as an override below an include, through a newly written include
    or through ``--expand-alias``) exits 3 with nothing on stdout, stderr naming the schema file,
    PATH, the failing key path and the validator's description; every file is byte-identical and
    no event is recorded."""
    theatre = _team(make_theatre, config_name)
    imported = _history(theatre)
    before = snapshot_tree(theatre)
    schema = theatre / CONFIG_DIR_NAME / SCHEMA_FILE_NAME
    path, value, *options = args

    result = run_set(theatre, path, value, *options)

    assert result.exit_code == ExitCode.WRITE_REFUSED, result.stderr
    assert result.stdout == b""
    assert result.stderr == (
        f"{schema}: {path}: the set would make the configuration invalid: {failing}\n"
    )
    assert snapshot_tree(theatre) == before
    assert _history(theatre) == imported


@pytest.mark.parametrize("config_name", CONFIG_NAMES)
@pytest.mark.parametrize(
    ("args", "relative", "expected", "events"),
    [
        pytest.param(
            ("agents.coder.model", "big"),
            CODER_FILE,
            CODER.replace("small", "big"),
            [("set", "agents.coder.model", "small", "big")],
            id="in-included-file",
        ),
        pytest.param(
            ("agents.coder.temperature", "0.3"),
            None,
            TEAM.replace(
                "  coder: file:agents/coder.yaml  # c\n",
                "  coder:  # c\n    $ref: file:agents/coder.yaml\n    temperature: 0.3\n",
            ),
            [("set", "agents.coder.temperature", None, 0.3)],
            id="override-below-an-include",
        ),
        pytest.param(
            ("agents.lead.model", "big", "--expand-alias"),
            None,
            TEAM.replace("  lead: *d\n", "  lead:  # expanded from *d by troupe\n    model: big\n"),
            [("set", "agents.lead.model", "small", "big")],
            id="expand-alias",
        ),
        pytest.param(
            ("agents.extra", "{model: big, max_tokens: 1}"),
            None,
            TEAM + "  extra:\n    model: big\n    max_tokens: 1\n",
            [
                ("set", "agents.extra.model", None, "big"),
                ("set", "agents.extra.max_tokens", None, 1),
            ],
            id="new-agent",
        ),
        pytest.param(
            ("agents.reviewer", "file:agents/coder.yaml", "--include"),
            None,
            TEAM + "  reviewer: file:agents/coder.yaml\n",
            [
                ("set", "agents.reviewer.model", None, "small"),
                ("set", "agents.reviewer.max_tokens", None, 100),
            ],
            id="new-valid-include",
        ),
    ],
)
def test_set_keeping_a_ref_schema_valid_is_written_byte_exactly(
    make_theatre: MakeTheatre,
    config_name: str,
    args: tuple[str, ...],
    relative: str | None,
    expected: str,
    events: list[tuple[str, str, object, object]],
) -> None:
    """Req 7.1, 10.1, 5.1: a set that keeps the configuration valid against the ``$defs``/``$ref``
    schema (in an included file, as an override, through ``--expand-alias``, a new mapping, a new
    include) exits
    0 with nothing on stdout or stderr, changes exactly the bytes of one file and records its
    ``set`` events."""
    theatre = _team(make_theatre, config_name)
    imported = _history(theatre)
    before = snapshot_tree(theatre)
    changed = relative if relative is not None else f"{CONFIG_DIR_NAME}/{config_name}"
    path, value, *options = args

    result = run_set(theatre, path, value, *options)

    assert (result.exit_code, result.stdout, result.stderr) == (0, b"", ""), result.stderr
    after = snapshot_tree(theatre)
    assert after[changed] == expected.encode("utf-8")
    assert {k: v for k, v in after.items() if k != changed} == {
        k: v for k, v in before.items() if k != changed
    }
    added = [(e["op"], e["path"], e["old"], e["new"]) for e in _history(theatre)[len(imported) :]]
    assert added == events


@pytest.mark.parametrize("config_name", CONFIG_NAMES)
@pytest.mark.parametrize(
    ("path", "value"),
    [
        pytest.param("agents.coder.max_tokens", "200", id="fixing-nothing"),
        pytest.param("name", "'demo'", id="same-value"),
        pytest.param("agents.lead.model", "small", id="would-fix-it"),
    ],
)
def test_set_on_an_already_invalid_configuration_exits_4(
    make_theatre: MakeTheatre, config_name: str, path: str, value: str
) -> None:
    """Req 5.2, 5.6, 7.1: when the configuration already fails the schema, ``set`` fails at
    loading: exit 4, nothing on stdout, stderr naming the schema file, the failing key path and
    the validator's description; no file is changed and no history state is created, even for a
    set whose result would be valid."""
    theatre = _team(make_theatre, config_name, TEAM.replace("lead: *d", "lead: {model: huge}"))
    before = snapshot_tree(theatre)
    schema = theatre / CONFIG_DIR_NAME / SCHEMA_FILE_NAME

    result = run_set(theatre, path, value)

    assert result.exit_code == ExitCode.LOAD, result.stderr
    assert result.stdout == b""
    assert result.stderr == (
        f"{schema}: agents.lead.model: the configuration does not match the schema:"
        " 'huge' is not one of ['small', 'big']\n"
    )
    assert snapshot_tree(theatre) == before
    assert sorted(p.name for p in (theatre / CONFIG_DIR_NAME).iterdir()) == sorted(
        [config_name, SCHEMA_FILE_NAME, "agents"]
    )
