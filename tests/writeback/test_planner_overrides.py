"""Unit tests of the PatchPlanner writing new keys at their placement and changing existing values
at their written place (config-include-overrides tasks 3.4, 3.5 and 3.6):
``troupe.config.writeback.planner.plan_patch`` for add-key targets whose placement is a written
mapping or a scalar-form include to convert to mapping form, and for replace targets below
mapping-form includes.

A new key below an include is written as an override in the including file: into an existing
override (the override side of a merged mapping), into a bare ``$ref`` mapping, or with the
intermediate mappings the override lacks; the step 1 rules for new keys in that mapping apply
(insertion point, sibling styles, block or flow). A scalar-form include is first converted to
mapping form in the file holding its include string (the contract example, research.md Decisions 6
and 7). A key below a plain mapping is written as before. An existing value is changed where it
is written: in the override when it is overridden, otherwise in the file it comes from; a whole
mapping-form include root that would change is refused, and a bare text include in mapping form
has its target's content replaced.

Each case loads a real theatre configuration (root file and included files on disk), resolves the
set with the real TargetResolver, plans the patch, compares the patched files byte-exactly (the
included file is never among them) and re-loads the patched texts with the real loader to prove
they read back as the intended configuration, in merged order.

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

from collections.abc import Mapping
from pathlib import Path

import pytest

from troupe.config.errors import ExitCode, WriteRefusedError
from troupe.config.loader import load_config
from troupe.config.paths import ConfigPath
from troupe.config.writeback.compare import plain_equal, root_plain
from troupe.config.writeback.planner import plan_patch
from troupe.config.writeback.target import AddKeyTarget, ReplaceTarget, resolve_target
from troupe.config.writeback.value import parse_value

ROOT = "troupe.yml"
CODER = "model: y\ntools: 1\n"
CODER_PARAMS = "model: y\nparams:\n  t: 1\n"


def _plan(
    tmp_path: Path,
    files: Mapping[str, str],
    path: str,
    value: str,
    target: type[AddKeyTarget | ReplaceTarget] = AddKeyTarget,
) -> dict[str, str]:
    """Plan a set; return each patched file's name and new text.

    Also proves that the target is of the ``target`` kind (a new key by default), that every
    patch changes its file and that the patched texts, re-loaded together, are the intended
    configuration (including key order).
    """
    _write(tmp_path, files)
    document = load_config(tmp_path / ROOT)
    config_path = ConfigPath.parse(path)
    request = resolve_target(document, config_path, parse_value(value, config_path))
    assert isinstance(request.target, target)
    patches = plan_patch(document, request)
    assert all(patch.new_text != patch.file.text for patch in patches)
    reloaded = load_config(
        tmp_path / ROOT, {patch.file.real_path: patch.new_text for patch in patches}
    )
    assert plain_equal(root_plain(reloaded), request.intended, ordered=True)
    result = {patch.file.real_path.name: patch.new_text for patch in patches}
    assert len(result) == len(patches), "never two patches for the same file"
    return result


def _write(tmp_path: Path, files: Mapping[str, str]) -> None:
    for name, text in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_bytes(text.encode("utf-8"))


# --- an existing override ------------------------------------------------------------------------


def test_new_key_goes_into_an_existing_block_override(tmp_path: Path) -> None:
    """Req 4.1, 4.3, 4.5: a key below a merged mapping is added after the last override of its
    `$ref` mapping in the including file; the included file is unchanged."""
    root = "agents:\n  coder:\n    $ref: file:coder.yaml\n    model: z\nother: 1\n"
    patched = _plan(tmp_path, {ROOT: root, "coder.yaml": CODER}, "agents.coder.temp", "0.5")
    assert patched == {
        ROOT: "agents:\n  coder:\n    $ref: file:coder.yaml\n    model: z\n    temp: 0.5\n"
        "other: 1\n"
    }


def test_new_key_goes_into_an_existing_flow_override(tmp_path: Path) -> None:
    """Req 4.1, 4.3, 4.5: a flow `$ref` mapping with an override gets the key after its last
    entry, in flow style; the pushed comment keeps one space (step 1 comment rule)."""
    root = "agents:\n  coder: {$ref: file:coder.yaml, model: z}  # note\n"
    patched = _plan(tmp_path, {ROOT: root, "coder.yaml": CODER}, "agents.coder.temp", "0.5")
    assert patched == {
        ROOT: "agents:\n  coder: {$ref: file:coder.yaml, model: z, temp: 0.5} # note\n"
    }


def test_new_key_copies_the_style_of_the_last_override(tmp_path: Path) -> None:
    """Req 4.3, 4.5: the new key and value are quoted like the last override (sibling styles)
    and go below its trailing comment."""
    root = "coder:\n  $ref: file:coder.yaml\n  'model': 'z'\n  # end of coder\nother: 1\n"
    patched = _plan(tmp_path, {ROOT: root, "coder.yaml": CODER}, "coder.mode", "fast")
    assert patched == {
        ROOT: "coder:\n  $ref: file:coder.yaml\n  'model': 'z'\n  # end of coder\n"
        "  'mode': 'fast'\nother: 1\n"
    }


def test_new_key_below_a_nested_mapping_the_override_holds(tmp_path: Path) -> None:
    """Req 4.3, 4.5: a key below a mapping present in both layers goes into the override's
    nested mapping."""
    root = "coder:\n  $ref: file:coder.yaml\n  params:\n    u: 3\n"
    patched = _plan(tmp_path, {ROOT: root, "coder.yaml": CODER_PARAMS}, "coder.params.k", "2")
    assert patched == {ROOT: "coder:\n  $ref: file:coder.yaml\n  params:\n    u: 3\n    k: 2\n"}


# --- a bare `$ref` mapping -----------------------------------------------------------------------


def test_new_key_goes_into_a_bare_block_ref_mapping(tmp_path: Path) -> None:
    """Req 4.1, 4.5: a `$ref` mapping without overrides gets the key after its `$ref` entry,
    with the `$ref` value's quote style as the sibling style."""
    root = 'coder:\n  $ref: "file:coder.yaml"\nother: 1\n'
    patched = _plan(tmp_path, {ROOT: root, "coder.yaml": CODER}, "coder.mode", "fast")
    assert patched == {ROOT: 'coder:\n  $ref: "file:coder.yaml"\n  mode: "fast"\nother: 1\n'}


def test_new_key_goes_into_a_bare_flow_ref_mapping(tmp_path: Path) -> None:
    """Req 4.1, 4.5: a flow `$ref` mapping without overrides gets the key in flow style."""
    root = "coder: {$ref: file:coder.yaml}\n"
    patched = _plan(tmp_path, {ROOT: root, "coder.yaml": CODER}, "coder.temp", "0.5")
    assert patched == {ROOT: "coder: {$ref: file:coder.yaml, temp: 0.5}\n"}


def test_new_key_in_a_bare_ref_mapping_with_a_comment_on_its_ref_line(tmp_path: Path) -> None:
    """Req 4.5: the comment after the `$ref` entry stays on its line."""
    root = "coder:\n  $ref: file:coder.yaml  # shared\n"
    patched = _plan(tmp_path, {ROOT: root, "coder.yaml": CODER}, "coder.temp", "0.5")
    assert patched == {ROOT: "coder:\n  $ref: file:coder.yaml  # shared\n  temp: 0.5\n"}


# --- intermediate mappings in the override -------------------------------------------------------


def test_key_below_an_included_nested_mapping_creates_it_in_the_override(tmp_path: Path) -> None:
    """Req 4.1, 4.4: `coder.params.k` below the included `params` creates `params:` holding the
    key in the override; the merged `params` keeps `t` and gains `k`."""
    root = "coder:\n  $ref: file:coder.yaml\n  model: z\n"
    patched = _plan(tmp_path, {ROOT: root, "coder.yaml": CODER_PARAMS}, "coder.params.k", "2")
    assert patched == {ROOT: "coder:\n  $ref: file:coder.yaml\n  model: z\n  params:\n    k: 2\n"}


def test_key_below_an_included_nested_mapping_of_a_bare_ref_mapping(tmp_path: Path) -> None:
    """Req 4.1, 4.4: the same below a bare flow `$ref` mapping, in flow style."""
    root = "coder: {$ref: file:coder.yaml}\n"
    patched = _plan(tmp_path, {ROOT: root, "coder.yaml": CODER_PARAMS}, "coder.params.k", "2")
    assert patched == {ROOT: "coder: {$ref: file:coder.yaml, params: {k: 2}}\n"}


def test_several_missing_segments_nest_below_the_created_mappings(tmp_path: Path) -> None:
    """Req 4.1, 4.4: the included path `params.a` and the missing `c.d` are all created as
    intermediate mappings in the override."""
    included = "model: y\nparams:\n  a:\n    b: 1\n"
    root = "coder:\n  $ref: file:coder.yaml\n  model: z\n"
    patched = _plan(tmp_path, {ROOT: root, "coder.yaml": included}, "coder.params.a.c.d", "2")
    assert patched == {
        ROOT: "coder:\n  $ref: file:coder.yaml\n  model: z\n  params:\n    a:\n      c:\n"
        "        d: 2\n"
    }


def test_missing_key_below_an_override_mapping_creates_intermediates(tmp_path: Path) -> None:
    """Req 4.3, 4.4: missing segments below an existing override create nested mappings
    there."""
    root = "coder:\n  $ref: file:coder.yaml\n  params:\n    u: 3\n"
    patched = _plan(tmp_path, {ROOT: root, "coder.yaml": CODER_PARAMS}, "coder.params.p.q", "1")
    assert patched == {
        ROOT: "coder:\n  $ref: file:coder.yaml\n  params:\n    u: 3\n    p:\n      q: 1\n"
    }


# --- root and nested includes --------------------------------------------------------------------


def test_new_key_below_a_root_include_goes_into_the_root_file(tmp_path: Path) -> None:
    """Req 4.1, 4.3: a root `$ref` mapping gets the new key after its overrides."""
    root = "$ref: file:base.yaml\nname: x\n"
    patched = _plan(tmp_path, {ROOT: root, "base.yaml": "a: 1\nname: b\n"}, "c", "3")
    assert patched == {ROOT: "$ref: file:base.yaml\nname: x\nc: 3\n"}


def test_new_key_below_an_included_root_nested_mapping(tmp_path: Path) -> None:
    """Req 4.1, 4.4: below an included mapping of a root include, the override gains the
    intermediate mapping."""
    root = "$ref: file:base.yaml\nname: x\n"
    patched = _plan(tmp_path, {ROOT: root, "base.yaml": "db:\n  host: h\n"}, "db.port", "5")
    assert patched == {ROOT: "$ref: file:base.yaml\nname: x\ndb:\n  port: 5\n"}


def test_override_in_an_included_file_is_written_there(tmp_path: Path) -> None:
    """Req 4.1, 4.3: the `$ref` mapping inside an included file receives the key; the root
    file and the innermost included file are unchanged."""
    files = {
        ROOT: "a: file:mid.yaml\n",
        "mid.yaml": "b:\n  $ref: file:leaf.yaml\n  x: 1\n",
        "leaf.yaml": "y: 2\n",
    }
    patched = _plan(tmp_path, files, "a.b.z", "3")
    assert patched == {"mid.yaml": "b:\n  $ref: file:leaf.yaml\n  x: 1\n  z: 3\n"}


# --- plain mappings keep the step 1 rule ---------------------------------------------------------


def test_new_key_in_a_plain_mapping_is_unchanged(tmp_path: Path) -> None:
    """Req 4.6: a mapping reached through no include gets the key in its own file, as before."""
    root = "db:\n  host: a\nother: 1\n"
    patched = _plan(tmp_path, {ROOT: root}, "db.port.n", "1")
    assert patched == {ROOT: "db:\n  host: a\n  port:\n    n: 1\nother: 1\n"}


def test_new_key_beside_an_include_in_a_plain_mapping(tmp_path: Path) -> None:
    """Req 4.6: a plain mapping holding an include gets the key after the include entry, in
    the same file."""
    root = "agents:\n  coder: file:coder.yaml\n"
    patched = _plan(tmp_path, {ROOT: root, "coder.yaml": CODER}, "agents.lead", "x")
    assert patched == {ROOT: "agents:\n  coder: file:coder.yaml\n  lead: x\n"}


# --- converting a scalar-form include (task 3.5) -------------------------------------------------


def test_conversion_reproduces_the_contract_example(tmp_path: Path) -> None:
    """Req 5.1, 5.2, 5.5: `coder: file:agents/coder.yaml  # c` + `set coder.temperature 0.3`
    gives exactly `coder:  # c` / `  $ref: file:agents/coder.yaml` / `  temperature: 0.3`; the
    included file is not patched."""
    files = {ROOT: "coder: file:agents/coder.yaml  # c\n", "agents/coder.yaml": CODER}
    patched = _plan(tmp_path, files, "coder.temperature", "0.3")
    assert patched == {ROOT: "coder:  # c\n  $ref: file:agents/coder.yaml\n  temperature: 0.3\n"}


def test_conversion_without_a_comment_ends_the_key_line_at_the_colon(tmp_path: Path) -> None:
    """Req 5.1, 5.2, 5.5: without a comment the key's line ends right after the `:` (trailing
    spaces go too); the following entries are untouched."""
    root = "agents:\n  coder: file:coder.yaml   \n  lead: x\nother: 1\n"
    patched = _plan(tmp_path, {ROOT: root, "coder.yaml": CODER}, "agents.coder.temp", "0.5")
    assert patched == {
        ROOT: "agents:\n  coder:\n    $ref: file:coder.yaml\n    temp: 0.5\n  lead: x\nother: 1\n"
    }


def test_conversion_of_a_block_list_item_without_a_comment_is_compact(tmp_path: Path) -> None:
    """Req 5.3, 6.3: a list-item include becomes `- $ref: <string>` with the override two columns
    right of the `-`, in the file holding the list."""
    root = "l:\n  - file:coder.yaml\n  - x\n"
    patched = _plan(tmp_path, {ROOT: root, "coder.yaml": CODER}, "l.0.temp", "0.5")
    assert patched == {ROOT: "l:\n  - $ref: file:coder.yaml\n    temp: 0.5\n  - x\n"}


def test_conversion_of_a_block_list_item_with_a_comment(tmp_path: Path) -> None:
    """Req 5.2, 5.3, 6.3: with a comment the item keeps `-` and the comment on its line; the
    `$ref` entry and the override follow two columns right of the `-`."""
    root = "l:\n- file:coder.yaml  # c\n- x\n"
    patched = _plan(tmp_path, {ROOT: root, "coder.yaml": CODER}, "l.0.temp", "0.5")
    assert patched == {ROOT: "l:\n-  # c\n  $ref: file:coder.yaml\n  temp: 0.5\n- x\n"}


def test_conversion_in_a_flow_mapping(tmp_path: Path) -> None:
    """Req 5.4: in a flow mapping the include string becomes `{$ref: <string>, k: v}`; the
    pushed end-of-line comment keeps one space (step 1 comment rule)."""
    root = "agents: {coder: file:coder.yaml, x: 1}  # note\n"
    patched = _plan(tmp_path, {ROOT: root, "coder.yaml": CODER}, "agents.coder.temp", "0.5")
    assert patched == {ROOT: "agents: {coder: {$ref: file:coder.yaml, temp: 0.5}, x: 1} # note\n"}


def test_conversion_in_a_flow_sequence(tmp_path: Path) -> None:
    """Req 5.4, 6.3: a flow list item include becomes a flow `$ref` mapping in place."""
    root = "l: [file:coder.yaml, x]\n"
    patched = _plan(tmp_path, {ROOT: root, "coder.yaml": CODER}, "l.0.temp", "0.5")
    assert patched == {ROOT: "l: [{$ref: file:coder.yaml, temp: 0.5}, x]\n"}


@pytest.mark.parametrize(
    ("written", "value"),
    [
        pytest.param('"file:coder.yaml"', '"fast"', id="double"),
        pytest.param("'file:coder.yaml'", "'fast'", id="single"),
    ],
)
def test_conversion_keeps_a_quoted_include_string_verbatim(
    tmp_path: Path, written: str, value: str
) -> None:
    """Req 5.1, 5.2: the include string keeps its quotes; the new string value copies the style
    of the `$ref` entry's value (sibling style)."""
    root = f"coder: {written} # c\n"
    patched = _plan(tmp_path, {ROOT: root, "coder.yaml": CODER}, "coder.mode", "fast")
    assert patched == {ROOT: f"coder: # c\n  $ref: {written}\n  mode: {value}\n"}


def test_conversion_creates_the_intermediate_keys(tmp_path: Path) -> None:
    """Req 4.4, 5.1: below an included nested mapping the override gains the intermediate
    mappings (prefix and missing keys) holding the new key."""
    root = "coder: file:coder.yaml\n"
    patched = _plan(tmp_path, {ROOT: root, "coder.yaml": CODER_PARAMS}, "coder.params.k.j", "2")
    assert patched == {ROOT: "coder:\n  $ref: file:coder.yaml\n  params:\n    k:\n      j: 2\n"}


def test_conversion_uses_the_files_indentation_step(tmp_path: Path) -> None:
    """Req 5.1: the `$ref` mapping is one step of the file's indentation (4) deeper."""
    root = "agents:\n    coder: file:coder.yaml\n    lead:\n        a: 1\n"
    patched = _plan(tmp_path, {ROOT: root, "coder.yaml": CODER}, "agents.coder.params.t", "1")
    assert patched == {
        ROOT: "agents:\n    coder:\n        $ref: file:coder.yaml\n        params:\n"
        "            t: 1\n    lead:\n        a: 1\n"
    }


def test_conversion_in_a_crlf_file(tmp_path: Path) -> None:
    """Req 5.1, 5.2, 5.5: the new lines use the file's CRLF line breaks."""
    root = "coder: file:coder.yaml  # c\r\nother: 1\r\n"
    patched = _plan(tmp_path, {ROOT: root, "coder.yaml": CODER}, "coder.temp", "0.5")
    assert patched == {
        ROOT: "coder:  # c\r\n  $ref: file:coder.yaml\r\n  temp: 0.5\r\nother: 1\r\n"
    }


@pytest.mark.parametrize(
    ("line", "key_line"),
    [
        pytest.param("coder: file:coder.yaml", "coder:", id="no-comment"),
        pytest.param("coder: file:coder.yaml # c", "coder: # c", id="comment"),
    ],
)
def test_conversion_at_a_file_end_without_a_final_line_break(
    tmp_path: Path, line: str, key_line: str
) -> None:
    """Req 5.1, 5.2, 5.5: the file keeps ending without a final line break."""
    root = "other: 1\n" + line
    patched = _plan(tmp_path, {ROOT: root, "coder.yaml": CODER}, "coder.temp", "0.5")
    assert patched == {ROOT: f"other: 1\n{key_line}\n  $ref: file:coder.yaml\n  temp: 0.5"}


def test_conversion_with_nested_includes_happens_in_the_middle_file(tmp_path: Path) -> None:
    """Req 4.2, 5.1, 5.5: the include of `leaf.yaml` written in `mid.yaml` is converted there;
    the root file and the innermost file are unchanged."""
    files = {ROOT: "a: file:mid.yaml\n", "mid.yaml": "b: file:leaf.yaml\n", "leaf.yaml": "y: 2\n"}
    patched = _plan(tmp_path, files, "a.b.z", "3")
    assert patched == {"mid.yaml": "b:\n  $ref: file:leaf.yaml\n  z: 3\n"}


def test_conversion_of_an_override_that_is_a_scalar_form_include(tmp_path: Path) -> None:
    """Req 4.3, 5.1: an override written as a scalar-form include is converted in place in the
    `$ref` mapping."""
    files = {
        ROOT: "coder:\n  $ref: file:coder.yaml\n  params: file:p.yaml\n",
        "coder.yaml": CODER_PARAMS,
        "p.yaml": "u: 1\n",
    }
    patched = _plan(tmp_path, files, "coder.params.k", "2")
    assert patched == {
        ROOT: "coder:\n  $ref: file:coder.yaml\n  params:\n    $ref: file:p.yaml\n    k: 2\n"
    }


def test_conversion_of_an_include_string_on_the_line_after_its_key_is_refused(
    tmp_path: Path,
) -> None:
    """Req 5.5 (research.md Decision 7): an include string that does not end on its key's line
    is refused with exit 3, naming the file and PATH."""
    _write(tmp_path, {ROOT: "coder:\n  file:coder.yaml\n", "coder.yaml": CODER})
    document = load_config(tmp_path / ROOT)
    config_path = ConfigPath.parse("coder.temp")
    request = resolve_target(document, config_path, parse_value("0.5", config_path))
    with pytest.raises(WriteRefusedError) as raised:
        plan_patch(document, request)
    assert raised.value.exit_code is ExitCode.WRITE_REFUSED
    message = str(raised.value)
    assert str(tmp_path / ROOT) in message
    assert "coder.temp" in message
    assert "this layout cannot be written" in message


# --- existing values at their written place (task 3.6) -------------------------------------------


def _replace(tmp_path: Path, files: Mapping[str, str], path: str, value: str) -> dict[str, str]:
    """Plan the change of an existing value; see :func:`_plan`."""
    return _plan(tmp_path, files, path, value, ReplaceTarget)


def _refused_replace(
    tmp_path: Path, files: Mapping[str, str], path: str, value: str
) -> WriteRefusedError:
    """Plan the change of an existing value that must be refused; return the refusal."""
    _write(tmp_path, files)
    document = load_config(tmp_path / ROOT)
    config_path = ConfigPath.parse(path)
    request = resolve_target(document, config_path, parse_value(value, config_path))
    assert isinstance(request.target, ReplaceTarget)
    with pytest.raises(WriteRefusedError) as raised:
        plan_patch(document, request)
    assert raised.value.exit_code is ExitCode.WRITE_REFUSED
    return raised.value


def test_overridden_scalar_is_changed_in_the_override(tmp_path: Path) -> None:
    """Req 7.1: an overridden scalar is changed in the override in the including file; the
    included file is unchanged."""
    root = "coder:\n  $ref: file:coder.yaml\n  model: z  # mine\nother: 1\n"
    patched = _replace(tmp_path, {ROOT: root, "coder.yaml": CODER}, "coder.model", "w")
    assert patched == {ROOT: "coder:\n  $ref: file:coder.yaml\n  model: w  # mine\nother: 1\n"}


def test_overridden_scalar_in_a_flow_ref_mapping_is_changed_there(tmp_path: Path) -> None:
    """Req 7.1: an override in a flow `$ref` mapping is changed in place, keeping its quotes."""
    root = "coder: {$ref: file:coder.yaml, model: 'z'}\n"
    patched = _replace(tmp_path, {ROOT: root, "coder.yaml": CODER}, "coder.model", "w")
    assert patched == {ROOT: "coder: {$ref: file:coder.yaml, model: 'w'}\n"}


def test_value_that_is_not_overridden_is_changed_in_the_included_file(tmp_path: Path) -> None:
    """Req 7.2: a value below a mapping-form include that is not overridden is changed in the
    included file; the including file is unchanged."""
    root = "coder:\n  $ref: file:coder.yaml\n  model: z\n"
    patched = _replace(tmp_path, {ROOT: root, "coder.yaml": CODER}, "coder.tools", "2")
    assert patched == {"coder.yaml": "model: y\ntools: 2\n"}


def test_value_below_a_bare_ref_mapping_is_changed_in_the_included_file(tmp_path: Path) -> None:
    """Req 7.2: below a `$ref` mapping without overrides a value is changed where it comes from."""
    root = "coder:\n  $ref: file:coder.yaml\n"
    patched = _replace(tmp_path, {ROOT: root, "coder.yaml": CODER}, "coder.model", "w")
    assert patched == {"coder.yaml": "model: w\ntools: 1\n"}


def test_nested_override_is_changed_in_the_override(tmp_path: Path) -> None:
    """Req 7.1: a key of a mapping present in both layers that the override holds is changed in
    the override's nested mapping."""
    root = "coder:\n  $ref: file:coder.yaml\n  params:\n    t: 2\n"
    patched = _replace(tmp_path, {ROOT: root, "coder.yaml": CODER_PARAMS}, "coder.params.t", "3")
    assert patched == {ROOT: "coder:\n  $ref: file:coder.yaml\n  params:\n    t: 3\n"}


def test_key_of_a_merged_nested_mapping_only_included_is_changed_there(tmp_path: Path) -> None:
    """Req 7.2: a key of a mapping present in both layers that only the included side holds is
    changed in the included file."""
    root = "coder:\n  $ref: file:coder.yaml\n  params:\n    u: 3\n"
    patched = _replace(tmp_path, {ROOT: root, "coder.yaml": CODER_PARAMS}, "coder.params.t", "4")
    assert patched == {"coder.yaml": "model: y\nparams:\n  t: 4\n"}


def test_merged_mapping_replaced_by_a_scalar_changes_only_the_override(tmp_path: Path) -> None:
    """Req 7.1: a mapping present in both layers replaced by a scalar replaces the override's
    mapping; the included mapping stays and the merged value reads back as the scalar."""
    root = "coder:\n  $ref: file:coder.yaml\n  params:\n    u: 3\n    v: 4\nother: 1\n"
    patched = _replace(tmp_path, {ROOT: root, "coder.yaml": CODER_PARAMS}, "coder.params", "5")
    assert patched == {ROOT: "coder:\n  $ref: file:coder.yaml\n  params: 5\nother: 1\n"}


def test_merged_mapping_replaced_by_a_list_changes_only_the_override(tmp_path: Path) -> None:
    """Req 7.1: a mapping present in both layers replaced by a list replaces the override's
    flow mapping in flow style."""
    root = "coder: {$ref: file:coder.yaml, params: {u: 3}}\n"
    patched = _replace(tmp_path, {ROOT: root, "coder.yaml": CODER_PARAMS}, "coder.params", "[a, b]")
    assert patched == {ROOT: "coder: {$ref: file:coder.yaml, params: [a, b]}\n"}


def test_overridden_list_is_replaced_in_the_override(tmp_path: Path) -> None:
    """Req 7.1: an overridden list is changed item-wise in the override; the included list is
    unchanged."""
    files = {
        ROOT: "coder:\n  $ref: file:coder.yaml\n  tools:\n    - b\n",
        "coder.yaml": "model: y\ntools:\n  - a\n  - x\n",
    }
    patched = _replace(tmp_path, files, "coder.tools", "[c, d]")
    assert patched == {ROOT: "coder:\n  $ref: file:coder.yaml\n  tools:\n    - c\n    - d\n"}


def test_overridden_list_replaced_by_a_scalar(tmp_path: Path) -> None:
    """Req 7.1: an overridden list replaced by a scalar is replaced in the override."""
    files = {
        ROOT: "coder:\n  $ref: file:coder.yaml\n  tools:\n    - b\nother: 1\n",
        "coder.yaml": "model: y\ntools:\n  - a\n",
    }
    patched = _replace(tmp_path, files, "coder.tools", "none")
    assert patched == {ROOT: "coder:\n  $ref: file:coder.yaml\n  tools: none\nother: 1\n"}


def test_existing_value_in_a_nested_override_in_an_included_file(tmp_path: Path) -> None:
    """Req 7.1: an override written in an included file (nested includes) is changed in that
    file; the root file and the innermost included file are unchanged."""
    files = {
        ROOT: "a: file:mid.yaml\n",
        "mid.yaml": "b:\n  $ref: file:leaf.yaml\n  x: 1\n",
        "leaf.yaml": "x: 0\ny: 2\n",
    }
    patched = _replace(tmp_path, files, "a.b.x", "5")
    assert patched == {"mid.yaml": "b:\n  $ref: file:leaf.yaml\n  x: 5\n"}


def test_value_below_a_nested_include_is_changed_in_the_innermost_file(tmp_path: Path) -> None:
    """Req 7.2: a value not overridden below an include in an included file is changed in the
    innermost file it comes from."""
    files = {
        ROOT: "a: file:mid.yaml\n",
        "mid.yaml": "b:\n  $ref: file:leaf.yaml\n  x: 1\n",
        "leaf.yaml": "x: 0\ny: 2\n",
    }
    patched = _replace(tmp_path, files, "a.b.y", "3")
    assert patched == {"leaf.yaml": "x: 0\ny: 3\n"}


def test_overridden_value_of_a_root_include_is_changed_in_the_root_file(tmp_path: Path) -> None:
    """Req 7.1: an override of a root mapping-form include is changed in the root file."""
    root = "$ref: file:base.yaml\nname: x\n"
    patched = _replace(tmp_path, {ROOT: root, "base.yaml": "name: b\nport: 1\n"}, "name", "y")
    assert patched == {ROOT: "$ref: file:base.yaml\nname: y\n"}


@pytest.mark.parametrize(
    "root",
    [
        pytest.param("coder:\n  $ref: file:coder.yaml\n  model: z\n", id="with-overrides"),
        pytest.param("coder:\n  $ref: file:coder.yaml\n", id="bare"),
        pytest.param("coder: {$ref: file:coder.yaml, model: z}\n", id="flow"),
    ],
)
def test_mapping_form_include_root_that_would_change_is_refused(tmp_path: Path, root: str) -> None:
    """Req 7.5: a whole mapping-form include whose value would change is refused with exit 3,
    naming PATH and the included file."""
    error = _refused_replace(tmp_path, {ROOT: root, "coder.yaml": CODER}, "coder", "5")
    assert error.file == tmp_path / "coder.yaml"
    assert error.path == "coder"
    message = str(error)
    assert "coder.yaml" in message
    assert "change the included file instead" in message


def test_bare_text_include_in_mapping_form_replaces_its_target(tmp_path: Path) -> None:
    """Req 7.6: a text include in bare mapping form set to a string has its target's content
    replaced; the including file is unchanged."""
    files = {ROOT: "prompt:\n  $ref: file:prompt.txt\nother: 1\n", "prompt.txt": "old\n"}
    patched = _replace(tmp_path, files, "prompt", '"new\\n"')
    assert patched == {"prompt.txt": "new\n"}


@pytest.mark.parametrize("value", ["{}", "{t: 1}", "{u: 4, t: 1}"])
def test_merged_mapping_replaced_by_a_mapping_is_refused_for_now(
    tmp_path: Path, value: str
) -> None:
    """Req 7.1, 7.3: a mapping present in both layers is not replaced by a mapping through the
    override alone (that would keep the included keys); until the layered merge exists such a
    change is refused with exit 3, naming PATH and the file of the override."""
    root = "coder:\n  $ref: file:coder.yaml\n  params:\n    u: 3\n"
    error = _refused_replace(
        tmp_path, {ROOT: root, "coder.yaml": CODER_PARAMS}, "coder.params", value
    )
    assert error.file == tmp_path / ROOT
    assert error.path == "coder.params"
    assert "cannot be replaced by a mapping" in str(error)
