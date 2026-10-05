"""Unit tests of the deep merge of mapping-form includes (task 1.1):
``troupe.config.overrides.deep_merge`` and the layer fields of the model.

Each case loads real files from a temporary theatre and merges the included content
(``coder.yaml``) with the ``$ref`` mapping written in the root file (``include.form``), as the
loader does for ``{$ref: ..., <overrides>}``.

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

import dataclasses
from collections.abc import Mapping
from pathlib import Path

from troupe.config import constants
from troupe.config.loader import load_config
from troupe.config.model import (
    ConfigMapping,
    ConfigNode,
    ConfigScalar,
    ConfigSequence,
    IncludeRef,
    MergeLayers,
)
from troupe.config.overrides import deep_merge, is_ref_key, override_entries

ROOT = "troupe.yml"

CODER = """\
model: small  # shared
params:
  temperature: 0.7
  top_p: 0.9
tools: [read, write]
name: coder
limits:
  tokens: 100
retries: 2
"""

CONFIG = """\
agents:
  coder: file:coder.yaml
over:
  $ref: file:coder.yaml
  extra: 1
  params:
    seed: 7
    temperature: 0.3
  tools: [read]
  name:
    short: c
  limits: false
  late: true
"""


def _write(tmp_path: Path, files: Mapping[str, str]) -> None:
    for name, text in files.items():
        (tmp_path / name).write_text(text, encoding="utf-8")


def _mapping(node: ConfigNode | None) -> ConfigMapping:
    assert isinstance(node, ConfigMapping)
    return node


def _value(mapping: ConfigMapping, key: str) -> ConfigNode:
    entry = mapping.get(key)
    assert entry is not None, key
    return entry.value


def _layers(tmp_path: Path) -> tuple[ConfigMapping, ConfigMapping, IncludeRef]:
    """Return the included content (with no include), the override mapping and the include ref."""
    _write(tmp_path, {"coder.yaml": CODER, ROOT: CONFIG})
    document = load_config(tmp_path / ROOT)
    root_include = _mapping(_value(_mapping(_value(document.root, "agents")), "coder"))
    # The loader merges ``over`` itself (task 2.1); its ``$ref`` mapping as written is the form.
    loaded_over = _value(document.root, "over")
    assert loaded_over.include is not None
    over = _mapping(loaded_over.include.form)
    assert root_include.include is not None
    include = dataclasses.replace(root_include.include, form=over)
    return dataclasses.replace(root_include, include=None), over, include


def _keys(mapping: ConfigMapping) -> list[str]:
    return [entry.key for entry in mapping.entries]


def test_ref_key_constant_is_dollar_ref() -> None:
    """Req 1.1, 1.5: the reserved key of a mapping-form include has one named home."""
    assert constants.INCLUDE_REF_KEY == "$ref"
    assert is_ref_key("$ref")
    assert not is_ref_key("ref")
    assert not is_ref_key("$ref ")


def test_override_entries_leave_out_the_ref_entry(tmp_path: Path) -> None:
    """Req 1.5: the overrides of a ``$ref`` mapping are its entries without ``$ref``, in order."""
    _base, over, _include = _layers(tmp_path)
    assert [entry.key for entry in override_entries(over)] == [
        "extra",
        "params",
        "tools",
        "name",
        "limits",
        "late",
    ]
    assert all(entry is over.get(entry.key) for entry in override_entries(over))


def test_merge_keeps_included_order_then_appends_override_only_keys(tmp_path: Path) -> None:
    """Req 1.3: included keys in their order, overridden ones in place, then override-only keys."""
    base, over, include = _layers(tmp_path)
    merged = deep_merge(base, over, include=include)
    assert _keys(merged) == [
        "model",
        "params",
        "tools",
        "name",
        "limits",
        "retries",
        "extra",
        "late",
    ]
    assert _keys(_mapping(_value(merged, "params"))) == ["temperature", "top_p", "seed"]


def test_merge_never_carries_the_ref_entry(tmp_path: Path) -> None:
    """Req 1.5: the ``$ref`` entry of the override mapping is not part of the merged value."""
    base, over, include = _layers(tmp_path)
    assert over.get(constants.INCLUDE_REF_KEY) is not None
    merged = deep_merge(base, over, include=include)
    assert merged.get(constants.INCLUDE_REF_KEY) is None


def test_merge_merges_nested_mappings_recursively(tmp_path: Path) -> None:
    """Req 1.2: mappings on both sides are merged: overridden, kept and added keys together."""
    base, over, include = _layers(tmp_path)
    params = _mapping(_value(deep_merge(base, over, include=include), "params"))
    values = {entry.key: entry.value for entry in params.entries}
    assert {key: _scalar(node).value for key, node in values.items()} == {
        "temperature": 0.3,
        "top_p": 0.9,
        "seed": 7,
    }
    assert params.layers == MergeLayers(
        base=_mapping(_value(base, "params")), over=_mapping(_value(over, "params"))
    )


def _scalar(node: ConfigNode) -> ConfigScalar:
    assert isinstance(node, ConfigScalar)
    return node


def test_merge_replaces_values_of_other_kinds_with_the_override(tmp_path: Path) -> None:
    """Req 1.2: a scalar over a mapping, a mapping over a scalar and a list replace the value."""
    base, over, include = _layers(tmp_path)
    merged = deep_merge(base, over, include=include)
    limits = _value(merged, "limits")
    name = _value(merged, "name")
    tools = _value(merged, "tools")
    assert limits is _value(over, "limits")
    assert _scalar(limits).value is False
    assert name is _value(over, "name")
    assert _mapping(name).layers is None
    assert tools is _value(over, "tools")
    assert isinstance(tools, ConfigSequence)
    assert [_scalar(item).value for item in tools.items] == ["read"]


def test_merge_keeps_not_overridden_values_from_the_included_side(tmp_path: Path) -> None:
    """Req 1.2: included keys that are not overridden keep their included value."""
    base, over, include = _layers(tmp_path)
    merged = deep_merge(base, over, include=include)
    assert merged.get("model") is base.get("model")
    assert merged.get("retries") is base.get("retries")


def test_overridden_scalars_keep_their_own_origin(tmp_path: Path) -> None:
    """Req 1.2, 7.1, 7.2: each merged scalar names the file it is written in."""
    base, over, include = _layers(tmp_path)
    params = _mapping(_value(deep_merge(base, over, include=include), "params"))
    temperature = _scalar(_value(params, "temperature"))
    top_p = _scalar(_value(params, "top_p"))
    seed = _scalar(_value(params, "seed"))
    assert temperature.origin.file.real_path == (tmp_path / ROOT).resolve()
    span = temperature.origin.span
    assert temperature.origin.file.text[span.start : span.end] == "0.3"
    assert seed.origin.file.real_path == (tmp_path / ROOT).resolve()
    assert top_p.origin.file.real_path == (tmp_path / "coder.yaml").resolve()


def test_merged_mapping_records_both_layers_and_the_include(tmp_path: Path) -> None:
    """Req 1.2: the merged mapping keeps the included side's style and origin, the given include
    and both layers as loaded."""
    base, over, include = _layers(tmp_path)
    merged = deep_merge(base, over, include=include)
    assert merged.layers is not None
    assert merged.layers.base is base
    assert merged.layers.over is over
    assert merged.style is base.style
    assert merged.origin == base.origin
    assert merged.include is include
    assert include.form is over


def test_nested_merged_mapping_carries_the_include_of_its_included_value(tmp_path: Path) -> None:
    """Req 1.2: a nested merged mapping keeps its included value's include (usually none)."""
    _write(
        tmp_path,
        {
            "coder.yaml": "params: file:params.yaml\nplain:\n  a: 1\n",
            "params.yaml": "temperature: 0.7\n",
            ROOT: "coder: file:coder.yaml\nover:\n  params:\n    seed: 1\n  plain:\n    b: 2\n",
        },
    )
    document = load_config(tmp_path / ROOT)
    base = dataclasses.replace(_mapping(_value(document.root, "coder")), include=None)
    merged = deep_merge(base, _mapping(_value(document.root, "over")), include=None)
    params = _mapping(_value(merged, "params"))
    assert params.include is not None
    assert params.include is _value(base, "params").include
    assert _keys(params) == ["temperature", "seed"]
    assert _mapping(_value(merged, "plain")).include is None
    assert merged.include is None


def test_merge_does_not_change_its_inputs(tmp_path: Path) -> None:
    """Req 1.2, 1.5: merging leaves both layers exactly as loaded."""
    base, over, include = _layers(tmp_path)
    fresh_base, fresh_over, _ = _layers(tmp_path)
    deep_merge(base, over, include=include)
    assert base == fresh_base
    assert over == fresh_over
    assert base.layers is None
    assert over.layers is None


def test_scalar_form_include_and_unmerged_nodes_have_no_layers_or_form(tmp_path: Path) -> None:
    """Req 1.4: a scalar-form include and every unmerged node look exactly as before."""
    _write(tmp_path, {"coder.yaml": CODER, ROOT: CONFIG})
    document = load_config(tmp_path / ROOT)
    coder = _mapping(_value(_mapping(_value(document.root, "agents")), "coder"))
    assert coder.include is not None
    assert coder.include.form is None
    assert coder.layers is None
    assert document.root.layers is None
