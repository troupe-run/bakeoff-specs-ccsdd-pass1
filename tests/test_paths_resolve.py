"""Dotted PATHs and their resolution (Requirements 1.5, 5.1-5.5, 6.8, 7.2, 8.3)."""

from pathlib import Path

import pytest

from support import MakeTheatre, TheatreFiles
from troupe.config.errors import ExitCode, PathNotFoundError, UsageError
from troupe.config.loader import load_config
from troupe.config.model import (
    ConfigDocument,
    ConfigMapping,
    ConfigScalar,
    ConfigSequence,
    IncludeKind,
)
from troupe.config.paths import ROOT, ConfigPath, is_index_segment, path_has_prefix
from troupe.config.resolve import WalkStop, flatten_leaves, resolve_path, walk_path


@pytest.mark.parametrize(
    ("text", "segments"),
    [
        ("a", ("a",)),
        ("a.b", ("a", "b")),
        ("agents.0.name", ("agents", "0", "name")),
        ("a b.c-d", ("a b", "c-d")),
        ("é.ü", ("é", "ü")),
    ],
)
def test_parse_splits_on_dots(text: str, segments: tuple[str, ...]) -> None:
    path = ConfigPath.parse(text)
    assert path.segments == segments
    assert str(path) == text


@pytest.mark.parametrize("text", ["a..b", ".a", "a.", ".", "..", "a.b."])
def test_parse_rejects_empty_path_or_segment_naming_path(text: str) -> None:
    with pytest.raises(UsageError) as info:
        ConfigPath.parse(text)
    error = info.value
    assert error.exit_code is ExitCode.USAGE
    assert error.path == text
    assert error.file is None
    assert text in str(error)


def test_parse_rejects_empty_path_naming_it_explicitly() -> None:
    with pytest.raises(UsageError) as info:
        ConfigPath.parse("")
    error = info.value
    assert error.exit_code is ExitCode.USAGE
    assert error.path is None
    assert error.file is None
    message = str(error)
    assert "''" in message
    assert "PATH" in message
    assert not message.startswith(":")


@pytest.mark.parametrize(
    ("segment", "expected"),
    [
        ("0", True),
        ("12", True),
        ("007", True),
        ("", False),
        ("-1", False),
        ("+1", False),
        ("1a", False),
        ("a", False),
        (" 1", False),
        ("1.0", False),
        ("\u0661", False),  # ARABIC-INDIC DIGIT ONE
        ("²", False),
    ],
)
def test_is_index_segment(segment: str, expected: bool) -> None:
    assert is_index_segment(segment) is expected


def test_child_builds_paths_from_keys_and_indices() -> None:
    path = ROOT.child("agents").child(0).child("name")
    assert path.segments == ("agents", "0", "name")
    assert str(path) == "agents.0.name"
    assert path == ConfigPath.parse("agents.0.name")


def test_root_is_empty_and_child_does_not_mutate() -> None:
    assert ROOT.segments == ()
    assert str(ROOT) == ""
    parent = ConfigPath.parse("a")
    child = parent.child("b")
    assert parent.segments == ("a",)
    assert child.segments == ("a", "b")


def test_config_path_is_hashable_value() -> None:
    assert {ConfigPath.parse("a.b"), ConfigPath.parse("a.b")} == {ConfigPath(("a", "b"))}


@pytest.mark.parametrize(
    ("path", "prefix", "expected"),
    [
        ("a", "a", True),
        ("a.b", "a", True),
        ("a.b.c", "a", True),
        ("a.b.c", "a.b", True),
        ("ab", "a", False),
        ("ab.c", "a", False),
        ("a", "a.b", False),
        ("b.a", "a", False),
        ("list.10", "list.1", False),
        ("list.1", "list.1", True),
    ],
)
def test_path_has_prefix(path: str, prefix: str, expected: bool) -> None:
    assert path_has_prefix(path, prefix) is expected


# --- Resolve: walk_path, resolve_path, flatten_leaves (5.1-5.5, 6.8, 7.2) ----------------------


def _load(make_theatre: MakeTheatre, config: str, files: TheatreFiles | None = None) -> Path:
    theatre = make_theatre(config, files=files)
    return theatre / ".troupe" / "troupe.yml"


_WALK_CONFIG = (
    "name: troupe\n"
    "agents: file:agents.yml\n"
    "prompt: file:prompt.md\n"
    "tags: [a, b]\n"
    "empty_list: []\n"
    "nothing: null\n"
)
_WALK_FILES: TheatreFiles = {
    ".troupe/agents.yml": "alice:\n  role: lead\n  skills:\n    - sing\n    - dance\nbob: 2\n",
    ".troupe/prompt.md": "Hello\nworld\n",
}


@pytest.fixture
def walk_document(make_theatre: MakeTheatre) -> ConfigDocument:
    return load_config(_load(make_theatre, _WALK_CONFIG, _WALK_FILES))


def test_walk_complete_through_include_reports_every_node(walk_document: ConfigDocument) -> None:
    path = ConfigPath.parse("agents.alice.skills.1")
    walk = walk_path(walk_document.root, path)
    assert walk.stop is WalkStop.COMPLETE
    assert walk.path == path
    assert walk.consumed == 4
    assert len(walk.nodes) == 5
    assert walk.nodes[0] is walk_document.root
    assert isinstance(walk.deepest, ConfigScalar)
    assert walk.deepest is walk.nodes[-1]
    assert walk.deepest.value == "dance"
    assert walk.deepest.origin.file.path.name == "agents.yml"


def test_walk_root_path_is_complete_at_root(walk_document: ConfigDocument) -> None:
    walk = walk_path(walk_document.root, ROOT)
    assert walk.stop is WalkStop.COMPLETE
    assert walk.consumed == 0
    assert walk.nodes == (walk_document.root,)


@pytest.mark.parametrize(
    ("text", "stop", "consumed", "deepest_file"),
    [
        ("missing", WalkStop.MISSING_KEY, 0, "troupe.yml"),
        ("agents.carol", WalkStop.MISSING_KEY, 1, "agents.yml"),
        ("agents.alice.nope.deeper", WalkStop.MISSING_KEY, 2, "agents.yml"),
        ("agents.alice.skills.2", WalkStop.INDEX_OUT_OF_RANGE, 3, "agents.yml"),
        ("tags.5", WalkStop.INDEX_OUT_OF_RANGE, 1, "troupe.yml"),
        ("empty_list.0", WalkStop.INDEX_OUT_OF_RANGE, 1, "troupe.yml"),
        ("tags.first", WalkStop.NOT_AN_INDEX, 1, "troupe.yml"),
        ("tags.-1", WalkStop.NOT_AN_INDEX, 1, "troupe.yml"),
        ("name.x", WalkStop.SCALAR, 1, "troupe.yml"),
        ("nothing.x", WalkStop.SCALAR, 1, "troupe.yml"),
        ("agents.bob.0", WalkStop.SCALAR, 2, "agents.yml"),
        ("prompt.line", WalkStop.SCALAR, 1, "prompt.md"),
        ("prompt.0", WalkStop.SCALAR, 1, "prompt.md"),
        ("0", WalkStop.MISSING_KEY, 0, "troupe.yml"),
        ("agents.0", WalkStop.MISSING_KEY, 1, "agents.yml"),
    ],
)
def test_walk_stop_reasons(
    walk_document: ConfigDocument,
    text: str,
    stop: WalkStop,
    consumed: int,
    deepest_file: str,
) -> None:
    walk = walk_path(walk_document.root, ConfigPath.parse(text))
    assert walk.stop is stop
    assert walk.consumed == consumed
    assert len(walk.nodes) == consumed + 1
    assert walk.deepest.origin.file.path.name == deepest_file


def test_walk_stop_values() -> None:
    assert {stop.value for stop in WalkStop} == {
        "complete",
        "missing-key",
        "index-out-of-range",
        "not-an-index",
        "scalar",
    }


def test_resolve_returns_node_and_include_is_transparent(walk_document: ConfigDocument) -> None:
    role = resolve_path(walk_document, ConfigPath.parse("agents.alice.role"))
    assert isinstance(role, ConfigScalar)
    assert role.value == "lead"
    prompt = resolve_path(walk_document, ConfigPath.parse("prompt"))
    assert isinstance(prompt, ConfigScalar)
    assert prompt.value == "Hello\nworld\n"
    agents = resolve_path(walk_document, ConfigPath.parse("agents"))
    assert isinstance(agents, ConfigMapping)
    tags = resolve_path(walk_document, ConfigPath.parse("tags"))
    assert isinstance(tags, ConfigSequence)
    item = resolve_path(walk_document, ConfigPath.parse("tags.1"))
    assert isinstance(item, ConfigScalar)
    assert item.value == "b"


@pytest.mark.parametrize(
    ("text", "deepest_file"),
    [
        ("missing", "troupe.yml"),
        ("tags.9", "troupe.yml"),
        ("tags.x", "troupe.yml"),
        ("name.x", "troupe.yml"),
        ("agents.carol", "agents.yml"),
        ("agents.alice.skills.7", "agents.yml"),
        ("agents.alice.role.x", "agents.yml"),
        ("prompt.x", "prompt.md"),
    ],
)
def test_resolve_not_found_names_deepest_file_and_path(
    walk_document: ConfigDocument, text: str, deepest_file: str
) -> None:
    with pytest.raises(PathNotFoundError) as info:
        resolve_path(walk_document, ConfigPath.parse(text))
    error = info.value
    assert error.exit_code is ExitCode.PATH
    assert error.path == text
    assert isinstance(error.file, Path)
    assert error.file.name == deepest_file
    expected_file = next(f.path for f in walk_document.files if f.path.name == deepest_file)
    assert error.file == expected_file
    message = str(error)
    assert message.startswith(f"{expected_file}: {text}: ")


def test_resolve_not_found_in_nested_include_names_innermost_file(
    make_theatre: MakeTheatre,
) -> None:
    config = _load(
        make_theatre,
        "outer: file:outer.yml\n",
        {
            ".troupe/outer.yml": "inner: file:sub/inner.yml\n",
            ".troupe/sub/inner.yml": "key: 1\n",
        },
    )
    document = load_config(config)
    with pytest.raises(PathNotFoundError) as info:
        resolve_path(document, ConfigPath.parse("outer.inner.other"))
    assert info.value.file == config.parent / "sub" / "inner.yml"
    assert info.value.path == "outer.inner.other"
    with pytest.raises(PathNotFoundError) as info:
        resolve_path(document, ConfigPath.parse("outer.missing"))
    assert info.value.file == config.parent / "outer.yml"


def test_flatten_leaves_in_document_order_with_nested_includes_and_lists(
    make_theatre: MakeTheatre,
) -> None:
    config = _load(
        make_theatre,
        "title: show\n"
        "cast: file:cast.yml\n"
        "empty_map: {}\n"
        "empty_list: []\n"
        "notes: file:notes.txt\n"
        "flags:\n"
        "  - true\n"
        "  - null\n"
        "  - [1, 2.5]\n"
        "  - {deep: ~}\n"
        "last:\n",
        {
            ".troupe/cast.yml": (
                "lead:\n  name: Ann\n  lines: file:lines.yml\nchorus: []\nextra: {}\nsize: 0x10\n"
            ),
            ".troupe/lines.yml": "- first\n- second: file:../notes.txt\n",
            ".troupe/notes.txt": "note text\n",
            "notes.txt": "outer note",
        },
    )
    document = load_config(config)
    leaves = flatten_leaves(document.root)
    assert [(str(leaf.path), leaf.value) for leaf in leaves] == [
        ("title", "show"),
        ("cast.lead.name", "Ann"),
        ("cast.lead.lines.0", "first"),
        ("cast.lead.lines.1.second", "outer note"),
        ("cast.size", 16),
        ("notes", "note text\n"),
        ("flags.0", True),
        ("flags.1", None),
        ("flags.2.0", 1),
        ("flags.2.1", 2.5),
        ("flags.3.deep", None),
        ("last", None),
    ]
    for leaf in leaves:
        assert isinstance(leaf.node, ConfigScalar)
        assert leaf.value == leaf.node.value
        assert resolve_path(document, leaf.path) is leaf.node
    notes = leaves[5].node
    assert notes.include is not None
    assert notes.include.kind is IncludeKind.TEXT
    assert notes.origin.file.path.name == "notes.txt"


def test_flatten_leaves_of_empty_configuration_is_empty(make_theatre: MakeTheatre) -> None:
    document = load_config(_load(make_theatre, "# nothing here\n"))
    assert flatten_leaves(document.root) == ()
    document = load_config(_load(make_theatre, "a: {}\nb: []\nc: {d: []}\n"))
    assert flatten_leaves(document.root) == ()
