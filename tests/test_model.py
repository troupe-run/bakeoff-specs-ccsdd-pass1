"""Source-mapped configuration model (Requirements 4.2, 4.3, 6.6)."""

import dataclasses
from pathlib import Path

import pytest

from troupe.config.model import (
    CollectionStyle,
    ConfigDocument,
    ConfigMapping,
    ConfigNode,
    ConfigScalar,
    ConfigSequence,
    IncludeKind,
    IncludeRef,
    LineEnding,
    MappingEntry,
    Origin,
    ScalarStyle,
    SourceFile,
    Span,
)

ROOT_TEXT = "name: demo\r\n\"list\": [1, 'x']\r\nsub: file:sub.yml\r\nnote: file:note.txt\r\n"
SUB_TEXT = "port: 8080\n"
NOTE_TEXT = "hello\r\nworld"


def _span_of(text: str, fragment: str) -> Span:
    start = text.index(fragment)
    return Span(start, start + len(fragment))


def _build_document() -> ConfigDocument:
    root_file = SourceFile(
        path=Path("theatre/.troupe/troupe.yml"),
        real_path=Path("/abs/theatre/.troupe/troupe.yml"),
        text=ROOT_TEXT,
        line_ending=LineEnding.CRLF,
    )
    sub_file = SourceFile(
        path=Path("theatre/.troupe/sub.yml"),
        real_path=Path("/abs/theatre/.troupe/sub.yml"),
        text=SUB_TEXT,
        line_ending=LineEnding.LF,
    )
    note_file = SourceFile(
        path=Path("theatre/.troupe/note.txt"),
        real_path=Path("/abs/theatre/.troupe/note.txt"),
        text=NOTE_TEXT,
        line_ending=LineEnding.CRLF,
    )

    def at(file: SourceFile, fragment: str) -> Origin:
        return Origin(file, _span_of(file.text, fragment))

    name_value = ConfigScalar(value="demo", style=ScalarStyle.PLAIN, origin=at(root_file, "demo"))
    sequence = ConfigSequence(
        items=(
            ConfigScalar(value=1, style=ScalarStyle.PLAIN, origin=at(root_file, "1")),
            ConfigScalar(value="x", style=ScalarStyle.SINGLE_QUOTED, origin=at(root_file, "'x'")),
        ),
        style=CollectionStyle.FLOW,
        origin=at(root_file, "[1, 'x']"),
    )
    sub_root = ConfigMapping(
        entries=(
            MappingEntry(
                key="port",
                key_origin=at(sub_file, "port"),
                key_style=ScalarStyle.PLAIN,
                value=ConfigScalar(
                    value=8080, style=ScalarStyle.PLAIN, origin=at(sub_file, "8080")
                ),
            ),
        ),
        style=CollectionStyle.BLOCK,
        origin=Origin(sub_file, Span(0, len(SUB_TEXT))),
        include=IncludeRef(
            kind=IncludeKind.YAML,
            spec="sub.yml",
            target=sub_file,
            directive=at(root_file, "file:sub.yml"),
        ),
    )
    note = ConfigScalar(
        value=NOTE_TEXT,
        style=None,
        origin=Origin(note_file, Span(0, len(NOTE_TEXT))),
        include=IncludeRef(
            kind=IncludeKind.TEXT,
            spec="note.txt",
            target=note_file,
            directive=at(root_file, "file:note.txt"),
        ),
    )
    root = ConfigMapping(
        entries=(
            MappingEntry("name", at(root_file, "name"), ScalarStyle.PLAIN, name_value),
            MappingEntry("list", at(root_file, '"list"'), ScalarStyle.DOUBLE_QUOTED, sequence),
            MappingEntry("sub", at(root_file, "sub"), ScalarStyle.PLAIN, sub_root),
            MappingEntry("note", at(root_file, "note"), ScalarStyle.PLAIN, note),
        ),
        style=CollectionStyle.BLOCK,
        origin=Origin(root_file, Span(0, len(ROOT_TEXT))),
    )
    return ConfigDocument(root_file=root_file, root=root, files=(root_file, sub_file, note_file))


def _text(origin: Origin) -> str:
    return origin.file.text[origin.span.start : origin.span.end]


def test_document_fields_read_back() -> None:
    document = _build_document()
    root_file, sub_file, note_file = document.files

    assert document.root_file is root_file
    assert root_file.path == Path("theatre/.troupe/troupe.yml")
    assert root_file.real_path == Path("/abs/theatre/.troupe/troupe.yml")
    assert root_file.text == ROOT_TEXT
    assert root_file.line_ending is LineEnding.CRLF
    assert sub_file.line_ending is LineEnding.LF
    assert note_file.text == NOTE_TEXT

    assert document.root.style is CollectionStyle.BLOCK
    assert document.root.include is None
    assert [entry.key for entry in document.root.entries] == ["name", "list", "sub", "note"]

    list_entry = document.root.get("list")
    assert list_entry is not None
    assert list_entry.key_style is ScalarStyle.DOUBLE_QUOTED
    assert _text(list_entry.key_origin) == '"list"'
    sequence = list_entry.value
    assert isinstance(sequence, ConfigSequence)
    assert sequence.style is CollectionStyle.FLOW
    assert _text(sequence.origin) == "[1, 'x']"
    first, second = sequence.items
    assert isinstance(first, ConfigScalar)
    assert first.value == 1
    assert isinstance(second, ConfigScalar)
    assert second.style is ScalarStyle.SINGLE_QUOTED
    assert _text(second.origin) == "'x'"

    name_entry = document.root.get("name")
    assert name_entry is not None
    assert isinstance(name_entry.value, ConfigScalar)
    assert name_entry.value.value == "demo"
    assert name_entry.value.style is ScalarStyle.PLAIN
    assert _text(name_entry.value.origin) == "demo"


def test_include_provenance_read_back() -> None:
    document = _build_document()
    root_file, sub_file, note_file = document.files

    sub_entry = document.root.get("sub")
    assert sub_entry is not None
    sub_root = sub_entry.value
    assert isinstance(sub_root, ConfigMapping)
    assert sub_root.include is not None
    assert sub_root.include.kind is IncludeKind.YAML
    assert sub_root.include.spec == "sub.yml"
    assert sub_root.include.target is sub_file
    assert sub_root.include.directive.file is root_file
    assert _text(sub_root.include.directive) == "file:sub.yml"
    assert sub_root.origin.file is sub_file
    port = sub_root.get("port")
    assert port is not None
    assert isinstance(port.value, ConfigScalar)
    assert port.value.value == 8080
    assert port.value.include is None
    assert port.value.origin.file is sub_file

    note_entry = document.root.get("note")
    assert note_entry is not None
    note = note_entry.value
    assert isinstance(note, ConfigScalar)
    assert note.style is None
    assert note.value == NOTE_TEXT
    assert note.origin == Origin(note_file, Span(0, len(NOTE_TEXT)))
    assert note.include is not None
    assert note.include.kind is IncludeKind.TEXT
    assert _text(note.include.directive) == "file:note.txt"


def test_include_kind_reports_none_for_non_include_nodes() -> None:
    document = _build_document()
    nodes: dict[str, ConfigNode] = {entry.key: entry.value for entry in document.root.entries}
    assert document.include_kind(nodes["name"]) is IncludeKind.NONE
    assert document.include_kind(nodes["list"]) is IncludeKind.NONE
    assert document.include_kind(nodes["sub"]) is IncludeKind.YAML
    assert document.include_kind(nodes["note"]) is IncludeKind.TEXT
    assert document.include_kind(document.root) is IncludeKind.NONE


def test_get_returns_none_for_missing_key() -> None:
    assert _build_document().root.get("absent") is None


def test_empty_root_mapping_shape() -> None:
    root_file = SourceFile(Path("t.yml"), Path("/t.yml"), "", LineEnding.NONE)
    root = ConfigMapping(
        entries=(), style=CollectionStyle.BLOCK, origin=Origin(root_file, Span(0, 0))
    )
    document = ConfigDocument(root_file=root_file, root=root, files=(root_file,))
    assert document.root.entries == ()
    assert document.root.get("a") is None
    assert document.root.origin.span == Span(0, 0)


def test_nodes_are_immutable() -> None:
    document = _build_document()
    with pytest.raises(dataclasses.FrozenInstanceError):
        document.root.style = CollectionStyle.FLOW  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        document.root_file.text = "changed"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        document.root.entries[0].key = "other"  # type: ignore[misc]


def test_enum_values_match_design() -> None:
    assert [member.value for member in LineEnding] == ["none", "lf", "crlf", "mixed"]
    assert [member.value for member in IncludeKind] == ["none", "yaml-include", "text-include"]
    assert [member.value for member in ScalarStyle] == [
        "plain",
        "single",
        "double",
        "literal",
        "folded",
    ]
    assert [member.value for member in CollectionStyle] == ["block", "flow"]
