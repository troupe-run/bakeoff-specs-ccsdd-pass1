"""The command's actor from ``TROUPE_ACTOR`` (config-lineage task 1.2).

Proves the grammar and the default actor of ``troupe.cli.actor.actor_from_environment`` with
explicit environment mappings (2.1, 2.2), the usage error naming ``TROUPE_ACTOR``, quoting the value
and stating the accepted forms (2.3), and that ``run`` refuses an invalid value for every
subcommand with exit 2 and empty stdout before any state is created or any file changed, while a
valid value lets a command run normally (2.3, 2.4).

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

import getpass
import io

import pytest

import troupe.cli.main as cli_main
from support import MakeTheatre
from troupe.cli.actor import TROUPE_ACTOR_VARIABLE, actor_from_environment
from troupe.config.errors import UsageError
from troupe.config.lineage import Actor

CONFIG = "# lineage fixture\nname: demo\nagents:\n  lead: alice  # keep\n"

INVALID_VALUES = [
    "",
    " ",
    "alice",
    "Human:alice",
    "AGENT:planner",
    "robot:r2",
    ":alice",
    "human:",
    "agent:",
    "human:   ",
    "agent:\t",
    " human:alice",
]


def _expected_message(value: str) -> str:
    return f"TROUPE_ACTOR: invalid value {value!r}: expected human:<id> or agent:<id>"


# ---- 2.1: the declared actor ----


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("human:alice", Actor(kind="human", id="alice")),
        ("agent:planner", Actor(kind="agent", id="planner")),
        ("agent:team:planner:7", Actor(kind="agent", id="team:planner:7")),
        ("human:  alice b  ", Actor(kind="human", id="  alice b  ")),
        ("agent:x", Actor(kind="agent", id="x")),
        ("human:élodie", Actor(kind="human", id="élodie")),
    ],
)
def test_valid_value_gives_kind_and_verbatim_id(value: str, expected: Actor) -> None:
    """Req 2.1: ``human:<id>``/``agent:<id>``: that kind, the id all text after the first ``:``."""
    assert actor_from_environment({TROUPE_ACTOR_VARIABLE: value}) == expected


def test_variable_name_is_troupe_actor() -> None:
    """Req 2.1: the variable read is exactly ``TROUPE_ACTOR``."""
    assert TROUPE_ACTOR_VARIABLE == "TROUPE_ACTOR"
    assert actor_from_environment({"TROUPE_ACTOR": "agent:a"}) == Actor(kind="agent", id="a")


# ---- 2.2: the default actor ----


def test_unset_uses_the_login_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """Req 2.2: unset → kind ``human`` with the login name taken from the environment."""
    for name in ("LOGNAME", "USER", "LNAME", "USERNAME"):
        monkeypatch.setenv(name, "carol-login")

    assert actor_from_environment({}) == Actor(kind="human", id="carol-login")


def test_unset_ignores_other_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    """Req 2.2: only ``TROUPE_ACTOR`` matters; other variables in the mapping set no actor."""
    monkeypatch.setenv("LOGNAME", "dave")

    assert actor_from_environment({"TROUPE_THEATRE": "agent:x"}) == Actor(kind="human", id="dave")


@pytest.mark.parametrize("error", [OSError("no login"), KeyError("uid")])
def test_unset_without_login_name_gives_null_id(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    """Req 2.2: unset and the login name cannot be determined → kind ``human``, id ``None``."""

    def _fail() -> str:
        raise error

    monkeypatch.setattr(getpass, "getuser", _fail)

    assert actor_from_environment({}) == Actor(kind="human", id=None)


# ---- 2.3: invalid values ----


@pytest.mark.parametrize("value", INVALID_VALUES)
def test_invalid_value_raises_usage_error_naming_the_variable(value: str) -> None:
    """Req 2.3: other text (empty, wrong or upper-case kind, no ``:``, blank id): usage error."""
    with pytest.raises(UsageError) as caught:
        actor_from_environment({TROUPE_ACTOR_VARIABLE: value})

    assert str(caught.value) == _expected_message(value)


def test_invalid_message_quotes_whitespace_visibly() -> None:
    """Req 2.3: an empty or whitespace value is visible in the message as a quoted text."""
    with pytest.raises(UsageError) as caught:
        actor_from_environment({TROUPE_ACTOR_VARIABLE: "human:\t"})

    assert "'human:\\t'" in str(caught.value)


# ---- 2.3, 2.4: every subcommand refuses an invalid value before any state ----


def _run(*argv: str) -> tuple[int, bytes, str]:
    stdout = io.BytesIO()
    stderr = io.StringIO()
    code = cli_main.run(list(argv), stdout, stderr)
    return code, stdout.getvalue(), stderr.getvalue()


SUBCOMMANDS = {
    "get": ["get", "name"],
    "show": ["show"],
    "set": ["set", "name", "other"],
    "history": ["history"],
    "watch": ["watch"],
}


@pytest.mark.parametrize("subcommand", list(SUBCOMMANDS))
@pytest.mark.parametrize("value", ["", "alice", "Human:alice", "agent:  "])
def test_every_subcommand_refuses_an_invalid_value_before_any_state(
    monkeypatch: pytest.MonkeyPatch, make_theatre: MakeTheatre, subcommand: str, value: str
) -> None:
    """Req 2.3, 2.4: exit 2, empty stdout, message names ``TROUPE_ACTOR`` and value; no state."""
    theatre = make_theatre(CONFIG)
    config = theatre / ".troupe" / "troupe.yml"
    before = config.read_bytes()
    monkeypatch.setenv("TROUPE_ACTOR", value)

    code, stdout, stderr = _run("config", *SUBCOMMANDS[subcommand], "--theatre", str(theatre))

    assert code == 2
    assert stdout == b""
    assert stderr == f"{_expected_message(value)}\n"
    assert not (theatre / ".troupe" / "history.sqlite").exists()
    assert sorted(p.name for p in (theatre / ".troupe").iterdir()) == ["troupe.yml"]
    assert config.read_bytes() == before


@pytest.mark.parametrize("value", ["human:alice", "agent:team:planner"])
def test_valid_value_lets_the_command_run(
    monkeypatch: pytest.MonkeyPatch, make_theatre: MakeTheatre, value: str
) -> None:
    """Req 2.1, 2.4: with a valid ``TROUPE_ACTOR`` a command runs normally."""
    theatre = make_theatre(CONFIG)
    monkeypatch.setenv("TROUPE_ACTOR", value)

    code, stdout, stderr = _run("config", "get", "agents.lead", "--theatre", str(theatre))

    assert (code, stdout, stderr) == (0, b"alice\n", "")


def test_unset_variable_lets_the_command_run(
    monkeypatch: pytest.MonkeyPatch, make_theatre: MakeTheatre
) -> None:
    """Req 2.2, 2.4: with ``TROUPE_ACTOR`` unset a command runs normally."""
    theatre = make_theatre(CONFIG)
    monkeypatch.delenv("TROUPE_ACTOR", raising=False)

    code, stdout, stderr = _run("config", "get", "name", "--theatre", str(theatre))

    assert (code, stdout, stderr) == (0, b"demo\n", "")
