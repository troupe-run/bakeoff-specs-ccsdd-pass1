"""The command's actor, declared with the ``TROUPE_ACTOR`` environment variable.

config-lineage 2.1-2.3.

``human:<id>`` or ``agent:<id>`` (split at the first ``:``; the kind exactly ``human`` or
``agent``; the id every character after the ``:``, kept verbatim, not empty and not only
whitespace) declares the actor. When the variable is not set, the actor is a human with the login
name, or with a null id when the login name cannot be determined. Any other value, including an
empty one, is a usage error naming the variable, quoting the value and stating the accepted forms.

The variable is read here only: ``run`` calls :func:`actor_from_environment` once per command,
before any handler runs (2.4); the core never reads the environment.
"""

import getpass
from collections.abc import Mapping
from typing import Final

from troupe.config.errors import UsageError
from troupe.config.lineage import Actor

TROUPE_ACTOR_VARIABLE: Final = "TROUPE_ACTOR"


def actor_from_environment(environ: Mapping[str, str]) -> Actor:
    """The command's actor (2.1, 2.2).

    Raises ``UsageError`` naming ``TROUPE_ACTOR`` on an invalid value (2.3).
    """
    value = environ.get(TROUPE_ACTOR_VARIABLE)
    if value is None:
        return Actor(kind="human", id=_login_name())
    kind, separator, actor_id = value.partition(":")
    if separator and actor_id.strip() != "":
        if kind == "human":
            return Actor(kind="human", id=actor_id)
        if kind == "agent":
            return Actor(kind="agent", id=actor_id)
    raise UsageError(
        f"{TROUPE_ACTOR_VARIABLE}: invalid value {value!r}: expected human:<id> or agent:<id>"
    )


def _login_name() -> str | None:
    """The login name, or None when it cannot be determined (2.2)."""
    try:
        return getpass.getuser()
    except (OSError, KeyError):
        return None
