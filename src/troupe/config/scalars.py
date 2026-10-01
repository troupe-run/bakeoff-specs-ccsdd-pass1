"""YAML 1.2 core-schema typing of plain scalars.

This module is the single home of core-schema typing (YAML 1.2.2, section 10.3.2). The loader
types every plain scalar it reads with :func:`resolve_plain_scalar`, and config-set-writeback types
a written plain VALUE with the same function, so both agree exactly.

ruamel.yaml's own resolver is not used because it deviates from the 1.2 core schema (for example it
reads ``1_0`` as an integer and ``2001-12-14`` as a timestamp; both are strings under the core
schema).

Quoted and block scalars are never passed here; they are always strings.
"""

import math
import re
from typing import Final, TypeAlias

# A runtime union (not a ``type`` statement) so callers can also use it with ``isinstance``.
ScalarValue: TypeAlias = str | int | float | bool | None  # noqa: UP040
"""A typed configuration value: always a plain built-in, never a library subclass."""

# Explicit ASCII character classes are used instead of ``\d`` so that non-ASCII digits stay strings.
_NULL: Final = re.compile(r"null|Null|NULL|~|")
_TRUE: Final = re.compile(r"true|True|TRUE")
_FALSE: Final = re.compile(r"false|False|FALSE")
_INT_DECIMAL: Final = re.compile(r"[-+]?[0-9]+")
_INT_OCTAL: Final = re.compile(r"0o([0-7]+)")
_INT_HEX: Final = re.compile(r"0x([0-9a-fA-F]+)")
_FLOAT: Final = re.compile(r"[-+]?(\.[0-9]+|[0-9]+(\.[0-9]*)?)([eE][-+]?[0-9]+)?")
_INFINITY: Final = re.compile(r"([-+]?)(\.inf|\.Inf|\.INF)")
_NOT_A_NUMBER: Final = re.compile(r"\.nan|\.NaN|\.NAN")


def resolve_plain_scalar(text: str) -> ScalarValue:
    """Return the value of the plain scalar ``text`` under the YAML 1.2 core schema.

    Null, boolean, integer (decimal, ``0o`` octal, ``0x`` hexadecimal) and float (including the
    infinities and NaN) forms are converted to ``None``, ``bool``, ``int`` and ``float``; any other
    text is returned unchanged as a string.
    """
    if _NULL.fullmatch(text):
        return None
    if _TRUE.fullmatch(text):
        return True
    if _FALSE.fullmatch(text):
        return False
    if _INT_DECIMAL.fullmatch(text):
        return int(text, 10)
    if match := _INT_OCTAL.fullmatch(text):
        return int(match.group(1), 8)
    if match := _INT_HEX.fullmatch(text):
        return int(match.group(1), 16)
    if _FLOAT.fullmatch(text):
        return float(text)
    if match := _INFINITY.fullmatch(text):
        return -math.inf if match.group(1) == "-" else math.inf
    if _NOT_A_NUMBER.fullmatch(text):
        return math.nan
    return text
