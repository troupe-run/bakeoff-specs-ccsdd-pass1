"""Loading mapping-form includes ``{$ref: "file:...", <overrides>}`` (config-include-overrides
Requirement 1, task 2.1).

Every test writes a real theatre with include files and loads it with ``load_config``. A bare
mapping-form include must load to the same plain value as the scalar form; one with overrides loads
to the deep merge of the included content with the overrides. No ``$ref`` key is ever part of the
loaded value.

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

import hashlib
from pathlib import Path

import pytest

from support import MakeTheatre
from troupe.config.constants import INCLUDE_REF_KEY
from troupe.config.errors import ConfigLoadError, ExitCode
from troupe.config.loader import load_config
from troupe.config.model import (
    ConfigDocument,
    ConfigMapping,
    ConfigNode,
    ConfigScalar,
    ConfigSequence,
    IncludeKind,
)
from troupe.config.resolve import flatten_leaves
from troupe.config.writeback.compare import IncludeDirective, PlainValue, node_to_plain

CODER = """\
model: small  # shared
params:
  temperature: 0.7
  top_p: 0.9
tools: [read, write]
name: coder
retries: 2
"""


def _config(theatre: Path) -> Path:
    return theatre / ".troupe" / "troupe.yml"


def _load(make_theatre: MakeTheatre, config: str, files: dict[str, str], name: str) -> Path:
    return _config(make_theatre(config, files=files, name=name))


def _node_at(document: ConfigDocument, *keys: str | int) -> ConfigNode:
    node: ConfigNode = document.root
    for key in keys:
        if isinstance(key, int):
            assert isinstance(node, ConfigSequence)
            node = node.items[key]
        else:
            assert isinstance(node, ConfigMapping)
            entry = node.get(key)
            assert entry is not None, key
            node = entry.value
    return node


def _mapping_at(document: ConfigDocument, *keys: str | int) -> ConfigMapping:
    node = _node_at(document, *keys)
    assert isinstance(node, ConfigMapping)
    return node


def _scalar_at(document: ConfigDocument, *keys: str | int) -> ConfigScalar:
    node = _node_at(document, *keys)
    assert isinstance(node, ConfigScalar)
    return node


def _keys(mapping: ConfigMapping) -> list[str]:
    return [entry.key for entry in mapping.entries]


def _plain(document: ConfigDocument) -> PlainValue:
    return node_to_plain(document.root)


def _ref_keys(node: ConfigNode) -> list[str]:
    """Return every mapping key ``$ref`` found anywhere in the loaded value."""
    found: list[str] = []
    if isinstance(node, ConfigMapping):
        for entry in node.entries:
            if entry.key == INCLUDE_REF_KEY:
                found.append(entry.key)
            found.extend(_ref_keys(entry.value))
    elif isinstance(node, ConfigSequence):
        for item in node.items:
            found.extend(_ref_keys(item))
    return found


def _assert_no_ref(document: ConfigDocument) -> None:
    assert _ref_keys(document.root) == []
    for leaf in flatten_leaves(document.root):
        assert INCLUDE_REF_KEY not in leaf.path.segments, leaf.path


def _text(node: ConfigNode) -> str:
    return node.origin.file.text[node.origin.span.start : node.origin.span.end]


# --- bare mapping-form includes (1.1, 1.4) ---------------------------------------------------


@pytest.mark.parametrize(
    "form",
    [
        "coder:\n  $ref: file:coder.yaml\n",
        "coder: {$ref: file:coder.yaml}\n",
        "coder:\n  '$ref': \"file:coder.yaml\"\n",
    ],
)
def test_bare_yaml_mapping_form_loads_like_the_scalar_form(
    make_theatre: MakeTheatre, form: str
) -> None:
    """Req 1.1, 1.4, 1.5: a bare `$ref` mapping (any key or value style) is the scalar form."""
    files = {".troupe/coder.yaml": CODER}
    scalar = load_config(_load(make_theatre, "coder: file:coder.yaml\n", files, "scalar"))
    mapping = load_config(_load(make_theatre, form, files, "mapping"))

    assert _plain(mapping) == _plain(scalar)
    coder = _mapping_at(mapping, "coder")
    assert _keys(coder) == ["model", "params", "tools", "name", "retries"]
    assert coder.layers is None
    _assert_no_ref(mapping)


def test_bare_yaml_mapping_form_records_its_include_and_written_form(
    make_theatre: MakeTheatre,
) -> None:
    """Req 1.1, 1.4: the include names the target and the `$ref` value; the form is as written."""
    theatre = make_theatre(
        "coder:\n  $ref: file:agents/coder.yaml\n", files={".troupe/agents/coder.yaml": CODER}
    )
    document = load_config(_config(theatre))
    coder = _mapping_at(document, "coder")

    include = coder.include
    assert include is not None
    assert include.kind is IncludeKind.YAML
    assert include.spec == "agents/coder.yaml"
    assert include.target.real_path == (theatre / ".troupe" / "agents" / "coder.yaml").resolve()
    assert include.directive.file is document.root_file
    assert (
        include.directive.file.text[include.directive.span.start : include.directive.span.end]
        == "file:agents/coder.yaml"
    )
    form = include.form
    assert form is not None
    assert _keys(form) == ["$ref"]
    ref_value = form.entries[0].value
    assert isinstance(ref_value, ConfigScalar)
    assert ref_value.value == "file:agents/coder.yaml"
    assert ref_value.include is None
    assert _text(form) == "$ref: file:agents/coder.yaml\n"
    assert coder.origin.file.real_path == include.target.real_path


@pytest.mark.parametrize(
    "form",
    ["notes:\n  $ref: file:notes.md\n", "notes: {\"$ref\": 'file:notes.md'}\n"],
)
def test_bare_text_mapping_form_loads_like_the_scalar_form(
    make_theatre: MakeTheatre, form: str
) -> None:
    """Req 1.1, 1.4: a bare `$ref` mapping of a non-YAML target is the target's exact text."""
    files = {".troupe/notes.md": "# Notes\r\nline: not yaml: [\n"}
    scalar = load_config(_load(make_theatre, "notes: file:notes.md\n", files, "scalar"))
    mapping = load_config(_load(make_theatre, form, files, "mapping"))

    assert _plain(mapping) == _plain(scalar)
    notes = _scalar_at(mapping, "notes")
    assert notes.value == "# Notes\r\nline: not yaml: [\n"
    assert notes.include is not None
    assert notes.include.kind is IncludeKind.TEXT
    assert notes.include.form is not None
    assert _keys(notes.include.form) == ["$ref"]
    _assert_no_ref(mapping)


def test_bare_mapping_form_of_an_empty_yaml_file_is_null(make_theatre: MakeTheatre) -> None:
    """Req 1.4: an empty YAML target is null in mapping form as in scalar form."""
    files = {".troupe/empty.yml": "# nothing\n"}
    scalar = load_config(_load(make_theatre, "e: file:empty.yml\n", files, "scalar"))
    mapping = load_config(_load(make_theatre, "e:\n  $ref: file:empty.yml\n", files, "mapping"))

    assert _plain(mapping) == _plain(scalar)
    assert _scalar_at(mapping, "e").value is None
    assert _ref_keys(mapping.root) == []


# --- overrides: the deep merge (1.2, 1.3, 1.5) -----------------------------------------------

MERGE_CONFIG = """\
coder:
  $ref: file:coder.yaml
  extra: 1
  params:
    seed: 7
    temperature: 0.3
  tools: [read]
  name:
    short: c
  model: big
"""


def test_overrides_are_deep_merged_into_the_included_content(make_theatre: MakeTheatre) -> None:
    """Req 1.2, 1.5: mappings merge recursively, other override values replace, others are kept."""
    theatre = make_theatre(MERGE_CONFIG, files={".troupe/coder.yaml": CODER})
    document = load_config(_config(theatre))

    assert _plain(document) == {
        "coder": IncludeDirective(
            "file:coder.yaml",
            {
                "model": "big",
                "params": {"temperature": 0.3, "top_p": 0.9, "seed": 7},
                "tools": ["read"],
                "name": {"short": "c"},
                "retries": 2,
                "extra": 1,
            },
        )
    }
    _assert_no_ref(document)


def test_merged_mapping_orders_included_keys_then_override_only_keys(
    make_theatre: MakeTheatre,
) -> None:
    """Req 1.3: included keys in written order (overridden ones in place), then override-only."""
    theatre = make_theatre(MERGE_CONFIG, files={".troupe/coder.yaml": CODER})
    document = load_config(_config(theatre))

    assert _keys(_mapping_at(document, "coder")) == [
        "model",
        "params",
        "tools",
        "name",
        "retries",
        "extra",
    ]
    assert _keys(_mapping_at(document, "coder", "params")) == ["temperature", "top_p", "seed"]


def test_merged_mapping_records_its_layers_and_origins(make_theatre: MakeTheatre) -> None:
    """Req 1.1, 1.2: the merged root keeps both layers; each scalar keeps the origin of its file."""
    theatre = make_theatre(MERGE_CONFIG, files={".troupe/coder.yaml": CODER})
    document = load_config(_config(theatre))
    coder = _mapping_at(document, "coder")
    included = (theatre / ".troupe" / "coder.yaml").resolve()

    assert coder.layers is not None
    assert coder.include is not None
    assert coder.include.form is coder.layers.over
    assert coder.layers.over.origin.file is document.root_file
    assert _keys(coder.layers.over)[0] == "$ref"
    assert coder.layers.base.include is None
    assert coder.layers.base.origin.file.real_path == included
    assert _scalar_at(document, "coder", "model").origin.file is document.root_file
    assert _scalar_at(document, "coder", "params", "temperature").origin.file is document.root_file
    assert _scalar_at(document, "coder", "params", "top_p").origin.file.real_path == included
    assert _scalar_at(document, "coder", "retries").origin.file.real_path == included
    assert _mapping_at(document, "coder", "params").layers is not None


def test_scalar_overrides_a_mapping_and_mapping_overrides_a_scalar(
    make_theatre: MakeTheatre,
) -> None:
    """Req 1.2: a scalar replaces an included mapping; a mapping replaces an included scalar."""
    theatre = make_theatre(
        "c:\n  $ref: file:coder.yaml\n  params: false\n  retries: {max: 3}\n",
        files={".troupe/coder.yaml": CODER},
    )
    document = load_config(_config(theatre))

    assert _scalar_at(document, "c", "params").value is False
    assert node_to_plain(_mapping_at(document, "c", "retries")) == {"max": 3}
    assert _mapping_at(document, "c", "retries").layers is None


# --- relative targets, files read and where `$ref` may appear (1.1, 1.6, 1.8) ----------------


def test_target_is_relative_to_the_file_holding_the_ref_mapping(
    make_theatre: MakeTheatre,
) -> None:
    """Req 1.1, 1.6, 1.8: in an included file, `$ref` resolves next to that file, files listed."""
    theatre = make_theatre(
        "team: file:../shared/team.yml\n",
        files={
            "shared/team.yml": "lead:\n  $ref: file:agents/coder.yaml\n  model: big\n",
            "shared/agents/coder.yaml": CODER,
        },
    )
    document = load_config(_config(theatre))

    assert _scalar_at(document, "team", "lead", "model").value == "big"
    assert _scalar_at(document, "team", "lead", "retries").value == 2
    team = (theatre / "shared" / "team.yml").resolve()
    assert _scalar_at(document, "team", "lead", "model").origin.file.real_path == team
    assert [source.real_path for source in document.files] == [
        _config(theatre).resolve(),
        team,
        (theatre / "shared" / "agents" / "coder.yaml").resolve(),
    ]
    _assert_no_ref(document)


def test_mapping_form_include_at_the_document_root(make_theatre: MakeTheatre) -> None:
    """Req 1.6: the whole root file may be a `$ref` mapping, bare or with overrides."""
    files = {".troupe/base.yml": "agents:\n  coder: {model: small}\nname: base\n"}
    bare = load_config(_load(make_theatre, "$ref: file:base.yml\n", files, "bare"))
    merged = load_config(
        _load(
            make_theatre,
            "$ref: file:base.yml\nagents:\n  coder:\n    model: big\nextra: 1\n",
            files,
            "merged",
        )
    )

    assert bare.root.include is not None
    assert node_to_plain(bare.root) == IncludeDirective(
        "file:base.yml", {"agents": {"coder": {"model": "small"}}, "name": "base"}
    )
    assert _keys(merged.root) == ["agents", "name", "extra"]
    assert _scalar_at(merged, "agents", "coder", "model").value == "big"
    assert _scalar_at(merged, "name").value == "base"
    _assert_no_ref(bare)
    _assert_no_ref(merged)


def test_root_ref_to_a_text_file_is_not_a_mapping(make_theatre: MakeTheatre) -> None:
    """Req 1.4, 1.6: a bare root `$ref` of a text file is a scalar, which no root may be."""
    theatre = make_theatre("$ref: file:notes.md\n", files={".troupe/notes.md": "text"})
    with pytest.raises(ConfigLoadError) as info:
        load_config(_config(theatre))
    assert info.value.exit_code is ExitCode.LOAD
    assert "top-level node is a scalar" in info.value.problem


def test_mapping_form_include_as_a_list_item_block_and_flow(make_theatre: MakeTheatre) -> None:
    """Req 1.6: list items may be `$ref` mappings in block and flow style."""
    theatre = make_theatre(
        "agents:\n"
        "  - $ref: file:coder.yaml\n"
        "    model: big\n"
        "  - {$ref: file:coder.yaml, retries: 9}\n"
        "  - [{$ref: file:coder.yaml}]\n",
        files={".troupe/coder.yaml": CODER},
    )
    document = load_config(_config(theatre))

    assert _scalar_at(document, "agents", 0, "model").value == "big"
    assert _scalar_at(document, "agents", 0, "retries").value == 2
    assert _scalar_at(document, "agents", 1, "model").value == "small"
    assert _scalar_at(document, "agents", 1, "retries").value == 9
    assert _keys(_mapping_at(document, "agents", 1))[-1] == "retries"
    nested = _mapping_at(document, "agents", 2, 0)
    assert nested.include is not None
    assert nested.layers is None
    _assert_no_ref(document)


def test_flow_mapping_form_with_overrides(make_theatre: MakeTheatre) -> None:
    """Req 1.2, 1.6: `{$ref: ..., k: v}` in flow style merges like the block form."""
    files = {".troupe/coder.yaml": CODER}
    flow = load_config(
        _load(
            make_theatre,
            "coder: {$ref: file:coder.yaml, model: big, params: {seed: 7}}\n",
            files,
            "flow",
        )
    )
    block = load_config(
        _load(
            make_theatre,
            "coder:\n  $ref: file:coder.yaml\n  model: big\n  params:\n    seed: 7\n",
            files,
            "block",
        )
    )
    assert _plain(flow) == _plain(block)
    assert _scalar_at(flow, "coder", "params", "seed").value == 7
    assert _scalar_at(flow, "coder", "params", "top_p").value == 0.9


def test_mapping_form_include_inside_included_content(make_theatre: MakeTheatre) -> None:
    """Req 1.6: an included file's own `$ref` mappings are loaded, also below a merged mapping."""
    theatre = make_theatre(
        "outer:\n  $ref: file:team.yml\n  size: 2\n",
        files={
            ".troupe/team.yml": "lead:\n  $ref: file:coder.yaml\n  model: medium\nsize: 1\n",
            ".troupe/coder.yaml": CODER,
        },
    )
    document = load_config(_config(theatre))

    assert _scalar_at(document, "outer", "size").value == 2
    assert _scalar_at(document, "outer", "lead", "model").value == "medium"
    assert _scalar_at(document, "outer", "lead", "name").value == "coder"
    _assert_no_ref(document)


def test_override_values_that_are_includes_are_resolved_before_merging(
    make_theatre: MakeTheatre,
) -> None:
    """Req 1.6, 1.7: scalar-form and mapping-form includes inside overrides are merged values."""
    theatre = make_theatre(
        "coder:\n"
        "  $ref: file:coder.yaml\n"
        "  params:\n"
        "    $ref: file:params.yml\n"
        "    seed: 7\n"
        "  tools: file:tools.yml\n"
        "  name: file:name.txt\n",
        files={
            ".troupe/coder.yaml": CODER,
            ".troupe/params.yml": "temperature: 0.1\nextra: true\n",
            ".troupe/tools.yml": "[grep]\n",
            ".troupe/name.txt": "Coder\n",
        },
    )
    document = load_config(_config(theatre))

    params = _mapping_at(document, "coder", "params")
    assert node_to_plain(params) == {
        "temperature": 0.1,
        "top_p": 0.9,
        "extra": True,
        "seed": 7,
    }
    assert params.layers is not None
    assert node_to_plain(_node_at(document, "coder", "tools")) == IncludeDirective(
        "file:tools.yml", ["grep"]
    )
    assert _scalar_at(document, "coder", "name").value == "Coder\n"
    _assert_no_ref(document)


# --- failures and files (1.8) ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("config", "spec"),
    [("c:\n  $ref: file:nope.yml\n  k: 1\n", "nope.yml"), ("c: {$ref: file:no.md}\n", "no.md")],
)
def test_missing_ref_target_names_target_and_including_file(
    make_theatre: MakeTheatre, config: str, spec: str
) -> None:
    """Req 1.8: a missing `$ref` target is exit 4 naming file, path and target."""
    theatre = make_theatre(config)
    with pytest.raises(ConfigLoadError) as info:
        load_config(_config(theatre))
    assert info.value.exit_code is ExitCode.LOAD
    assert info.value.file == _config(theatre)
    assert info.value.path == "c"
    assert "does not exist" in info.value.problem
    assert str(_config(theatre).parent / spec) in info.value.problem


def test_ref_cycle_is_refused(make_theatre: MakeTheatre) -> None:
    """Req 1.8: a `$ref` back to a file on the include chain is a cycle (exit 4)."""
    theatre = make_theatre(
        "a:\n  $ref: file:one.yml\n  k: 1\n",
        files={".troupe/one.yml": "b:\n  $ref: file:troupe.yml\n"},
    )
    with pytest.raises(ConfigLoadError) as info:
        load_config(_config(theatre))
    assert info.value.exit_code is ExitCode.LOAD
    assert "cycle" in info.value.problem
    assert str(_config(theatre)) in info.value.problem


def test_unloadable_ref_target_names_the_target(make_theatre: MakeTheatre) -> None:
    """Req 1.8: a `$ref` target that is not loadable YAML is exit 4 naming the target file."""
    theatre = make_theatre("a:\n  $ref: file:bad.yml\n", files={".troupe/bad.yml": "x: [\n"})
    with pytest.raises(ConfigLoadError) as info:
        load_config(_config(theatre))
    assert info.value.exit_code is ExitCode.LOAD
    assert info.value.file == theatre / ".troupe" / "bad.yml"


# --- refusals (2.1, 2.2, 2.3) -----------------------------------------------------------------


def _refusal(config_file: Path) -> ConfigLoadError:
    with pytest.raises(ConfigLoadError) as info:
        load_config(config_file)
    assert info.value.exit_code is ExitCode.LOAD
    return info.value


@pytest.mark.parametrize(
    ("config", "path"),
    [
        ("agents:\n  coder:\n    $ref: file:notes.md\n    k: 1\n", "agents.coder"),
        ("agents:\n  coder: {$ref: file:notes.md, k: {x: 1}}\n", "agents.coder"),
        ("items:\n  - $ref: file:notes.md\n    k: 1\n", "items.0"),
        ("$ref: file:notes.md\nk: 1\n", None),
    ],
)
def test_text_target_with_overrides_names_file_path_and_target(
    make_theatre: MakeTheatre, config: str, path: str | None
) -> None:
    """Req 2.1: overrides on a non-YAML target name the including file, path and target."""
    theatre = make_theatre(config, files={".troupe/notes.md": "notes\n"})
    error = _refusal(_config(theatre))
    assert error.file == _config(theatre)
    assert error.path == path
    assert str(theatre / ".troupe" / "notes.md") in error.problem
    assert "overrides apply only to YAML includes" in error.problem


def test_text_target_refusal_in_an_included_file_names_that_file(
    make_theatre: MakeTheatre,
) -> None:
    """Req 2.1: a refused `$ref` mapping inside an included file names the included file."""
    theatre = make_theatre(
        "agents: file:sub/agents.yml\n",
        files={
            ".troupe/sub/agents.yml": "coder:\n  $ref: file:prompt.txt\n  k: 1\n",
            ".troupe/sub/prompt.txt": "p\n",
        },
    )
    error = _refusal(_config(theatre))
    assert error.file == theatre / ".troupe" / "sub" / "agents.yml"
    assert error.path == "agents.coder"
    assert str(theatre / ".troupe" / "sub" / "prompt.txt") in error.problem
    assert "overrides apply only to YAML includes" in error.problem


@pytest.mark.parametrize(
    "value",
    [
        "coder.yaml",
        "'coder.yaml'",
        "FILE:coder.yaml",
        " ' file:coder.yaml'",
        "1",
        "true",
        "null",
        "''",
        "[file:coder.yaml]",
        "{a: file:coder.yaml}",
        "\n      - file:coder.yaml",
    ],
)
def test_ref_value_that_is_not_an_include_string_names_file_and_path(
    make_theatre: MakeTheatre, value: str
) -> None:
    """Req 2.2: a `$ref` value that is not a `file:` string names the file and mapping path."""
    theatre = make_theatre(
        f"agents:\n  coder:\n    $ref: {value}\n    k: 1\n",
        files={".troupe/coder.yaml": CODER},
    )
    error = _refusal(_config(theatre))
    assert error.file == _config(theatre)
    assert error.path == "agents.coder"
    assert "`$ref` must be an include string" in error.problem


@pytest.mark.parametrize(
    ("config", "path"),
    [
        ("coder: {$ref: 7}\n", "coder"),
        ("'$ref': [a]\n", None),
        ('items:\n  - {"$ref": null, k: 1}\n', "items.0"),
    ],
)
def test_ref_refusal_is_reported_for_bare_flow_root_and_list_item_mappings(
    make_theatre: MakeTheatre, config: str, path: str | None
) -> None:
    """Req 2.2: bare, flow, quoted-key, root and list item `$ref` mappings are all refused."""
    theatre = make_theatre(config)
    error = _refusal(_config(theatre))
    assert (error.file, error.path) == (_config(theatre), path)
    assert "`$ref` must be an include string" in error.problem


@pytest.mark.parametrize(
    ("content", "kind"),
    [
        ("- a\n- b\n", "a list"),
        ("plain text\n", "a scalar"),
        ("42\n", "a scalar"),
        ("", "nothing (the file is empty)"),
        ("# only a comment\n", "nothing (the file is empty)"),
    ],
)
def test_overrides_on_non_mapping_yaml_content_name_file_path_and_included_file(
    make_theatre: MakeTheatre, content: str, kind: str
) -> None:
    """Req 2.3: overrides on a list, scalar or empty YAML target are refused with the reason."""
    theatre = make_theatre(
        "agents:\n  coder:\n    $ref: file:coder.yml\n    k: 1\n",
        files={".troupe/coder.yml": content},
    )
    error = _refusal(_config(theatre))
    assert error.file == _config(theatre)
    assert error.path == "agents.coder"
    assert "overrides need a mapping" in error.problem
    assert f"{theatre / '.troupe' / 'coder.yml'} holds {kind}" in error.problem


def test_bare_ref_to_non_mapping_yaml_content_is_not_refused(make_theatre: MakeTheatre) -> None:
    """Req 2.3: only overrides need a mapping; a bare `$ref` to a list or empty file loads."""
    theatre = make_theatre(
        "a: {$ref: file:list.yml}\nb: {$ref: file:empty.yml}\n",
        files={".troupe/list.yml": "- x\n", ".troupe/empty.yml": ""},
    )
    document = load_config(_config(theatre))
    assert _scalar_at(document, "a", 0).value == "x"
    assert _scalar_at(document, "b").value is None


def test_loading_mapping_form_includes_changes_no_file(make_theatre: MakeTheatre) -> None:
    """Req 1.8: loading `$ref` mappings creates, changes and deletes nothing."""
    theatre = make_theatre(
        MERGE_CONFIG + "notes: {$ref: file:notes.md}\n",
        files={".troupe/coder.yaml": CODER, ".troupe/notes.md": "n\r\n"},
    )

    def snapshot() -> dict[str, str]:
        return {
            str(path.relative_to(theatre)): hashlib.sha256(path.read_bytes()).hexdigest()
            if path.is_file()
            else "<dir>"
            for path in sorted(theatre.rglob("*"))
        }

    before = snapshot()
    document = load_config(_config(theatre))
    assert snapshot() == before
    assert [source.real_path.name for source in document.files] == [
        "troupe.yml",
        "coder.yaml",
        "notes.md",
    ]
