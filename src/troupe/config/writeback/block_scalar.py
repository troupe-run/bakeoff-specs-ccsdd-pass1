"""BlockScalar: literal rendering, chomping, header rewrite, folded paragraphs and wrapping.

A multi-line string written in block context becomes a literal block (9.1): the header is ``|``
followed by the chomping indicator the value's trailing line breaks require (strip ``-`` for none,
clip for exactly one, keep ``+`` for more), and the content lines are the value's lines at a given
indentation, empty lines written as bare line breaks (9.2). A string a literal block cannot hold
(:func:`literal_fits` is false) is written double-quoted by the caller instead (9.7).

Folded paragraphs are re-wrapped greedily by word with :func:`wrap_paragraph` (10.2, 10.5).
"""

import re
from enum import Enum
from typing import Final


class Chomping(Enum):
    """Block chomping indicator: strip (``-``), clip (nothing) or keep (``+``)."""

    STRIP = "-"
    CLIP = ""
    KEEP = "+"


_UNFIT: Final = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f\u2028\u2029\ud800-\udfff\ufffe\uffff]")
"""Characters a literal block cannot hold (9.7): control characters other than TAB and LF
(C0, DEL, C1 including U+0085), the Unicode line/paragraph separators, lone surrogates and the
non-characters U+FFFE/U+FFFF, none of which YAML accepts literally."""

_BREAK_POINT: Final = re.compile(r"(?<=[^ \t]) (?=[^ \t])")
"""A single space with a non-whitespace character on both sides, the only break point (10.5).

A space next to a TAB is never a break point: a folded line starting with a TAB is more-indented
and its preceding break is kept, and a line ending in whitespace would lose the fold."""


def _trailing_breaks(value: str) -> int:
    return len(value) - len(value.rstrip("\n"))


def required_chomping(value: str) -> Chomping:
    """The chomping a value's trailing line breaks require (9.1).

    Strip for none, clip for exactly one, keep for more. A value made only of line breaks needs
    keep even for a single one (``"\\n"``): clip without a content line reads back as ``""``.
    """
    trailing = _trailing_breaks(value)
    if trailing == 0:
        return Chomping.STRIP
    return Chomping.CLIP if trailing == 1 and trailing < len(value) else Chomping.KEEP


def chomping_fits(chomping: Chomping, value: str) -> bool:
    """Whether an existing chomping can still represent the value (9.4).

    Keep represents one or more trailing line breaks, clip exactly one after some content (not
    ``"\\n"`` alone), strip none.
    """
    trailing = _trailing_breaks(value)
    if chomping is Chomping.KEEP:
        return trailing >= 1
    if chomping is Chomping.CLIP:
        return trailing == 1 and trailing < len(value)
    return trailing == 0


def chomping_for(existing: Chomping, value: str) -> Chomping:
    """The existing chomping when it still fits the value, otherwise the required one (9.4)."""
    return existing if chomping_fits(existing, value) else required_chomping(value)


def literal_fits(value: str) -> bool:
    """Whether a literal block can hold the string exactly (9.7).

    Refused: a control character other than TAB and LF, U+2028/U+2029 (or another character YAML
    does not accept literally), a first non-empty line starting with a space (its indentation would
    be taken as the block's).
    """
    if _UNFIT.search(value):
        return False
    first = next((line for line in value.split("\n") if line), "")
    return not first.startswith(" ")


def render_literal(value: str, indent: int, line_break: str) -> tuple[str, str]:
    """Render a string as a new literal block: ``(header, content lines text)`` (9.1, 9.2).

    The header is ``|`` plus the required chomping indicator. The content is the value without its
    trailing line breaks, split on LF, each non-empty line indented by ``indent`` spaces and empty
    lines written as bare breaks, followed for keep by one bare break per trailing line break beyond
    the first (all of them when the value has no content line). Every break is ``line_break``.

    Raises:
        ValueError: when the string does not fit a literal block (callers check
            :func:`literal_fits` first and fall back to double quotes).
    """
    if not literal_fits(value):
        raise ValueError(f"string cannot be written as a literal block: {value!r}")
    body = value.rstrip("\n")
    trailing = len(value) - len(body)
    chomping = required_chomping(value)
    pad = " " * indent
    lines = [pad + line if line else "" for line in body.split("\n")] if body else []
    extra = trailing - 1 if body else trailing
    if chomping is Chomping.KEEP:
        lines.extend([""] * extra)
    content = "".join(line + line_break for line in lines)
    return "|" + chomping.value, content


def wrap_paragraph(text: str, width: int | None) -> list[str]:
    """Wrap a paragraph greedily by word at ``width`` code points (10.2, 10.5).

    Words are the maximal substrings between single spaces that have a non-whitespace (neither
    space nor TAB) character on both sides; no other position is a break point, so the lines
    written as a folded block fold back to exactly ``text``. Each line takes as many words as
    fit within the width; a word longer than the width takes a line of its own. Without a width
    the paragraph stays on one line.
    """
    if width is None:
        return [text]
    lines: list[str] = []
    current: str | None = None
    for word in _BREAK_POINT.split(text):
        if current is None:
            current = word
        elif len(current) + 1 + len(word) <= width:
            current += " " + word
        else:
            lines.append(current)
            current = word
    lines.append(current if current is not None else "")
    return lines
