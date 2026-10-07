"""Include recognition: the one place that says whether a string would be an include.

A string value starting with :data:`~troupe.config.constants.INCLUDE_PREFIX` or one of
:data:`~troupe.config.constants.REMOTE_INCLUDE_PREFIXES` (case-sensitive) is an include string.
Whether such a string actually is an include is the loader's decision (a ``!literal`` tag or a
schema ``format: uri`` makes it a plain string); these functions only say it would be one if
written plain.
"""

from pathlib import PurePosixPath
from urllib.parse import urlsplit

from troupe.config.constants import INCLUDE_PREFIX, REMOTE_INCLUDE_PREFIXES, YAML_INCLUDE_SUFFIXES
from troupe.config.scalars import ScalarValue

_INCLUDE_PREFIXES = (INCLUDE_PREFIX, *REMOTE_INCLUDE_PREFIXES)


def is_include_string(value: ScalarValue) -> bool:
    """Whether ``value`` is a string starting with ``file:``, ``https://`` or ``http://``."""
    return isinstance(value, str) and value.startswith(_INCLUDE_PREFIXES)


def is_remote_include(text: str) -> bool:
    """Whether the include string ``text`` is remote: it starts with ``https://`` or ``http://``."""
    return text.startswith(REMOTE_INCLUDE_PREFIXES)


def include_is_yaml(text: str) -> bool:
    """Whether the include string ``text`` targets YAML content rather than text.

    A remote include is YAML when its URL path, without query and fragment, ends in a YAML suffix.
    A ``file:`` include is YAML when its target's name does, as before step 7. Suffixes are
    case-sensitive.
    """
    if is_remote_include(text):
        return urlsplit(text).path.endswith(YAML_INCLUDE_SUFFIXES)
    return PurePosixPath(text.removeprefix(INCLUDE_PREFIX)).name.endswith(YAML_INCLUDE_SUFFIXES)
