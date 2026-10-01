"""ValueParser: the typed, style-free value tree of a VALUE given to ``troupe config set``.

The parsed-value types live here so that the parser and the plain-tree module share them.
"""

from dataclasses import dataclass
from typing import TypeAlias

from troupe.config.scalars import ScalarValue


@dataclass(frozen=True)
class NewScalar:
    """A scalar of VALUE: its typed value and, for non-strings, the plain text it was written as."""

    value: ScalarValue
    spelling: str | None
    """Source text for ``int``/``float``/``bool``/``None`` (e.g. ``0x1F``); ``None`` for strings."""


@dataclass(frozen=True)
class NewMapping:
    """A flow mapping of VALUE; entries keep VALUE order."""

    entries: tuple[tuple[str, "NewValue"], ...]


@dataclass(frozen=True)
class NewSequence:
    """A flow list of VALUE."""

    items: tuple["NewValue", ...]


# A runtime union (not a ``type`` statement) so callers can also use it with ``isinstance``.
NewValue: TypeAlias = NewScalar | NewMapping | NewSequence  # noqa: UP040
"""One node of a parsed VALUE."""
