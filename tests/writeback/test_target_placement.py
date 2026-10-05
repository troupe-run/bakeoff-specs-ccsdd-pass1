"""Unit tests of where new keys are written and of the ``$ref`` PATH refusal (task 3.3).

``resolve_target`` gives every :class:`AddKeyTarget` a :class:`KeyPlacement` computed by the
placement walk (design.md "Placement walk", research.md Decision 2): it follows the layers that
hold the content of the deepest existing mapping A (an override side when it holds A, else the
included side, chained includes included) and, going up from A's layer, stops at the first YAML
include root (into its ``$ref`` mapping, or to convert, also a file's whole root), override side or
list item (the written mapping holding A); reaching the root keeps it. The remediation cases pin
requirements choice 5 with chained includes. A set PATH with a ``$ref`` segment is refused first.

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
from troupe.config.writeback.compare import IncludeDirective, PlainValue, root_plain
from troupe.config.writeback.target import (
    AddKeyTarget,
    IncludeSite,
    KeyPlacement,
    ReplaceTarget,
    SetRequest,
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


# --- an override on a file whose root is itself a `$ref` mapping (requirements choice 5) -------

CHAINED_ROOT = "c:\n  $ref: file:a.yaml\n  x: 1\n"
CHAINED_INNER = "sub:\n  m: 1\n"


def test_key_from_a_chained_bare_include_goes_into_the_middle_ref_mapping(tmp_path: Path) -> None:
    """Req 4.2, 4.4 (choice 5): `c` overrides a.yaml, whose root is the bare `$ref: file:b.yaml`;
    `c.sub` comes from b.yaml only, so `c.sub.n` goes into a.yaml's `$ref` mapping (the file
    holding the reference to b.yaml) with `sub` as prefix, not into troupe.yml's override."""
    document = _load(
        tmp_path, CHAINED_ROOT, {"a.yaml": "$ref: file:b.yaml\n", "b.yaml": CHAINED_INNER}
    )
    c = _mapping_at(document, "c")
    assert c.layers is not None
    placement = _placement(document, "c.sub.n")
    assert placement.mapping is _form(c.layers.base)
    assert _file(tmp_path, placement.mapping) == Path("a.yaml")
    assert placement.prefix == ("sub",)
    assert placement.convert is None


def test_key_from_a_chained_include_with_overrides_goes_into_the_middle_override(
    tmp_path: Path,
) -> None:
    """Req 4.2, 4.3, 4.4 (choice 5): when a.yaml's root `$ref` mapping has overrides of its own,
    the key coming from b.yaml goes into that override side in a.yaml, prefix `sub`."""
    document = _load(
        tmp_path,
        CHAINED_ROOT,
        {"a.yaml": "$ref: file:b.yaml\nk: 1\n", "b.yaml": CHAINED_INNER},
    )
    c = _mapping_at(document, "c")
    assert c.layers is not None
    placement = _placement(document, "c.sub.n")
    assert placement.mapping is _override_side(c.layers.base)
    assert _file(tmp_path, placement.mapping) == Path("a.yaml")
    assert placement.prefix == ("sub",)


def test_key_directly_below_a_chained_include_stays_in_the_outer_override(tmp_path: Path) -> None:
    """Req 4.3: with A the merged mapping `c` itself, the key goes into troupe.yml's override."""
    document = _load(
        tmp_path, CHAINED_ROOT, {"a.yaml": "$ref: file:b.yaml\n", "b.yaml": CHAINED_INNER}
    )
    placement = _placement(document, "c.n")
    assert placement.mapping is _override_side(_mapping_at(document, "c"))
    assert _file(tmp_path, placement.mapping) == Path("troupe.yml")
    assert placement.prefix == ()


def test_key_below_a_mapping_the_outer_override_writes_stays_there(tmp_path: Path) -> None:
    """Req 4.3: when the deepest mapping is also written in troupe.yml's override, the key goes
    there even though a.yaml's root is a `$ref` mapping."""
    document = _load(
        tmp_path,
        CHAINED_ROOT + "  sub:\n    o: 1\n",
        {"a.yaml": "$ref: file:b.yaml\n", "b.yaml": CHAINED_INNER},
    )
    placement = _placement(document, "c.sub.n")
    assert placement.mapping is _override_side(_mapping_at(document, "c.sub"))
    assert _file(tmp_path, placement.mapping) == Path("troupe.yml")
    assert placement.prefix == ()


def _ref_mapping_of(tmp_path: Path, placement: KeyPlacement, file: str) -> ConfigMapping:
    """The placement's mapping, asserted to be the `$ref` mapping written as the root of
    ``file`` (its first entry is `$ref`, at the start of the file)."""
    mapping = placement.mapping
    assert mapping is not None
    assert _file(tmp_path, mapping) == Path(file)
    assert mapping.entries[0].key == "$ref"
    assert mapping.origin.span.start == 0
    return mapping


@pytest.mark.parametrize(
    "outer",
    ["c:\n  $ref: file:a.yaml\n", "c: file:a.yaml\n", "c: {$ref: file:a.yaml}\n"],
    ids=["bare-outer", "scalar-outer", "flow-bare-outer"],
)
def test_key_from_a_chained_include_without_outer_overrides_goes_into_the_middle_file(
    tmp_path: Path, outer: str
) -> None:
    """Req 4.2, 4.4 (choice 5): whatever the form of troupe.yml's include of a.yaml (bare, scalar,
    flow), when a.yaml's root is `$ref: file:b.yaml` and `c.sub` comes from b.yaml, `c.sub.n`
    goes into a.yaml's `$ref` mapping with prefix `sub`; troupe.yml is not converted."""
    document = _load(tmp_path, outer, {"a.yaml": "$ref: file:b.yaml\n", "b.yaml": CHAINED_INNER})
    placement = _placement(document, "c.sub.n")
    _ref_mapping_of(tmp_path, placement, "a.yaml")
    assert placement.convert is None
    assert placement.prefix == ("sub",)


@pytest.mark.parametrize(
    "outer",
    [CHAINED_ROOT, "c:\n  $ref: file:a.yaml\n", "c: file:a.yaml\n"],
    ids=["outer-with-overrides", "bare-outer", "scalar-outer"],
)
def test_key_from_a_scalar_middle_root_converts_the_middle_files_root(
    tmp_path: Path, outer: str
) -> None:
    """Req 4.2, 4.4, 5.1 (choice 5): when a.yaml's whole root is the scalar `file:b.yaml`, the key
    coming from b.yaml converts that root include in a.yaml (a root-level site, no parent),
    prefix `sub`."""
    document = _load(tmp_path, outer, {"a.yaml": "file:b.yaml\n", "b.yaml": CHAINED_INNER})
    placement = _placement(document, "c.sub.n")
    assert placement.mapping is None
    site = placement.convert
    assert site is not None
    assert site.parent is None and site.key is None
    assert site.ref.directive.file.path == tmp_path / "a.yaml"
    assert site.ref.target.path == tmp_path / "b.yaml"
    assert placement.prefix == ("sub",)


def test_key_below_an_override_mapping_lacking_it_follows_the_content_into_the_middle_file(
    tmp_path: Path,
) -> None:
    """Req 4.2, 4.4 (choice 5): troupe.yml overrides `c.sub` (`k: 1`) but `c.sub.deep` comes from
    b.yaml, referenced by a.yaml's root `$ref`; `c.sub.deep.n` goes into a.yaml's `$ref`
    mapping with prefix `sub`, `deep` (not into troupe.yml's `sub` override)."""
    document = _load(
        tmp_path,
        "c:\n  $ref: file:a.yaml\n  sub:\n    k: 1\n",
        {"a.yaml": "$ref: file:b.yaml\n", "b.yaml": "sub:\n  deep:\n    m: 1\n"},
    )
    placement = _placement(document, "c.sub.deep.n")
    _ref_mapping_of(tmp_path, placement, "a.yaml")
    assert placement.prefix == ("sub", "deep")


def test_key_below_a_scalar_form_root_include_converts_the_root_file(tmp_path: Path) -> None:
    """Req 4.1, 5.1: a root file that is the scalar `file:base.yaml` gets the new key as an
    override: its root include is converted (a root-level site), base.yaml is not written."""
    document = _load(tmp_path, "file:base.yaml\n", {"base.yaml": CODER})
    placement = _placement(document, "extra")
    site = placement.convert
    assert site is not None and placement.mapping is None
    assert site.parent is None and site.key is None
    assert site.ref.directive.file.path == tmp_path / "troupe.yml"
    assert placement.prefix == ()
    assert _placement(document, "params.top_p").prefix == ("params",)


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
    placement = place_new_keys(document, ConfigPath.parse("agent.params"), in_include=False)
    assert placement.mapping is _override_side(_mapping_at(document, "agent"))
    assert placement.prefix == ("params",)


# --- `--in-include`: the step 1 placement (CR-B, task 3.8) ---------------------------------------


def _in_include(document: ConfigDocument, path: str, value: str = "1") -> SetRequest:
    config_path = ConfigPath.parse(path)
    return resolve_target(document, config_path, parse_value(value, config_path), in_include=True)


def _in_include_placement(document: ConfigDocument, path: str) -> KeyPlacement:
    target = _in_include(document, path).target
    assert isinstance(target, AddKeyTarget)
    return target.placement


def _content_keys(value: PlainValue) -> list[str]:
    if isinstance(value, IncludeDirective):
        value = value.content
    assert isinstance(value, dict)
    return list(value)


def test_in_include_below_a_scalar_form_include_places_into_the_included_root(
    tmp_path: Path,
) -> None:
    """Req 10.1: with ``in_include`` a key below a scalar-form YAML include goes into the
    included content (the included file), with no conversion."""
    document = _load(tmp_path, "agent: file:coder.yaml\n", {"coder.yaml": CODER})
    placement = _in_include_placement(document, "agent.top_p")
    assert placement.mapping is _mapping_at(document, "agent")
    assert _file(tmp_path, placement.mapping) == Path("coder.yaml")
    assert placement.convert is None
    assert placement.prefix == ()


def test_in_include_below_a_merged_mapping_places_into_its_included_side(tmp_path: Path) -> None:
    """Req 10.1, 10.2: with ``in_include`` a key below a merged mapping goes into its included
    side, and the intended configuration lists it after the included keys and before the
    override-only keys."""
    document = _load(
        tmp_path,
        "agent:\n  $ref: file:coder.yaml\n  model: big\n  extra: 1\n",
        {"coder.yaml": CODER},
    )
    agent = _mapping_at(document, "agent")
    request = _in_include(document, "agent.top_p.x")
    target = request.target
    assert isinstance(target, AddKeyTarget)
    assert target.placement.mapping is not None
    assert agent.layers is not None
    assert target.placement.mapping is agent.layers.base
    assert _file(tmp_path, target.placement.mapping) == Path("coder.yaml")
    assert target.placement.prefix == ()
    assert target.missing == ("top_p", "x")
    assert _content_keys(request.intended["agent"]) == ["model", "params", "top_p", "extra"]


def test_in_include_with_nested_includes_places_into_the_innermost_file(tmp_path: Path) -> None:
    """Req 10.1, 10.2: the included side is followed through nested merged includes to the
    innermost file; the new key comes after the innermost keys, before every override-only
    key."""
    document = _load(
        tmp_path,
        "agent:\n  $ref: file:mid.yaml\n  a: 1\n",
        {"mid.yaml": "$ref: file:inner.yaml\nb: 2\n", "inner.yaml": "c: 3\n"},
    )
    request = _in_include(document, "agent.d")
    target = request.target
    assert isinstance(target, AddKeyTarget)
    assert _file(tmp_path, target.placement.mapping) == Path("inner.yaml")
    assert _content_keys(request.intended["agent"]) == ["c", "d", "b", "a"]


def test_in_include_below_an_override_only_mapping_places_into_it(tmp_path: Path) -> None:
    """Req 10.1: an ancestor written only in an override is not merged, so the key goes into
    it, in the including file."""
    document = _load(
        tmp_path, "agent:\n  $ref: file:coder.yaml\n  extra:\n    x: 1\n", {"coder.yaml": CODER}
    )
    placement = _in_include_placement(document, "agent.extra.y")
    assert placement.mapping is _mapping_at(document, "agent.extra")
    assert _file(tmp_path, placement.mapping) == Path("troupe.yml")
    assert placement.prefix == ()


def test_in_include_below_an_included_nested_mapping_places_into_it(tmp_path: Path) -> None:
    """Req 10.1: a nested mapping only on the included side receives the key itself."""
    document = _load(
        tmp_path, "agent:\n  $ref: file:coder.yaml\n  model: big\n", {"coder.yaml": CODER}
    )
    placement = _in_include_placement(document, "agent.params.top_p")
    assert placement.mapping is _mapping_at(document, "agent.params")
    assert _file(tmp_path, placement.mapping) == Path("coder.yaml")
    assert placement.prefix == ()


def test_in_include_with_no_include_is_the_same_as_without(tmp_path: Path) -> None:
    """Req 10.5: below no include the placement and the intended configuration are those
    without ``in_include``."""
    document = _load(tmp_path, "a:\n  x: 1\nb: 2\n")
    with_option = _in_include(document, "a.p.q")
    without = _add_key(document, "a.p.q")
    assert isinstance(with_option.target, AddKeyTarget)
    assert with_option.target.placement == without.placement
    assert with_option.target.placement.mapping is _mapping_at(document, "a")
    config_path = ConfigPath.parse("a.p.q")
    plain = resolve_target(document, config_path, parse_value("1", config_path))
    assert with_option.intended == plain.intended
    assert with_option.in_include is True
    assert plain.in_include is False


def test_place_new_keys_in_include_takes_the_included_side(tmp_path: Path) -> None:
    """Req 10.1, 10.3: ``place_new_keys`` with ``in_include`` gives the included side of the
    mapping at ``mapping_path`` (also used for the new keys of a mapping VALUE)."""
    document = _load(
        tmp_path, "agent:\n  $ref: file:coder.yaml\n  model: big\n", {"coder.yaml": CODER}
    )
    agent = _mapping_at(document, "agent")
    assert agent.layers is not None
    placement = place_new_keys(document, ConfigPath.parse("agent"), in_include=True)
    assert placement == KeyPlacement(mapping=agent.layers.base, convert=None, prefix=())


def test_in_include_mapping_value_orders_new_keys_before_override_only_keys(
    tmp_path: Path,
) -> None:
    """Req 10.2, 10.3: for a mapping VALUE over a merged mapping, new keys are intended after
    the included keys and before the override-only keys, at every merged mapping below PATH."""
    document = _load(
        tmp_path,
        "agent:\n  $ref: file:coder.yaml\n  params:\n    u: 3\n    inner:\n      b: 1\n",
        {"coder.yaml": "params:\n  t: 1\n  inner:\n    a: 1\n"},
    )
    request = _in_include(document, "agent.params", "{t: 1, u: 3, inner: {a: 1, b: 1, c: 2}, k: 2}")
    assert isinstance(request.target, ReplaceTarget)
    agent = request.intended["agent"]
    assert isinstance(agent, IncludeDirective) and isinstance(agent.content, dict)
    params = agent.content["params"]
    assert _content_keys(params) == ["t", "inner", "k", "u"]
    assert isinstance(params, dict)
    assert _content_keys(params["inner"]) == ["a", "c", "b"]


def test_in_include_does_not_change_an_existing_value_target(tmp_path: Path) -> None:
    """Req 10.4: an existing value resolves to the same target and intended configuration."""
    document = _load(
        tmp_path, "agent:\n  $ref: file:coder.yaml\n  model: big\n", {"coder.yaml": CODER}
    )
    config_path = ConfigPath.parse("agent.model")
    without = resolve_target(document, config_path, parse_value("z", config_path))
    with_option = _in_include(document, "agent.model", "z")
    assert with_option.target == without.target
    assert with_option.intended == without.intended


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


# --- the override rule mark (task 5.2, choice 17) ---------------------------------------------


@pytest.mark.parametrize(
    ("config", "files", "path", "by_override"),
    [
        pytest.param(
            "agent:\n  $ref: file:coder.yaml\n", {"coder.yaml": CODER}, "agent.x", True, id="ref"
        ),
        pytest.param("agent: file:coder.yaml\n", {"coder.yaml": CODER}, "agent.x", True, id="conv"),
        pytest.param(
            "agent:\n  $ref: file:coder.yaml\n  extra:\n    a: 1\n",
            {"coder.yaml": CODER},
            "agent.extra.b",
            True,
            id="override-side",
        ),
        pytest.param(
            "a: file:mid.yaml\nb: file:mid.yaml\n",
            {"mid.yaml": "$ref: file:leaf.yaml\n", "leaf.yaml": "sub:\n  m: 1\n"},
            "a.sub.n",
            True,
            id="shared-middle",
        ),
        pytest.param(
            "a: file:mid.yaml\n",
            {"mid.yaml": "items:\n  - k: 1\n"},
            "a.items.0.j",
            False,
            id="list-item",
        ),
        pytest.param("plain:\n  k: 1\n", {}, "plain.j", False, id="plain"),
    ],
)
def test_placement_marks_whether_the_override_rule_placed_the_keys(
    tmp_path: Path, config: str, files: dict[str, str], path: str, by_override: bool
) -> None:
    """Req 4.8, 4.2, 6.2, 4.6 (choice 17): a placement into a `$ref` mapping, an override side or
    an include to convert is made by the override rule, so its keys appear wherever the written
    mapping or include is reached; a list item or a plain mapping keeps the step 1 rule."""
    document = _load(tmp_path, config, files)
    assert _placement(document, path).by_override is by_override


def test_in_include_placement_is_not_the_override_rule(tmp_path: Path) -> None:
    """Req 4.8, 10.1 (choice 17): the `--in-include` placement is the step 1 rule, never marked as
    placed by the override rule, so a shared included file keeps the step 1 refusal."""
    document = _load(tmp_path, "agent: file:coder.yaml\n", {"coder.yaml": CODER})
    config_path = ConfigPath.parse("agent.x")
    request = resolve_target(document, config_path, parse_value("1", config_path), in_include=True)
    assert isinstance(request.target, AddKeyTarget)
    assert request.target.placement.by_override is False
