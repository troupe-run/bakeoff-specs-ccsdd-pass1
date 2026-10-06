"""Resolving ``file:`` includes while loading (Requirements 4.1-4.7).

Every test writes a real theatre with include files and loads it with ``load_config``.
"""

import hashlib
from pathlib import Path

import pytest

from support import MakeTheatre
from troupe.config.errors import ConfigLoadError, ExitCode
from troupe.config.loader import load_config
from troupe.config.model import (
    ConfigDocument,
    ConfigMapping,
    ConfigNode,
    ConfigScalar,
    ConfigSequence,
    IncludeKind,
    ScalarStyle,
    Span,
)


def _config(theatre: Path) -> Path:
    return theatre / ".troupe" / "troupe.yml"


def _node_at(document: ConfigDocument, *keys: str | int) -> ConfigNode:
    node: ConfigNode = document.root
    for key in keys:
        if isinstance(key, int):
            assert isinstance(node, ConfigSequence)
            node = node.items[key]
        else:
            assert isinstance(node, ConfigMapping)
            entry = node.get(key)
            assert entry is not None, key
            node = entry.value
    return node


def _scalar_at(document: ConfigDocument, *keys: str | int) -> ConfigScalar:
    node = _node_at(document, *keys)
    assert isinstance(node, ConfigScalar)
    return node


def _span_text(node: ConfigNode) -> str:
    return node.origin.file.text[node.origin.span.start : node.origin.span.end]


def _directive_text(node: ConfigNode) -> str:
    assert node.include is not None
    directive = node.include.directive
    return directive.file.text[directive.span.start : directive.span.end]


# --- YAML includes (4.1, 4.2) ----------------------------------------------------------------


def test_yaml_include_is_embedded_as_if_written_in_place(make_theatre: MakeTheatre) -> None:
    theatre = make_theatre(
        "agents: file:agents.yml\nname: x\n",
        files={".troupe/agents.yml": "alice:\n  role: lead\nbob: 2\n"},
    )
    document = load_config(_config(theatre))
    agents = _node_at(document, "agents")
    assert isinstance(agents, ConfigMapping)
    assert [entry.key for entry in agents.entries] == ["alice", "bob"]
    assert _scalar_at(document, "agents", "alice", "role").value == "lead"
    assert _scalar_at(document, "agents", "bob").value == 2
    assert _scalar_at(document, "name").value == "x"


def test_yaml_include_records_provenance(make_theatre: MakeTheatre) -> None:
    theatre = make_theatre(
        "agents: file:agents.yaml\n",
        files={".troupe/agents.yaml": "alice: 1\n"},
    )
    config = _config(theatre)
    document = load_config(config)
    agents = _node_at(document, "agents")
    assert agents.include is not None
    assert agents.include.kind is IncludeKind.YAML
    assert document.include_kind(agents) is IncludeKind.YAML
    assert agents.include.spec == "agents.yaml"
    assert agents.include.target.path == config.parent / "agents.yaml"
    assert agents.include.target.real_path == (config.parent / "agents.yaml").resolve()
    assert agents.include.directive.file is document.root_file
    assert _directive_text(agents) == "file:agents.yaml"
    assert agents.origin.file is agents.include.target
    # Nodes nested inside the included file are not include roots and live in the target.
    alice = _scalar_at(document, "agents", "alice")
    assert alice.include is None
    assert alice.origin.file is agents.include.target
    assert _span_text(alice) == "1"


@pytest.mark.parametrize("written", ["'file:inc.yml'", '"file:inc.yml"'])
def test_quoted_include_strings_are_includes(make_theatre: MakeTheatre, written: str) -> None:
    theatre = make_theatre(f"a: {written}\n", files={".troupe/inc.yml": "b: 1\n"})
    document = load_config(_config(theatre))
    assert _scalar_at(document, "a", "b").value == 1
    node = _node_at(document, "a")
    assert _directive_text(node) == written


def test_include_in_a_list_item(make_theatre: MakeTheatre) -> None:
    theatre = make_theatre(
        "items:\n  - file:one.yml\n  - plain\n  - [file:two.txt]\n",
        files={".troupe/one.yml": "[1, 2]\n", ".troupe/two.txt": "two"},
    )
    document = load_config(_config(theatre))
    one = _node_at(document, "items", 0)
    assert isinstance(one, ConfigSequence)
    assert [item.value for item in one.items if isinstance(item, ConfigScalar)] == [1, 2]
    assert _scalar_at(document, "items", 1).value == "plain"
    assert _scalar_at(document, "items", 2, 0).value == "two"


def test_non_mapping_yaml_target_root_is_embedded(make_theatre: MakeTheatre) -> None:
    theatre = make_theatre("a: file:scalar.yml\n", files={".troupe/scalar.yml": "'hello'\n"})
    document = load_config(_config(theatre))
    node = _scalar_at(document, "a")
    assert node.value == "hello"
    assert node.style is ScalarStyle.SINGLE_QUOTED
    assert node.include is not None
    assert node.include.kind is IncludeKind.YAML


@pytest.mark.parametrize("content", ["", "# only a comment\n"])
def test_empty_yaml_target_is_null(make_theatre: MakeTheatre, content: str) -> None:
    theatre = make_theatre("a: file:empty.yml\n", files={".troupe/empty.yml": content})
    document = load_config(_config(theatre))
    node = _scalar_at(document, "a")
    assert node.value is None
    assert node.origin.span == Span(0, 0)
    assert node.include is not None
    assert node.include.kind is IncludeKind.YAML
    assert node.origin.file is node.include.target


def test_keys_starting_with_the_prefix_are_not_includes(make_theatre: MakeTheatre) -> None:
    theatre = make_theatre("'file:x.yml': 1\n")
    document = load_config(_config(theatre))
    assert _scalar_at(document, "file:x.yml").value == 1
    assert len(document.files) == 1


@pytest.mark.parametrize("value", ["xfile:a.yml", "File:a.yml", "0", "true", "see file:a.yml"])
def test_other_values_are_not_includes(make_theatre: MakeTheatre, value: str) -> None:
    theatre = make_theatre(f"a: {value!r}\nb: {value}\n")
    document = load_config(_config(theatre))
    assert _node_at(document, "a").include is None
    assert _node_at(document, "b").include is None
    assert len(document.files) == 1


# --- text includes (4.3) ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "content",
    [
        "line one\nline two\n",
        "line one\r\nline two\r\n",
        "no final newline",
        "mixed\r\nends\nlone\rcr",
        "",
        "a: 1\n# looks like YAML but is text\n",
        "﻿bom and ünïcödé 😀\n",
    ],
)
def test_text_include_is_the_exact_file_text(make_theatre: MakeTheatre, content: str) -> None:
    theatre = make_theatre("prompt: file:prompt.md\n", files={".troupe/prompt.md": content})
    document = load_config(_config(theatre))
    node = _scalar_at(document, "prompt")
    assert node.value == content
    assert isinstance(node.value, str)
    assert node.value.encode("utf-8") == (theatre / ".troupe" / "prompt.md").read_bytes()
    assert node.style is None
    assert node.include is not None
    assert node.include.kind is IncludeKind.TEXT
    assert node.include.spec == "prompt.md"
    assert node.origin.file is node.include.target
    assert node.origin.span == Span(0, len(content))
    assert _directive_text(node) == "file:prompt.md"


def test_text_include_of_a_yaml_looking_file_without_yaml_suffix_is_not_parsed(
    make_theatre: MakeTheatre,
) -> None:
    # A text target is never parsed, so neither invalid YAML nor include strings inside it matter.
    theatre = make_theatre(
        "a: file:data.YML\nb: file:self.txt\n",
        files={".troupe/data.YML": "[unclosed\n", ".troupe/self.txt": "file:self.txt"},
    )
    document = load_config(_config(theatre))
    assert _scalar_at(document, "a").value == "[unclosed\n"
    assert _scalar_at(document, "b").value == "file:self.txt"


def test_non_utf8_text_target_names_the_target(make_theatre: MakeTheatre) -> None:
    theatre = make_theatre("a: file:bin.dat\n", files={".troupe/bin.dat": b"\xff\xfe\x00"})
    with pytest.raises(ConfigLoadError) as info:
        load_config(_config(theatre))
    assert info.value.file == theatre / ".troupe" / "bin.dat"
    assert info.value.exit_code is ExitCode.LOAD


# --- nested includes (4.4) -------------------------------------------------------------------


def test_nested_includes_resolve_relative_to_the_included_file(make_theatre: MakeTheatre) -> None:
    theatre = make_theatre(
        "team: file:../shared/team.yml\n",
        files={
            "shared/team.yml": "lead: file:people/lead.yaml\nmotto: file:motto.txt\n",
            "shared/people/lead.yaml": "name: alice\nbio: file:bio.txt\n",
            "shared/people/bio.txt": "Alice.\r\n",
            "shared/motto.txt": "go",
        },
    )
    document = load_config(_config(theatre))
    assert _scalar_at(document, "team", "lead", "name").value == "alice"
    assert _scalar_at(document, "team", "lead", "bio").value == "Alice.\r\n"
    assert _scalar_at(document, "team", "motto").value == "go"
    lead = _node_at(document, "team", "lead")
    assert lead.include is not None
    assert lead.include.directive.file.real_path == (theatre / "shared" / "team.yml").resolve()
    bio = _node_at(document, "team", "lead", "bio")
    assert bio.include is not None
    assert bio.include.target.path == theatre / ".troupe" / "../shared/people/bio.txt"
    real_paths = [source.real_path for source in document.files]
    assert real_paths == [
        (theatre / relative).resolve()
        for relative in (
            ".troupe/troupe.yml",
            "shared/team.yml",
            "shared/people/lead.yaml",
            "shared/people/bio.txt",
            "shared/motto.txt",
        )
    ]


def test_diamond_include_is_not_a_cycle(make_theatre: MakeTheatre) -> None:
    theatre = make_theatre(
        "a: file:left.yml\nb: file:right.yml\nc: file:common.yml\n",
        files={
            ".troupe/left.yml": "x: file:common.yml\n",
            ".troupe/right.yml": "y: file:common.yml\n",
            ".troupe/common.yml": "v: 1\n",
        },
    )
    document = load_config(_config(theatre))
    assert _scalar_at(document, "a", "x", "v").value == 1
    assert _scalar_at(document, "b", "y", "v").value == 1
    assert _scalar_at(document, "c", "v").value == 1
    real_paths = [source.real_path for source in document.files]
    assert len(real_paths) == len(set(real_paths)) == 4


def test_overrides_apply_to_included_files(make_theatre: MakeTheatre) -> None:
    theatre = make_theatre(
        "a: file:inc.yml\nt: file:note.txt\n",
        files={".troupe/inc.yml": "v: 1\n", ".troupe/note.txt": "old"},
    )
    overrides = {
        (theatre / ".troupe" / "inc.yml").resolve(): "v: 2\n",
        (theatre / ".troupe" / "note.txt").resolve(): "new\r\n",
    }
    document = load_config(_config(theatre), overrides)
    assert _scalar_at(document, "a", "v").value == 2
    assert _scalar_at(document, "t").value == "new\r\n"


# --- refusals (4.5, 4.6, 4.7) ----------------------------------------------------------------


@pytest.mark.parametrize("spec", ["missing.yml", "missing.txt", "sub/missing.yaml", "", "dir"])
def test_missing_or_non_regular_target_names_target_and_including_file(
    make_theatre: MakeTheatre, spec: str
) -> None:
    theatre = make_theatre(
        "outer:\n  inner: file:inc.yml\n",
        files={".troupe/inc.yml": f"k: 'file:{spec}'\n", ".troupe/dir/x.txt": "x"},
    )
    including = theatre / ".troupe" / "inc.yml"
    with pytest.raises(ConfigLoadError) as info:
        load_config(_config(theatre))
    assert info.value.exit_code is ExitCode.LOAD
    assert info.value.file == including
    assert info.value.path == "outer.inner.k"
    assert str(including.parent / spec) in info.value.problem
    assert str(info.value).startswith(f"{including}: outer.inner.k: ")


def test_missing_target_in_root_file(make_theatre: MakeTheatre) -> None:
    theatre = make_theatre("a: file:nope.md\n")
    config = _config(theatre)
    with pytest.raises(ConfigLoadError) as info:
        load_config(config)
    assert info.value.file == config
    assert info.value.path == "a"
    assert str(config.parent / "nope.md") in info.value.problem


def test_self_include_is_a_cycle(make_theatre: MakeTheatre) -> None:
    theatre = make_theatre("a: file:troupe.yml\n")
    config = _config(theatre)
    with pytest.raises(ConfigLoadError) as info:
        load_config(config)
    assert info.value.exit_code is ExitCode.LOAD
    assert info.value.file == config
    assert "cycle" in info.value.problem
    assert str(config) in info.value.problem


def test_indirect_cycle_names_every_file_in_the_cycle(make_theatre: MakeTheatre) -> None:
    theatre = make_theatre(
        "a: file:one.yml\n",
        files={
            ".troupe/one.yml": "b: file:../two/two.yml\n",
            "two/two.yml": "c: file:../.troupe/one.yml\n",
        },
    )
    one = theatre / ".troupe" / "one.yml"
    two = theatre / ".troupe" / "../two/two.yml"
    with pytest.raises(ConfigLoadError) as info:
        load_config(_config(theatre))
    assert info.value.exit_code is ExitCode.LOAD
    assert info.value.file is not None
    assert info.value.file.resolve() == one.resolve()
    assert "cycle" in info.value.problem
    assert str(one) in info.value.problem
    assert str(two) in info.value.problem


@pytest.mark.parametrize(
    ("content", "path"),
    [
        ("a: [unclosed\n", None),
        ("a: 1\na: 2\n", "inc.a"),
        ("a: !tag 1\n", "inc.a"),
        ("a:\n  &k b: 1\n", "inc.a.b"),
        ("? [a]\n: 1\n", "inc"),
        (b"a: \xff\n", None),
    ],
)
def test_included_file_failing_single_file_checks_names_the_included_file(
    make_theatre: MakeTheatre, content: str | bytes, path: str | None
) -> None:
    theatre = make_theatre("inc: file:sub/inc.yml\n", files={".troupe/sub/inc.yml": content})
    with pytest.raises(ConfigLoadError) as info:
        load_config(_config(theatre))
    assert info.value.exit_code is ExitCode.LOAD
    assert info.value.file == theatre / ".troupe" / "sub/inc.yml"
    assert info.value.path == path


def test_loading_with_includes_leaves_every_file_byte_identical(
    make_theatre: MakeTheatre,
) -> None:
    theatre = make_theatre(
        "a: file:../shared/a.yml\nb: file:b.txt\n",
        files={"shared/a.yml": "x: file:x.txt\r\n", "shared/x.txt": "x\r\n", ".troupe/b.txt": "b"},
    )

    def snapshot() -> dict[str, str]:
        return {
            str(path.relative_to(theatre)): hashlib.sha256(path.read_bytes()).hexdigest()
            if path.is_file()
            else "<dir>"
            for path in sorted(theatre.rglob("*"))
        }

    before = snapshot()
    load_config(_config(theatre))
    assert snapshot() == before
