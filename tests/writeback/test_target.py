"""Unit tests of the TargetResolver (``troupe.config.writeback.target``).

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

from collections.abc import Mapping
from pathlib import Path

import pytest

from set_support import marked_value
from troupe.config.errors import ExitCode, PathNotFoundError
from troupe.config.loader import load_config
from troupe.config.model import (
    ConfigDocument,
    ConfigMapping,
    ConfigScalar,
    ConfigSequence,
    IncludeKind,
    LineEnding,
    SourceFile,
)
from troupe.config.paths import ConfigPath
from troupe.config.writeback.compare import IncludeDirective, node_to_plain, plain_equal
from troupe.config.writeback.target import (
    AddKeyTarget,
    AppendTarget,
    ReplaceTarget,
    SetRequest,
    resolve_target,
    text_include_value,
)
from troupe.config.writeback.value import NewScalar, NewValue, parse_value


def _load(
    tmp_path: Path, config: str | bytes, files: Mapping[str, str | bytes] | None = None
) -> ConfigDocument:
    for name, text in (files or {}).items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text if isinstance(text, bytes) else text.encode("utf-8"))
    root = tmp_path / "troupe.yml"
    root.write_bytes(config if isinstance(config, bytes) else config.encode("utf-8"))
    return load_config(root)


def _resolve(
    document: ConfigDocument, path: str, value: str, *, include: bool = False
) -> SetRequest:
    config_path = ConfigPath.parse(path)
    marked = marked_value(document, value, config_path, include=include)
    return resolve_target(document, config_path, marked)


def _not_found(document: ConfigDocument, path: str, value: str) -> PathNotFoundError:
    with pytest.raises(PathNotFoundError) as caught:
        _resolve(document, path, value)
    return caught.value


INCLUDES = {
    "agents/coder.yaml": "model: small\ntemperature: 0.2\ntools: [read]\nnote: file:note.txt\n",
    "agents/note.txt": "hello\n",
}
ROOT_WITH_INCLUDES = "model: gpt\nagents:\n  coder: file:agents/coder.yaml\n"


# --- target kinds --------------------------------------------------------------------------------


def test_existing_value_is_a_replace_target_with_parent_and_key(tmp_path: Path) -> None:
    """Req 2.1: an existing value is replaced where it is; parent mapping and key are given."""
    document = _load(tmp_path, "a:\n  b: 1\n")
    request = _resolve(document, "a.b", "2")
    target = request.target
    assert isinstance(target, ReplaceTarget)
    assert isinstance(target.node, ConfigScalar)
    assert target.node.value == 1
    assert isinstance(target.parent, ConfigMapping)
    assert target.key == "b"
    assert target.node.origin.file.path == tmp_path / "troupe.yml"
    assert request.path == ConfigPath.parse("a.b")
    assert request.value == NewScalar(2, "2")


def test_existing_list_item_is_a_replace_target_keyed_by_int(tmp_path: Path) -> None:
    """Req 2.1: an existing list item is replaced in place; the key is its int index."""
    document = _load(tmp_path, "l: [a, b]\n")
    target = _resolve(document, "l.1", "c").target
    assert isinstance(target, ReplaceTarget)
    assert isinstance(target.parent, ConfigSequence)
    assert target.key == 1
    assert isinstance(target.node, ConfigScalar) and target.node.value == "b"


def test_value_inside_yaml_include_is_replaced_in_the_included_file(tmp_path: Path) -> None:
    """Req 2.1, 2.6: a value inside a YAML-included file is targeted in that file."""
    document = _load(tmp_path, ROOT_WITH_INCLUDES, INCLUDES)
    target = _resolve(document, "agents.coder.model", "large").target
    assert isinstance(target, ReplaceTarget)
    assert target.node.origin.file.path == tmp_path / "agents/coder.yaml"


def test_include_root_is_a_replace_target(tmp_path: Path) -> None:
    """Req 2.2, 2.5: include roots (YAML, text) are replace targets; the planner refuses."""
    document = _load(tmp_path, ROOT_WITH_INCLUDES, INCLUDES)
    yaml_target = _resolve(document, "agents.coder", "{model: x}").target
    assert isinstance(yaml_target, ReplaceTarget)
    assert document.include_kind(yaml_target.node) is IncludeKind.YAML
    text_target = _resolve(document, "agents.coder.note", "5").target
    assert isinstance(text_target, ReplaceTarget)
    assert document.include_kind(text_target.node) is IncludeKind.TEXT


def test_new_key_is_an_add_key_target_in_the_mapping(tmp_path: Path) -> None:
    """Req 3.1: a missing key below an existing mapping is added to that mapping."""
    document = _load(tmp_path, "a:\n  x: 1\nb: 2\n")
    target = _resolve(document, "a.y", "2").target
    assert isinstance(target, AddKeyTarget)
    assert target.missing == ("y",)
    assert [entry.key for entry in target.mapping.entries] == ["x"]


def test_new_key_with_intermediates_lists_every_missing_segment(tmp_path: Path) -> None:
    """Req 3.2: missing intermediate mappings are listed, first missing key first."""
    document = _load(tmp_path, "a:\n  x: 1\nb: 2\n")
    request = _resolve(document, "a.p.q", "1")
    assert isinstance(request.target, AddKeyTarget)
    assert request.target.missing == ("p", "q")
    assert request.intended == {"a": {"x": 1, "p": {"q": 1}}, "b": 2}


def test_new_key_in_empty_root_targets_the_root_file(tmp_path: Path) -> None:
    """Req 3.3: a comment-only root file gets the new key in its root mapping."""
    document = _load(tmp_path, "# config\n")
    request = _resolve(document, "a", "1")
    assert isinstance(request.target, AddKeyTarget)
    assert request.target.mapping is document.root
    assert request.target.missing == ("a",)
    assert request.intended == {"a": 1}


def test_new_key_below_yaml_include_targets_the_included_mapping(tmp_path: Path) -> None:
    """Req 3.1, 2.6: a new key below an included mapping is added in the included file."""
    document = _load(tmp_path, ROOT_WITH_INCLUDES, INCLUDES)
    target = _resolve(document, "agents.coder.top_p", "0.9").target
    assert isinstance(target, AddKeyTarget)
    assert target.mapping.origin.file.path == tmp_path / "agents/coder.yaml"


def test_index_equal_to_length_is_an_append_target(tmp_path: Path) -> None:
    """Req 4.1: an index equal to the list length appends."""
    document = _load(tmp_path, "l:\n  - a\n  - b\n")
    request = _resolve(document, "l.2", "c")
    assert isinstance(request.target, AppendTarget)
    assert len(request.target.sequence.items) == 2
    assert request.intended == {"l": ["a", "b", "c"]}


# --- exit 5 cases ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("config", "path"),
    [
        ("l:\n  - a\n  - b\n", "l.3"),  # 4.2 index beyond the length
        ("l:\n  - a\n  - b\n", "l.2.x"),  # 4.3 segment after an appended index
        ("l:\n  - a\n  - b\n", "l.x"),  # 3.5 non-index segment on a list
        ("n:\n", "n.k"),  # 3.4 below a null
        ("s: 5\n", "s.k"),  # 3.4 below a scalar
        ("m:\n  k: v\n", "m.0"),  # 3.6 digit-only key to be created
        ("m:\n  k: v\n", "m.x.0"),  # 3.6 digit-only intermediate key
        ("m:\n  k: v\n", "m.k.z"),  # 3.4 below a string scalar
        ("l: [[a]]\n", "l.0.5"),  # 4.2 nested list
    ],
)
def test_path_errors_exit_5_naming_path_and_the_root_file(
    tmp_path: Path, config: str, path: str
) -> None:
    """Req 3.4, 3.5, 3.6, 4.2, 4.3, 16.3: exit 5 naming PATH and the deepest node's file."""
    document = _load(tmp_path, config)
    error = _not_found(document, path, "1")
    assert error.exit_code == ExitCode.PATH
    assert error.path == path
    assert error.file == tmp_path / "troupe.yml"
    assert path in str(error)


@pytest.mark.parametrize(
    ("path", "file"),
    [
        ("agents.coder.model.x", "agents/coder.yaml"),  # 3.4 scalar inside the included file
        ("agents.coder.tools.x", "agents/coder.yaml"),  # 3.5 list inside the included file
        ("agents.coder.tools.2", "agents/coder.yaml"),  # 4.2 list inside the included file
        ("agents.coder.7", "agents/coder.yaml"),  # 3.6 digit key in the included mapping
        ("agents.coder.note.x", "agents/note.txt"),  # 3.4 below a text include
    ],
)
def test_path_errors_inside_included_files_name_the_included_file(
    tmp_path: Path, path: str, file: str
) -> None:
    """Req 3.4, 3.5, 3.6, 4.2, 16.3: a path ending inside an included file names that file."""
    document = _load(tmp_path, ROOT_WITH_INCLUDES, INCLUDES)
    error = _not_found(document, path, "1")
    assert error.exit_code == ExitCode.PATH
    assert error.path == path
    assert error.file == tmp_path / file


# --- text include content rule -------------------------------------------------------------------


def _source(line_ending: LineEnding) -> SourceFile:
    return SourceFile(
        path=Path("t.txt"), real_path=Path("/t.txt"), text="", line_ending=line_ending
    )


@pytest.mark.parametrize(
    ("line_ending", "value", "expected"),
    [
        (LineEnding.CRLF, "new\ntext\n", "new\r\ntext\r\n"),
        (LineEnding.CRLF, "a\r\nb\nc", "a\r\nb\r\nc"),
        (LineEnding.CRLF, "\n\n", "\r\n\r\n"),
        (LineEnding.LF, "new\ntext\n", "new\ntext\n"),
        (LineEnding.LF, "a\r\nb\n", "a\r\nb\n"),
        (LineEnding.NONE, "a\nb", "a\nb"),
        (LineEnding.MIXED, "a\nb\n", "a\nb\n"),
    ],
)
def test_text_include_value_converts_line_feeds_only_for_crlf_targets(
    line_ending: LineEnding, value: str, expected: str
) -> None:
    """Req 2.2, 2.3: VALUE exactly, with bare LFs written as CRLF only for CRLF targets."""
    assert text_include_value(value, _source(line_ending)) == expected


def _text_include_content(request: SetRequest, *keys: str) -> object:
    node: object = request.intended
    for key in keys:
        assert isinstance(node, dict)
        node = node[key]
    assert isinstance(node, IncludeDirective)
    return node.content


def test_text_include_target_at_path_keeps_directive_and_converts_crlf(tmp_path: Path) -> None:
    """Req 2.2, 2.3: at a CRLF text-include target the intended content is CRLF-converted."""
    document = _load(tmp_path, "prompt: file:prompt.txt\n", {"prompt.txt": b"old\r\nline\r\n"})
    request = _resolve(document, "prompt", '"new\\ntext\\n"')
    assert isinstance(request.target, ReplaceTarget)
    directive = request.intended["prompt"]
    assert isinstance(directive, IncludeDirective)
    assert directive.text == "file:prompt.txt"
    assert directive.kind is IncludeKind.TEXT
    assert directive.content == "new\r\ntext\r\n"


def test_text_include_target_with_lf_keeps_value_exactly(tmp_path: Path) -> None:
    """Req 2.2: an LF text-include target takes VALUE exactly."""
    document = _load(tmp_path, "prompt: file:prompt.txt\n", {"prompt.txt": b"old\n"})
    request = _resolve(document, "prompt", '"new\\ntext\\n"')
    assert _text_include_content(request, "prompt") == "new\ntext\n"


def test_nested_text_include_in_replaced_mapping_gets_crlf_content(tmp_path: Path) -> None:
    """Req 2.2, 2.3, 2.8: a text include nested in a replaced mapping gets the CRLF rule too."""
    document = _load(tmp_path, "p:\n  t: file:t.txt\n  n: 1\n", {"t.txt": b"old\r\n"})
    request = _resolve(document, "p", '{t: "new\\n", n: 1}')
    assert isinstance(request.target, ReplaceTarget)
    assert _text_include_content(request, "p", "t") == "new\r\n"
    assert request.intended["p"] == {
        "t": IncludeDirective("file:t.txt", "new\r\n", IncludeKind.TEXT),
        "n": 1,
    }


def test_crlf_rule_follows_each_text_include_target(tmp_path: Path) -> None:
    """Req 2.3: only text includes whose target uses CRLF get CRLF content."""
    document = _load(
        tmp_path,
        "p:\n  c: file:c.txt\n  l: file:l.txt\n",
        {"c.txt": b"x\r\n", "l.txt": b"x\n"},
    )
    request = _resolve(document, "p", '{c: "a\\nb\\n", l: "a\\nb\\n"}')
    assert _text_include_content(request, "p", "c") == "a\r\nb\r\n"
    assert _text_include_content(request, "p", "l") == "a\nb\n"


def test_non_string_over_text_include_replaces_the_directive(tmp_path: Path) -> None:
    """Req 2.4: a non-string at a text include differs as a value (the planner refuses it)."""
    document = _load(tmp_path, "prompt: file:prompt.txt\n", {"prompt.txt": "old\n"})
    request = _resolve(document, "prompt", "5")
    assert request.intended == {"prompt": 5}


# --- intended configuration ----------------------------------------------------------------------


def test_current_is_the_plain_tree_of_the_loaded_root(tmp_path: Path) -> None:
    """Req 2.8: ``current`` is the ordered plain tree of the document as loaded."""
    document = _load(tmp_path, ROOT_WITH_INCLUDES, INCLUDES)
    request = _resolve(document, "model", "x")
    assert plain_equal(request.current, node_to_plain(document.root), ordered=True)
    assert request.intended["model"] == "x"
    assert plain_equal(request.intended["agents"], request.current["agents"], ordered=True)


def test_include_root_set_to_its_own_file_string_is_unchanged(tmp_path: Path) -> None:
    """Req 2.8 (config-remote-schema 9.3): setting an include root to its own ``file:`` string
    with ``--include`` keeps the configuration; without it the string is a ``!literal`` value,
    which differs (8.1)."""
    document = _load(tmp_path, ROOT_WITH_INCLUDES, INCLUDES)
    request = _resolve(document, "agents.coder", "file:agents/coder.yaml", include=True)
    assert plain_equal(request.intended, request.current, ordered=True)
    literal = _resolve(document, "agents.coder", "file:agents/coder.yaml")
    assert not plain_equal(literal.intended, literal.current, ordered=False)


def test_text_include_set_to_its_own_file_string_is_unchanged(tmp_path: Path) -> None:
    """Req 2.8 (config-remote-schema 9.3): a text include set to its own ``file:`` string with
    ``--include`` keeps the configuration."""
    document = _load(tmp_path, "prompt: file:prompt.txt\n", {"prompt.txt": "old\n"})
    request = _resolve(document, "prompt", "file:prompt.txt", include=True)
    assert plain_equal(request.intended, request.current, ordered=True)


def test_text_include_set_to_its_current_text_is_unchanged(tmp_path: Path) -> None:
    """Req 2.2, 2.3, 2.8: setting the text include's current text (CRLF target) is a no-op."""
    document = _load(tmp_path, "p:\n  t: file:t.txt\n", {"t.txt": b"old\r\n"})
    request = _resolve(document, "p", '{t: "old\\n"}')
    assert plain_equal(request.intended, request.current, ordered=True)


def test_include_root_set_to_another_file_string_differs(tmp_path: Path) -> None:
    """Req 2.5: another ``file:`` string at an include root is a different configuration."""
    document = _load(tmp_path, ROOT_WITH_INCLUDES, INCLUDES)
    request = _resolve(document, "agents.coder", "file:agents/other.yaml", include=True)
    assert not plain_equal(request.intended, request.current, ordered=False)


def test_value_types_are_new_value(tmp_path: Path) -> None:
    """Req 2.1: the request carries the parsed VALUE unchanged."""
    document = _load(tmp_path, "a: 1\n")
    value: NewValue = parse_value("[x, 1]", ConfigPath.parse("a"))
    request = resolve_target(document, ConfigPath.parse("a"), value)
    assert request.value is value
    assert request.intended == {"a": ["x", 1]}
