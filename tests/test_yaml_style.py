"""Unit tests of YamlStyle (``troupe.config.yaml_style``): the write-neutral scalar presentation
rules shared by write-back (``set``) and ``show``.

The rules moved here from ``troupe.config.writeback.scalar_render`` and
``troupe.config.writeback.block_scalar`` (design Decision 1). These tests prove the write-back
names are the very objects defined here (one home for each rule), that ``quote`` writes the plain,
single- and double-quoted forms with the same escapes as before, and which characters a literal
block can hold. Quoted renderings are re-loaded with the real loader (``load_config``) to prove
they read back as the original string.

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

import ast
from pathlib import Path
from typing import Final

import pytest

from troupe.config import yaml_style
from troupe.config.loader import load_config
from troupe.config.model import ConfigScalar, ScalarStyle
from troupe.config.writeback import block_scalar, scalar_render
from troupe.config.yaml_style import QuoteStyle, literal_characters_fit, quote

PLAIN: Final = ScalarStyle.PLAIN
SINGLE: Final = ScalarStyle.SINGLE_QUOTED
DOUBLE: Final = ScalarStyle.DOUBLE_QUOTED


def _reloaded(tmp_path: Path, rendered: str) -> str:
    """The string read back from ``k: <rendered>`` in a real file."""
    target = tmp_path / "troupe.yml"
    target.write_bytes(f"k: {rendered}\n".encode())
    value = load_config(target).root.entries[0].value
    assert isinstance(value, ConfigScalar)
    assert isinstance(value.value, str)
    return value.value


# --- one home for each rule ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "name", ["Context", "QuoteStyle", "quote_style_of", "can_represent", "choose_style"]
)
def test_scalar_render_names_are_the_core_objects(name: str) -> None:
    """Req 4.3 (design Decision 1): write-back's quoting rules are the very objects defined in the
    core module, so ``set`` and ``show`` decide quoting in one place."""
    assert getattr(scalar_render, name) is getattr(yaml_style, name)


@pytest.mark.parametrize("name", ["Chomping", "required_chomping"])
def test_block_scalar_names_are_the_core_objects(name: str) -> None:
    """Req 4.3 (design Decision 1): write-back's chomping rules are the very objects defined in
    the core module."""
    assert getattr(block_scalar, name) is getattr(yaml_style, name)


def test_core_module_imports_neither_writeback_nor_cli() -> None:
    """Req 4.3 (design Decision 1): the shared rules live in core, which depends on neither the
    write-back package nor the CLI."""
    tree = ast.parse(Path(yaml_style.__file__).read_text(encoding="utf-8"))
    imported = [
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    ] + [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module]
    assert imported
    assert not [m for m in imported if m.startswith(("troupe.config.writeback", "troupe.cli"))]


# --- quote ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "style", "expected"),
    [
        ("hello", PLAIN, "hello"),
        ("café", PLAIN, "café"),
        ("true", SINGLE, "'true'"),
        ("it's", SINGLE, "'it''s'"),
        ("", SINGLE, "''"),
        ('say "hi"', DOUBLE, '"say \\"hi\\""'),
        ("back\\slash", DOUBLE, '"back\\\\slash"'),
        ("café", DOUBLE, '"café"'),
    ],
)
def test_quote_writes_plain_single_and_double_forms(
    tmp_path: Path, text: str, style: QuoteStyle, expected: str
) -> None:
    """Req 4.3, 4.6: ``quote`` writes plain text as is, doubles single quotes inside single quotes,
    escapes ``\\`` and ``"`` inside double quotes, keeps non-ASCII printable characters unescaped,
    and every form reads back as the original string."""
    rendered = quote(text, style)
    assert rendered == expected
    assert _reloaded(tmp_path, rendered) == text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("a\x00b", '"a\\0b"'),
        ("a\tb", '"a\\tb"'),
        ("a\nb", '"a\\nb"'),
        ("a\rb", '"a\\rb"'),
        ("a\x1bb", '"a\\eb"'),
        ("a\x85b", '"a\\Nb"'),
        ("a\x01b", '"a\\x01b"'),
        ("a\x7fb", '"a\\x7Fb"'),
        ("a\ufffeb", '"a\\uFFFEb"'),
    ],
)
def test_quote_double_escapes_non_printable_characters(
    tmp_path: Path, text: str, expected: str
) -> None:
    """Req 4.6, 4.7: double quotes escape control and non-printable characters (named escape where
    one exists, otherwise upper-case ``\\xHH``/``\\uHHHH``) on one line, reading back exactly."""
    rendered = quote(text, DOUBLE)
    assert rendered == expected
    assert "\n" not in rendered
    assert _reloaded(tmp_path, rendered) == text


def test_quote_double_escapes_a_lone_surrogate_without_refusing() -> None:
    """Req 4.6: the core quoting never refuses; a lone surrogate is escaped (the write refusal
    stays in write-back)."""
    assert quote("a\ud800b", DOUBLE) == '"a\\uD800b"'


# --- literal_characters_fit ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "text", ["a\rb\n", "a\x01b", "a\u2028b", "a\u2029b", "a\x85b", "a\ud800b", "a\uffffb"]
)
def test_literal_characters_fit_rejects_characters_a_literal_block_cannot_hold(text: str) -> None:
    """Req 4.3: a carriage return, other control characters, U+2028/U+2029, lone surrogates and
    the non-characters cannot be held literally by a literal block."""
    assert not literal_characters_fit(text)


@pytest.mark.parametrize("text", ["a\tb\n", "a\nb\n\n", "café\n", "  indented\n", ""])
def test_literal_characters_fit_accepts_tabs_line_feeds_and_non_ascii(text: str) -> None:
    """Req 4.3, 4.6: TAB, LF and non-ASCII printable characters fit a literal block; the
    first-line-starts-with-a-space rule is not part of the character check."""
    assert literal_characters_fit(text)
