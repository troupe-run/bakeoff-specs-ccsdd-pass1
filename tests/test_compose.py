"""Unit tests of the recording composer (task 1.2): ``troupe.config.compose.compose_file``.

Each case writes a real file, reads it back as a ``SourceFile`` and composes it. The tests pin, for
every alias, the recorded name and the span of its ``*name`` token, keyed by the parent node and
the slot it fills: the key node of a mapping value, the index of a list item, or ``None`` for an
alias used as a mapping key. The loader still refuses aliases at this point (until task 2.2), so
the end-to-end cases only check that composing reports and stays silent as before.

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

import warnings
from pathlib import Path

import pytest
from ruamel.yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

from support import MakeTheatre, RunCli
from troupe.config.compose import AliasOccurrence, ComposedFile, compose_file
from troupe.config.errors import ConfigLoadError
from troupe.config.model import LineEnding, SourceFile, Span


def _source(tmp_path: Path, text: str, line_ending: LineEnding = LineEnding.LF) -> SourceFile:
    path = tmp_path / "troupe.yml"
    path.write_bytes(text.encode("utf-8"))
    decoded = path.read_bytes().decode("utf-8")
    return SourceFile(path=path, real_path=path.resolve(), text=decoded, line_ending=line_ending)


def _compose(tmp_path: Path, text: str) -> tuple[SourceFile, ComposedFile]:
    source = _source(tmp_path, text)
    return source, compose_file(source)


def _mapping(node: Node | None) -> MappingNode:
    assert isinstance(node, MappingNode)
    return node


def _sequence(node: Node) -> SequenceNode:
    assert isinstance(node, SequenceNode)
    return node


def _entry(mapping: MappingNode, key: str) -> tuple[ScalarNode, Node]:
    for key_node, value_node in mapping.value:
        if isinstance(key_node, ScalarNode) and key_node.value == key:
            return key_node, value_node
    raise AssertionError(f"no key {key!r}")


def _token(text: str, token: str, occurrence: int = 0) -> Span:
    """The span of the ``occurrence``-th (0-based) ``token`` in ``text``."""
    start = -1
    for _ in range(occurrence + 1):
        start = text.index(token, start + 1)
    return Span(start, start + len(token))


def test_alias_as_mapping_value_is_recorded_under_its_key_node(tmp_path: Path) -> None:
    """Req 1.1: an alias as a mapping value is recorded by its parent mapping and key node."""
    text = "defaults: &d\n  model: opus\ncoder:\n  settings: *d\n"
    _, composed = _compose(tmp_path, text)
    coder = _mapping(_entry(_mapping(composed.root), "coder")[1])
    settings_key, _ = _entry(coder, "settings")

    assert composed.aliases == {
        (id(coder), id(settings_key)): AliasOccurrence("d", _token(text, "*d")),
    }
    assert composed.aliases[(id(coder), id(settings_key))].span == Span(46, 48)


def test_alias_as_list_item_is_recorded_under_its_index(tmp_path: Path) -> None:
    """Req 1.1: an alias as a list item is recorded by its parent list and item index."""
    text = "base: &d 1\nitems:\n  - 0\n  - *d\n  - [2, *d]\n"
    _, composed = _compose(tmp_path, text)
    items = _sequence(_entry(_mapping(composed.root), "items")[1])
    flow = _sequence(items.value[2])

    assert composed.aliases == {
        (id(items), 1): AliasOccurrence("d", _token(text, "*d")),
        (id(flow), 1): AliasOccurrence("d", _token(text, "*d", 1)),
    }


def test_alias_as_merge_key_value_is_recorded_under_the_merge_key(tmp_path: Path) -> None:
    """Req 1.1: an alias as the value of a ``<<`` key is recorded under the ``<<`` key node."""
    text = "base: &d {a: 1}\nchild:\n  <<: *d\n  b: 2\n"
    _, composed = _compose(tmp_path, text)
    child = _mapping(_entry(_mapping(composed.root), "child")[1])
    merge_key, _ = _entry(child, "<<")

    assert composed.aliases == {
        (id(child), id(merge_key)): AliasOccurrence("d", _token(text, "*d")),
    }


def test_aliases_in_a_list_of_merge_sources_are_recorded_by_index(tmp_path: Path) -> None:
    """Req 1.1: each alias in ``<<: [*a, *b]`` is recorded by the list and its index."""
    text = "a: &a {x: 1}\nb: &b\n  y: 2\nc:\n  <<: [*a, *b]\n  z: 3\n"
    _, composed = _compose(tmp_path, text)
    c = _mapping(_entry(_mapping(composed.root), "c")[1])
    sources = _sequence(_entry(c, "<<")[1])

    assert composed.aliases == {
        (id(sources), 0): AliasOccurrence("a", _token(text, "*a")),
        (id(sources), 1): AliasOccurrence("b", _token(text, "*b")),
    }


def test_alias_inside_an_anchored_node_is_recorded_once_in_that_node(tmp_path: Path) -> None:
    """Req 1.1: an alias inside an anchored node is recorded under the anchored node itself, so
    it is found again wherever that node is used through an alias."""
    text = "base: &b 1\nouter: &o\n  inner: *b\nuse: *o\n"
    _, composed = _compose(tmp_path, text)
    root = _mapping(composed.root)
    outer = _mapping(_entry(root, "outer")[1])
    inner_key, _ = _entry(outer, "inner")
    use_key, used = _entry(root, "use")

    assert used is outer
    assert composed.aliases == {
        (id(outer), id(inner_key)): AliasOccurrence("b", _token(text, "*b")),
        (id(root), id(use_key)): AliasOccurrence("o", _token(text, "*o")),
    }


def test_alias_as_a_mapping_key_is_recorded_with_slot_none(tmp_path: Path) -> None:
    """Req 1.1: an alias used as a mapping key is recorded with the key slot ``None``."""
    text = "k: &k a\nm:\n  *k : 1\n"
    _, composed = _compose(tmp_path, text)
    m = _mapping(_entry(_mapping(composed.root), "m")[1])

    assert composed.aliases == {(id(m), None): AliasOccurrence("k", _token(text, "*k"))}


def test_alias_spans_are_character_indices_with_a_bom_and_crlf(tmp_path: Path) -> None:
    """Req 1.1: spans index the decoded text, including a leading BOM and CRLF line ends."""
    text = "﻿a: &x 1\r\nlist:\r\n  - *x\r\n"
    source = _source(tmp_path, text, LineEnding.CRLF)
    composed = compose_file(source)
    items = _sequence(_entry(_mapping(composed.root), "list")[1])

    assert composed.aliases == {(id(items), 0): AliasOccurrence("x", _token(text, "*x"))}
    assert source.text[_token(text, "*x").start : _token(text, "*x").end] == "*x"


def test_a_file_without_aliases_records_nothing(tmp_path: Path) -> None:
    """Req 1.1: composing a file without aliases records no alias occurrence."""
    _, composed = _compose(tmp_path, "a: '*x'\nb: [1, 2]\n")

    assert composed.aliases == {}
    assert isinstance(composed.root, MappingNode)


def test_an_empty_file_composes_to_no_root(tmp_path: Path) -> None:
    """Req 1.1: an empty or comment-only file composes to no root and no aliases."""
    _, composed = _compose(tmp_path, "# only a comment\n")

    assert composed == ComposedFile(root=None, aliases={})


def test_a_reused_anchor_name_composes_and_issues_no_warning(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    """Req 1.2: a reused anchor name composes, each alias refers to the latest definition, and
    no warning is issued or written to stdout or stderr."""
    text = "a: &x 1\nb: *x\nc: &x 2\nd: *x\n"
    source = _source(tmp_path, text)
    filters_before = list(warnings.filters)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        composed = compose_file(source)

    root = _mapping(composed.root)
    assert caught == []
    assert capfd.readouterr() == ("", "")
    assert list(warnings.filters) == filters_before
    b_value = _entry(root, "b")[1]
    d_value = _entry(root, "d")[1]
    assert isinstance(b_value, ScalarNode) and b_value.value == "1"
    assert isinstance(d_value, ScalarNode) and d_value.value == "2"
    assert composed.aliases == {
        (id(root), id(_entry(root, "b")[0])): AliasOccurrence("x", _token(text, "*x")),
        (id(root), id(_entry(root, "d")[0])): AliasOccurrence("x", _token(text, "*x", 1)),
    }


def test_other_warnings_are_not_silenced_after_composing(tmp_path: Path) -> None:
    """Req 1.2: only composing is silenced; a warning issued afterwards is still issued."""
    compose_file(_source(tmp_path, "a: &x 1\nb: &x 2\n"))

    with pytest.warns(UserWarning, match="still visible"):
        warnings.warn("still visible", UserWarning, stacklevel=1)


def test_undefined_alias_is_a_load_error_naming_the_alias(tmp_path: Path) -> None:
    """Req 1.3: an alias with no anchor before it is a load error naming the file and alias."""
    source = _source(tmp_path, "b: *x\na: &x 1\n")

    with pytest.raises(ConfigLoadError) as raised:
        compose_file(source)

    assert raised.value.file == source.path
    assert str(raised.value) == (
        f"{source.path}: invalid YAML: found undefined alias 'x' (line 1, column 4)"
    )


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("a: [1\n", "invalid YAML: "),
        ("a: 1\n---\nb: 2\n", "expected a single document in the stream"),
    ],
    ids=["syntax-error", "several-documents"],
)
def test_composer_errors_are_load_errors_naming_the_file(
    tmp_path: Path, text: str, message: str
) -> None:
    """Req 1.3: syntax errors and several documents stay load errors naming the file."""
    source = _source(tmp_path, text)

    with pytest.raises(ConfigLoadError) as raised:
        compose_file(source)

    assert raised.value.file == source.path
    assert message in str(raised.value)


def test_cli_reports_an_undefined_alias_with_exit_4(
    make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    """Req 1.3: an undefined alias in the root file exits 4 naming the file and the alias."""
    theatre = make_theatre("a: 1\nb: *x\n")

    result = run_cli("config", "get", "a", cwd=theatre)

    assert result.exit_code == 4
    assert result.stdout == b""
    assert result.stderr == (
        f"{theatre / '.troupe' / 'troupe.yml'}: invalid YAML: found undefined alias 'x'"
        " (line 2, column 4)\n"
    )


def test_cli_reports_an_undefined_alias_in_an_included_file(
    make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    """Req 1.3: an alias defined only in the including file is undefined in the included file."""
    theatre = make_theatre(
        "part: file:part.yml\nbase: &x 1\n", files={".troupe/part.yml": "v: *x\n"}
    )

    result = run_cli("config", "get", "base", cwd=theatre)

    assert result.exit_code == 4
    assert result.stdout == b""
    assert "part.yml: invalid YAML: found undefined alias 'x'" in result.stderr


def test_cli_writes_no_reused_anchor_warning_to_stderr(
    make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    """Req 1.2: a reused anchor name writes no warning to stderr; the only line on stderr is the
    load error (aliases are still refused until task 2.2)."""
    theatre = make_theatre("a: &x 1\nb: *x\nc: &x 2\nd: *x\n")

    result = run_cli("config", "get", "b", cwd=theatre)

    assert result.exit_code == 4
    assert result.stdout == b""
    assert "duplicate anchor" not in result.stderr
    assert "Warning" not in result.stderr
    assert result.stderr.count("\n") == 1
