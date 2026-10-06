"""Unit tests of the expander: replace one alias by a copy of its anchored node (config-aliases
tasks 3.6, 3.7).

``expand_alias`` (``troupe.config.writeback.expand``) replaces the alias of one alias site in its
file and returns the new text; it loads nothing. Each case loads a real theatre configuration from
disk, takes the alias site from the real guard (``alias_sites`` with ``expand=True`` on a request
from the real TargetResolver), compares the expanded text byte for byte, and loads the expanded
text again (as an override of the file) to prove that the expanded configuration is unchanged
and that every other alias place is still an alias of the same anchor.
Every refusal's message, file and PATH are asserted.

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

from pathlib import Path

import pytest

from support import MakeTheatre
from troupe.config.errors import ExitCode, WriteRefusedError
from troupe.config.loader import load_config
from troupe.config.model import ConfigDocument, ConfigMapping, ConfigNode, ConfigSequence
from troupe.config.paths import ROOT, ConfigPath
from troupe.config.writeback.aliases import AliasSite, alias_sites
from troupe.config.writeback.compare import node_to_plain, plain_equal
from troupe.config.writeback.expand import expand_alias
from troupe.config.writeback.target import resolve_target
from troupe.config.writeback.value import parse_value


def _config_file(make_theatre: MakeTheatre, text: str, files: dict[str, str] | None = None) -> Path:
    return make_theatre(text, files=files) / ".troupe" / "troupe.yml"


def _site(document: ConfigDocument, path: str) -> AliasSite:
    config_path = ConfigPath.parse(path)
    request = resolve_target(document, config_path, parse_value("x", config_path))
    sites = alias_sites(document, request, expand=True)
    assert len(sites) == 1
    return sites[0]


def _alias_names(node: ConfigNode, path: ConfigPath = ROOT) -> dict[str, str]:
    """``{path: alias name}`` of every alias place in the expanded configuration below ``node``."""
    found = {} if node.alias is None else {str(path): node.alias.name}
    if isinstance(node, ConfigMapping):
        for entry in node.entries:
            found.update(_alias_names(entry.value, path.child(entry.key)))
    elif isinstance(node, ConfigSequence):
        for index, item in enumerate(node.items):
            found.update(_alias_names(item, path.child(index)))
    return found


def _expanded(
    make_theatre: MakeTheatre, text: str, path: str, files: dict[str, str] | None = None
) -> str:
    """Expand the alias on ``path`` in a theatre whose root file is ``text``; return the new
    text after proving that it loads to the same plain configuration, in which every other alias
    place (including the inner aliases of the copy) is still an alias of the same anchor."""
    config_file = _config_file(make_theatre, text, files)
    document = load_config(config_file)
    site = _site(document, path)
    texts = expand_alias(document, site)
    real_path = config_file.resolve()
    assert list(texts) == [real_path]
    assert config_file.read_bytes() == text.encode()  # nothing written
    reloaded = load_config(config_file, texts)
    assert plain_equal(node_to_plain(reloaded.root), node_to_plain(document.root), ordered=True)
    aliases = _alias_names(document.root)
    del aliases[str(site.path)]
    assert _alias_names(reloaded.root) == aliases
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


# --- collection copies ------------------------------------------------------------------------


def test_issue_example_expands_coder_settings_only(make_theatre: MakeTheatre) -> None:
    """Req 7.1, 7.2, 7.6: the tracker issue's file: `coder.settings` becomes a block copy under
    the key's line with the comment, and `reviewer.settings` stays an alias of `&d`."""
    text = "defaults: &d\n  model: opus\ncoder:\n  settings: *d\nreviewer:\n  settings: *d\n"
    expected = (
        "defaults: &d\n  model: opus\ncoder:\n  settings:  # expanded from *d by troupe\n"
        "    model: opus\nreviewer:\n  settings: *d\n"
    )
    assert _expanded(make_theatre, text, "coder.settings.model") == expected


def test_mapping_copy_is_block_style_with_nested_and_empty_collections(
    make_theatre: MakeTheatre,
) -> None:
    """Req 7.2, 7.4, 7.6: a mapping is copied in block style one step right of the key; nested
    lists use the file's dash offset, nested mappings the step, empty ones are `{}`/`[]`, scalars
    keep their source text and no comment from inside the anchored node is copied."""
    text = (
        "defaults: &d  # shared\n"
        "  model: opus  # the model\n"
        "  # tools follow\n"
        "  tools:\n"
        "    - read\n"
        "    - 'write'\n"
        "  extra: {}\n"
        "  none: [ ]\n"
        "  empty:\n"
        "  opts:\n"
        "    a: 0x1F\n"
        '    b: "two"\n'
        "coder:\n"
        "  settings: *d\n"
        "  name: c\n"
        "reviewer:\n"
        "  settings: *d\n"
    )
    expected = (
        "defaults: &d  # shared\n"
        "  model: opus  # the model\n"
        "  # tools follow\n"
        "  tools:\n"
        "    - read\n"
        "    - 'write'\n"
        "  extra: {}\n"
        "  none: [ ]\n"
        "  empty:\n"
        "  opts:\n"
        "    a: 0x1F\n"
        '    b: "two"\n'
        "coder:\n"
        "  settings:  # expanded from *d by troupe\n"
        "    model: opus\n"
        "    tools:\n"
        "      - read\n"
        "      - 'write'\n"
        "    extra: {}\n"
        "    none: []\n"
        "    empty:\n"
        "    opts:\n"
        "      a: 0x1F\n"
        '      b: "two"\n'
        "  name: c\n"
        "reviewer:\n"
        "  settings: *d\n"
    )
    assert _expanded(make_theatre, text, "coder.settings.model") == expected


def test_list_copy_uses_the_dash_offset_and_compact_items(make_theatre: MakeTheatre) -> None:
    """Req 7.2, 7.4: a list is copied with its `-` at the file's dash offset from the key; a
    mapping or list item is compact (its first entry after `- `, the others two columns right of
    the `-`)."""
    text = "d: &d\n  - a\n  - k: 1\n    j: [x]\n  - - x\n    - y\nx: *d\n"
    expected = (
        "d: &d\n  - a\n  - k: 1\n    j: [x]\n  - - x\n    - y\n"
        "x:  # expanded from *d by troupe\n  - a\n  - k: 1\n    j:\n      - x\n"
        "  - - x\n    - y\n"
    )
    assert _expanded(make_theatre, text, "x.0") == expected


def test_list_copy_in_a_file_with_dash_offset_zero(make_theatre: MakeTheatre) -> None:
    """Req 7.2: in a file whose lists have their `-` at their key's column, the copy does too."""
    text = "d: &d\n- a\n- b\nx: *d\n"
    expected = "d: &d\n- a\n- b\nx:  # expanded from *d by troupe\n- a\n- b\n"
    assert _expanded(make_theatre, text, "x.0") == expected


def test_flow_style_anchored_mapping_is_copied_in_block_style(make_theatre: MakeTheatre) -> None:
    """Req 7.2, 7.4: a flow mapping (with a nested flow list and mapping) is copied in block
    style; its scalars keep their source text and empty collections stay `{}`/`[]`."""
    text = (
        "d: &d {model: opus, tools: [read, \"write\"], e: {}, q: 'it''s',\n"
        "  n: {k: 1}}\nx:\n  s: *d\n  t: 1\n"
    )
    expected = (
        "d: &d {model: opus, tools: [read, \"write\"], e: {}, q: 'it''s',\n"
        "  n: {k: 1}}\nx:\n  s:  # expanded from *d by troupe\n    model: opus\n    tools:\n"
        "      - read\n      - \"write\"\n    e: {}\n    q: 'it''s'\n    n:\n      k: 1\n  t: 1\n"
    )
    assert _expanded(make_theatre, text, "x.s.model") == expected


def test_quoted_keys_keep_their_source_text(make_theatre: MakeTheatre) -> None:
    """Req 7.4: keys of the copy are written as in the anchored node, quotes included."""
    text = "d: &d\n  'a b': 1\n  \"c:d\": 2\n  plain: x\n  '007': [ ]\nx: *d\n"
    expected = (
        "d: &d\n  'a b': 1\n  \"c:d\": 2\n  plain: x\n  '007': [ ]\n"
        "x:  # expanded from *d by troupe\n  'a b': 1\n  \"c:d\": 2\n  plain: x\n  '007': []\n"
    )
    assert _expanded(make_theatre, text, "x.plain") == expected


def test_alias_of_an_include_string_copies_the_directive(make_theatre: MakeTheatre) -> None:
    """Req 7.3, 7.4: an alias of an include string is replaced by the include string as written,
    so the copy includes the same file."""
    text = "i: &i 'file:part.yml'\nx:\n  y: *i\n"
    expected = "i: &i 'file:part.yml'\nx:\n  y: 'file:part.yml'  # expanded from *i by troupe\n"
    files = {".troupe/part.yml": "m: 1\n"}
    assert _expanded(make_theatre, text, "x.y.m", files) == expected


def test_includes_inside_a_mapping_are_copied_as_written(make_theatre: MakeTheatre) -> None:
    """Req 7.4: include strings and a `$ref` mapping inside the anchored node are copied as
    written (the `$ref` mapping in block style, `$ref` first, then its overrides)."""
    text = 'd: &d\n  a: file:part.yml\n  b: {$ref: "file:part.yml", m: 2}\n  c: x\nx: *d\ny: *d\n'
    expected = (
        'd: &d\n  a: file:part.yml\n  b: {$ref: "file:part.yml", m: 2}\n  c: x\n'
        "x:  # expanded from *d by troupe\n  a: file:part.yml\n  b:\n"
        '    $ref: "file:part.yml"\n    m: 2\n  c: x\ny: *d\n'
    )
    files = {".troupe/part.yml": "m: 1\nn: 1\n"}
    assert _expanded(make_theatre, text, "x.c", files) == expected


def test_alias_of_a_ref_mapping_copies_it_in_block_style(make_theatre: MakeTheatre) -> None:
    """Req 7.2, 7.4: an alias of an anchored `$ref` mapping becomes a block `$ref` mapping under
    the key's line, `$ref` first and then the overrides (nested overrides in block style)."""
    text = "i: &i {$ref: file:part.yml, n: {k: 2}}\nx: *i\n"
    expected = (
        "i: &i {$ref: file:part.yml, n: {k: 2}}\n"
        "x:  # expanded from *i by troupe\n  $ref: file:part.yml\n  n:\n    k: 2\n"
    )
    files = {".troupe/part.yml": "m: 1\nn:\n  k: 1\n  j: 1\n"}
    assert _expanded(make_theatre, text, "x.m", files) == expected


def test_inner_alias_is_kept_and_inner_anchor_is_not_repeated(make_theatre: MakeTheatre) -> None:
    """Req 7.5: an anchor written inside the anchored node is not repeated in the copy and an
    alias written inside it stays the same alias, so later aliases still refer to the original."""
    text = "d: &d\n  a: &a 1\n  b: *a\n  c: &c\n    k: v\n  l:\n    - *c\nx: *d\ny: *a\nz: *c\n"
    expected = (
        "d: &d\n  a: &a 1\n  b: *a\n  c: &c\n    k: v\n  l:\n    - *c\n"
        "x:  # expanded from *d by troupe\n  a: 1\n  b: *a\n  c:\n    k: v\n  l:\n    - *c\n"
        "y: *a\nz: *c\n"
    )
    assert _expanded(make_theatre, text, "x.a") == expected


@pytest.mark.parametrize(
    ("anchored", "copy", "path"),
    [
        ("\n  model: opus\n  tools: [a]", "    model: opus\n    tools:\n      - a\n", "l.0.model"),
        (" [1, 2]", "    - 1\n    - 2\n", "l.0.0"),
    ],
    ids=["mapping", "list"],
)
def test_list_item_alias_gets_its_copy_under_the_item(
    make_theatre: MakeTheatre, anchored: str, copy: str, path: str
) -> None:
    """Req 8.3, 7.2: a block list item alias gets the comment two spaces after the `-` and the
    copy on the following lines, two columns right of the `-`."""
    text = f"d: &d{anchored}\nl:\n  - *d\n  - other\n"
    expected = f"d: &d{anchored}\nl:\n  -  # expanded from *d by troupe\n{copy}  - other\n"
    assert _expanded(make_theatre, text, path) == expected


def test_collection_copy_in_a_crlf_file_uses_crlf(make_theatre: MakeTheatre) -> None:
    """Req 7.2, 7.6: every copied line ends with the file's line break."""
    text = "defaults: &d\r\n  model: opus\r\n  t:\r\n    - a\r\ncoder:\r\n  settings: *d\r\n"
    expected = (
        "defaults: &d\r\n  model: opus\r\n  t:\r\n    - a\r\ncoder:\r\n"
        "  settings:  # expanded from *d by troupe\r\n    model: opus\r\n    t:\r\n"
        "      - a\r\n"
    )
    assert _expanded(make_theatre, text, "coder.settings.model") == expected


def test_collection_copy_in_a_four_space_file(make_theatre: MakeTheatre) -> None:
    """Req 7.2: the copy uses the file's indentation step and dash offset (four spaces here)."""
    text = (
        "defaults: &d\n    model: opus\n    tools:\n        - a\n    o:\n        k: 1\n"
        "coder:\n    settings: *d\n"
    )
    expected = (
        "defaults: &d\n    model: opus\n    tools:\n        - a\n    o:\n        k: 1\n"
        "coder:\n    settings:  # expanded from *d by troupe\n        model: opus\n"
        "        tools:\n            - a\n        o:\n            k: 1\n"
    )
    assert _expanded(make_theatre, text, "coder.settings.model") == expected


def test_nested_scalars_on_several_lines_are_copied(make_theatre: MakeTheatre) -> None:
    """Req 7.4: a block scalar inside the copy keeps its header and is re-indented; a multi-line
    flow scalar is rendered on one line in its own style."""
    text = (
        'd: &d\n  s: |  # about s\n    one\n      two\n  q: "a\n    b"\n  l:\n    - >-\n'
        "      f\nx:\n  y: *d\n"
    )
    expected = (
        'd: &d\n  s: |  # about s\n    one\n      two\n  q: "a\n    b"\n  l:\n    - >-\n'
        "      f\nx:\n  y:  # expanded from *d by troupe\n    s: |\n      one\n        two\n"
        '    q: "a b"\n    l:\n      - >-\n        f\n'
    )
    assert _expanded(make_theatre, text, "x.y.s") == expected


def test_collection_alias_written_below_its_key(make_theatre: MakeTheatre) -> None:
    """Req 7.1, 7.2: an alias written on the line after its key is replaced from the key's `:`,
    and the copy follows the key's line."""
    text = "d: &d\n  m: 1\nother:\n  *d\nlast: 1\n"
    expected = "d: &d\n  m: 1\nother:  # expanded from *d by troupe\n  m: 1\nlast: 1\n"
    assert _expanded(make_theatre, text, "other.m") == expected


# --- the end of a file without a final line break ---------------------------------------------


@pytest.mark.parametrize(
    ("text", "path", "expected"),
    [
        (
            "d: &d\n  a: 1\n  b: [x]\nx: *d",
            "x.a",
            "d: &d\n  a: 1\n  b: [x]\nx:  # expanded from *d by troupe\n  a: 1\n  b:\n    - x",
        ),
        (
            "d: &d\n  a: 1\nl:\n  - *d",
            "l.0.a",
            "d: &d\n  a: 1\nl:\n  -  # expanded from *d by troupe\n    a: 1",
        ),
        (
            "d: &d\n  a: 1\n  s: |-\n    one\nx: *d",
            "x.a",
            "d: &d\n  a: 1\n  s: |-\n    one\nx:  # expanded from *d by troupe\n  a: 1\n"
            "  s: |-\n    one",
        ),
    ],
    ids=["mapping", "list-item", "strip-block-scalar"],
)
def test_copy_at_the_end_of_a_file_without_a_final_break_adds_none(
    make_theatre: MakeTheatre, text: str, path: str, expected: str
) -> None:
    """Req 7.2, 7.6 (config-set-writeback 11.6): a copy after the last line of a file without a
    final line break starts on a new line and leaves the file ending without a break, also when
    its last value is a strip block scalar, whose value does not need one."""
    assert _expanded(make_theatre, text, path) == expected


@pytest.mark.parametrize("header", ["|", "|+"], ids=["clip", "keep"])
def test_copy_ending_in_a_clip_or_keep_block_scalar_keeps_the_final_break(
    make_theatre: MakeTheatre, header: str
) -> None:
    """Req 7.4, 7.6: at the end of a file without a final line break, a copy whose last line ends a
    clip or keep block scalar ends with a break as at the anchor, so the block keeps its value."""
    text = f"d: &d\n  a: 1\n  s: {header}\n    one\nx: *d"
    expected = (
        f"d: &d\n  a: 1\n  s: {header}\n    one\nx:  # expanded from *d by troupe\n  a: 1\n"
        f"  s: {header}\n    one\n"
    )
    assert _expanded(make_theatre, text, "x.a") == expected


def test_key_written_over_several_lines_is_rendered_in_its_style(
    make_theatre: MakeTheatre,
) -> None:
    """Req 7.4: a key written over several lines cannot be copied as written in block style, so
    it is rendered on one line in its own quoting style. (The first mapping fixes the step: the
    layout measures an explicit key's column after its `?`.)"""
    head = 'o:\n  k: 1\nd: &d\n  ? "a\n    b"\n  : 1\n'
    text = head + "x: *d\n"
    expected = head + 'x:  # expanded from *d by troupe\n  "a b": 1\n'
    assert _expanded(make_theatre, text, "x") == expected
