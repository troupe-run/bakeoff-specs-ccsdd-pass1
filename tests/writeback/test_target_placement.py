"""Unit tests of where new keys are written and of the ``$ref`` PATH refusal (task 3.3).

``resolve_target`` gives every :class:`AddKeyTarget` a :class:`KeyPlacement` computed by the
placement walk (design.md "Placement walk", research.md Decision 2): it goes up from the deepest
existing mapping A and stops at the first merged mapping (into its override side), bare
mapping-form include (into its ``$ref`` mapping), scalar-form YAML include (to convert) or list
item (A itself); reaching the root keeps A. A set PATH with a ``$ref`` segment is refused first.

Each case loads real files from a temporary theatre.

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

from collections.abc import Mapping
from pathlib import Path

import pytest

from troupe.config.errors import ExitCode, WriteRefusedError
from troupe.config.loader import load_config
from troupe.config.model import ConfigDocument, ConfigMapping, ConfigNode, ConfigSequence
from troupe.config.paths import ConfigPath
from troupe.config.writeback.compare import IncludeDirective, root_plain
from troupe.config.writeback.target import (
    AddKeyTarget,
    IncludeSite,
    KeyPlacement,
    place_new_keys,
    resolve_target,
)
from troupe.config.writeback.value import parse_value


def _load(tmp_path: Path, config: str, files: Mapping[str, str] | None = None) -> ConfigDocument:
    for name, text in (files or {}).items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    root = tmp_path / "troupe.yml"
    root.write_text(config, encoding="utf-8")
    return load_config(root)


def _add_key(document: ConfigDocument, path: str, value: str = "1") -> AddKeyTarget:
    config_path = ConfigPath.parse(path)
    target = resolve_target(document, config_path, parse_value(value, config_path)).target
    assert isinstance(target, AddKeyTarget)
    return target


def _placement(document: ConfigDocument, path: str) -> KeyPlacement:
    return _add_key(document, path).placement


def _at(document: ConfigDocument, path: str) -> ConfigNode:
    node: ConfigNode = document.root
    for segment in ConfigPath.parse(path).segments:
        if isinstance(node, ConfigSequence):
            node = node.items[int(segment)]
        else:
            assert isinstance(node, ConfigMapping)
            entry = node.get(segment)
            assert entry is not None
            node = entry.value
    return node


def _mapping_at(document: ConfigDocument, path: str) -> ConfigMapping:
    node = _at(document, path)
    assert isinstance(node, ConfigMapping)
    return node


def _override_side(mapping: ConfigMapping) -> ConfigMapping:
    assert mapping.layers is not None
    return mapping.layers.over


def _form(node: ConfigNode) -> ConfigMapping:
    assert node.include is not None and node.include.form is not None
    return node.include.form


def _file(tmp_path: Path, mapping: ConfigMapping | None) -> Path:
    assert mapping is not None
    return mapping.origin.file.path.relative_to(tmp_path)


CODER = "model: small\nparams:\n  temperature: 0.2\n"


# --- merged mappings ---------------------------------------------------------------------------


def test_new_key_in_merged_root_goes_to_the_root_ref_mapping(tmp_path: Path) -> None:
    """Req 4.1, 4.3: a new top-level key below a root include with overrides goes into the
    root file's ``$ref`` mapping, not into the included file."""
    document = _load(tmp_path, "$ref: file:base.yaml\nlocal: 1\n", {"base.yaml": CODER})
    placement = _placement(document, "extra")
    assert placement.mapping is _override_side(document.root)
    assert _file(tmp_path, placement.mapping) == Path("troupe.yml")
    assert placement.convert is None
    assert placement.prefix == ()


def test_new_key_below_included_nested_mapping_creates_it_in_the_override(tmp_path: Path) -> None:
    """Req 4.1, 4.4: a key below an included nested mapping that the override does not hold yet
    goes into the include's ``$ref`` mapping with ``params`` as an intermediate mapping."""
    document = _load(
        tmp_path, "agent:\n  $ref: file:coder.yaml\n  model: big\n", {"coder.yaml": CODER}
    )
    target = _add_key(document, "agent.params.top_p")
    assert target.mapping is _mapping_at(document, "agent.params")
    assert target.missing == ("top_p",)
    placement = target.placement
    assert placement.mapping is _override_side(_mapping_at(document, "agent"))
    assert _file(tmp_path, placement.mapping) == Path("troupe.yml")
    assert placement.prefix == ("params",)
    assert placement.convert is None


def test_new_key_in_merged_root_below_included_mapping_has_prefix(tmp_path: Path) -> None:
    """Req 4.1, 4.4: below a merged root, a key under an included mapping gets the prefix."""
    document = _load(tmp_path, "$ref: file:base.yaml\nlocal: 1\n", {"base.yaml": CODER})
    placement = _placement(document, "params.top_p")
    assert placement.mapping is _override_side(document.root)
    assert placement.prefix == ("params",)


def test_new_key_in_existing_merged_override_mapping_goes_into_it(tmp_path: Path) -> None:
    """Req 4.3: when the deepest mapping is also written in the override, the key goes into
    that override mapping."""
    document = _load(
        tmp_path,
        "agent:\n  $ref: file:coder.yaml\n  params:\n    temperature: 0.5\n",
        {"coder.yaml": CODER},
    )
    params = _mapping_at(document, "agent.params")
    placement = _placement(document, "agent.params.top_p")
    assert placement.mapping is _override_side(params)
    assert _file(tmp_path, placement.mapping) == Path("troupe.yml")
    assert [entry.key for entry in placement.mapping.entries] == ["temperature"]
    assert placement.prefix == ()


def test_new_key_descends_into_override_only_mapping(tmp_path: Path) -> None:
    """Req 4.3: a mapping written only in the override is reached by descending the override
    side, and the key goes into it with no intermediate mappings."""
    document = _load(
        tmp_path,
        "agent:\n  $ref: file:coder.yaml\n  extra:\n    deep:\n      a: 1\n",
        {"coder.yaml": CODER},
    )
    deep = _mapping_at(document, "agent.extra.deep")
    placement = _placement(document, "agent.extra.deep.b")
    assert placement.mapping is deep
    assert _file(tmp_path, placement.mapping) == Path("troupe.yml")
    assert placement.prefix == ()


def test_new_key_below_merged_mapping_lacking_the_deeper_key(tmp_path: Path) -> None:
    """Req 4.3, 4.4: below a merged mapping whose override lacks the next key, the key goes into
    that override mapping with the missing keys as prefix."""
    document = _load(
        tmp_path,
        "agent:\n  $ref: file:coder.yaml\n  params:\n    temperature: 0.5\n",
        {"coder.yaml": "params:\n  temperature: 0.2\n  sampling:\n    k: 1\n"},
    )
    params = _mapping_at(document, "agent.params")
    placement = _placement(document, "agent.params.sampling.p")
    assert placement.mapping is _override_side(params)
    assert placement.prefix == ("sampling",)


def test_override_holding_a_scalar_form_include_is_marked_for_conversion(tmp_path: Path) -> None:
    """Req 4.2, 4.4: an override value that is itself a scalar-form include is converted in the
    including file, at its written entry below the merged parent."""
    document = _load(
        tmp_path,
        "agent:\n  $ref: file:coder.yaml\n  params: file:params.yaml\n",
        {"coder.yaml": CODER, "params.yaml": "top_k: 3\nsampling:\n  k: 1\n"},
    )
    params = _mapping_at(document, "agent.params")
    placement = _placement(document, "agent.params.sampling.p")
    assert placement.mapping is None
    site = placement.convert
    assert site is not None
    assert site.node is _override_side(params)
    assert site.parent is _mapping_at(document, "agent")
    assert site.key == "params"
    assert placement.prefix == ("sampling",)


# --- bare mapping form and scalar form ---------------------------------------------------------


def test_new_key_below_bare_mapping_form_goes_to_its_ref_mapping(tmp_path: Path) -> None:
    """Req 4.1, 4.5: a bare mapping-form include takes the new key in its ``$ref`` mapping."""
    document = _load(tmp_path, "agent:\n  $ref: file:coder.yaml\n", {"coder.yaml": CODER})
    agent = _at(document, "agent")
    placement = _placement(document, "agent.top_p")
    assert placement.mapping is _form(agent)
    assert _file(tmp_path, placement.mapping) == Path("troupe.yml")
    assert placement.prefix == ()
    deeper = _placement(document, "agent.params.top_p")
    assert deeper.mapping is _form(agent)
    assert deeper.prefix == ("params",)


def test_new_key_below_scalar_form_include_marks_it_for_conversion(tmp_path: Path) -> None:
    """Req 4.1, 4.4: a scalar-form YAML include is marked for conversion where it is written."""
    document = _load(tmp_path, "agent: file:coder.yaml\n", {"coder.yaml": CODER})
    placement = _placement(document, "agent.params.top_p")
    assert placement.mapping is None
    assert placement.convert == IncludeSite(
        node=_at(document, "agent"), parent=document.root, key="agent"
    )
    assert placement.convert is not None and placement.convert.node is _at(document, "agent")
    assert placement.prefix == ("params",)


def test_root_level_bare_ref_file_takes_new_keys_in_its_ref_mapping(tmp_path: Path) -> None:
    """Req 4.1, 4.4: a root that is a bare ``$ref`` mapping takes new keys in it."""
    document = _load(tmp_path, "$ref: file:base.yaml\n", {"base.yaml": CODER})
    placement = _placement(document, "extra")
    assert placement.mapping is _form(document.root)
    assert _file(tmp_path, placement.mapping) == Path("troupe.yml")
    assert placement.prefix == ()
    assert _placement(document, "params.top_p").prefix == ("params",)


# --- nested includes ---------------------------------------------------------------------------


def test_nested_scalar_includes_convert_the_innermost_in_its_directive_file(
    tmp_path: Path,
) -> None:
    """Req 4.2: below nested includes the innermost include is converted, in the included file
    that holds its reference."""
    document = _load(
        tmp_path,
        "agents:\n  coder: file:coder.yaml\n",
        {"coder.yaml": "params: file:params.yaml\n", "params.yaml": "temperature: 0.2\n"},
    )
    placement = _placement(document, "agents.coder.params.top_p")
    site = placement.convert
    assert site is not None
    assert site.node is _at(document, "agents.coder.params")
    assert site.parent is _at(document, "agents.coder")
    assert site.parent.origin.file.path == tmp_path / "coder.yaml"
    assert site.key == "params"
    assert placement.prefix == ()


def test_nested_mapping_form_includes_choose_the_innermost_ref_mapping(tmp_path: Path) -> None:
    """Req 4.2, 4.4: the nearest include above A wins, even in an included file."""
    document = _load(
        tmp_path,
        "x:\n  $ref: file:a.yaml\n  k: 1\n",
        {"a.yaml": "inner:\n  $ref: file:b.yaml\n  m: 2\n", "b.yaml": "deep:\n  v: 1\n"},
    )
    inner = _mapping_at(document, "x.inner")
    placement = _placement(document, "x.inner.deep.w")
    assert placement.mapping is _override_side(inner)
    assert _file(tmp_path, placement.mapping) == Path("a.yaml")
    assert placement.prefix == ("deep",)


# --- lists -------------------------------------------------------------------------------------


def test_list_item_inside_an_include_keeps_the_step_1_rule(tmp_path: Path) -> None:
    """Req 6.2: a list item reached before any include keeps the key in the item's file."""
    files = {"coder.yaml": "steps:\n  - name: a\n"}
    for config in ("agent: file:coder.yaml\n", "agent:\n  $ref: file:coder.yaml\n  m: 1\n"):
        document = _load(tmp_path, config, files)
        item = _mapping_at(document, "agent.steps.0")
        placement = _placement(document, "agent.steps.0.retry")
        assert placement.mapping is item
        assert _file(tmp_path, placement.mapping) == Path("coder.yaml")
        assert placement.convert is None
        assert placement.prefix == ()


def test_mapping_below_a_list_item_keeps_the_step_1_rule(tmp_path: Path) -> None:
    """Req 6.2: a mapping below a list item (A inside the item) is A itself."""
    document = _load(
        tmp_path, "agent: file:coder.yaml\n", {"coder.yaml": "steps:\n  - opts:\n      a: 1\n"}
    )
    placement = _placement(document, "agent.steps.0.opts.b")
    assert placement.mapping is _mapping_at(document, "agent.steps.0.opts")
    assert placement.prefix == ()


def test_list_item_include_is_written_as_override_in_the_list_file(tmp_path: Path) -> None:
    """Req 6.3: a list item that is an include takes the key as its override in the list's file
    (converted in scalar form, in its ``$ref`` mapping in bare form)."""
    document = _load(
        tmp_path,
        "steps:\n  - file:step.yaml\n  - $ref: file:step.yaml\n",
        {"step.yaml": "name: a\n"},
    )
    steps = _at(document, "steps")
    scalar_form = _placement(document, "steps.0.retry")
    assert scalar_form.mapping is None
    assert scalar_form.convert is not None
    assert scalar_form.convert.node is _at(document, "steps.0")
    assert scalar_form.convert.parent is steps
    assert scalar_form.convert.key == 0
    assert scalar_form.prefix == ()
    bare_form = _placement(document, "steps.1.retry")
    assert bare_form.mapping is _form(_at(document, "steps.1"))
    assert _file(tmp_path, bare_form.mapping) == Path("troupe.yml")
    assert bare_form.prefix == ()


# --- no include --------------------------------------------------------------------------------


def test_plain_mapping_keeps_the_new_key_in_itself(tmp_path: Path) -> None:
    """Req 4.6: below no include the key goes into A itself, as before."""
    document = _load(tmp_path, "a:\n  x: 1\n")
    placement = _placement(document, "a.p.q")
    assert placement.mapping is _mapping_at(document, "a")
    assert placement.convert is None
    assert placement.prefix == ()
    root = _placement(document, "b")
    assert root.mapping is document.root
    assert root.prefix == ()


def test_place_new_keys_takes_the_path_of_the_deepest_mapping(tmp_path: Path) -> None:
    """Req 4.1, 4.4: ``place_new_keys`` runs the walk from the mapping at ``mapping_path``."""
    document = _load(
        tmp_path, "agent:\n  $ref: file:coder.yaml\n  model: big\n", {"coder.yaml": CODER}
    )
    placement = place_new_keys(document, ConfigPath.parse("agent.params"))
    assert placement.mapping is _override_side(_mapping_at(document, "agent"))
    assert placement.prefix == ("params",)


# --- the root plain form -------------------------------------------------------------------------


def test_current_configuration_unwraps_a_root_include(tmp_path: Path) -> None:
    """Req 4.1, 8.1: target resolution reads the current configuration from the unwrapped root."""
    document = _load(
        tmp_path,
        "$ref: file:base.yaml\nnote: file:note.txt\n",
        {"base.yaml": CODER, "note.txt": "hi\r\nthere\r\n"},
    )
    config_path = ConfigPath.parse("note")
    request = resolve_target(document, config_path, parse_value('"a\\nb"', config_path))
    assert request.current == root_plain(document)
    note = request.intended["note"]
    assert isinstance(note, IncludeDirective)
    assert note.content == "a\r\nb"
    assert request.intended["model"] == "small"


# --- `$ref` in PATH ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path", ["$ref", "agent.$ref", "agent.$ref.x", "agent.params.$ref", "missing.x.$ref"]
)
def test_ref_segment_in_set_path_is_refused(tmp_path: Path, path: str) -> None:
    """Req 8.1: a ``$ref`` segment anywhere in PATH is refused (exit 3) before anything else,
    naming PATH and the root file and stating that ``$ref`` is reserved for includes."""
    document = _load(
        tmp_path, "agent:\n  $ref: file:coder.yaml\n  model: big\n", {"coder.yaml": CODER}
    )
    config_path = ConfigPath.parse(path)
    with pytest.raises(WriteRefusedError) as caught:
        resolve_target(document, config_path, parse_value("1", config_path))
    error = caught.value
    assert error.exit_code is ExitCode.WRITE_REFUSED
    assert error.path == path
    assert error.file == tmp_path / "troupe.yml"
    assert "`$ref` is reserved for includes" in error.problem
    assert str(error).startswith(f"{tmp_path / 'troupe.yml'}: {path}: ")
