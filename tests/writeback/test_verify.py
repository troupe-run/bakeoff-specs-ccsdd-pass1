"""Unit tests of the WriteVerifier (task 4.1): ``troupe.config.writeback.verify.verify_patch``.

Each case loads a real theatre configuration from disk, resolves the set with the real
TargetResolver and (unless a deliberately broken patch is under test) plans it with the real
PatchPlanner, then verifies all patches together. Every test also proves that verification changes
no file on disk.

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

from collections.abc import Mapping
from pathlib import Path

import pytest

from set_support import snapshot_tree
from troupe.config.errors import ExitCode, WriteRefusedError
from troupe.config.loader import load_config
from troupe.config.model import ConfigDocument
from troupe.config.paths import ConfigPath
from troupe.config.writeback.compare import (
    IncludeDirective,
    PlainMapping,
    node_to_plain,
    plain_equal,
    root_plain,
)
from troupe.config.writeback.planner import FilePatch, plan_patch
from troupe.config.writeback.target import ReplaceTarget, SetRequest, resolve_target
from troupe.config.writeback.value import parse_value
from troupe.config.writeback.verify import verify_patch

ROOT = "troupe.yml"
P_ROOT = "p:\n  t: file:t.txt\n  n: 1\n"


def _write(tmp_path: Path, files: Mapping[str, str | bytes]) -> None:
    for name, content in files.items():
        data = content.encode("utf-8") if isinstance(content, str) else content
        (tmp_path / name).write_bytes(data)


def _setup(
    tmp_path: Path, files: Mapping[str, str | bytes], path: str, value: str
) -> tuple[ConfigDocument, SetRequest]:
    _write(tmp_path, files)
    document = load_config(tmp_path / ROOT)
    config_path = ConfigPath.parse(path)
    return document, resolve_target(document, config_path, parse_value(value, config_path))


def _verify(
    tmp_path: Path, files: Mapping[str, str | bytes], path: str, value: str
) -> tuple[ConfigDocument, SetRequest, tuple[FilePatch, ...], ConfigDocument]:
    document, request = _setup(tmp_path, files, path, value)
    patches = plan_patch(document, request)
    before = snapshot_tree(tmp_path)
    after = verify_patch(tmp_path / ROOT, document, patches, request)
    assert snapshot_tree(tmp_path) == before
    return document, request, patches, after


def _refused(
    tmp_path: Path,
    document: ConfigDocument,
    patches: tuple[FilePatch, ...],
    request: SetRequest,
) -> WriteRefusedError:
    before = snapshot_tree(tmp_path)
    with pytest.raises(WriteRefusedError) as caught:
        verify_patch(tmp_path / ROOT, document, patches, request)
    assert snapshot_tree(tmp_path) == before
    error = caught.value
    assert error.exit_code is ExitCode.WRITE_REFUSED
    return error


# --- successful verification ---------------------------------------------------------------------


def test_correct_patch_returns_the_reloaded_document(tmp_path: Path) -> None:
    """Req 14.1: a correct patch verifies; the returned document is the patched configuration
    re-loaded (holding the patched text), not the document from before."""
    document, request, patches, after = _verify(tmp_path, {ROOT: "a: 1\nb: x\n"}, "a", "2")
    assert [patch.new_text for patch in patches] == ["a: 2\nb: x\n"]
    assert after is not document
    assert after.root_file.text == "a: 2\nb: x\n"
    assert after.root_file.real_path == (tmp_path / ROOT).resolve()
    assert node_to_plain(after.root) == {"a": 2, "b": "x"}
    assert plain_equal(node_to_plain(after.root), request.intended, ordered=True)


def test_two_patches_of_the_two_file_plan_verify_together(tmp_path: Path) -> None:
    """Req 13.6, 14.1: troupe.yml and t.txt of the 3.6 two-file plan are verified together; the
    read-back holds both patched texts."""
    _document, _request, patches, after = _verify(
        tmp_path, {ROOT: P_ROOT, "t.txt": "old\n"}, "p", "{t: new, n: 2}"
    )
    assert [patch.file.real_path.name for patch in patches] == [ROOT, "t.txt"]
    texts = {file.real_path.name: file.text for file in after.files}
    assert texts == {ROOT: "p:\n  t: file:t.txt\n  n: 2\n", "t.txt": "new"}
    p = node_to_plain(after.root)
    assert isinstance(p, dict)
    assert p["p"] == {"t": IncludeDirective("file:t.txt", "new"), "n": 2}


def test_new_include_with_an_existing_target_verifies(tmp_path: Path) -> None:
    """Req 14.1, 2.7: a newly written `file:` string reads back as an include of exactly that
    string; its target is read from disk and embedded in the returned document."""
    _document, _request, _patches, after = _verify(
        tmp_path, {ROOT: "a: 1\n", "x.yaml": "k: v\n"}, "b", "file:x.yaml"
    )
    plain = node_to_plain(after.root)
    assert isinstance(plain, dict)
    assert plain["b"] == IncludeDirective("file:x.yaml", {"k": "v"})
    assert [file.real_path.name for file in after.files] == [ROOT, "x.yaml"]


def test_unchanged_files_are_read_from_their_loaded_text_not_from_disk(tmp_path: Path) -> None:
    """Req 13.6, 14.1: every file of the document is overridden with its loaded text, so an
    unpatched include changed on disk after the load does not affect verification."""
    document, request = _setup(
        tmp_path, {ROOT: "a: 1\ninc: file:inc.yaml\n", "inc.yaml": "k: v\n"}, "a", "2"
    )
    patches = plan_patch(document, request)
    (tmp_path / "inc.yaml").write_text("k: changed\n", encoding="utf-8")
    after = verify_patch(tmp_path / ROOT, document, patches, request)
    texts = {file.real_path.name: file.text for file in after.files}
    assert texts["inc.yaml"] == "k: v\n"


# --- refusals ------------------------------------------------------------------------------------


def test_unparsable_patch_is_refused_naming_path_and_file(tmp_path: Path) -> None:
    """Req 14.2: a patched text that cannot be read back → exit 3 naming PATH, the patched file
    and the loader's problem."""
    document, request = _setup(tmp_path, {ROOT: "a: 1\n"}, "a", "2")
    broken = (FilePatch(file=document.root_file, new_text="a: [2\n"),)
    error = _refused(tmp_path, document, broken, request)
    assert error.path == "a"
    assert error.file == document.root_file.path
    assert error.problem.startswith("written value cannot be read back: ")


def test_value_mismatch_of_a_file_included_twice_is_refused(tmp_path: Path) -> None:
    """Req 14.2: the verifier compares exactly with the intended configuration it is given: when
    that holds the change at one place only, a file included at two places reads back differently
    → exit 3 naming PATH and the file. (Since step 5, ``plan_set`` widens the intended
    configuration to every place, config-include-overrides 11.2.)"""
    document, request = _setup(
        tmp_path,
        {ROOT: "a: file:x.yaml\nb: file:x.yaml\n", "x.yaml": "k: 1\n"},
        "a.k",
        "2",
    )
    patches = plan_patch(document, request)
    assert [patch.file.real_path.name for patch in patches] == ["x.yaml"]
    error = _refused(tmp_path, document, patches, request)
    assert error.path == "a.k"
    assert error.file == tmp_path / "x.yaml"
    assert "differs from the intended configuration" in error.problem


def test_key_order_mismatch_is_refused(tmp_path: Path) -> None:
    """Req 14.1, 14.2: the read-back must equal the intended configuration including key order."""
    document, request = _setup(tmp_path, {ROOT: "a: 1\nb: 2\n"}, "a", "3")
    reordered = (FilePatch(file=document.root_file, new_text="b: 2\na: 3\n"),)
    error = _refused(tmp_path, document, reordered, request)
    assert error.path == "a"
    assert error.file == document.root_file.path


def test_mismatch_names_the_first_patched_file_and_lists_the_others(tmp_path: Path) -> None:
    """Req 14.2, 13.6: a mismatch over several patches names the first patched file and mentions
    the further patched files in the problem."""
    document, request = _setup(tmp_path, {ROOT: P_ROOT, "t.txt": "old\n"}, "p", "{t: new, n: 2}")
    t_file = document.files[1]
    wrong = (
        FilePatch(file=document.root_file, new_text="p:\n  t: file:t.txt\n  n: 2\n"),
        FilePatch(file=t_file, new_text="other"),
    )
    error = _refused(tmp_path, document, wrong, request)
    assert error.file == document.root_file.path
    assert error.path == "p"
    assert str(t_file.path) in error.problem


def test_typed_duplicate_key_written_beside_an_existing_one_is_refused(tmp_path: Path) -> None:
    """Req 14.2 (2.4 note): a new plain `true` key beside `True` is a typed duplicate the loader
    refuses, so the read-back fails → exit 3 naming PATH."""
    document, request = _setup(tmp_path, {ROOT: "m:\n  True: 1\n"}, "m.true", "2")
    patches = plan_patch(document, request)
    error = _refused(tmp_path, document, patches, request)
    assert error.path == "m.true"
    assert error.file == document.root_file.path
    assert "duplicate key" in error.problem


def test_missing_new_include_target_is_refused_naming_path_and_target(tmp_path: Path) -> None:
    """Req 14.8: a newly written include whose target is missing → exit 3; the message names PATH
    and the include target."""
    document, request = _setup(tmp_path, {ROOT: "a: 1\n"}, "b", "file:missing.yaml")
    patches = plan_patch(document, request)
    error = _refused(tmp_path, document, patches, request)
    assert error.path == "b"
    assert error.file == document.root_file.path
    assert str(tmp_path / "missing.yaml") in error.problem
    assert str(tmp_path / "missing.yaml") in str(error)
    assert not (tmp_path / "missing.yaml").exists()


def test_include_cycle_of_a_new_include_is_refused_naming_path_and_target(tmp_path: Path) -> None:
    """Req 14.8: a newly written include that leads back into its own include chain → exit 3;
    the message names PATH, the cycle and the include target."""
    document, request = _setup(
        tmp_path, {ROOT: "a: file:x.yaml\n", "x.yaml": "k: 1\n"}, "a.k", "file:troupe.yml"
    )
    patches = plan_patch(document, request)
    assert [patch.file.real_path.name for patch in patches] == ["x.yaml"]
    error = _refused(tmp_path, document, patches, request)
    assert error.path == "a.k"
    assert error.file == tmp_path / "x.yaml"
    assert "include cycle" in error.problem
    assert str(tmp_path / ROOT) in error.problem


def test_unloadable_new_include_target_is_refused(tmp_path: Path) -> None:
    """Req 14.8: a newly written YAML include whose target fails to load → exit 3 naming PATH and
    the target."""
    document, request = _setup(
        tmp_path, {ROOT: "a: 1\n", "bad.yaml": "k: [1\n"}, "b", "file:bad.yaml"
    )
    patches = plan_patch(document, request)
    error = _refused(tmp_path, document, patches, request)
    assert error.path == "b"
    assert "bad.yaml" in error.problem


# --- mapping-form root include (config-include-overrides) ----------------------------------------

REF_ROOT = {ROOT: "$ref: file:base.yaml\nb: 2\n", "base.yaml": "a: 1\nb: 1\n"}


def _ref_root_request(tmp_path: Path) -> tuple[ConfigDocument, SetRequest]:
    """Set `b` to 3 in a root `$ref` file (built by hand: target resolution adopts root_plain in
    task 3.3)."""
    _write(tmp_path, REF_ROOT)
    document = load_config(tmp_path / ROOT)
    root = document.root
    assert root.include is not None
    path = ConfigPath.parse("b")
    value = parse_value("3", path)
    request = SetRequest(
        path=path,
        target=ReplaceTarget(node=root.entries[1].value, parent=root, key="b"),
        value=value,
        current={"a": 1, "b": 2},
        intended={"a": 1, "b": 3},
    )
    return document, request


def test_patched_root_ref_file_reading_back_as_intended_verifies(tmp_path: Path) -> None:
    """Req 9.2: the read-back of a file whose root is a `$ref` mapping is compared through its
    unwrapped root mapping; a patch that loads as intended is accepted."""
    document, request = _ref_root_request(tmp_path)
    patches = (FilePatch(file=document.root_file, new_text="$ref: file:base.yaml\nb: 3\n"),)
    before = snapshot_tree(tmp_path)
    after = verify_patch(tmp_path / ROOT, document, patches, request)
    assert snapshot_tree(tmp_path) == before
    assert root_plain(after) == {"a": 1, "b": 3}


@pytest.mark.parametrize(
    "new_text", ["$ref: file:base.yaml\nb: 4\n", "b: 3\n", "$ref: file:base.yaml\nb: 3\nc: 1\n"]
)
def test_patched_root_ref_file_differing_from_intended_is_refused(
    tmp_path: Path, new_text: str
) -> None:
    """Req 9.2: a patched root `$ref` file whose merged root differs from the intended
    configuration is refused with exit 3 naming PATH and the file."""
    document, request = _ref_root_request(tmp_path)
    patches = (FilePatch(file=document.root_file, new_text=new_text),)
    error = _refused(tmp_path, document, patches, request)
    assert error.path == "b"
    assert error.file == document.root_file.path
    assert "differs from the intended configuration" in error.problem


def test_value_ref_mapping_verifies_against_the_include_it_describes(tmp_path: Path) -> None:
    """Req 8.2: a VALUE `{$ref: file:..., k: v}` written as given verifies when the read-back is
    the include with the override merged; a differing override is refused (exit 3)."""
    files = {ROOT: "a: 1\n", "x.yaml": "k: 1\nj: 2\n"}
    document, request, patches, after = _verify(tmp_path, files, "b", "{$ref: file:x.yaml, k: 5}")
    assert [patch.new_text for patch in patches] == ["a: 1\nb:\n  $ref: file:x.yaml\n  k: 5\n"]
    plain = root_plain(after)
    assert plain["b"] == IncludeDirective("file:x.yaml", {"k": 5, "j": 2})
    wrong = (FilePatch(file=patches[0].file, new_text="a: 1\nb:\n  $ref: file:x.yaml\n  k: 6\n"),)
    error = _refused(tmp_path, document, wrong, request)
    assert error.path == "b"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ('{$ref: "file:a.yaml", m: "file:m.yaml"}', {"m": {"p": 1, "q": 2}}),
        ('{$ref: "file:a.yaml", m: {$ref: "file:m.yaml", r: 3}}', {"m": {"p": 1, "q": 2, "r": 3}}),
    ],
)
def test_value_ref_with_an_include_override_merged_into_a_mapping_verifies(
    tmp_path: Path, value: str, expected: PlainMapping
) -> None:
    """Req 8.2, 1.7: an include override at a key where the included file holds a mapping is merged
    into it on read-back; the write verifies (scalar-form and `$ref` override)."""
    files = {ROOT: "a: 1\n", "a.yaml": "m: {p: 1}\n", "m.yaml": "q: 2\n"}
    _document, _request, _patches, after = _verify(tmp_path, files, "x", value)
    assert root_plain(after)["x"] == IncludeDirective("file:a.yaml", expected)
