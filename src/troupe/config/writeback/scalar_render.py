"""ScalarRender: plain/single/double scalar and key rendering for write-back.

A string is written in a *quote style*: plain, single-quoted or double-quoted. The style of an
existing scalar (or of the preceding sibling of a new one) is kept when it can represent the new
text; otherwise the minimal style is used: plain if possible, otherwise single-quoted, otherwise
double-quoted, which can represent every string.

The write-neutral rules (quoting contexts, quote styles, representability with its plain probes,
the style choice, quoting and escaping) live in the core module :mod:`troupe.config.yaml_style`,
shared with ``show``; this module re-exports the names other write-back modules import from here
and adds the write refusal: a lone surrogate cannot be written as UTF-8 (exit 3).

Existing styles are taken from the model (``MappingEntry.key_style``, ``ConfigScalar.style``) and
narrowed with :func:`quote_style_of`, never from span text. Strings starting with ``file:`` get no
special treatment here (2.7).
"""

import re
from typing import Final

from troupe.config.errors import WriteRefusedError
from troupe.config.paths import ConfigPath
from troupe.config.writeback.value import NewScalar
from troupe.config.yaml_style import (
    Context,
    QuoteStyle,
    can_represent,
    choose_style,
    quote,
    quote_style_of,
)

# Re-exported: other write-back modules and their tests import these names from here.
__all__ = [
    "Context",
    "QuoteStyle",
    "can_represent",
    "choose_style",
    "quote_style_of",
    "render_key",
    "render_scalar",
]

_KEY_CONTEXTS: Final = frozenset({Context.BLOCK_KEY, Context.FLOW_KEY})
_SURROGATE: Final = re.compile("[\ud800-\udfff]")


def render_scalar(
    value: NewScalar, context: Context, preferred: QuoteStyle | None, path: ConfigPath
) -> str:
    """Return the text of ``value`` written at ``context``.

    Non-strings are written plain in the spelling given in VALUE (6.3; an empty spelling of null
    gives an empty text). Strings are written in :func:`choose_style`'s style; a string with a line
    break is double-quoted here (callers in block context use a literal block instead).

    Raises :class:`WriteRefusedError` naming ``path`` when the string holds a lone surrogate (from
    a ``\\ud800`` escape in VALUE), which cannot be written as UTF-8.
    """
    if not isinstance(value.value, str):
        if value.spelling is None:
            raise ValueError(f"non-string scalar {value.value!r} has no VALUE spelling")
        return value.spelling
    _refuse_surrogates(value.value, path)
    return quote(value.value, choose_style(value.value, context, preferred))


def render_key(
    key: str, context: Context, preferred: QuoteStyle | None, *, path: ConfigPath | None = None
) -> str:
    """Return the text of mapping key ``key`` written at ``context`` (a block or flow key, 7.1).

    Raises :class:`WriteRefusedError` (naming ``path`` when given) for a lone surrogate.
    """
    if context not in _KEY_CONTEXTS:
        raise ValueError(f"{context} is not a key context")
    _refuse_surrogates(key, path)
    return quote(key, choose_style(key, context, preferred))


def _refuse_surrogates(text: str, path: ConfigPath | None) -> None:
    match = _SURROGATE.search(text)
    if match is not None:
        raise WriteRefusedError(
            f"the string {text!r} contains the lone surrogate U+{ord(match.group()):04X}, "
            "which cannot be written as UTF-8",
            path=None if path is None else str(path),
        )
