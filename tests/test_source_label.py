"""Source labels of loaded files for ``config show --sources`` (config-get-show Requirement 5.4).

Every test writes a real theatre, loads it with ``load_config`` and labels the loaded
``document.files`` entries with :func:`troupe.cli.render.source_label`.
"""

from pathlib import Path

import pytest

from support import MakeTheatre
from troupe.cli.render import source_label
from troupe.config.loader import load_config
from troupe.config.model import SourceFile


def _labels(theatre: Path) -> list[str]:
    document = load_config(theatre / ".troupe" / "troupe.yml")
    return [source_label(file, theatre) for file in document.files]


def _file_named(theatre: Path, name: str) -> SourceFile:
    document = load_config(theatre / ".troupe" / "troupe.yml")
    matches = [file for file in document.files if file.path.name == name]
    assert len(matches) == 1, [file.path for file in document.files]
    return matches[0]


def test_root_file_is_labelled_relative_to_the_theatre_directory(
    make_theatre: MakeTheatre,
) -> None:
    """Req 5.4: the root file is labelled ``.troupe/troupe.yml``."""
    theatre = make_theatre("name: x\n")

    assert _labels(theatre) == [".troupe/troupe.yml"]


def test_nested_yaml_include_is_labelled_with_its_subdirectory(
    make_theatre: MakeTheatre,
) -> None:
    """Req 5.4: a YAML include in a subdirectory is labelled ``.troupe/agents/lead.yml``."""
    theatre = make_theatre(
        "lead: file:agents/lead.yml\n",
        files={".troupe/agents/lead.yml": "role: lead\n"},
    )

    assert _labels(theatre) == [".troupe/troupe.yml", ".troupe/agents/lead.yml"]


def test_dot_dot_inside_an_include_spec_is_normalised(make_theatre: MakeTheatre) -> None:
    """Req 5.4: the include spec ``sub/../x.yml`` is labelled ``.troupe/x.yml`` (lexical)."""
    theatre = make_theatre(
        "x: file:sub/../x.yml\n",
        files={".troupe/x.yml": "1\n", ".troupe/sub/keep.txt": ""},
    )

    label = source_label(_file_named(theatre, "x.yml"), theatre)

    assert label == ".troupe/x.yml"


def test_file_outside_the_theatre_directory_gets_a_leading_dot_dot(
    make_theatre: MakeTheatre, tmp_path: Path
) -> None:
    """Req 5.4: an include outside the theatre directory is labelled ``../shared/p.md``."""
    theatre = make_theatre("prompt: file:../../shared/p.md\n")
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "p.md").write_text("hello\n", encoding="utf-8")

    assert _labels(theatre) == [".troupe/troupe.yml", "../shared/p.md"]


def test_relative_and_absolute_theatre_arguments_give_identical_labels(
    make_theatre: MakeTheatre, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Req 5.4: labels do not depend on whether the theatre was given relative or absolute."""
    theatre = make_theatre(
        "lead: file:agents/lead.yml\nprompt: file:../../shared/p.md\n",
        files={".troupe/agents/lead.yml": "role: lead\n"},
    )
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "p.md").write_text("hello\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    relative = Path(theatre.name)
    assert not relative.is_absolute()

    relative_labels = _labels(relative)
    absolute_labels = _labels(theatre.absolute())

    expected = [".troupe/troupe.yml", ".troupe/agents/lead.yml", "../shared/p.md"]
    assert relative_labels == expected
    assert absolute_labels == expected


def test_symlinked_directory_is_kept_unresolved(make_theatre: MakeTheatre, tmp_path: Path) -> None:
    """Req 5.4: an include through a symlinked directory keeps the link in its label."""
    theatre = make_theatre("x: file:linked/x.yml\n")
    target = tmp_path / "elsewhere" / "real"
    target.mkdir(parents=True)
    (target / "x.yml").write_text("1\n", encoding="utf-8")
    (theatre / ".troupe" / "linked").symlink_to(target, target_is_directory=True)

    file = _file_named(theatre, "x.yml")

    assert file.real_path == (target / "x.yml").resolve()
    assert source_label(file, theatre) == ".troupe/linked/x.yml"
