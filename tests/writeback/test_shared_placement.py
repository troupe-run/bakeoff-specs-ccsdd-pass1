"""Unit tests of new keys written in an including file that is itself included at several places
(config-include-overrides task 5.2, requirements choice 17, research.md Decision 11).

The placement walk writes a new key into the file holding the include reference (choice 5). When
that written mapping or include site is reached at more than one path of the merged configuration,
the key appears at every such path, like any other content of the file. ``plan_set`` therefore
gives an intended configuration holding the same new keys at every other such path, in merged
position (after that layer's keys, before the outer override-only keys of each place), and the
strict ordered read-back (``verify_patch``) accepts the write. Changed existing values, the
``--in-include`` placement and the step 1 rule for list items keep the step 1 shared-file refusal
(config-set-writeback 14.2).

Each case loads a real theatre configuration from disk, resolves the set with the real
TargetResolver, plans it, and verifies the patched texts with the real read-back verifier against
the planned intended configuration.

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

import dataclasses
from collections.abc import Mapping
from pathlib import Path

import pytest

from troupe.config.errors import ExitCode, WriteRefusedError
from troupe.config.loader import load_config
from troupe.config.paths import ConfigPath
from troupe.config.writeback.compare import IncludeDirective, PlainMapping, PlainValue, root_plain
from troupe.config.writeback.planner import plan_set
from troupe.config.writeback.target import resolve_target
from troupe.config.writeback.value import parse_value
from troupe.config.writeback.verify import verify_patch

ROOT = "troupe.yml"
TWICE = "a: file:mid.yaml\nb: file:mid.yaml\n"
LEAF = "sub:\n  m: 1\n"


def _set(
    tmp_path: Path, files: Mapping[str, str], path: str, value: str, *, in_include: bool = False
) -> tuple[dict[str, str], PlainMapping]:
    """Plan and verify a set; return each patched file's name and new text, and the read-back
    plain root (which equals the planned intended configuration, in order)."""
    for name, text in files.items():
        (tmp_path / name).write_bytes(text.encode("utf-8"))
    document = load_config(tmp_path / ROOT)
    config_path = ConfigPath.parse(path)
    request = resolve_target(
        document, config_path, parse_value(value, config_path), in_include=in_include
    )
    plan = plan_set(document, request)
    after = verify_patch(
        tmp_path / ROOT,
        document,
        plan.patches,
        dataclasses.replace(request, intended=plan.intended),
    )
    return {patch.file.real_path.name: patch.new_text for patch in plan.patches}, root_plain(after)


def _at(plain: PlainValue, path: str) -> PlainValue:
    """The plain value at ``path``, include directives unwrapped to their content."""
    for segment in path.split("."):
        if isinstance(plain, IncludeDirective):
            assert plain.content is not None
            plain = plain.content
        assert isinstance(plain, dict)
        plain = plain[segment]
    if isinstance(plain, IncludeDirective):
        assert plain.content is not None
        return plain.content
    return plain


def _keys(plain: PlainValue, path: str) -> list[str]:
    value = _at(plain, path)
    assert isinstance(value, dict)
    return list(value)


@pytest.mark.parametrize("path", ["a.sub.n", "b.sub.n"])
def test_shared_bare_middle_file_gets_the_key_once_and_shows_it_at_both_places(
    tmp_path: Path, path: str
) -> None:
    """Req 4.8, 4.2, 9.2: mid.yaml (`$ref: file:leaf.yaml`) is included as `a` and `b`; the new
    key is written once in mid.yaml's `$ref` mapping, and the read-back holds it under both
    `a.sub` and `b.sub`."""
    files = {ROOT: TWICE, "mid.yaml": "$ref: file:leaf.yaml\n", "leaf.yaml": LEAF}
    patched, after = _set(tmp_path, files, path, "1")
    assert patched == {"mid.yaml": "$ref: file:leaf.yaml\nsub:\n  n: 1\n"}
    assert _at(after, "a.sub") == {"m": 1, "n": 1}
    assert _at(after, "b.sub") == {"m": 1, "n": 1}


def test_shared_scalar_root_middle_file_is_converted_once(tmp_path: Path) -> None:
    """Req 4.8, 4.2, 5.1, 9.2: mid.yaml is the scalar `file:leaf.yaml`, included as `a` and `b`;
    its root is converted to `$ref` form holding the override once, visible at both places."""
    files = {ROOT: TWICE, "mid.yaml": "file:leaf.yaml\n", "leaf.yaml": LEAF}
    patched, after = _set(tmp_path, files, "a.sub.n", "1")
    assert patched == {"mid.yaml": "$ref: file:leaf.yaml\nsub:\n  n: 1\n"}
    assert _at(after, "a.sub") == {"m": 1, "n": 1}
    assert _at(after, "b.sub") == {"m": 1, "n": 1}


def test_shared_file_with_a_nested_include_converts_it_once(tmp_path: Path) -> None:
    """Req 4.8, 4.2, 5.1, 9.2: mid.yaml holds `coder: file:leaf.yaml` and is included as `a` and
    `b`; the nested include is converted once in mid.yaml and both places gain the key."""
    files = {ROOT: TWICE, "mid.yaml": "coder: file:leaf.yaml\n", "leaf.yaml": "m: 1\n"}
    patched, after = _set(tmp_path, files, "a.coder.t", "0.3")
    assert patched == {"mid.yaml": "coder:\n  $ref: file:leaf.yaml\n  t: 0.3\n"}
    assert _at(after, "a.coder") == {"m": 1, "t": 0.3}
    assert _at(after, "b.coder") == {"m": 1, "t": 0.3}


OWN_OVERRIDES = "a: file:mid.yaml\nb:\n  $ref: file:mid.yaml\n  sub:\n    x: 2\n  y: 3\n"
"""`b` includes the shared mid.yaml with overrides of its own, nested (`sub.x`) and top-level."""


def test_place_with_own_overrides_lists_the_key_in_merged_order(tmp_path: Path) -> None:
    """Req 4.8, 1.3, 9.2: at `b`, whose own overrides add `sub.x` and `y`, the key from mid.yaml
    comes after the included keys and before `b`'s override-only key (merged order)."""
    files = {ROOT: OWN_OVERRIDES, "mid.yaml": "$ref: file:leaf.yaml\n", "leaf.yaml": LEAF}
    patched, after = _set(tmp_path, files, "a.sub.n", "1")
    assert patched == {"mid.yaml": "$ref: file:leaf.yaml\nsub:\n  n: 1\n"}
    assert _keys(after, "a.sub") == ["m", "n"]
    assert _keys(after, "b.sub") == ["m", "n", "x"]
    assert _keys(after, "b") == ["sub", "y"]


def test_top_level_key_at_a_place_with_own_overrides_precedes_them(tmp_path: Path) -> None:
    """Req 4.8, 1.3, 9.2: a new top-level key of mid.yaml comes, at `b`, after the included keys
    and before `b`'s override-only key `y`."""
    files = {ROOT: OWN_OVERRIDES, "mid.yaml": "$ref: file:leaf.yaml\n", "leaf.yaml": LEAF}
    patched, after = _set(tmp_path, files, "a.extra", "1")
    assert patched == {"mid.yaml": "$ref: file:leaf.yaml\nextra: 1\n"}
    assert _keys(after, "a") == ["sub", "extra"]
    assert _keys(after, "b") == ["sub", "extra", "y"]
    assert _at(after, "b.extra") == 1


def test_new_keys_of_a_collection_replacement_appear_at_every_place(tmp_path: Path) -> None:
    """Req 4.8, 7.3, 9.2: a mapping VALUE whose new key is placed in the shared mid.yaml shows
    it at both places."""
    files = {ROOT: TWICE, "mid.yaml": "$ref: file:leaf.yaml\n", "leaf.yaml": LEAF}
    patched, after = _set(tmp_path, files, "a.sub", "{m: 1, n: 1}")
    assert patched == {"mid.yaml": "$ref: file:leaf.yaml\nsub:\n  n: 1\n"}
    assert _at(after, "b.sub") == {"m": 1, "n": 1}


def _refused(
    tmp_path: Path, files: Mapping[str, str], path: str, value: str, *, in_include: bool = False
) -> WriteRefusedError:
    with pytest.raises(WriteRefusedError) as raised:
        _set(tmp_path, files, path, value, in_include=in_include)
    error = raised.value
    assert error.exit_code is ExitCode.WRITE_REFUSED
    assert error.path == path
    assert "differs from the intended configuration" in error.problem
    return error


def test_existing_value_in_a_shared_file_is_still_refused(tmp_path: Path) -> None:
    """Req 4.8, 9.2 (config-set-writeback 14.2): changing an existing value written in the
    shared mid.yaml keeps the step 1 refusal."""
    files = {ROOT: TWICE, "mid.yaml": "$ref: file:leaf.yaml\nsub:\n  n: 1\n", "leaf.yaml": LEAF}
    error = _refused(tmp_path, files, "a.sub.n", "2")
    assert error.file == tmp_path / "mid.yaml"


def test_in_include_into_a_shared_file_is_still_refused(tmp_path: Path) -> None:
    """Req 4.8, 10.1, 9.2: `--in-include` writes into the included side (leaf.yaml, shared by
    both places through mid.yaml) and keeps the step 1 refusal."""
    files = {ROOT: TWICE, "mid.yaml": "$ref: file:leaf.yaml\n", "leaf.yaml": LEAF}
    error = _refused(tmp_path, files, "a.sub.n", "1", in_include=True)
    assert error.file == tmp_path / "leaf.yaml"


def test_new_key_in_a_list_item_of_a_shared_file_is_still_refused(tmp_path: Path) -> None:
    """Req 4.8, 6.2, 9.2: a new key in a list item of a shared included file follows the step 1
    rule (not the override rule) and keeps the step 1 refusal."""
    files = {ROOT: TWICE, "mid.yaml": "items:\n  - k: 1\n"}
    error = _refused(tmp_path, files, "a.items.0.j", "2")
    assert error.file == tmp_path / "mid.yaml"
