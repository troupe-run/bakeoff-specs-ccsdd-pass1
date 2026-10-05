"""Unit tests of writes into a file included at more than one place (config-include-overrides
tasks 5.2 and 6.1, Requirements 4.8 and 11, requirements choices 17 and 18, research.md Decisions
11 and 12).

The placement rules may direct a change into a file reached at more than one path of the merged
configuration: a new key placed by the override rule (choice 17), an existing value changed where
it lives, a key written by ``--in-include`` or into a list item, a list append, a removal in a
collection replacement, or a text-include target replaced (choice 18). The change is written once
and appears at every such path, merged with that place's own overrides (an outer override still
wins there, and new keys come in merged position). ``plan_set`` therefore gives an intended
configuration holding the change at every place, and the strict ordered read-back
(``verify_patch``) accepts the write.

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
from troupe.config.model import ConfigDocument, ConfigMapping
from troupe.config.paths import ConfigPath
from troupe.config.writeback.compare import (
    IncludeDirective,
    PlainMapping,
    PlainValue,
    node_to_plain,
    root_plain,
)
from troupe.config.writeback.planner import plan_set
from troupe.config.writeback.shared import WrittenChanges, rebuilt_plain, with_other_places
from troupe.config.writeback.target import SetRequest, resolve_target
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


# --- every written change at every place (task 6.1, choice 18, Requirement 11) ---------------


def test_existing_value_in_a_shared_file_is_changed_at_both_places(tmp_path: Path) -> None:
    """Req 11.1, 11.2, 9.2: an existing value written in the shared mid.yaml changes where it
    lives (rule 1), once, and the read-back shows the new value under both `a.sub` and `b.sub`."""
    files = {ROOT: TWICE, "mid.yaml": "$ref: file:leaf.yaml\nsub:\n  n: 1\n", "leaf.yaml": LEAF}
    patched, after = _set(tmp_path, files, "a.sub.n", "2")
    assert patched == {"mid.yaml": "$ref: file:leaf.yaml\nsub:\n  n: 2\n"}
    assert _at(after, "a.sub") == {"m": 1, "n": 2}
    assert _at(after, "b.sub") == {"m": 1, "n": 2}


SHADOWED = "a: file:mid.yaml\nb:\n  $ref: file:mid.yaml\n  k: 9\n"
"""`b` overrides `k` of the shared mid.yaml; `a` includes it as written."""


def test_existing_value_overridden_at_one_place_keeps_that_override(tmp_path: Path) -> None:
    """Req 11.2, 6.1 (choice 18): changing `k` in the shared mid.yaml changes `a.k`; at `b` the
    outer override `k: 9` still wins, so `b` is unchanged."""
    files = {ROOT: SHADOWED, "mid.yaml": "k: 1\nj: 2\n"}
    patched, after = _set(tmp_path, files, "a.k", "5")
    assert patched == {"mid.yaml": "k: 5\nj: 2\n"}
    assert _at(after, "a") == {"k": 5, "j": 2}
    assert _at(after, "b") == {"k": 9, "j": 2}


@pytest.mark.parametrize(
    ("value", "written", "expected"),
    [
        pytest.param(
            '"two\\nlines\\n"', "k: |\n  two\n  lines\nj: 2\n", "two\nlines\n", id="block"
        ),
        pytest.param("{x: 1}", "k:\n  x: 1\nj: 2\n", {"x": 1}, id="kind"),
    ],
)
def test_existing_value_changed_in_kind_or_form_in_a_shared_file(
    tmp_path: Path, value: str, written: str, expected: PlainValue
) -> None:
    """Req 11.1, 11.2: a new block scalar or a new kind written over a value of the shared
    mid.yaml is visible at both places."""
    files = {ROOT: TWICE, "mid.yaml": "k: 1\nj: 2\n"}
    patched, after = _set(tmp_path, files, "b.k", value)
    assert patched == {"mid.yaml": written}
    assert _at(after, "a.k") == expected
    assert _at(after, "b.k") == expected


def test_in_include_into_a_shared_file_is_written_once(tmp_path: Path) -> None:
    """Req 11.1, 11.2, 10.1: `--in-include` writes into the included side (leaf.yaml, shared by
    both places through mid.yaml), once, and both places show the new key."""
    files = {ROOT: TWICE, "mid.yaml": "$ref: file:leaf.yaml\n", "leaf.yaml": LEAF}
    patched, after = _set(tmp_path, files, "a.sub.n", "1", in_include=True)
    assert patched == {"leaf.yaml": "sub:\n  m: 1\n  n: 1\n"}
    assert _at(after, "a.sub") == {"m": 1, "n": 1}
    assert _at(after, "b.sub") == {"m": 1, "n": 1}


def test_in_include_key_comes_before_the_override_only_keys_of_the_other_place(
    tmp_path: Path,
) -> None:
    """Req 11.2, 10.2, 1.3: a key written by `--in-include` into the shared mid.yaml comes, at
    `b`, after mid.yaml's keys and before `b`'s override-only key `y`."""
    root = "a: file:mid.yaml\nb:\n  $ref: file:mid.yaml\n  y: 3\n"
    files = {ROOT: root, "mid.yaml": "k: 1\n"}
    patched, after = _set(tmp_path, files, "a.n", "1", in_include=True)
    assert patched == {"mid.yaml": "k: 1\nn: 1\n"}
    assert _keys(after, "a") == ["k", "n"]
    assert _keys(after, "b") == ["k", "n", "y"]


ITEMS = "items:\n  - k: 1\n"


def test_new_key_in_a_list_item_of_a_shared_file_is_written_once(tmp_path: Path) -> None:
    """Req 11.1, 11.2, 6.2: a new key in a list item of the shared mid.yaml follows the step 1
    rule (the item itself), once, and both places show it."""
    files = {ROOT: TWICE, "mid.yaml": ITEMS}
    patched, after = _set(tmp_path, files, "a.items.0.j", "2")
    assert patched == {"mid.yaml": "items:\n  - k: 1\n    j: 2\n"}
    assert _at(after, "a.items") == [{"k": 1, "j": 2}]
    assert _at(after, "b.items") == [{"k": 1, "j": 2}]


def test_list_append_in_a_shared_file_is_written_once(tmp_path: Path) -> None:
    """Req 11.1, 11.2, 6.1: appending to a list of the shared mid.yaml writes the item there,
    once, and both places show it."""
    files = {ROOT: TWICE, "mid.yaml": ITEMS}
    patched, after = _set(tmp_path, files, "b.items.1", "{k: 2}")
    assert patched == {"mid.yaml": "items:\n  - k: 1\n  - k: 2\n"}
    assert _at(after, "a.items") == [{"k": 1}, {"k": 2}]
    assert _at(after, "b.items") == [{"k": 1}, {"k": 2}]


def test_list_replacement_in_a_shared_file_shortens_it_at_both_places(tmp_path: Path) -> None:
    """Req 11.1, 11.2: a shorter list VALUE deletes the surplus items of the shared list once."""
    files = {ROOT: TWICE, "mid.yaml": "items:\n  - 1\n  - 2\n"}
    patched, after = _set(tmp_path, files, "a.items", "[3]")
    assert patched == {"mid.yaml": "items:\n  - 3\n"}
    assert _at(after, "b.items") == [3]


@pytest.mark.parametrize(
    ("value", "written", "expected"),
    [
        pytest.param("{m: 1}", "sub:\n  m: 1\n", {"m": 1}, id="removal"),
        pytest.param("{}", "sub: {}\n", {}, id="empty"),
    ],
)
def test_removal_in_a_collection_replacement_of_a_shared_file(
    tmp_path: Path, value: str, written: str, expected: PlainValue
) -> None:
    """Req 11.1, 11.2, 7.4: a mapping VALUE removing keys of a mapping written in the shared
    mid.yaml removes them there, once ("existing settings still change where they live"), and
    both places show the result."""
    files = {ROOT: TWICE, "mid.yaml": "sub:\n  m: 1\n  n: 2\n"}
    patched, after = _set(tmp_path, files, "a.sub", value)
    assert patched == {"mid.yaml": written}
    assert _at(after, "a.sub") == expected
    assert _at(after, "b.sub") == expected


def test_replacement_removing_shared_keys_and_adding_an_override(tmp_path: Path) -> None:
    """Req 11.1, 11.2, 7.3, 7.4: `{o: 3}` over `a.sub` removes `m` and `n` from the shared
    mid.yaml (written as `{}`, so `b.sub` becomes empty) while the new key `o` is an override of
    `a`'s include in troupe.yml (the override rule), so it appears at `a` only."""
    files = {ROOT: TWICE, "mid.yaml": "sub:\n  m: 1\n  n: 2\n"}
    patched, after = _set(tmp_path, files, "a.sub", "{o: 3}")
    assert patched == {
        "troupe.yml": "a:\n  $ref: file:mid.yaml\n  sub:\n    o: 3\nb: file:mid.yaml\n",
        "mid.yaml": "sub: {}\n",
    }
    assert _at(after, "a.sub") == {"o": 3}
    assert _at(after, "b.sub") == {}


def test_removal_of_a_shared_override_is_pruned_at_both_places(tmp_path: Path) -> None:
    """Req 11.1, 11.2, 7.4: the override `sub.n` written in the shared mid.yaml (over leaf.yaml)
    is removed once, its emptied override mapping pruned, and both places show only `m`."""
    files = {ROOT: TWICE, "mid.yaml": "$ref: file:leaf.yaml\nsub:\n  n: 1\n", "leaf.yaml": LEAF}
    patched, after = _set(tmp_path, files, "a.sub", "{m: 1}")
    assert patched == {"mid.yaml": "$ref: file:leaf.yaml\n"}
    assert _at(after, "a.sub") == {"m": 1}
    assert _at(after, "b.sub") == {"m": 1}


def test_text_include_shared_by_two_places_is_replaced_once(tmp_path: Path) -> None:
    """Req 11.1, 11.2 (choice 18): `a` and `b` both include prompt.md; setting `a` rewrites the
    text file once, and the read-back gives the new text at both places."""
    files = {ROOT: "a: file:prompt.md\nb: file:prompt.md\nc: 1\n", "prompt.md": "old\n"}
    patched, after = _set(tmp_path, files, "a", '"new\\n"')
    assert patched == {"prompt.md": "new\n"}
    assert _at(after, "a") == "new\n"
    assert _at(after, "b") == "new\n"
    assert _at(after, "c") == 1


def test_text_include_in_a_shared_file_is_replaced_once(tmp_path: Path) -> None:
    """Req 11.1, 11.2: mid.yaml (included as `a` and `b`) holds `p: file:prompt.md`; setting
    `b.p` rewrites prompt.md once and both places show the new text."""
    files = {ROOT: TWICE, "mid.yaml": "p: file:prompt.md\n", "prompt.md": "old"}
    patched, after = _set(tmp_path, files, "b.p", "new")
    assert patched == {"prompt.md": "new"}
    assert _at(after, "a.p") == "new"
    assert _at(after, "b.p") == "new"


def test_change_in_a_file_shared_through_nested_places_reaches_every_place(
    tmp_path: Path,
) -> None:
    """Req 11.2: leaf.yaml is reached at four places (`a`/`b` in the root, each through mid.yaml's
    `x` and `y`); changing its value once shows the new value at all four."""
    files = {
        ROOT: TWICE,
        "mid.yaml": "x: file:leaf.yaml\ny: file:leaf.yaml\n",
        "leaf.yaml": "k: 1\n",
    }
    patched, after = _set(tmp_path, files, "a.y.k", "2")
    assert patched == {"leaf.yaml": "k: 2\n"}
    for place in ("a.x", "a.y", "b.x", "b.y"):
        assert _at(after, place) == {"k": 2}


def test_change_in_a_file_not_shared_keeps_the_intended_configuration(tmp_path: Path) -> None:
    """Req 11.2 (non-shared behaviour unchanged): a change in a file included once gives exactly
    the request's intended configuration."""
    for name, text in {ROOT: "a: file:mid.yaml\nb:\n  k: 1\n", "mid.yaml": "k: 1\n"}.items():
        (tmp_path / name).write_bytes(text.encode("utf-8"))
    document = load_config(tmp_path / ROOT)
    config_path = ConfigPath.parse("a.k")
    request = resolve_target(document, config_path, parse_value("2", config_path))
    assert plan_set(document, request).intended == request.intended


# --- review round 1: new includes, replicas only, faithful recomputation -------------------------

NEW_INCLUDE_ROOT = "x: file:s.yaml\ny:\n  $ref: file:s.yaml\n  p:\n    w: 9\n"
"""`y` overrides `p` of the shared s.yaml with a mapping; `x` includes s.yaml as written."""


def test_new_include_in_a_shared_file_is_merged_with_another_places_override(
    tmp_path: Path,
) -> None:
    """Req 11.1, 11.2, 1.2, 1.7: `x.p` set to `file:t.yaml` is written in the shared s.yaml; at
    `y` the outer override `p: {w: 9}` is deep-merged with t.yaml's content (loaded from the
    pre-write files), so the intended configuration matches the read-back `{v: 1, w: 9}`."""
    files = {ROOT: NEW_INCLUDE_ROOT, "s.yaml": "p: 1\nq: 1\n", "t.yaml": "v: 1\n"}
    patched, after = _set(tmp_path, files, "x.p", "file:t.yaml")
    assert patched == {"s.yaml": "p: file:t.yaml\nq: 1\n"}
    assert _at(after, "x.p") == {"v": 1}
    assert _at(after, "y.p") == {"v": 1, "w": 9}
    assert _at(after, "y.q") == 1


def test_new_include_with_a_missing_target_merged_at_another_place_is_refused(
    tmp_path: Path,
) -> None:
    """Req 11.2, 9.2: when the new include must be merged at another place but its target is
    missing, the set is refused (exit 3) naming PATH and the file holding the include, as the
    read-back would refuse it; nothing is planned from the read-back."""
    files = {ROOT: NEW_INCLUDE_ROOT, "s.yaml": "p: 1\nq: 1\n"}
    with pytest.raises(WriteRefusedError) as raised:
        _set(tmp_path, files, "x.p", "file:t.yaml")
    error = raised.value
    assert error.exit_code is ExitCode.WRITE_REFUSED
    assert error.path == "x.p"
    assert error.file == tmp_path / "s.yaml"
    assert "t.yaml" in error.problem


def _document_and_request(
    tmp_path: Path, files: Mapping[str, str], path: str, value: str
) -> tuple[ConfigDocument, SetRequest]:
    for name, text in files.items():
        (tmp_path / name).write_bytes(text.encode("utf-8"))
    document = load_config(tmp_path / ROOT)
    config_path = ConfigPath.parse(path)
    return document, resolve_target(document, config_path, parse_value(value, config_path))


def test_recorded_change_not_reached_from_paths_place_is_refused(tmp_path: Path) -> None:
    """Req 11.2, 9.2 (Principle 2): a recorded change whose written location is not reached at
    PATH's own place (here `c.k` in troupe.yml for PATH `a.k`) is never accepted as a replica:
    the widening refuses it (exit 3) naming PATH and that file."""
    files = {ROOT: "a: file:mid.yaml\nc:\n  k: 1\n", "mid.yaml": "k: 1\n"}
    document, request = _document_and_request(tmp_path, files, "a.k", "2")
    changes = WrittenChanges()
    c = document.root.get("c")
    assert c is not None and isinstance(c.value, ConfigMapping)
    changes.replace(c.value, "k", parse_value("2", ConfigPath.parse("c.k")))
    with pytest.raises(WriteRefusedError) as raised:
        with_other_places(document, request.intended, request.path, changes)
    error = raised.value
    assert error.exit_code is ExitCode.WRITE_REFUSED
    assert error.path == "a.k"
    assert error.file == tmp_path / ROOT
    assert "not reached" in error.problem


def test_recorded_text_change_not_reached_from_paths_place_is_refused(tmp_path: Path) -> None:
    """Req 11.2, 9.2 (Principle 2): a text file replaced although PATH's place does not reach it
    is refused naming PATH and the text file."""
    files = {ROOT: "a: 1\nb: file:p.md\n", "p.md": "old"}
    document, request = _document_and_request(tmp_path, files, "a", "2")
    changes = WrittenChanges()
    b = document.root.get("b")
    assert b is not None and b.value.include is not None
    changes.text(b.value.include.target, "new")
    with pytest.raises(WriteRefusedError) as raised:
        with_other_places(document, request.intended, request.path, changes)
    assert raised.value.file == tmp_path / "p.md"
    assert raised.value.path == "a"


FAITHFUL_CASES = {
    "scalar-form": ({ROOT: "a: file:s.yaml\n", "s.yaml": "k: 1\nl: [1, {m: 2}]\n"}),
    "bare-ref": ({ROOT: "a:\n  $ref: file:s.yaml\n", "s.yaml": "k: 1\n"}),
    "ref-overrides": (
        {
            ROOT: "a:\n  $ref: file:s.yaml\n  sub:\n    y: 2\n    z: {q: 1}\n  o: 3\n",
            "s.yaml": "sub:\n  x: 1\n  z: {p: 0}\nk: 1\n",
        }
    ),
    "chained": (
        {
            ROOT: "a: file:mid.yaml\nb:\n  $ref: file:mid.yaml\n  n: 1\n",
            "mid.yaml": "$ref: file:leaf.yaml\nsub:\n  t: 1\n",
            "leaf.yaml": "sub:\n  m: 1\nk: 0\n",
        }
    ),
    "scalar-root-chain": (
        {ROOT: "a: file:mid.yaml\n", "mid.yaml": "file:leaf.yaml\n", "leaf.yaml": "k: 1\n"}
    ),
    "text": (
        {
            ROOT: "p: file:p.md\nq:\n  $ref: file:p.md\nr: {s: file:p.md}\n",
            "p.md": "hello\n",
        }
    ),
    "root-ref": (
        {ROOT: "$ref: file:base.yaml\nextra: 1\nk:\n  y: 2\n", "base.yaml": "k:\n  x: 1\n"}
    ),
    "list-items": (
        {
            ROOT: "l:\n  - file:s.yaml\n  - $ref: file:s.yaml\n    k: 2\n  - {$ref: file:s.yaml}\n",
            "s.yaml": "k: 1\nj: 0\n",
        }
    ),
    "flow": ({ROOT: "a: {$ref: file:s.yaml, k: 2, n: {o: 1}}\n", "s.yaml": "{k: 1, n: {p: 1}}\n"}),
    "override-is-include": (
        {
            ROOT: "a:\n  $ref: file:s.yaml\n  sub: file:o.yaml\n",
            "s.yaml": "sub:\n  x: 1\n",
            "o.yaml": "y: 2\n",
        }
    ),
    "empty-and-scalar-targets": (
        {ROOT: "a: file:e.yaml\nb: file:n.yaml\n", "e.yaml": "", "n.yaml": "3\n"}
    ),
}


def _strict(plain: PlainValue) -> object:
    """``plain`` with every directive's kind and overrides made part of equality."""
    if isinstance(plain, IncludeDirective):
        overrides = None if plain.overrides is None else _strict(plain.overrides)
        return ("directive", plain.text, _strict(plain.content), plain.kind, overrides)
    if isinstance(plain, dict):
        return ("map", [(key, _strict(value)) for key, value in plain.items()])
    if isinstance(plain, list):
        return ("list", [_strict(item) for item in plain])
    return ("scalar", type(plain), plain)


@pytest.mark.parametrize("files", FAITHFUL_CASES.values(), ids=FAITHFUL_CASES.keys())
def test_unedited_recomputation_equals_the_loaded_plain_tree(
    tmp_path: Path, files: Mapping[str, str]
) -> None:
    """Req 11.2, 1.2, 1.3: recomputing the plain configuration from the written layers without
    any change gives exactly ``node_to_plain`` of the loaded root (order, directives, kinds and
    written overrides), for both include forms, chains, text includes, a root `$ref`, list items
    and flow style."""
    for name, text in files.items():
        (tmp_path / name).write_bytes(text.encode("utf-8"))
    document = load_config(tmp_path / ROOT)
    assert _strict(rebuilt_plain(document.root, WrittenChanges())) == _strict(
        node_to_plain(document.root)
    )
