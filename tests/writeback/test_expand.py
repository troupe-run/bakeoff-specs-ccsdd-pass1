"""Unit tests of the expander: replace one alias by a copy of its anchored node (config-aliases
tasks 3.6, 3.7).

``expand_alias`` (``troupe.config.writeback.expand``) replaces the alias of one alias site in its
file and returns the new text; it loads nothing. Each case loads a real theatre configuration from
disk, takes the alias site from the real guard (``alias_sites`` with ``expand=True`` on a request
from the real TargetResolver), compares the expanded text byte for byte, and loads the expanded
text again (as an override of the file) to prove that the expanded configuration is unchanged.
Every refusal's message, file and PATH are asserted.

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

from pathlib import Path

import pytest

from support import MakeTheatre
from troupe.config.errors import ExitCode, WriteRefusedError
from troupe.config.loader import load_config
from troupe.config.model import ConfigDocument
from troupe.config.paths import ConfigPath
from troupe.config.writeback.aliases import AliasSite, alias_sites
from troupe.config.writeback.compare import node_to_plain, plain_equal
from troupe.config.writeback.expand import expand_alias
from troupe.config.writeback.target import resolve_target
from troupe.config.writeback.value import parse_value


def _config_file(make_theatre: MakeTheatre, text: str) -> Path:
    return make_theatre(text) / ".troupe" / "troupe.yml"


def _site(document: ConfigDocument, path: str) -> AliasSite:
    config_path = ConfigPath.parse(path)
    request = resolve_target(document, config_path, parse_value("x", config_path))
    sites = alias_sites(document, request, expand=True)
    assert len(sites) == 1
    return sites[0]


def _expanded(make_theatre: MakeTheatre, text: str, path: str) -> str:
    """Expand the alias on ``path`` in a theatre whose root file is ``text``; return the new
    text after proving that it loads to the same plain configuration."""
    config_file = _config_file(make_theatre, text)
    document = load_config(config_file)
    texts = expand_alias(document, _site(document, path))
    real_path = config_file.resolve()
    assert list(texts) == [real_path]
    assert config_file.read_bytes() == text.encode()  # nothing written
    reloaded = load_config(config_file, texts)
    assert plain_equal(node_to_plain(reloaded.root), node_to_plain(document.root), ordered=True)
    return texts[real_path]


def _refusal(make_theatre: MakeTheatre, text: str, path: str) -> WriteRefusedError:
    config_file = _config_file(make_theatre, text)
    document = load_config(config_file)
    site = _site(document, path)
    with pytest.raises(WriteRefusedError) as raised:
        expand_alias(document, site)
    assert raised.value.exit_code is ExitCode.WRITE_REFUSED
    assert raised.value.file == config_file
    assert raised.value.path == path
    return raised.value


# --- scalars on the key's line ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("anchored", "copy"),
    [
        ("foo", "foo"),
        ("'it''s here'", "'it''s here'"),
        ('"tab\\there"', '"tab\\there"'),
        ("0x1F", "0x1F"),
        ("~", "~"),
    ],
    ids=["plain", "single-quoted", "double-quoted", "hex-int", "null"],
)
def test_scalar_alias_becomes_its_source_text_and_the_comment(
    make_theatre: MakeTheatre, anchored: str, copy: str
) -> None:
    """Req 7.1, 7.3, 7.4, 7.6: a scalar alias is replaced on the key's line by the anchored
    scalar's source text, two spaces and the comment; nothing else changes."""
    text = f"# head\nname: &n {anchored}  # anchor comment\n\nother: *n\nlast: 1\n"
    expected = (
        f"# head\nname: &n {anchored}  # anchor comment\n\n"
        f"other: {copy}  # expanded from *n by troupe\nlast: 1\n"
    )
    assert _expanded(make_theatre, text, "other") == expected


def test_trailing_spaces_after_the_alias_are_replaced(make_theatre: MakeTheatre) -> None:
    """Req 7.1, 7.6: the spaces after the alias token belong to the replaced text, so the comment
    is exactly two spaces after the value."""
    text = "a:\n  name: &n foo\nb:\n  other:   *n   \n  next: 2\n"
    expected = "a:\n  name: &n foo\nb:\n  other: foo  # expanded from *n by troupe\n  next: 2\n"
    assert _expanded(make_theatre, text, "b.other") == expected


def test_scalar_alias_as_a_list_item_gets_the_comment_on_the_item_line(
    make_theatre: MakeTheatre,
) -> None:
    """Req 7.3, 8.3: an alias in a block list item is replaced on the item's line, after `- `."""
    text = "d: &d foo\nl:\n  - a\n  - *d\n  - b\n"
    expected = "d: &d foo\nl:\n  - a\n  - foo  # expanded from *d by troupe\n  - b\n"
    assert _expanded(make_theatre, text, "l.1") == expected


def test_alias_written_on_the_line_after_its_key_moves_to_the_key_line(
    make_theatre: MakeTheatre,
) -> None:
    """Req 7.1, 7.3: an alias written below its key is replaced from the key's `:`, so the copy and
    the comment are on the key's line."""
    text = "d: &d foo\nother:\n  *d\nlast: 1\n"
    expected = "d: &d foo\nother: foo  # expanded from *d by troupe\nlast: 1\n"
    assert _expanded(make_theatre, text, "other") == expected


def test_empty_null_anchor_leaves_only_the_comment(make_theatre: MakeTheatre) -> None:
    """Req 7.1, 7.3: an anchored empty null has no source text, so only the comment is written,
    two spaces after the `:`."""
    text = "d: &d\nother: *d\n"
    assert _expanded(make_theatre, text, "other") == "d: &d\nother:  # expanded from *d by troupe\n"


@pytest.mark.parametrize(
    ("anchored", "copy"),
    [
        ('"one\n  two"', '"one two"'),
        ("one\n  two", "one two"),
        ("'one\n\n  two'", '"one\\ntwo"'),
    ],
    ids=["double-quoted", "plain", "single-quoted-with-a-line-break"],
)
def test_multi_line_flow_scalar_is_rendered_in_its_style_where_it_can_be(
    make_theatre: MakeTheatre, anchored: str, copy: str
) -> None:
    """Req 7.3, 7.4: a flow scalar written over several lines is rendered on the key's line by the
    scalar rules with its own style preferred (double-quoted when that style cannot hold it)."""
    text = f"d: &d {anchored}\nother: *d\n"
    expected = f"d: &d {anchored}\nother: {copy}  # expanded from *d by troupe\n"
    assert _expanded(make_theatre, text, "other") == expected


# --- empty collections ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("anchored", "copy"),
    [("{}", "{}"), ("{ }", "{}"), ("[]", "[]"), ("[ ]", "[]")],
    ids=["mapping", "spaced-mapping", "list", "spaced-list"],
)
def test_empty_collection_alias_becomes_flow_empty_and_the_comment(
    make_theatre: MakeTheatre, anchored: str, copy: str
) -> None:
    """Req 7.3, 7.6: an alias of an empty mapping or list is written `{}`/`[]` on the key's line,
    followed by two spaces and the comment."""
    text = f"defaults: &d {anchored}\ncoder:\n  settings: *d\n  name: c\n"
    expected = (
        f"defaults: &d {anchored}\ncoder:\n  settings: {copy}  # expanded from *d by troupe\n"
        "  name: c\n"
    )
    assert _expanded(make_theatre, text, "coder.settings") == expected


# --- block scalars ----------------------------------------------------------------------------


def test_literal_scalar_keeps_its_header_and_content_re_indented(
    make_theatre: MakeTheatre,
) -> None:
    """Req 7.3, 7.4, 7.6: a literal block keeps its header on the key's line, followed by the
    comment (the anchor's header comment is not copied), and its content lines follow, shifted by
    the difference of the key columns."""
    text = "a: &d |  # about a\n  one\n    two\n\n  three\nx:\n  s: *d\n  t: 1\n"
    expected = (
        "a: &d |  # about a\n  one\n    two\n\n  three\nx:\n"
        "  s: |  # expanded from *d by troupe\n    one\n      two\n\n    three\n  t: 1\n"
    )
    assert _expanded(make_theatre, text, "x.s") == expected


def test_literal_with_indentation_indicator_and_keep_moves_left(make_theatre: MakeTheatre) -> None:
    """Req 7.3, 7.4: an indentation indicator and keep chomping are kept, and content lines move
    left when the alias's key is left of the anchor's key (trailing blank lines included)."""
    text = "a:\n  k: &d |2+\n     lead\n    x\n\nb: *d\nc: 1\n"
    expected = (
        "a:\n  k: &d |2+\n     lead\n    x\n\n"
        "b: |2+  # expanded from *d by troupe\n   lead\n  x\n\nc: 1\n"
    )
    assert _expanded(make_theatre, text, "b") == expected


def test_folded_scalar_in_a_list_item_keeps_its_lines(make_theatre: MakeTheatre) -> None:
    """Req 7.3, 7.4, 8.3: a folded block copied as a list item keeps its header after `- `, the
    comment, and its content lines (blank lines stay empty)."""
    text = "a: &d >-\n  one\n  two\n\n  three\nl:\n  - *d\n"
    expected = (
        "a: &d >-\n  one\n  two\n\n  three\nl:\n"
        "  - >-  # expanded from *d by troupe\n    one\n    two\n\n    three\n"
    )
    assert _expanded(make_theatre, text, "l.0") == expected


def test_block_scalar_copy_in_a_crlf_file_uses_crlf(make_theatre: MakeTheatre) -> None:
    """Req 7.3, 7.6: the copied content lines end with the file's line break."""
    text = "a: &d |\r\n  one\r\n  two\r\nx:\r\n  s: *d\r\n"
    expected = (
        "a: &d |\r\n  one\r\n  two\r\nx:\r\n"
        "  s: |  # expanded from *d by troupe\r\n    one\r\n    two\r\n"
    )
    assert _expanded(make_theatre, text, "x.s") == expected


def test_block_scalar_copy_at_the_end_of_a_file_without_a_final_break(
    make_theatre: MakeTheatre,
) -> None:
    """Req 7.3, 7.4: at the end of a file without a final line break the content starts on a new
    line, and its last line ends with a break as at the anchor, so the block keeps its value."""
    text = "a: &d |\n  one\n  two\nb: *d"
    expected = "a: &d |\n  one\n  two\nb: |  # expanded from *d by troupe\n  one\n  two\n"
    assert _expanded(make_theatre, text, "b") == expected


# --- refusals ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "path"),
    [
        ("d: &d 1\nx: {s: *d, t: 2}\n", "x.s"),
        ("d: &d 1\nx: [a, *d]\n", "x.1"),
    ],
    ids=["flow-mapping", "flow-list"],
)
def test_alias_inside_a_flow_collection_is_refused(
    make_theatre: MakeTheatre, text: str, path: str
) -> None:
    """Req 8.4: an alias inside a flow collection cannot be expanded (exit 3, file, PATH and the
    alias named)."""
    error = _refusal(make_theatre, text, path)
    assert error.problem == "alias '*d' cannot be expanded inside a flow collection"


@pytest.mark.parametrize(
    "text",
    [
        "d: &d 1\nx: *d  # mine\n",
        "d: &d 1\nx: *d #mine\n",
        "d: &d 1\nx:  # mine\n  *d\n",
        "d: &d 1\nx:\n  # mine\n  *d\n",
    ],
    ids=["after-alias", "one-space", "key-line", "between"],
)
def test_alias_line_with_an_end_of_line_comment_is_refused(
    make_theatre: MakeTheatre, text: str
) -> None:
    """Req 8.5: a comment on the line that would receive the expansion comment (or between the key
    and the alias) is refused (exit 3, file, PATH and the alias named)."""
    error = _refusal(make_theatre, text, "x")
    assert error.problem == "the line of alias '*d' already has an end-of-line comment"


@pytest.mark.parametrize(
    ("text", "key_path"),
    [
        ("b: &b {m: 1}\nd: &d\n  <<: *b\n  k: 2\nx: *d\n", "x.<<"),
        ("d: &d\n  inner:\n    <<: {m: 1}\n    k: 2\nx: *d\n", "x.inner.<<"),
        ("d: &d\n  - <<: {m: 1}\nx: *d\n", "x.0.<<"),
        ("d: &d {<<: {}}\nx: *d\n", "x.<<"),
    ],
    ids=["at-root", "nested", "in-list", "empty-merge"],
)
def test_anchored_node_holding_a_merge_key_is_refused(
    make_theatre: MakeTheatre, text: str, key_path: str
) -> None:
    """Req 9.1: a copy of an anchored node that holds a merge key would write `<<`, so it is
    refused with the merge-key message extended with the alias's name (exit 3, file and PATH)."""
    error = _refusal(make_theatre, text, "x")
    assert error.problem == (
        f"alias '*d' cannot be expanded: value comes from merge key '<<' at {key_path}; "
        "merge keys are never written, and --expand-alias cannot expand them"
    )


def test_merge_key_behind_an_inner_alias_is_not_refused(make_theatre: MakeTheatre) -> None:
    """Req 9.1: an inner alias is copied as an alias, so a merge key in its own anchored node is
    not written by the copy and does not refuse it (here an empty mapping's copy proves it)."""
    text = "b: &b {<<: {m: 1}}\nd: &d []\nx: *d\ny: *b\n"
    expected = "b: &b {<<: {m: 1}}\nd: &d []\nx: []  # expanded from *d by troupe\ny: *b\n"
    assert _expanded(make_theatre, text, "x") == expected


@pytest.mark.parametrize(
    "anchored",
    ["\n  m: 1", "\n  - 1", " {m: 1}", " [1]"],
    ids=["block-mapping", "block-list", "flow-mapping", "flow-list"],
)
def test_non_empty_collection_alias_is_refused_until_block_copies_exist(
    make_theatre: MakeTheatre, anchored: str
) -> None:
    """Req 7.2: until block copies of collections are written (task 3.7), an alias of a non-empty
    collection is refused cleanly (exit 3, file and PATH) instead of written wrongly."""
    error = _refusal(make_theatre, f"d: &d{anchored}\nx: *d\n", "x")
    assert error.problem == "expanding a collection alias is not supported yet"


def test_include_alias_is_refused_until_include_copies_exist(make_theatre: MakeTheatre) -> None:
    """Req 7.4: until includes are copied as written (task 3.7), an alias of an include is refused
    cleanly (exit 3, file and PATH)."""
    root = make_theatre("i: &i file:part.yml\nx: *i\n", files={".troupe/part.yml": "m: 1\n"})
    config_file = root / ".troupe" / "troupe.yml"
    document = load_config(config_file)
    site = _site(document, "x.m")
    with pytest.raises(WriteRefusedError) as raised:
        expand_alias(document, site)
    assert raised.value.exit_code is ExitCode.WRITE_REFUSED
    assert raised.value.file == config_file
    assert raised.value.path == "x"
    assert raised.value.problem == "expanding an alias of an include is not supported yet"
