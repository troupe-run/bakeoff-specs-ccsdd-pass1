"""BlockScalar: literal rendering, chomping, header rewrite, folded paragraphs and wrapping.

A multi-line string written in block context becomes a literal block (9.1): the header is ``|``
followed by the chomping indicator the value's trailing line breaks require (strip ``-`` for none,
clip for exactly one, keep ``+`` for more), and the content lines are the value's lines at a given
indentation, empty lines written as bare line breaks (9.2). A string a literal block cannot hold
(:func:`literal_fits` is false) is written double-quoted by the caller instead (9.7).

The chomping kinds, the required chomping and the check of which characters a literal block can
hold live in the core module :mod:`troupe.config.yaml_style`, shared with ``show``; this module
re-exports :class:`Chomping` and :func:`required_chomping` for the write-back modules that import
them from here, and adds the write-back rule that a first line starting with a space is refused.

Folded paragraphs are re-wrapped greedily by word with :func:`wrap_paragraph` (10.2, 10.5).

An existing literal or folded block changed to another string is rewritten in place by
:func:`rewrite_block` (9.4, 9.5, 10.x): the header keeps its indicator, indentation indicator and
comment, the chomping changes only when required, and the content lines are written at the block's
existing content indentation; a folded block keeps the bytes of its unchanged paragraphs.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Final, NoReturn

from troupe.config.errors import WriteRefusedError
from troupe.config.model import ConfigScalar
from troupe.config.writeback.edits import EditKind, TextEdit
from troupe.config.writeback.layout import FileLayout, ValueSlot
from troupe.config.yaml_style import Chomping, literal_characters_fit, required_chomping

# Re-exported: other write-back modules and their tests import these names from here.
__all__ = [
    "Chomping",
    "chomping_fits",
    "chomping_for",
    "literal_fits",
    "render_literal",
    "required_chomping",
    "rewrite_block",
    "wrap_paragraph",
]


_BREAK_POINT: Final = re.compile(r"(?<=[^ \t]) (?=[^ \t])")
"""A single space with a non-whitespace character on both sides, the only break point (10.5).

A space next to a TAB is never a break point: a folded line starting with a TAB is more-indented
and its preceding break is kept, and a line ending in whitespace would lose the fold."""


def chomping_fits(chomping: Chomping, value: str) -> bool:
    """Whether an existing chomping can still represent the value (9.4).

    Keep represents one or more trailing line breaks, clip exactly one after some content (not
    ``"\\n"`` alone), strip none.
    """
    trailing = len(value) - len(value.rstrip("\n"))
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
    does not accept literally; the core :func:`~troupe.config.yaml_style.literal_characters_fit`),
    a first non-empty line starting with a space (its indentation would
    be taken as the block's).
    """
    if not literal_characters_fit(value):
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


# --- rewrite of existing blocks ------------------------------------------------------------------

_FOLDED: Final = ">"
_CHOMPING_CHARS: Final = "+-"
_MORE_INDENTED: Final = " \t"

Line = tuple[str, str]
"""One output line: its content and the line break that ends it."""


@dataclass(frozen=True)
class _Region:
    """The lines a rewrite replaces: ``[start, end)`` of the text and how to join the new lines."""

    start: int
    end: int
    first_line: int  # first old content line
    last_line: int  # last old content line (before ``first_line`` when there is none)
    line_break: str  # break for new lines
    leading_break: bool  # the header ends the file without a break: start with one
    final_break: bool  # the region ends with a line break (else the new content ends without)


def rewrite_block(
    layout: FileLayout, slot: ValueSlot, scalar: ConfigScalar, value: str
) -> list[TextEdit]:
    """Edits that change the literal or folded block ``scalar`` at ``slot`` to ``value``.

    The header keeps ``|``/``>``, any indentation indicator and the header comment; the chomping
    indicator changes only when the value's trailing line breaks require it (9.4). The content
    region runs from the first content line through the content end, and on through the blank
    lines after it when the new chomping is keep (they would otherwise become content); its new
    lines are written at the existing content indentation (9.5). A folded block is rewritten
    paragraph by paragraph (10.1-10.5). No edits are returned for an unchanged value.

    Raises:
        WriteRefusedError: the change would put a more-indented line into a folded value
            (10.6, 10.7); an unchanged run of the old value's more-indented lines is kept.
        ValueError: the value holds a character no block scalar can hold (callers check
            :func:`literal_fits` first and fall back to double quotes).
    """
    if scalar.value == value:
        return []
    if not literal_characters_fit(value):
        raise ValueError(f"string cannot be written as a block scalar: {value!r}")
    text = layout.file.text
    header = layout.inline_extent(slot)
    indicators = text[header.start : header.end]
    chomping = chomping_for(_chomping_of(indicators), value)
    region = _content_region(layout, slot, scalar, chomping)
    indent = _content_indentation(layout, slot, indicators, region)

    if indicators.startswith(_FOLDED):
        explicit_indent = any(char.isdigit() for char in indicators)
        lines = _folded_lines(layout, slot, region, indent, value, chomping, explicit_indent)
    else:
        _, content = render_literal(value, indent, region.line_break)
        lines = [(line, region.line_break) for line in content.split(region.line_break)[:-1]]
    if lines and not region.final_break:
        lines[-1] = (lines[-1][0], "")
    new_content = "".join(line + brk for line, brk in lines)
    if region.leading_break and new_content:
        new_content = region.line_break + new_content

    edits: list[TextEdit] = []
    new_indicators = _with_chomping(indicators, chomping)
    if new_indicators != indicators:
        edits.append(TextEdit(header.start, header.end, new_indicators, EditKind.INLINE))
    if new_content != text[region.start : region.end]:
        edits.append(TextEdit(region.start, region.end, new_content, EditKind.LINES))
    return edits


def _chomping_of(indicators: str) -> Chomping:
    for chomping in (Chomping.KEEP, Chomping.STRIP):
        if chomping.value in indicators:
            return chomping
    return Chomping.CLIP


def _with_chomping(indicators: str, chomping: Chomping) -> str:
    """The header indicators with the chomping character replaced in place, or appended."""
    for index, char in enumerate(indicators):
        if char in _CHOMPING_CHARS:
            return indicators[:index] + chomping.value + indicators[index + 1 :]
    return indicators + chomping.value


def _content_region(
    layout: FileLayout, slot: ValueSlot, scalar: ConfigScalar, chomping: Chomping
) -> _Region:
    header_line = layout.line_of(slot.node.origin.span.start)
    first = header_line + 1
    content_end = layout.content_end(scalar, slot)
    last = (
        layout.line_of(content_end) if content_end > layout.inline_extent(slot).end else header_line
    )
    end_line = last
    if chomping is Chomping.KEEP:
        # Blank lines right after a keep block are content: replace them too.
        while (
            end_line + 1 < layout.line_count
            and layout.is_blank(end_line + 1)
            and layout.line_break(end_line + 1) != ""
        ):
            end_line += 1
    if end_line >= first:
        start = layout.line_start(first)
        end = (
            layout.line_start(end_line + 1)
            if end_line + 1 < layout.line_count
            else len(layout.file.text)
        )
        return _Region(
            start,
            end,
            first,
            last,
            layout.line_break_at(start),
            leading_break=False,
            final_break=layout.line_break(end_line) != "",
        )
    if first < layout.line_count:
        start = layout.line_start(first)
        return _Region(start, start, first, last, layout.line_break_at(start), False, True)
    end = len(layout.file.text)
    return _Region(end, end, first, last, layout.line_break_at(end), True, False)


def _content_indentation(
    layout: FileLayout, slot: ValueSlot, indicators: str, region: _Region
) -> int:
    """The explicit indicator added to the owner column, else the first content line's indentation
    (the owner column plus the file's step when the block has no content line)."""
    digits = [char for char in indicators if char.isdigit()]
    if digits:
        return slot.owner_column + int(digits[0])
    for line in range(region.first_line, region.last_line + 1):
        if not layout.is_blank(line):
            return layout.indentation(line)
    return slot.owner_column + layout.indent_step()


@dataclass(frozen=True)
class _Unit:
    """An old or new piece of a folded value between empty lines (or between line breaks YAML
    does not fold).

    A paragraph is a run of non-empty lines that are not more-indented; its text is the lines'
    content joined by single spaces. A more-indented unit is a run of consecutive more-indented
    lines (content starting with space or TAB); its text is their content joined by LF, exactly as
    the value holds it, because YAML keeps the line breaks before, inside and after it.
    """

    spaced: bool  # a run of more-indented lines
    text: str
    lines: tuple[int, ...] = ()  # old line numbers (none for a new unit)
    width: int = 0  # longest old content line


def _separator_lines(gap: int, before: _Unit, after: _Unit) -> int:
    """Empty lines between two units for ``gap`` empty value segments between them.

    Between two paragraphs one line break folds away, so ``gap + 1`` empty lines are needed; next
    to a more-indented unit the line break is kept as is and ``gap`` empty lines are needed.
    """
    return gap if before.spaced or after.spaced else gap + 1


def _folded_lines(
    layout: FileLayout,
    slot: ValueSlot,
    region: _Region,
    indent: int,
    value: str,
    chomping: Chomping,
    explicit_indent: bool,
) -> list[Line]:
    """The new content lines of a folded block (10.1-10.7)."""
    units, separators, leading = _old_units(layout, region, indent)
    width = max((unit.width for unit in units), default=None)

    body = value.rstrip("\n")
    trailing = len(value) - len(body)
    segments = body.split("\n") if body else []
    new_units: list[_Unit] = []
    gaps: list[int] = []  # empty segments before each new unit
    empty = 0
    for segment in segments:
        if not segment:
            empty += 1
            continue
        spaced = segment[0] in _MORE_INDENTED
        if spaced and new_units and new_units[-1].spaced and empty == 0:
            new_units[-1] = _Unit(True, new_units[-1].text + "\n" + segment)
            continue
        new_units.append(_Unit(spaced, segment))
        gaps.append(empty)
        empty = 0

    # For each new unit: the old unit whose bytes it keeps, or the width to wrap at.
    kept: list[int | None] = [None] * len(new_units)
    widths: list[int | None] = [width] * len(new_units)
    matcher = SequenceMatcher(
        None,
        [(unit.spaced, unit.text) for unit in units],
        [(unit.spaced, unit.text) for unit in new_units],
        autojunk=False,
    )
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for offset in range(j2 - j1):
                kept[j1 + offset] = i1 + offset
        elif tag == "replace":
            old_paragraphs = [i for i in range(i1, i2) if not units[i].spaced]
            new_paragraphs = [j for j in range(j1, j2) if not new_units[j].spaced]
            for i, j in zip(old_paragraphs, new_paragraphs, strict=False):
                widths[j] = units[i].width

    # Only an unchanged old run of more-indented lines may stay (10.6, 10.7); the block's first
    # line must not be more-indented unless the header fixes the indentation.
    for index, unit in enumerate(new_units):
        if unit.spaced and (kept[index] is None or (index == 0 and not explicit_indent)):
            line = unit.text.split("\n")[0]
            _refuse(layout, slot, f"the line {line!r} would be more-indented in a folded block")

    brk = region.line_break
    pad = " " * indent
    lines: list[Line] = []
    if new_units and units and gaps[0] == len(leading):
        lines.extend(_old_lines(layout, leading, brk))
    elif new_units:
        lines.extend([("", brk)] * gaps[0])
    for index, unit in enumerate(new_units):
        old = kept[index]
        if index > 0:
            previous = kept[index - 1]
            needed = _separator_lines(gaps[index], new_units[index - 1], unit)
            if (
                old is not None
                and previous is not None
                and old == previous + 1
                and len(separators[previous]) == needed
            ):
                lines.extend(_old_lines(layout, separators[previous], brk))
            else:
                lines.extend([("", brk)] * needed)
        if old is not None:
            lines.extend(_old_lines(layout, units[old].lines, brk))
        else:
            lines.extend((pad + line, brk) for line in wrap_paragraph(unit.text, widths[index]))
    if chomping is Chomping.KEEP:
        lines.extend([("", brk)] * (trailing - 1 if new_units else trailing))
    return lines


def _old_units(
    layout: FileLayout, region: _Region, indent: int
) -> tuple[list[_Unit], list[tuple[int, ...]], tuple[int, ...]]:
    """Old units, the empty lines between consecutive ones, and the leading empty lines."""
    units: list[_Unit] = []
    separators: list[tuple[int, ...]] = []
    leading: tuple[int, ...] = ()
    run: list[int] = []
    run_spaced = False
    empties: list[int] = []

    def close() -> None:
        contents = [layout.line_text(n)[indent:] for n in run]
        text = ("\n" if run_spaced else " ").join(contents)
        units.append(_Unit(run_spaced, text, tuple(run), max(len(c) for c in contents)))

    for line in range(region.first_line, region.last_line + 1):
        content = layout.line_text(line)
        spaced = len(content) > indent and content[indent] in _MORE_INDENTED
        if not spaced and content.strip(" \t") == "":
            if run:
                close()
                run = []
            empties.append(line)
            continue
        if run and spaced != run_spaced:
            # YAML keeps the line break between a paragraph line and a more-indented line.
            close()
            run = []
            empties = []
            separators.append(())
        if not run:
            if units and empties:
                separators.append(tuple(empties))
            elif not units:
                leading = tuple(empties)
            empties = []
            run_spaced = spaced
        run.append(line)
    if run:
        close()
    return units, separators, leading


def _old_lines(layout: FileLayout, lines: Sequence[int], line_break: str) -> list[Line]:
    """Old lines with their own bytes and breaks (``line_break`` for a line ending the file)."""
    return [(layout.line_text(n), layout.line_break(n) or line_break) for n in lines]


def _refuse(layout: FileLayout, slot: ValueSlot, problem: str) -> NoReturn:
    raise WriteRefusedError(problem, file=layout.file.shown, path=str(slot.path))
