"""Unit tests of ``set --in-include`` in the file layer (config-include-overrides task 3.8, CR-B).

With ``in_include`` the target resolver skips the placement walk and uses the step 1 rule: a new
key (and each new key of a mapping VALUE) goes into the included side of the deepest existing
ancestor, followed through nested merged includes to the innermost file, or into that ancestor
itself when it is not merged. Existing values, appends and removals do not depend on the choice.
When keys go into the included side of a merged mapping, the intended configuration lists them
after the included keys and before the override-only keys, so the ordered read-back holds
(design.md "Placement with --in-include", research.md Decision 8).

Each case loads a real theatre configuration from disk, resolves the set with the real
TargetResolver, plans the patch, compares the patched files byte-exactly and re-loads the patched
texts with the real loader to prove they read back as the intended configuration, in merged
order (the merged order of the changed mapping is also checked explicitly after reload).

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

from collections.abc import Mapping
from pathlib import Path

import pytest

from troupe.config.loader import load_config
from troupe.config.paths import ConfigPath
from troupe.config.writeback.compare import (
    IncludeDirective,
    PlainMapping,
    PlainValue,
    plain_equal,
    root_plain,
)
from troupe.config.writeback.planner import plan_patch
from troupe.config.writeback.target import resolve_target
from troupe.config.writeback.value import parse_value

ROOT = "troupe.yml"
CODER = "model: y\ntools: 1\n"
CODER_PARAMS = "model: y\nparams:\n  t: 1\n"


def _plan(
    tmp_path: Path, files: Mapping[str, str], path: str, value: str, *, in_include: bool = True
) -> tuple[dict[str, str], PlainMapping]:
    """Plan a set; return each patched file's name and new text, and the reloaded plain root.

    Also proves that every patch changes its file and that the patched texts, re-loaded
    together, are the intended configuration (including key order).
    """
    for name, text in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_bytes(text.encode("utf-8"))
    document = load_config(tmp_path / ROOT)
    config_path = ConfigPath.parse(path)
    request = resolve_target(
        document, config_path, parse_value(value, config_path), in_include=in_include
    )
    assert request.in_include is in_include
    patches = plan_patch(document, request)
    assert all(patch.new_text != patch.file.text for patch in patches)
    reloaded = root_plain(
        load_config(tmp_path / ROOT, {patch.file.real_path: patch.new_text for patch in patches})
    )
    assert plain_equal(reloaded, request.intended, ordered=True)
    result = {patch.file.real_path.name: patch.new_text for patch in patches}
    assert len(result) == len(patches), "never two patches for the same file"
    return result, reloaded


def _keys(value: PlainValue) -> list[str]:
    if isinstance(value, IncludeDirective):
        value = value.content
    assert isinstance(value, dict)
    return list(value)


def _child(value: PlainValue, *keys: str) -> PlainValue:
    for key in keys:
        if isinstance(value, IncludeDirective):
            value = value.content
        assert isinstance(value, dict)
        value = value[key]
    return value


def test_key_below_a_scalar_form_include_goes_into_the_included_file(tmp_path: Path) -> None:
    """Req 10.1: the key is added to the included file; the include stays in scalar form (no
    conversion) and the including file is unchanged."""
    files = {ROOT: "agents:\n  coder: file:coder.yaml  # c\n", "coder.yaml": CODER}
    patched, _ = _plan(tmp_path, files, "agents.coder.temperature", "0.3")
    assert patched == {"coder.yaml": "model: y\ntools: 1\ntemperature: 0.3\n"}


def test_key_below_a_merged_mapping_goes_into_its_included_side(tmp_path: Path) -> None:
    """Req 10.1, 10.2: the key is added to the included file of a merged mapping; after reload
    it comes after the included keys and before the override-only key."""
    files = {ROOT: "coder:\n  $ref: file:coder.yaml\n  model: z\n  extra: 1\n", "coder.yaml": CODER}
    patched, reloaded = _plan(tmp_path, files, "coder.temp", "0.5")
    assert patched == {"coder.yaml": "model: y\ntools: 1\ntemp: 0.5\n"}
    assert _keys(reloaded["coder"]) == ["model", "tools", "temp", "extra"]


def test_missing_intermediate_keys_go_into_the_included_side(tmp_path: Path) -> None:
    """Req 10.1, 10.2: the missing intermediate mappings are created in the included file."""
    files = {ROOT: "coder:\n  $ref: file:coder.yaml\n  extra: 1\n", "coder.yaml": CODER}
    patched, reloaded = _plan(tmp_path, files, "coder.p.q", "2")
    assert patched == {"coder.yaml": "model: y\ntools: 1\np:\n  q: 2\n"}
    assert _keys(reloaded["coder"]) == ["model", "tools", "p", "extra"]


def test_key_below_a_merged_root_include_goes_into_the_included_file(tmp_path: Path) -> None:
    """Req 10.1, 10.2: below a root include with overrides the key goes into the included
    file, before the root's override-only keys."""
    files = {ROOT: "$ref: file:base.yaml\nlocal: 1\n", "base.yaml": CODER}
    patched, reloaded = _plan(tmp_path, files, "extra", "2")
    assert patched == {"base.yaml": "model: y\ntools: 1\nextra: 2\n"}
    assert list(reloaded) == ["model", "tools", "extra", "local"]


def test_nested_includes_write_in_the_innermost_file(tmp_path: Path) -> None:
    """Req 10.1, 10.2: through nested mapping-form includes the key goes into the innermost
    file, after its keys and before the override-only keys of every level."""
    files = {
        ROOT: "agent:\n  $ref: file:mid.yaml\n  a: 1\n",
        "mid.yaml": "$ref: file:inner.yaml\nb: 2\n",
        "inner.yaml": "c: 3\n",
    }
    patched, reloaded = _plan(tmp_path, files, "agent.d", "4")
    assert patched == {"inner.yaml": "c: 3\nd: 4\n"}
    assert _keys(reloaded["agent"]) == ["c", "d", "b", "a"]


def test_nested_scalar_form_includes_write_in_the_innermost_file(tmp_path: Path) -> None:
    """Req 10.1: through nested scalar-form includes the key goes into the innermost file,
    with no conversion in the middle file."""
    files = {ROOT: "a: file:mid.yaml\n", "mid.yaml": "b: file:leaf.yaml\n", "leaf.yaml": "y: 2\n"}
    patched, _ = _plan(tmp_path, files, "a.b.z", "3")
    assert patched == {"leaf.yaml": "y: 2\nz: 3\n"}


def test_ancestor_written_only_in_an_override_writes_in_the_including_file(
    tmp_path: Path,
) -> None:
    """Req 10.1: an ancestor written only in an override receives the key in the including
    file; the included file is unchanged."""
    files = {ROOT: "coder:\n  $ref: file:coder.yaml\n  extra:\n    x: 1\n", "coder.yaml": CODER}
    patched, _ = _plan(tmp_path, files, "coder.extra.y", "2")
    assert patched == {ROOT: "coder:\n  $ref: file:coder.yaml\n  extra:\n    x: 1\n    y: 2\n"}


def test_mapping_value_new_keys_go_into_the_included_side(tmp_path: Path) -> None:
    """Req 10.2, 10.3: the new keys of a mapping VALUE over a merged mapping go into the
    included file and come before the override-only key after reload."""
    files = {
        ROOT: "coder:\n  $ref: file:coder.yaml\n  params:\n    u: 3\n",
        "coder.yaml": "model: y\nparams:\n  t: 1\n  v: 2\n",
    }
    patched, reloaded = _plan(tmp_path, files, "coder.params", "{t: 1, v: 2, u: 3, k: 4, m: 5}")
    assert patched == {"coder.yaml": "model: y\nparams:\n  t: 1\n  v: 2\n  k: 4\n  m: 5\n"}
    assert _keys(_child(reloaded, "coder", "params")) == ["t", "v", "k", "m", "u"]


def test_mapping_value_new_keys_of_a_nested_merged_mapping(tmp_path: Path) -> None:
    """Req 10.2, 10.3: below PATH, a nested merged mapping's new keys also go into its included
    side, before its override-only keys."""
    files = {
        ROOT: "coder:\n  $ref: file:coder.yaml\n  params:\n    inner:\n      b: 2\n    u: 3\n",
        "coder.yaml": "params:\n  t: 1\n  inner:\n    a: 1\n",
    }
    patched, reloaded = _plan(
        tmp_path, files, "coder.params", "{t: 1, inner: {a: 1, b: 2, c: 3}, u: 3, n: 5}"
    )
    assert patched == {"coder.yaml": "params:\n  t: 1\n  inner:\n    a: 1\n    c: 3\n  n: 5\n"}
    params = _child(reloaded, "coder", "params")
    assert _keys(params) == ["t", "inner", "n", "u"]
    assert _keys(_child(params, "inner")) == ["a", "c", "b"]


def test_mapping_value_with_removals_and_new_keys_in_the_included_side(tmp_path: Path) -> None:
    """Req 10.3, 10.4: removals are made as without the option (from every layer) while the new
    key goes into the included side."""
    files = {
        ROOT: "coder:\n  $ref: file:coder.yaml\n  params:\n    u: 3\n    w: 4\n",
        "coder.yaml": "model: y\nparams:\n  t: 1\n  v: 2\n",
    }
    patched, reloaded = _plan(tmp_path, files, "coder.params", "{v: 2, u: 3, k: 5}")
    assert patched == {
        ROOT: "coder:\n  $ref: file:coder.yaml\n  params:\n    u: 3\n",
        "coder.yaml": "model: y\nparams:\n  v: 2\n  k: 5\n",
    }
    assert _keys(_child(reloaded, "coder", "params")) == ["v", "k", "u"]


def test_mapping_value_over_an_unmerged_included_mapping(tmp_path: Path) -> None:
    """Req 10.3: a mapping VALUE over a mapping below a bare `$ref` include gains its new keys
    in the included file (no override is written)."""
    files = {ROOT: "coder:\n  $ref: file:coder.yaml\n", "coder.yaml": CODER_PARAMS}
    patched, _ = _plan(tmp_path, files, "coder.params", "{t: 1, k: 2}")
    assert patched == {"coder.yaml": "model: y\nparams:\n  t: 1\n  k: 2\n"}


def test_plain_root_mapping_is_the_same_as_without_the_option(tmp_path: Path) -> None:
    """Req 10.5: below no include the result is byte-identical to the set without the option."""
    files = {ROOT: "a:\n  x: 1  # c\nb: 2\n"}
    with_option, _ = _plan(tmp_path, files, "a.p.q", "1")
    without, _ = _plan(tmp_path, files, "a.p.q", "1", in_include=False)
    assert with_option == without == {ROOT: "a:\n  x: 1  # c\n  p:\n    q: 1\nb: 2\n"}


@pytest.mark.parametrize(
    ("path", "value", "expected"),
    [
        (
            "coder.model",
            "w",
            {ROOT: "coder:\n  $ref: file:coder.yaml\n  model: w\n  extra: 1\n"},
        ),
        ("coder.tools", "2", {"coder.yaml": "model: y\ntools: 2\nlist:\n  - a\n"}),
        ("coder.list.1", "b", {"coder.yaml": "model: y\ntools: 1\nlist:\n  - a\n  - b\n"}),
        ("coder.list", "[]", {"coder.yaml": "model: y\ntools: 1\nlist: []\n"}),
    ],
)
def test_existing_values_and_appends_do_not_depend_on_the_option(
    tmp_path: Path, path: str, value: str, expected: dict[str, str]
) -> None:
    """Req 10.4: an overridden value changes in the override, a non-overridden one in the
    included file, and an append or a list replacement is written as without the option."""
    files = {
        ROOT: "coder:\n  $ref: file:coder.yaml\n  model: z\n  extra: 1\n",
        "coder.yaml": "model: y\ntools: 1\nlist:\n  - a\n",
    }
    with_option, _ = _plan(tmp_path, files, path, value)
    without, _ = _plan(tmp_path, files, path, value, in_include=False)
    assert with_option == without == expected


def test_key_below_a_merged_list_item_goes_into_its_included_side(tmp_path: Path) -> None:
    """Req 10.1, 10.2: a merged mapping-form include as a list item gains the key in its
    included file, before the item's override-only key."""
    files = {ROOT: "steps:\n  - $ref: file:step.yaml\n    retry: 2\n", "step.yaml": "name: a\n"}
    patched, reloaded = _plan(tmp_path, files, "steps.0.timeout", "5")
    assert patched == {"step.yaml": "name: a\ntimeout: 5\n"}
    steps = reloaded["steps"]
    assert isinstance(steps, list)
    assert _keys(steps[0]) == ["name", "timeout", "retry"]
