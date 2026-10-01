"""Unit tests of the BlockScalar (``troupe.config.writeback.block_scalar``) parts owned by task 2.5:
literal-block fit, chomping, literal rendering and greedy word wrapping.

Every literal rendering is re-loaded with the real loader (``load_config``) as the value of a key,
followed by a sibling key and at the end of the file, to prove it reads back as the intended string.

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

import re
from pathlib import Path

import pytest

from troupe.config.loader import load_config
from troupe.config.model import ConfigMapping, ConfigScalar
from troupe.config.writeback.block_scalar import (
    Chomping,
    chomping_fits,
    chomping_for,
    literal_fits,
    render_literal,
    required_chomping,
    wrap_paragraph,
)


def _load_value(tmp_path: Path, text: str, name: str) -> object:
    target = tmp_path / f"{name}.yml"
    target.write_bytes(text.encode("utf-8"))
    node = load_config(target).root.entries[0].value
    assert isinstance(node, ConfigScalar)
    return node.value


def _assert_reloads(tmp_path: Path, value: str, indent: int = 2, line_break: str = "\n") -> None:
    header, content = render_literal(value, indent, line_break)
    head = f"k: {header}{line_break}"
    tail = f"z: 1{line_break}"
    assert _load_value(tmp_path, head + content + tail, "followed") == value
    assert _load_value(tmp_path, head + content, "at_end") == value


# --- chomping ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("one", Chomping.STRIP),
        ("one\ntwo", Chomping.STRIP),
        ("", Chomping.STRIP),
        ("one\n", Chomping.CLIP),
        ("one\ntwo\n", Chomping.CLIP),
        ("one\n\n", Chomping.KEEP),
        ("one\n\n\n", Chomping.KEEP),
        ("\n\n", Chomping.KEEP),
        ("\n", Chomping.KEEP),
    ],
)
def test_required_chomping_follows_trailing_line_breaks(value: str, expected: Chomping) -> None:
    """Req 9.1: strip without a trailing line break, clip with exactly one, keep with more
    (and keep for a lone line break, which clip cannot hold without a content line)."""
    assert required_chomping(value) is expected


def test_chomping_indicator_characters() -> None:
    """Req 9.1: the chomping indicators are `-`, nothing and `+`."""
    assert (Chomping.STRIP.value, Chomping.CLIP.value, Chomping.KEEP.value) == ("-", "", "+")


@pytest.mark.parametrize(
    ("chomping", "value", "fits"),
    [
        (Chomping.STRIP, "a", True),
        (Chomping.STRIP, "a\n", False),
        (Chomping.CLIP, "a\n", True),
        (Chomping.CLIP, "a", False),
        (Chomping.CLIP, "a\n\n", False),
        (Chomping.CLIP, "\n", False),
        (Chomping.KEEP, "\n", True),
        (Chomping.KEEP, "a\n", True),
        (Chomping.KEEP, "a\n\n\n", True),
        (Chomping.KEEP, "a", False),
    ],
)
def test_existing_chomping_fits_only_matching_trailing_breaks(
    chomping: Chomping, value: str, fits: bool
) -> None:
    """Req 9.4: keep holds one or more trailing breaks, clip exactly one, strip none."""
    assert chomping_fits(chomping, value) is fits


@pytest.mark.parametrize(
    ("existing", "value", "expected"),
    [
        (Chomping.KEEP, "a\n", Chomping.KEEP),
        (Chomping.KEEP, "a", Chomping.STRIP),
        (Chomping.CLIP, "a\n\n", Chomping.KEEP),
        (Chomping.CLIP, "a", Chomping.STRIP),
        (Chomping.STRIP, "a\n", Chomping.CLIP),
        (Chomping.STRIP, "a", Chomping.STRIP),
        (Chomping.CLIP, "\n", Chomping.KEEP),
    ],
)
def test_existing_chomping_changes_only_when_required(
    existing: Chomping, value: str, expected: Chomping
) -> None:
    """Req 9.4: an existing chomping is kept when it still fits, otherwise the required one."""
    assert chomping_for(existing, value) is expected


# --- literal fit ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "one\ntwo\n",
        "one\ntwo",
        "one\n\n",
        "a\tb\n\tc\n",
        "trailing  \nspaces \n",
        "\none\n",
        "a\n   \nb\n",
        "# not a comment\n",
        "caf\u00e9 \U0001f600\n",
        "a\ufeffb\n",
        "\n\n",
        "\n",
    ],
)
def test_literal_fits_and_reloads(tmp_path: Path, value: str) -> None:
    """Req 9.1, 9.2, 9.7: strings a literal block can hold are accepted and read back unchanged."""
    assert literal_fits(value)
    _assert_reloads(tmp_path, value)


@pytest.mark.parametrize(
    "value",
    [
        "a\x00b\n",
        "a\rb\n",
        "a\r\nb\n",
        "a\x1bb\n",
        "a\x7fb\n",
        "a\x85b\n",
        "a\u2028b\n",
        "a\u2029b\n",
        "a\ufffeb\n",
        "a\uffffb\n",
        "a\ud800b\n",
        " x\ny\n",
        "  x\ny\n",
        "\n  x\ny\n",
        "  \nx\n",
    ],
)
def test_literal_refuses_unrepresentable_strings(value: str) -> None:
    """Req 9.7: control characters (other than TAB/LF), U+2028/U+2029, non-printable characters,
    and a first non-empty line starting with a space do not fit."""
    assert not literal_fits(value)


def test_render_literal_rejects_strings_that_do_not_fit() -> None:
    """Req 9.7: rendering a string that does not fit is a programming error, never silent output."""
    with pytest.raises(ValueError, match="literal"):
        render_literal(" x\ny\n", 2, "\n")


# --- literal rendering ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "header", "content"),
    [
        ("one\ntwo\n", "|", "  one\n  two\n"),
        ("one\ntwo", "|-", "  one\n  two\n"),
        ("one\n\n", "|+", "  one\n\n"),
        ("one\n\n\n", "|+", "  one\n\n\n"),
        ("one\n\ntwo\n", "|", "  one\n\n  two\n"),
        ("\none\n", "|", "\n  one\n"),
        ("\n\n", "|+", "\n\n"),
        ("\n", "|+", "\n"),
        ("", "|-", ""),
    ],
)
def test_render_literal_header_and_content(
    tmp_path: Path, value: str, header: str, content: str
) -> None:
    """Req 9.1, 9.2: header by chomping; content indented, empty lines as bare breaks, keep adds
    one bare break per trailing line break beyond the first."""
    assert render_literal(value, 2, "\n") == (header, content)
    _assert_reloads(tmp_path, value)


def test_render_literal_at_deeper_indentation(tmp_path: Path) -> None:
    """Req 9.2: content lines start at the given indentation."""
    assert render_literal("a\n\nb\n", 4, "\n") == ("|", "    a\n\n    b\n")
    text = "m:\n  k: |\n" + render_literal("a\n\nb\n", 4, "\n")[1] + "n: 1\n"
    target = tmp_path / "t.yml"
    target.write_bytes(text.encode())
    inner = load_config(target).root.entries[0].value
    assert isinstance(inner, ConfigMapping)
    leaf = inner.entries[0].value
    assert isinstance(leaf, ConfigScalar)
    assert leaf.value == "a\n\nb\n"


def test_render_literal_uses_crlf_line_breaks(tmp_path: Path) -> None:
    """Req 9.2 (13.x line breaks): every break of the content, bare ones included, is CRLF."""
    assert render_literal("one\n\ntwo\n\n", 2, "\r\n") == ("|+", "  one\r\n\r\n  two\r\n\r\n")
    _assert_reloads(tmp_path, "one\n\ntwo\n\n", line_break="\r\n")
    _assert_reloads(tmp_path, "one\ntwo", line_break="\r\n")


# --- wrapping ---------------------------------------------------------------------------------


def test_wrap_greedy_example_from_contract() -> None:
    """Req 10.2: greedy fill at the width, three lines for the contract example."""
    assert wrap_paragraph("Second para changed now and longer.", 17) == [
        "Second para",
        "changed now and",
        "longer.",
    ]


def test_wrap_places_overlong_word_on_its_own_line() -> None:
    """Req 10.2: a single word longer than the width takes a line of its own."""
    assert wrap_paragraph("aaaaaaaa bb", 5) == ["aaaaaaaa", "bb"]
    assert wrap_paragraph("bb aaaaaaaa cc dd", 5) == ["bb", "aaaaaaaa", "cc dd"]


def test_wrap_line_exactly_at_width_is_kept() -> None:
    """Req 10.2: a line whose length equals the width is not broken."""
    assert wrap_paragraph("ab cd ef", 5) == ["ab cd", "ef"]


def test_wrap_breaks_only_at_single_spaces() -> None:
    """Req 10.5: double spaces and edge spaces are never break points."""
    assert wrap_paragraph("a  b c", 1) == ["a  b", "c"]
    assert wrap_paragraph("a b ", 1) == ["a", "b "]
    assert wrap_paragraph("a\tb c", 1) == ["a\tb", "c"]
    assert wrap_paragraph("a \tb c", 1) == ["a \tb", "c"]
    assert wrap_paragraph("a\t b c", 1) == ["a\t b", "c"]


def test_wrap_counts_code_points() -> None:
    """Req 10.5: widths are measured in Unicode code points."""
    text = "\u00e9\u00e9\u00e9 \u00e9\u00e9"
    assert len(text.encode("utf-8")) > 6
    assert wrap_paragraph(text, 6) == [text]
    assert wrap_paragraph(text, 5) == ["\u00e9\u00e9\u00e9", "\u00e9\u00e9"]


def test_wrap_without_width_keeps_one_line() -> None:
    """Req 10.2: without a width there is no wrapping."""
    text = "a very long paragraph that would otherwise be wrapped"
    assert wrap_paragraph(text, None) == [text]


def test_wrapped_lines_rejoin_to_the_paragraph() -> None:
    """Req 10.2, 10.5: wrapping only turns single spaces into breaks (folding restores them)."""
    text = "lorem ipsum  dolor sit amet, consectetur adipiscing elit sed do"
    for width in range(1, 30):
        lines = wrap_paragraph(text, width)
        assert " ".join(lines) == text
        for line in lines:
            assert len(line) <= width or len(_words(line)) == 1


def _words(line: str) -> list[str]:
    """Split at single spaces with non-space characters on both sides."""
    return re.split(r"(?<=[^ ]) (?=[^ ])", line)


@pytest.mark.parametrize(
    "text",
    [
        "Second para changed now and longer.",
        "lorem ipsum  dolor sit amet,   consectetur adipiscing elit",
        "alpha \tbeta gamma delta \t epsilon zeta",
        "alpha\t beta gamma\t\tdelta epsilon",
        "a \tb c",
        "supercalifragilistic is a word and so is antidisestablishmentarianism",
        "x",
    ],
)
@pytest.mark.parametrize("width", [1, 3, 5, 8, 17, None])
def test_wrapped_paragraph_reloads_from_a_folded_block(
    tmp_path: Path, text: str, width: int | None
) -> None:
    """Req 10.2, 10.5: wrapped lines written as a `>-` block fold back to the paragraph."""
    for indent in (2, 4):
        pad = " " * indent
        lines = wrap_paragraph(text, width)
        body = "".join(f"{pad}{line}\n" for line in lines)
        prefix = "k: >-\n" if indent == 2 else "m:\n  k: >-\n"
        document = prefix + body + "z: 1\n"
        target = tmp_path / f"folded{indent}.yml"
        target.write_bytes(document.encode("utf-8"))
        node = load_config(target).root.entries[0].value
        if isinstance(node, ConfigMapping):
            node = node.entries[0].value
        assert isinstance(node, ConfigScalar)
        assert node.value == text, document
