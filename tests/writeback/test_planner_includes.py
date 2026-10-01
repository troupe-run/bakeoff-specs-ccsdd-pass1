"""Unit tests of the PatchPlanner for includes inside replaced collections and multi-file plans
(task 3.6): ``troupe.config.writeback.planner.plan_patch``.

Each case loads a real theatre configuration (root file and included files on disk), resolves the
set with the real TargetResolver and plans the patches. Every patch's new text is compared
byte-exactly, the patches come in the order the files appear in the loaded document (root file
first), and all patched texts re-loaded together (``load_config`` with overrides) read back as the
intended configuration (including key order).

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

from collections.abc import Mapping
from pathlib import Path

import pytest

from troupe.config.errors import ExitCode, WriteRefusedError
from troupe.config.loader import load_config
from troupe.config.paths import ConfigPath
from troupe.config.writeback.compare import node_to_plain, plain_equal
from troupe.config.writeback.planner import plan_patch
from troupe.config.writeback.target import ReplaceTarget, resolve_target
from troupe.config.writeback.value import parse_value

ROOT = "troupe.yml"


def _write(tmp_path: Path, files: Mapping[str, str | bytes]) -> None:
    for name, content in files.items():
        data = content.encode("utf-8") if isinstance(content, str) else content
        (tmp_path / name).write_bytes(data)


def _patches(
    tmp_path: Path, files: Mapping[str, str | bytes], path: str, value: str
) -> list[tuple[str, str]]:
    """Plan the set; return ``(file name, new text)`` per patch, in the planner's order.

    Also proves that the target is an existing value, that every patch changes its file, that no
    file is patched twice, that the order is the loaded document's file order and that all patched
    texts together re-load as the intended configuration (including key order).
    """
    _write(tmp_path, files)
    document = load_config(tmp_path / ROOT)
    config_path = ConfigPath.parse(path)
    request = resolve_target(document, config_path, parse_value(value, config_path))
    assert isinstance(request.target, ReplaceTarget)
    patches = plan_patch(document, request)
    real_paths = [patch.file.real_path for patch in patches]
    assert len(set(real_paths)) == len(real_paths)
    file_order = [file.real_path for file in document.files]
    assert real_paths == sorted(real_paths, key=file_order.index)
    for patch in patches:
        assert patch.new_text != patch.file.text
    reloaded = load_config(
        tmp_path / ROOT, {patch.file.real_path: patch.new_text for patch in patches}
    )
    assert plain_equal(node_to_plain(reloaded.root), request.intended, ordered=True)
    return [(patch.file.real_path.name, patch.new_text) for patch in patches]


def _refused(
    tmp_path: Path, files: Mapping[str, str | bytes], path: str, value: str
) -> WriteRefusedError:
    _write(tmp_path, files)
    document = load_config(tmp_path / ROOT)
    config_path = ConfigPath.parse(path)
    request = resolve_target(document, config_path, parse_value(value, config_path))
    with pytest.raises(WriteRefusedError) as caught:
        plan_patch(document, request)
    assert caught.value.exit_code is ExitCode.WRITE_REFUSED
    return caught.value


AGENTS = "agents:\n  coder: file:coder.yaml\n  lead: x\n"
P_ROOT = "p:\n  t: file:t.txt\n  n: 1\n"


# --- YAML include roots inside a replaced collection ---------------------------------------------


def test_yaml_include_root_changing_inside_a_merge_names_the_nested_path(tmp_path: Path) -> None:
    """Req 5.8: the nested include root `agents.coder` would change → exit 3 naming that nested
    path (not PATH) and the included file coder.yaml."""
    error = _refused(
        tmp_path,
        {ROOT: AGENTS, "coder.yaml": "model: y\n"},
        "agents",
        "{coder: {model: z}, lead: x}",
    )
    assert error.path == "agents.coder"
    assert error.file == tmp_path / "coder.yaml"
    assert "file:coder.yaml" in error.problem


def test_yaml_include_root_in_a_list_merge_names_the_item_path(tmp_path: Path) -> None:
    """Req 5.8: an include root at list index 1 inside a merged list is named `l.1`."""
    error = _refused(
        tmp_path,
        {ROOT: "l:\n  - a\n  - file:item.yaml\n", "item.yaml": "k: 1\n"},
        "l",
        "[a, {k: 2}]",
    )
    assert error.path == "l.1"
    assert error.file == tmp_path / "item.yaml"


def test_yaml_include_root_deep_inside_nested_merges_names_the_full_path(tmp_path: Path) -> None:
    """Req 5.8: the path of the include root is built through every merged level
    (`a.b.0.c`)."""
    error = _refused(
        tmp_path,
        {ROOT: "a:\n  b:\n    - c: file:c.yaml\n      d: 1\n", "c.yaml": "k: 1\n"},
        "a",
        "{b: [{c: {k: 2}, d: 1}]}",
    )
    assert error.path == "a.b.0.c"
    assert error.file == tmp_path / "c.yaml"


def test_yaml_include_root_at_path_still_names_path(tmp_path: Path) -> None:
    """Req 2.5: an include root at PATH itself is named by PATH."""
    error = _refused(
        tmp_path, {ROOT: AGENTS, "coder.yaml": "model: y\n"}, "agents.coder", "{model: z}"
    )
    assert error.path == "agents.coder"
    assert error.file == tmp_path / "coder.yaml"


def test_nested_text_include_with_a_non_string_names_path_and_target(tmp_path: Path) -> None:
    """Req 2.4: a nested text include set to a non-string is refused naming PATH and t.txt."""
    error = _refused(tmp_path, {ROOT: P_ROOT, "t.txt": "old\n"}, "p", "{t: 5, n: 1}")
    assert error.path == "p"
    assert error.file == tmp_path / "t.txt"


def test_unchanged_yaml_include_inside_a_merge_is_untouched(tmp_path: Path) -> None:
    """Req 5.8, 2.8, 13.4: an include root set to its own `file:` string is unchanged, so it is not
    refused and coder.yaml gets no patch; only `lead` changes in the root file."""
    patches = _patches(
        tmp_path,
        {ROOT: AGENTS, "coder.yaml": "model: y\n"},
        "agents",
        "{coder: file:coder.yaml, lead: z}",
    )
    assert patches == [(ROOT, "agents:\n  coder: file:coder.yaml\n  lead: z\n")]


def test_dropped_yaml_include_removes_only_the_directive_entry(tmp_path: Path) -> None:
    """Req 5.9: dropping `coder` removes only its entry from troupe.yml; coder.yaml is not
    patched."""
    patches = _patches(tmp_path, {ROOT: AGENTS, "coder.yaml": "model: y\n"}, "agents", "{lead: y}")
    assert patches == [(ROOT, "agents:\n  lead: y\n")]


def test_dropped_text_include_removes_only_the_directive_entry(tmp_path: Path) -> None:
    """Req 5.9: dropping the text include `t` removes only its entry; t.txt is not patched."""
    patches = _patches(tmp_path, {ROOT: P_ROOT, "t.txt": "old\n"}, "p", "{n: 1}")
    assert patches == [(ROOT, "p:\n  n: 1\n")]


# --- text includes inside a replaced collection ---------------------------------------------------


def test_nested_text_include_of_a_crlf_target_patches_only_the_target(tmp_path: Path) -> None:
    """Req 2.2, 2.3: the nested text include's content becomes VALUE with CRLF breaks; the root file
    (with `n` unchanged) gets no patch."""
    patches = _patches(tmp_path, {ROOT: P_ROOT, "t.txt": b"old\r\n"}, "p", '{t: "new\\n", n: 1}')
    assert patches == [("t.txt", "new\r\n")]


def test_nested_text_include_of_an_lf_target_patches_only_the_target(tmp_path: Path) -> None:
    """Req 2.2: an LF target gets VALUE exactly."""
    patches = _patches(tmp_path, {ROOT: P_ROOT, "t.txt": "old\n"}, "p", '{t: "new\\n", n: 1}')
    assert patches == [("t.txt", "new\n")]


# --- multi-file plans (product-owner decision B) --------------------------------------------------


def test_two_file_plan_patches_root_then_text_target(tmp_path: Path) -> None:
    """Req 13.6, 2.2: a set that must change two files yields exactly two patches, root file first,
    with byte-exact new texts."""
    patches = _patches(tmp_path, {ROOT: P_ROOT, "t.txt": "old\n"}, "p", "{t: new, n: 2}")
    assert patches == [(ROOT, "p:\n  t: file:t.txt\n  n: 2\n"), ("t.txt", "new")]


def test_multi_file_plan_follows_document_file_order(tmp_path: Path) -> None:
    """Req 13.6: patches follow the loaded document's file order (root, then includes in read
    order), here root, b.txt, a.txt."""
    root = "p:\n  first: file:b.txt\n  second: file:a.txt\n  n: 1\n"
    patches = _patches(
        tmp_path,
        {ROOT: root, "a.txt": "a\n", "b.txt": "b\n"},
        "p",
        "{first: B, second: A, n: 2}",
    )
    assert patches == [
        (ROOT, "p:\n  first: file:b.txt\n  second: file:a.txt\n  n: 2\n"),
        ("b.txt", "B"),
        ("a.txt", "A"),
    ]


def test_merge_inside_a_yaml_included_file_patches_it_and_its_text_include(
    tmp_path: Path,
) -> None:
    """Req 13.6, 2.2, 2.6: PATH inside a YAML-included file whose merge changes a value there and a
    text include patches inc.yaml then t.txt; the root file gets no patch."""
    files: dict[str, str | bytes] = {
        ROOT: "x: file:inc.yaml\n",
        "inc.yaml": "sub:\n  k: 1  # keep\n  t: file:t.txt\n",
        "t.txt": "old\n",
    }
    patches = _patches(tmp_path, files, "x.sub", '{k: 22, t: "new\\n"}')
    assert patches == [
        ("inc.yaml", "sub:\n  k: 22 # keep\n  t: file:t.txt\n"),
        ("t.txt", "new\n"),
    ]


def test_text_include_used_twice_with_the_same_new_content_gives_one_patch(
    tmp_path: Path,
) -> None:
    """Req 13.6: one file included at two places and set to the same content at both is patched
    once (never two patches for the same file)."""
    root = "p:\n  a: file:t.txt\n  b: file:t.txt\n"
    patches = _patches(tmp_path, {ROOT: root, "t.txt": "old\n"}, "p", "{a: new, b: new}")
    assert patches == [("t.txt", "new")]


def test_text_include_used_twice_with_different_contents_is_refused(tmp_path: Path) -> None:
    """Req 13.6, 14.9: one file that would have to get two different contents is refused naming
    PATH and the file."""
    root = "p:\n  a: file:t.txt\n  b: file:t.txt\n"
    error = _refused(tmp_path, {ROOT: root, "t.txt": "old\n"}, "p", "{a: one, b: two}")
    assert error.path == "p"
    assert error.file == tmp_path / "t.txt"
