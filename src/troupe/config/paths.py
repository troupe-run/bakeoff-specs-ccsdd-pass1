"""Dotted configuration PATH syntax (Requirements 1.5, 5.1, 5.3, 8.3).

A PATH is a sequence of segments separated by :data:`PATH_SEPARATOR`, applied from the top-level
mapping downward. A segment of ASCII digits only addresses a list item by 0-based index when it is
applied to a list. An empty PATH or an empty segment (``a..b``, ``.a``, ``a.``) is a usage error.
"""

from dataclasses import dataclass
from typing import Final

from troupe.config.constants import PATH_SEPARATOR
from troupe.config.errors import UsageError

_DIGITS: Final = frozenset("0123456789")


@dataclass(frozen=True)
class ConfigPath:
    """An immutable dotted path; ``segments`` holds the raw segment texts."""

    segments: tuple[str, ...]

    @classmethod
    def parse(cls, text: str) -> "ConfigPath":
        """Split ``text`` into segments, raising :class:`UsageError` naming it when malformed."""
        if text == "":
            # path=None so the message does not start with an empty path field; the problem
            # names the empty PATH explicitly instead.
            raise UsageError("malformed PATH '': PATH is empty")
        segments = tuple(text.split(PATH_SEPARATOR))
        if any(segment == "" for segment in segments):
            raise UsageError(
                f"malformed PATH {text!r}: segments must not be empty "
                f"(no leading, trailing or doubled {PATH_SEPARATOR!r})",
                path=text,
            )
        return cls(segments)

    def child(self, segment: str | int) -> "ConfigPath":
        """Return this path extended by a mapping key or a list index."""
        return ConfigPath((*self.segments, str(segment)))

    def __str__(self) -> str:
        return PATH_SEPARATOR.join(self.segments)


ROOT: Final[ConfigPath] = ConfigPath(())
"""The empty path addressing the root mapping; used only internally."""


def is_index_segment(segment: str) -> bool:
    """Return whether ``segment`` consists only of ASCII digits (a list index)."""
    return segment != "" and all(char in _DIGITS for char in segment)


def path_has_prefix(path: str, prefix: str) -> bool:
    """Return whether ``path`` equals ``prefix`` or lies below it (textual, for ``history``)."""
    return path == prefix or path.startswith(prefix + PATH_SEPARATOR)
