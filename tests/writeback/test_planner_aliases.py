"""Unit tests of target resolution and the PatchPlanner in mappings with a merge key (task 3.4).

Each case loads a real theatre configuration with the real loader, resolves the set with the real
``resolve_target``, plans it with the real ``plan_set`` and compares the patched root file byte for
byte. The patched text is read back with the real ``verify_patch`` against the intended
configuration of the plan. An own entry of a mapping with a merge key is changed in place, a new
key goes after the mapping's last written entry (which may be the ``<<`` entry), and the ``<<``
entry and its value are never written. A merged entry reaching the planner is refused, never
written (the guard refuses it first in the setter).

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

from dataclasses import replace
from pathlib import Path

import pytest

from troupe.config.errors import ExitCode, WriteRefusedError
from troupe.config.loader import load_config
from troupe.config.paths import ConfigPath
from troupe.config.writeback.planner import plan_set
from troupe.config.writeback.target import AddKeyTarget, ReplaceTarget, WriteTarget, resolve_target
from troupe.config.writeback.value import parse_value
from troupe.config.writeback.verify import verify_patch

ROOT = "troupe.yml"

BLOCK = (
    "defaults: &d\n"
    "  model: opus\n"
    "  temperature: 0.5\n"
    "coder:\n"
    "  name: c  # own\n"
    "  model: haiku\n"
    "  <<: *d  # shared\n"
)

SEVERAL = 'a: &a {k: 1}\nb: &b\n  m: 2\nc:\n  "n": 0\n  <<: [*a, *b]\n\n# trailing comment\nd: 1\n'

FLOW = "defaults: &d {model: opus, temperature: 0.5}\nreviewer: {name: r, <<: *d}\n"


def _set(tmp_path: Path, config: str, path: str, value: str, kind: type[WriteTarget]) -> str:
    """Plan the set on the real theatre; return the new root text after verifying its read-back.

    The plan must change the root file only, leave every `<<` line of the file in place, and read
    back as the plan's intended configuration.
    """
    (tmp_path / ROOT).write_bytes(config.encode("utf-8"))
    document = load_config(tmp_path / ROOT)
    config_path = ConfigPath.parse(path)
    request = resolve_target(document, config_path, parse_value(value, config_path))
    assert isinstance(request.target, kind)
    plan = plan_set(document, request)
    assert [patch.file.real_path for patch in plan.patches] == [document.root_file.real_path]
    verify_patch(tmp_path / ROOT, document, plan.patches, replace(request, intended=plan.intended))
    return plan.patches[0].new_text


def _refused(tmp_path: Path, config: str, path: str, value: str) -> WriteRefusedError:
    (tmp_path / ROOT).write_bytes(config.encode("utf-8"))
    document = load_config(tmp_path / ROOT)
    config_path = ConfigPath.parse(path)
    request = resolve_target(document, config_path, parse_value(value, config_path))
    with pytest.raises(WriteRefusedError) as caught:
        plan_set(document, request)
    assert caught.value.exit_code is ExitCode.WRITE_REFUSED
    assert caught.value.path == path
    assert caught.value.file == document.root_file.path
    return caught.value


# --- own keys changed in place --------------------------------------------------------------------


def test_changed_own_key_before_the_merge_key_is_replaced_in_place(tmp_path: Path) -> None:
    """Req 9.1, 9.3: an own key of a mapping with `<<` changes in its slot, comment kept; the
    `<<` line is unchanged."""
    new_text = _set(tmp_path, BLOCK, "coder.name", "x", ReplaceTarget)
    assert new_text == BLOCK.replace("  name: c  # own\n", "  name: x  # own\n")


def test_changed_own_key_that_shadows_a_merged_key_is_replaced_in_place(tmp_path: Path) -> None:
    """Req 9.1, 9.3: an own key that a merge source also has changes only in the mapping."""
    new_text = _set(tmp_path, BLOCK, "coder.model", "sonnet", ReplaceTarget)
    assert new_text == BLOCK.replace("  model: haiku\n", "  model: sonnet\n")


def test_changed_own_key_after_the_merge_key_in_a_flow_mapping(tmp_path: Path) -> None:
    """Req 9.1, 9.3: an own key after `<<` in a flow mapping changes in place."""
    config = "defaults: &d {model: opus}\nreviewer: {<<: *d, name: r}\n"
    new_text = _set(tmp_path, config, "reviewer.name", "q", ReplaceTarget)
    assert new_text == "defaults: &d {model: opus}\nreviewer: {<<: *d, name: q}\n"


# --- new keys after the last written entry --------------------------------------------------------


def test_new_key_goes_after_a_trailing_merge_key_in_a_block_mapping(tmp_path: Path) -> None:
    """Req 9.1, 9.3: a new key of a block mapping whose last entry is `<<` goes after that line,
    at the entries column; the `<<` line keeps its bytes."""
    new_text = _set(tmp_path, BLOCK, "coder.effort", "high", AddKeyTarget)
    assert new_text == BLOCK + "  effort: high\n"


def test_new_key_after_a_merge_key_with_several_sources_is_styled_after_an_own_key(
    tmp_path: Path,
) -> None:
    """Req 9.1, 9.3: after `<<: [*a, *b]` and before the blank line and the next top-level key;
    the key is styled after the last own entry (double-quoted), not after `<<`."""
    new_text = _set(tmp_path, SEVERAL, "c.p", "3", AddKeyTarget)
    assert new_text == SEVERAL.replace("  <<: [*a, *b]\n", '  <<: [*a, *b]\n  "p": 3\n')


def test_new_key_goes_after_a_trailing_merge_key_in_a_flow_mapping(tmp_path: Path) -> None:
    """Req 9.1, 9.3: in a flow mapping the new key goes after the `<<` entry on its line; the
    merge key's bytes are unchanged."""
    new_text = _set(tmp_path, FLOW, "reviewer.effort", "high", AddKeyTarget)
    assert new_text == FLOW.replace("<<: *d}", "<<: *d, effort: high}")


def test_new_key_goes_after_the_last_own_key_after_a_flow_merge_key(tmp_path: Path) -> None:
    """Req 9.1, 9.3: with an own key after `<<`, the new key goes after that own key."""
    config = "defaults: &d {model: opus}\nreviewer: {<<: *d, name: r}\n"
    new_text = _set(tmp_path, config, "reviewer.effort", "high", AddKeyTarget)
    assert new_text == "defaults: &d {model: opus}\nreviewer: {<<: *d, name: r, effort: high}\n"


def test_new_nested_keys_below_a_mapping_with_a_merge_key(tmp_path: Path) -> None:
    """Req 9.1, 9.3: missing intermediate mappings are created after the trailing `<<` entry."""
    new_text = _set(tmp_path, BLOCK, "coder.limits.tokens", "10", AddKeyTarget)
    assert new_text == BLOCK + "  limits:\n    tokens: 10\n"


# --- collection replacement of a mapping with a merge key -----------------------------------------


def test_replacement_changes_own_keys_and_leaves_unchanged_merged_keys(tmp_path: Path) -> None:
    """Req 9.1, 9.3: replacing the mapping changes its own key in place; merged keys whose value
    is unchanged are left alone and the `<<` entry keeps its bytes."""
    value = "{name: x, model: haiku, temperature: 0.5}"
    new_text = _set(tmp_path, BLOCK, "coder", value, ReplaceTarget)
    assert new_text == BLOCK.replace("  name: c  # own\n", "  name: x  # own\n")


def test_replacement_adds_and_removes_own_keys_only(tmp_path: Path) -> None:
    """Req 9.1, 9.3: a replacement removing an own key and adding one deletes the own line and
    adds the new key after the trailing `<<` entry."""
    value = "{model: haiku, temperature: 0.5, effort: high}"
    new_text = _set(tmp_path, BLOCK, "coder", value, ReplaceTarget)
    assert new_text == BLOCK.replace("  name: c  # own\n", "") + "  effort: high\n"


def test_replacement_of_a_flow_mapping_with_a_merge_key(tmp_path: Path) -> None:
    """Req 9.1, 9.3: in a flow mapping a replacement removes and adds own keys; `<<: *d` stays."""
    value = "{model: opus, temperature: 0.5, effort: high}"
    new_text = _set(tmp_path, FLOW, "reviewer", value, ReplaceTarget)
    assert new_text == FLOW.replace("{name: r, <<: *d}", "{<<: *d, effort: high}")


# --- merged entries are never written -------------------------------------------------------------


def test_change_of_a_merged_key_is_refused_by_the_planner(tmp_path: Path) -> None:
    """Req 9.1: a value the mapping receives from its merge key is never written there; the
    planner refuses (exit 3) naming the file, PATH and `<<`."""
    error = _refused(tmp_path, BLOCK, "coder.temperature", "0.9")
    assert "'<<'" in error.problem
    assert "never written" in error.problem


def test_replacement_changing_a_merged_key_is_refused(tmp_path: Path) -> None:
    """Req 9.1: a replacement that changes a merged key's value is refused, nothing written."""
    value = "{name: c, model: haiku, temperature: 0.9}"
    error = _refused(tmp_path, BLOCK, "coder", value)
    assert "'<<'" in error.problem


def test_replacement_removing_a_merged_key_is_refused(tmp_path: Path) -> None:
    """Req 9.1: a replacement that drops a merged key is refused, nothing written."""
    error = _refused(tmp_path, FLOW, "reviewer", "{name: r}")
    assert "'<<'" in error.problem


def test_replacement_removing_an_own_key_right_after_a_flow_merge_key(tmp_path: Path) -> None:
    """Req 9.1, 9.3: the own key after `<<` is removed with the `,` before it, which follows the
    `<<` entry as written (merged keys are not written in this mapping)."""
    config = "defaults: &d {model: opus}\nreviewer: {<<: *d, name: r, t: 1}\n"
    new_text = _set(tmp_path, config, "reviewer", "{model: opus, t: 1}", ReplaceTarget)
    assert new_text == "defaults: &d {model: opus}\nreviewer: {<<: *d, t: 1}\n"


def test_replacement_removing_every_own_key_keeps_the_merge_key(tmp_path: Path) -> None:
    """Req 9.1, 9.3: removing all own keys deletes their lines; the `<<` line stays."""
    config = "defaults: &d\n  model: opus\ncoder:\n  name: c\n  <<: *d\n  t: 1\n"
    new_text = _set(tmp_path, config, "coder", "{model: opus}", ReplaceTarget)
    assert new_text == "defaults: &d\n  model: opus\ncoder:\n  <<: *d\n"


def test_set_below_a_merged_entry_is_refused_by_the_planner(tmp_path: Path) -> None:
    """Req 9.1: a PATH below a merged mapping value would write at the merge source; refused."""
    config = "defaults: &d\n  limits:\n    tokens: 1\ncoder:\n  <<: *d\n"
    for path, value in (("coder.limits.tokens", "2"), ("coder.limits.new", "2")):
        error = _refused(tmp_path, config, path, value)
        assert error.problem.startswith("value comes from merge key '<<' at coder.<<;")


@pytest.mark.parametrize("value", ["x", "[1]", "{}"])
def test_whole_replacement_of_a_mapping_with_a_merge_key_is_refused(
    tmp_path: Path, value: str
) -> None:
    """Req 9.1: replacing the mapping by another kind or `{}` would remove its `<<` entry."""
    error = _refused(tmp_path, BLOCK, "coder", value)
    assert "'<<'" in error.problem


# --- removing a used anchor (4.6) -----------------------------------------------------------------

SHARED = "shared:\n  defaults: &d  # anchored\n    model: opus\n  other: 1\ncoder: *d\n"


@pytest.mark.parametrize(
    ("config", "path", "value", "anchor", "alias_path"),
    [
        # A collection replacement dropping the key that holds the anchored node.
        (SHARED, "shared", "{other: 1}", "d", "coder"),
        # A kind change of an ancestor of the anchored node.
        (SHARED, "shared", "5", "d", "coder"),
        (SHARED, "shared", "{}", "d", "coder"),
        # A list replacement dropping the anchored item.
        ("items:\n  - 1\n  - &d {a: 1}\nuse: *d\n", "items", "[1]", "d", "use"),
        # The remaining use is a merge key's alias.
        (
            "shared:\n  defaults: &d {model: opus}\n  other: 1\ncoder:\n  name: c\n  <<: *d\n",
            "shared",
            "{other: 1}",
            "d",
            "coder.<<",
        ),
        # An anchored include string removed while an alias to it remains.
        (
            "shared:\n  i: &i file:inc.yml\n  o: 1\nx: *i\n",
            "shared",
            "{o: 1}",
            "i",
            "x",
        ),
    ],
)
def test_removal_of_an_anchored_node_still_used_by_an_alias_is_refused(
    tmp_path: Path, config: str, path: str, value: str, anchor: str, alias_path: str
) -> None:
    """Req 4.6: a set whose removals would remove an anchored node that an alias outside the
    removed part still uses is refused (exit 3), naming PATH, the file and `&name`, and the
    alias that still uses it; no patch is planned."""
    (tmp_path / "inc.yml").write_text("k: v\n", encoding="utf-8")
    error = _refused(tmp_path, config, path, value)
    assert error.problem == (
        f"anchor '&{anchor}' is still used by alias '*{anchor}' at {alias_path}"
    )


def test_removal_of_an_anchored_node_with_all_its_aliases_is_planned(tmp_path: Path) -> None:
    """Req 4.6: an anchored node whose aliases are all removed with it may be removed; the plan
    reads back."""
    config = (
        "shared:\n"
        "  defaults: &d\n"
        "    model: opus\n"
        "  coder: *d\n"
        "  list:\n"
        "    - *d\n"
        "  other: 1  # kept\n"
        "top: 2\n"
    )
    new_text = _set(tmp_path, config, "shared", "{other: 1}", ReplaceTarget)
    assert new_text == "shared:\n  other: 1  # kept\ntop: 2\n"


@pytest.mark.parametrize(
    ("config", "path", "value", "expected"),
    [
        (
            "shared:\n  items:\n    - 1\n    - &d {a: 1}\n  use: *d\n  o: 1\n",
            "shared",
            "{items: [1], o: 1}",
            "shared:\n  items:\n    - 1\n  o: 1\n",
        ),
        ("l:\n  - 1\n  - &d\n    a: 1\n", "l", "[1]", "l:\n  - 1\n"),
        ("l: [1, &d {a: 1}]\n", "l", "[1]", "l: [1]\n"),
        ("l: [&d {a: 1}, 2]\n", "l", "[{a: 1}]", "l: [&d {a: 1}]\n"),
    ],
)
def test_removal_of_an_anchored_list_item_takes_its_anchor_with_it(
    tmp_path: Path, config: str, path: str, value: str, expected: str
) -> None:
    """Req 4.6: an anchored list item whose aliases go with it is deleted with its `&name`
    property (block and flow lists); the plan reads back."""
    assert _set(tmp_path, config, path, value, ReplaceTarget) == expected


def test_removal_of_an_alias_that_keeps_its_anchor_is_planned(tmp_path: Path) -> None:
    """Req 4.6: removing an alias place leaves its anchored node in place, so the planner plans
    it (in the real flow the guard decides first)."""
    config = "defaults: &d\n  model: opus\nshared:\n  coder: *d\n  other: 1\n"
    new_text = _set(tmp_path, config, "shared", "{other: 1}", ReplaceTarget)
    assert new_text == "defaults: &d\n  model: opus\nshared:\n  other: 1\n"


def test_replacing_the_anchored_node_itself_is_not_a_removal(tmp_path: Path) -> None:
    """Req 4.3, 4.6: replacing the anchored node keeps its anchor on the new value, so the
    alias still has its anchor and the set is planned."""
    new_text = _set(tmp_path, SHARED, "shared.defaults", "{effort: high}", ReplaceTarget)
    assert new_text == SHARED.replace("    model: opus\n", "    effort: high\n")


# --- converting an anchored include (4.7) ---------------------------------------------------------


@pytest.mark.parametrize(
    "config",
    [
        "i: &i file:inc.yml\n",
        "i: &i file:inc.yml\nx: *i\n",
        "l:\n  - &i file:inc.yml\n",
    ],
)
def test_conversion_of_an_anchored_scalar_form_include_is_refused(
    tmp_path: Path, config: str
) -> None:
    """Req 4.7: a new key that would convert an anchored scalar-form include to mapping form is
    refused (exit 3), naming PATH, the file and `&name`; no patch is planned."""
    (tmp_path / "inc.yml").write_text("k: v\n", encoding="utf-8")
    path = "l.0.extra" if config.startswith("l:") else "i.extra"
    error = _refused(tmp_path, config, path, "1")
    assert error.problem == "the include at anchor '&i' cannot be converted to mapping form"


def test_conversion_of_an_unanchored_include_beside_an_anchor_is_planned(tmp_path: Path) -> None:
    """Req 4.7: only an anchored include is refused; another include string still converts."""
    (tmp_path / "inc.yml").write_text("k: v\n", encoding="utf-8")
    config = "a: &a 1\ni: file:inc.yml\n"
    new_text = _set(tmp_path, config, "i.extra", "1", AddKeyTarget)
    assert new_text == "a: &a 1\ni:\n  $ref: file:inc.yml\n  extra: 1\n"


# --- replacing an anchored node itself (task 3.8) -------------------------------------------------


@pytest.mark.parametrize(
    ("config", "path", "value", "expected", "intended"),
    [
        # A scalar replaced at its anchor.
        ("a: &d 1\nu: *d\n", "a", "2", "a: &d 2\nu: *d\n", {"a": 2, "u": 2}),
        # A null replacement is a value, not an absent one, at the alias place.
        ("a: &d 1\nu: *d\n", "a", "null", "a: &d null\nu: *d\n", {"a": None, "u": None}),
        # Scalar to mapping: the comment after the anchor keeps its column (11.4, 11.5).
        (
            "a: &d 1  # c\nu: *d\n",
            "a",
            "{k: 1}",
            "a: &d    # c\n  k: 1\nu: *d\n",
            {"a": {"k": 1}, "u": {"k": 1}},
        ),
        # Mapping to scalar: the value reaches the comment's column, so one space (12.1, 12.2).
        (
            SHARED,
            "shared.defaults",
            "5",
            "shared:\n  defaults: &d 5 # anchored\n  other: 1\ncoder: *d\n",
            {"shared": {"defaults": 5, "other": 1}, "coder": 5},
        ),
        # Mapping to scalar with room before the comment: the comment keeps its column (12.1).
        (
            "d: &d    # c\n  k: 1\nu: *d\n",
            "d",
            "5",
            "d: &d 5  # c\nu: *d\n",
            {"d": 5, "u": 5},
        ),
        # Mapping to `{}`.
        (
            SHARED,
            "shared.defaults",
            "{}",
            "shared:\n  defaults: &d {} # anchored\n  other: 1\ncoder: *d\n",
            {"shared": {"defaults": {}, "other": 1}, "coder": {}},
        ),
        # A list to `[]`, used by an alias list item.
        (
            "l: &d [1]  # c\nu:\n  - *d\n",
            "l",
            "[]",
            "l: &d []   # c\nu:\n  - *d\n",
            {"l": [], "u": [[]]},
        ),
        # An anchored list item replaced in its slot.
        (
            "l:\n  - &d 1  # c\n  - *d\n",
            "l.0",
            "22",
            "l:\n  - &d 22 # c\n  - *d\n",
            {"l": [22, 22]},
        ),
        # An anchored node used through `<<: *d`, replaced by `{}`: it merges nothing any more.
        (
            "defaults: &d  # anchored\n  model: opus\ncoder:\n  <<: *d\n  t: 1\n",
            "defaults",
            "{}",
            "defaults: &d {} # anchored\ncoder:\n  <<: *d\n  t: 1\n",
            {"defaults": {}, "coder": {"t": 1}},
        ),
        # An anchored node inside an anchored mapping, replaced while merging the outer one.
        (
            "o: &o\n  a: &d 1\np: *o\nu: *d\n",
            "o",
            "{a: [2]}",
            "o: &o\n  a: &d\n    - 2\np: *o\nu: *d\n",
            {"o": {"a": [2]}, "p": {"a": [2]}, "u": [2]},
        ),
    ],
)
def test_replaced_anchored_node_keeps_its_anchor_and_reads_back_at_every_alias(
    tmp_path: Path,
    config: str,
    path: str,
    value: str,
    expected: str,
    intended: dict[str, object],
) -> None:
    """Req 4.1, 4.3, 4.5, 10.2: replacing an anchored node itself writes the new value once, at
    the anchor, keeping `&d` and the end-of-line comment's column (byte for byte); the widened
    intended configuration holds it at every alias place and merge use, and the read-back
    passes."""
    (tmp_path / ROOT).write_bytes(config.encode("utf-8"))
    document = load_config(tmp_path / ROOT)
    config_path = ConfigPath.parse(path)
    request = resolve_target(document, config_path, parse_value(value, config_path))
    plan = plan_set(document, request)
    assert plan.intended == intended
    assert [patch.new_text for patch in plan.patches] == [expected]
    verify_patch(tmp_path / ROOT, document, plan.patches, replace(request, intended=plan.intended))


def test_anchored_mapping_merged_by_a_merge_key_replaced_by_a_scalar_is_refused(
    tmp_path: Path,
) -> None:
    """Req 4.5, 10.2: a scalar at the anchor would leave `<<: *d` merging a scalar; the read-back
    refuses it (exit 3) with the loader's message, and nothing crashes on the way."""
    config = "defaults: &d {model: opus}\ncoder:\n  <<: *d\n"
    (tmp_path / ROOT).write_bytes(config.encode("utf-8"))
    document = load_config(tmp_path / ROOT)
    config_path = ConfigPath.parse("defaults")
    request = resolve_target(document, config_path, parse_value("5", config_path))
    plan = plan_set(document, request)
    with pytest.raises(WriteRefusedError) as caught:
        verify_patch(
            tmp_path / ROOT, document, plan.patches, replace(request, intended=plan.intended)
        )
    assert caught.value.exit_code is ExitCode.WRITE_REFUSED
    assert "merge key '<<' needs a mapping" in caught.value.problem


# --- an anchored block list item replaced by a collection (task 3.9) ------------------------------


@pytest.mark.parametrize(
    ("config", "path", "value", "expected", "intended"),
    [
        # A mapping starts on the line after `- &d`, so `&d` stays the item's anchor.
        ("l:\n  - &d 1\n", "l.0", "{k: 1}", "l:\n  - &d\n    k: 1\n", {"l": [{"k": 1}]}),
        # The same with an alias of the item elsewhere: the alias sees the mapping.
        (
            "l:\n  - &d 1\nu: *d\n",
            "l.0",
            "{k: 1, j: 2}",
            "l:\n  - &d\n    k: 1\n    j: 2\nu: *d\n",
            {"l": [{"k": 1, "j": 2}], "u": {"k": 1, "j": 2}},
        ),
        # An end-of-line comment after the item keeps its column (11.4, 11.5).
        (
            "l:\n  - &d 1  # c\n  - *d\n",
            "l.0",
            "{k: 1}",
            "l:\n  - &d    # c\n    k: 1\n  - *d\n",
            {"l": [{"k": 1}, {"k": 1}]},
        ),
        # A list item at the key's column (dash offset 0).
        (
            "l:\n- &d 1\nu: *d\n",
            "l.0",
            "{k: 1}",
            "l:\n- &d\n  k: 1\nu: *d\n",
            {"l": [{"k": 1}], "u": {"k": 1}},
        ),
        # A block list cannot start after `- &d` on its line either.
        (
            "l:\n  - &d 1\nu: *d\n",
            "l.0",
            "[1, 2]",
            "l:\n  - &d\n    - 1\n    - 2\nu: *d\n",
            {"l": [[1, 2]], "u": [1, 2]},
        ),
        # An anchored flow list item replaced by a block mapping.
        (
            "l:\n  - &d [1]\nu: *d\n",
            "l.0",
            "{k: 1}",
            "l:\n  - &d\n    k: 1\nu: *d\n",
            {"l": [{"k": 1}], "u": {"k": 1}},
        ),
        # An anchored block mapping item replaced by a block list.
        (
            "l:\n  - &d\n    k: 1\nu: *d\n",
            "l.0",
            "[1]",
            "l:\n  - &d\n    - 1\nu: *d\n",
            {"l": [[1]], "u": [1]},
        ),
    ],
)
def test_anchored_list_item_replaced_by_a_collection_starts_it_on_the_next_line(
    tmp_path: Path,
    config: str,
    path: str,
    value: str,
    expected: str,
    intended: dict[str, object],
) -> None:
    """Req 4.3, 10.1, 10.2: an anchored block list item replaced by a block collection keeps
    `- &d` on its line and the collection on the next lines at the item's content indentation
    (byte for byte), so `&d` stays the item's anchor; every alias sees the new value and the
    read-back passes."""
    (tmp_path / ROOT).write_bytes(config.encode("utf-8"))
    document = load_config(tmp_path / ROOT)
    config_path = ConfigPath.parse(path)
    request = resolve_target(document, config_path, parse_value(value, config_path))
    plan = plan_set(document, request)
    assert plan.intended == intended
    assert [patch.new_text for patch in plan.patches] == [expected]
    verify_patch(tmp_path / ROOT, document, plan.patches, replace(request, intended=plan.intended))


@pytest.mark.parametrize(
    ("config", "value", "expected"),
    [
        ("l:\n  - 1\n", "{k: 1}", "l:\n  - k: 1\n"),
        ("l:\n  - 1\n", "[1, 2]", "l:\n  - - 1\n    - 2\n"),
        ("a: &d 0\nl:\n  - 1\n", "{k: 1}", "a: &d 0\nl:\n  - k: 1\n"),
    ],
)
def test_unanchored_list_item_replaced_by_a_collection_stays_compact(
    tmp_path: Path, config: str, value: str, expected: str
) -> None:
    """Req 10.1: a list item without an anchor still gets the compact form (config-set-writeback
    8.4), even beside an anchor elsewhere in the file."""
    new_text = _set(tmp_path, config, "l.0", value, ReplaceTarget)
    assert new_text == expected
