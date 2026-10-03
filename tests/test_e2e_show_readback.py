"""``troupe config show`` output reads back exactly (task 4.3; design Testing Strategy, read-back).

For each fixture the real ``troupe`` console script prints ``show [PATH]`` for a real theatre; its
stdout is written as a fresh theatre's ``.troupe/troupe.yml`` and loaded with the project's own
loader (``load_config``, which applies the exact YAML 1.2 core schema; research.md "Reading back
in tests"). The reloaded configuration must equal the printed node of the original (4.1):

- ``flatten_leaves`` gives the same leaf paths, in the same order, with ``scalar_equal`` values
  (same types, floats by ``repr``);
- a structural walk of every node (empty mappings and lists included, which leaves skip) gives the
  same node kinds at the same paths, with each key compared as a typed key: a key written plain in
  the output is typed by the core schema, so an awkward key printed plain (``true``, ``1.5``)
  would come back as a non-string and fail (4.8);
- the reloaded document holds no include at all: YAML includes and text includes were printed in
  place (3.4).

A section that is a list is wrapped as ``w:`` followed by the unchanged output lines (a block
sequence may sit at its key's column), since the loader requires a top-level mapping. Fixtures
avoid strings starting with ``file:`` (the loader would read them as includes) and text includes
holding a carriage return or another character a literal block cannot hold (the approved 4.5
exception, proven byte-exact by the ``--sources`` and unit tests). A root scalar document is not
loadable as a theatre and is checked against exact bytes elsewhere.

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from support import MakeTheatre, RunCli, TheatreFiles
from troupe.config.constants import CONFIG_DIR_NAME
from troupe.config.loader import load_config
from troupe.config.model import (
    ConfigDocument,
    ConfigMapping,
    ConfigNode,
    ConfigScalar,
    ConfigSequence,
    ScalarStyle,
)
from troupe.config.paths import ConfigPath
from troupe.config.resolve import flatten_leaves, resolve_path
from troupe.config.scalars import ScalarValue, resolve_plain_scalar, scalar_equal

WRAPPER_KEY = "w"
"""The key a printed list is placed under so that the reloaded root is a mapping."""


@dataclass(frozen=True)
class Fixture:
    """A theatre to ``show``: its root file, extra files (includes) and the PATH shown (or all)."""

    config: str
    files: TheatreFiles = field(default_factory=dict)
    path: str | None = None


type Segment = tuple[str, str, str]
"""One path step: ``("key", type name, repr)`` of the typed key, or ``("index", "int", "N")``."""

type NodeShape = tuple[tuple[Segment, ...], str, ScalarValue]
"""A node's path, kind (``mapping``/``sequence``/``scalar``) and, for a scalar, its value."""


def _typed_key(key: str, style: ScalarStyle) -> ScalarValue:
    """The key as YAML reads it: a plain key is typed by the core schema, a quoted one is text."""
    return resolve_plain_scalar(key) if style is ScalarStyle.PLAIN else key


def _shapes(node: ConfigNode, path: tuple[Segment, ...] = ()) -> list[NodeShape]:
    """Every node below and including ``node``, depth-first in document order."""
    if isinstance(node, ConfigScalar):
        return [(path, "scalar", node.value)]
    shapes: list[NodeShape] = []
    if isinstance(node, ConfigMapping):
        shapes.append((path, "mapping", None))
        for entry in node.entries:
            typed = _typed_key(entry.key, entry.key_style)
            segment = ("key", type(typed).__name__, repr(typed))
            shapes += _shapes(entry.value, (*path, segment))
    else:
        shapes.append((path, "sequence", None))
        for index, item in enumerate(node.items):
            shapes += _shapes(item, (*path, ("index", "int", str(index))))
    return shapes


def _has_include(node: ConfigNode) -> bool:
    if node.include is not None:
        return True
    if isinstance(node, ConfigMapping):
        return any(_has_include(entry.value) for entry in node.entries)
    if isinstance(node, ConfigSequence):
        return any(_has_include(item) for item in node.items)
    return False


def _relative_leaves(
    document: ConfigDocument, prefix: tuple[str, ...], strip: int
) -> list[tuple[tuple[str, ...], ScalarValue]]:
    """``flatten_leaves`` of ``document`` below ``prefix``, each path without its first ``strip``
    segments."""
    return [
        (leaf.path.segments[strip:], leaf.value)
        for leaf in flatten_leaves(document.root)
        if leaf.path.segments[: len(prefix)] == prefix
    ]


def _load(theatre: Path) -> ConfigDocument:
    return load_config(theatre / CONFIG_DIR_NAME / "troupe.yml")


def _show(run_cli: RunCli, theatre: Path, path: str | None) -> bytes:
    args = () if path is None else (path,)
    result = run_cli("config", "show", *args, "--theatre", str(theatre))
    assert (result.exit_code, result.stderr) == (0, ""), result.stderr
    return result.stdout


def _assert_reads_back(make_theatre: MakeTheatre, run_cli: RunCli, fixture: Fixture) -> bytes:
    """Run ``show``, reload its output as a fresh theatre, assert it equals the printed node and
    return the output."""
    theatre = make_theatre(fixture.config, files=fixture.files, name="original")
    output = _show(run_cli, theatre, fixture.path)
    original = _load(theatre)
    prefix = () if fixture.path is None else ConfigPath.parse(fixture.path).segments
    printed = original.root if fixture.path is None else resolve_path(original, ConfigPath(prefix))
    assert not isinstance(printed, ConfigScalar), "read-back fixtures show a mapping or a list"
    wrapped = isinstance(printed, ConfigSequence)

    fresh = make_theatre(
        f"{WRAPPER_KEY}:\n".encode() + output if wrapped else output, name="reloaded"
    )
    text = output.decode("utf-8")
    reloaded = _load(fresh)
    back: ConfigNode = reloaded.root
    if wrapped:
        assert [entry.key for entry in reloaded.root.entries] == [WRAPPER_KEY], text
        back = reloaded.root.entries[0].value

    # 3.4: every include was printed in place, so the output reads back with no include.
    assert len(reloaded.files) == 1, text
    assert not _has_include(back), text

    # 4.1: the same leaf paths, in the same order, with the same typed values.
    expected_leaves = _relative_leaves(original, prefix, len(prefix))
    actual_leaves = _relative_leaves(reloaded, (), 1 if wrapped else 0)
    assert [path for path, _ in actual_leaves] == [path for path, _ in expected_leaves], text
    for (path, actual), (_, expected) in zip(actual_leaves, expected_leaves, strict=True):
        assert scalar_equal(actual, expected), (path, actual, expected, text)

    # 4.1, 4.8: every node (empty collections included) at the same path, keys typed as strings.
    expected_shapes = _shapes(printed)
    actual_shapes = _shapes(back)
    assert [shape[:2] for shape in actual_shapes] == [shape[:2] for shape in expected_shapes], text
    for actual_shape, expected_shape in zip(actual_shapes, expected_shapes, strict=True):
        assert scalar_equal(actual_shape[2], expected_shape[2]), (actual_shape, text)
    return output


# --- scalar kinds (4.2) --------------------------------------------------------------------------

SCALAR_KINDS = [
    ("true", "true"),
    ("false", "false"),
    ("True", "true"),
    ("FALSE", "false"),
    ("~", "null"),
    ("", "null"),
    ("null", "null"),
    ("NULL", "null"),
    ("0", "0"),
    ("-17", "-17"),
    ("+5", "5"),
    ("0o17", "15"),
    ("0x1F", "31"),
    ("0x0", "0"),
    ("12345678901234567890", "12345678901234567890"),
    ("1.5", "1.5"),
    ("-0.0", "-0.0"),
    ("0.0", "0.0"),
    ("1e16", "1e+16"),
    ("1E-7", "1e-07"),
    ("1.", "1.0"),
    (".5", "0.5"),
    ("6.02e23", "6.02e+23"),
    ("0.1", "0.1"),
    (".inf", ".inf"),
    ("+.Inf", ".inf"),
    ("-.inf", "-.inf"),
    ("-.INF", "-.inf"),
    (".nan", ".nan"),
    (".NaN", ".nan"),
]


@pytest.mark.parametrize(("source", "printed"), SCALAR_KINDS)
def test_every_scalar_kind_prints_canonically_and_reads_back(
    make_theatre: MakeTheatre, run_cli: RunCli, source: str, printed: str
) -> None:
    """Req 4.1, 4.2: booleans, nulls, decimal/octal/hex integers and floats (including -0.0, 1e16,
    the infinities and NaN) print in their canonical form and read back as the same typed value."""
    output = _assert_reads_back(make_theatre, run_cli, Fixture(f"v: {source}\nafter: 1\n"))
    assert output == f"v: {printed}\nafter: 1\n".encode()


# --- one-line strings (4.3) ----------------------------------------------------------------------

QUOTED_STRINGS = [
    '"true"',
    '"False"',
    '"5"',
    '"-3"',
    '"1.5"',
    '"1e3"',
    '".inf"',
    '".nan"',
    '"null"',
    '"~"',
    '""',
    '"0o17"',
    '"0x1F"',
    '"1_0"',
    '"2001-12-14"',
    '"a: b"',
    '"a:"',
    '"a #b"',
    '"#x"',
    '" lead"',
    '"trail "',
    '"  "',
    '"it\'s"',
    "'say \"hi\"'",
    '"\'"',
    '"\\\\"',
    '"a\\\\b"',
    '"- x"',
    '"-"',
    '"? x"',
    '"[x]"',
    '"{x}"',
    '"x, y"',
    '"*a"',
    '"&a"',
    '"!t"',
    '"%x"',
    '"@x"',
    '"`x"',
    '"|x"',
    '">x"',
    '"---"',
    '"--- a"',
    '"..."',
    '"\\a"',
    '"\\x01"',
    '"\\t"',
    '"tab\\there"',
    '"\\x7f"',
    '"\\x85"',
    '"\\u2028"',
    '"\\ufeff"',
    '"\\0"',
    "café",
    "日本",
    '"😀"',
    '"e\\u0301"',
    '"plain words stay plain"',
    '"' + "long words " * 40 + 'end"',
]


@pytest.mark.parametrize("source", QUOTED_STRINGS)
def test_one_line_strings_read_back_as_the_same_string(
    make_theatre: MakeTheatre, run_cli: RunCli, source: str
) -> None:
    """Req 4.1, 4.3: a string that looks like another type, holds `: `, ` #`, leading or trailing
    spaces, quotes, indicator characters, document markers, control characters or non-ASCII text
    reads back as the very same string, on one line."""
    output = _assert_reads_back(make_theatre, run_cli, Fixture(f"v: {source}\nafter: 1\n"))
    assert output.count(b"\n") == 2, output  # 4.7: never wrapped


# --- multi-line strings that are not includes (4.4) ----------------------------------------------

MULTI_LINE_STRINGS = [
    '"a\\nb\\n"',
    '"a\\nb"',
    '"x\\n\\n"',
    '"  y\\nz"',
    '"  y\\nz\\n\\n\\n"',
    '"\\nlead"',
    '"\\n\\n  lead\\n"',
    '"\\n"',
    '"\\n\\n"',
    '" \\na"',
    '"a\\n \\n"',
    '"a  \\nb  \\n"',
    '"\\ta\\n\\tb\\n"',
    '"# not a comment\\n- not a list\\n"',
    '"caf\\u00e9\\n\\u65e5\\u672c\\n"',
    '"a\\rb\\n"',
    '"a\\x01\\nb"',
]


@pytest.mark.parametrize("source", MULTI_LINE_STRINGS)
def test_multi_line_strings_read_back_as_the_same_string(
    make_theatre: MakeTheatre, run_cli: RunCli, source: str
) -> None:
    """Req 4.1, 4.4: a string with line breaks from a double-quoted source (trailing line breaks
    none, one or several, a first line starting with a space, leading empty lines; also ones a
    literal block cannot hold, which are double-quoted) reads back exactly, also nested in a list
    item mapping."""
    config = f"v: {source}\nl:\n  - k: {source}\n  - - {source}\nafter: 1\n"
    _assert_reads_back(make_theatre, run_cli, Fixture(config))


# --- awkward keys (4.8) --------------------------------------------------------------------------

AWKWARD_KEYS = [
    '"true"',
    '"No"',
    '"null"',
    '"~"',
    '"5"',
    '"1.5"',
    '"0x1F"',
    '"a: b"',
    '"<<"',
    '""',
    '"- k"',
    '"? k"',
    '"#k"',
    '"k #c"',
    '"key with spaces"',
    '" lead"',
    '"trail "',
    '"it\'s"',
    "'say \"hi\"'",
    '"[k]"',
    '"*k"',
    '"a\\tb"',
    '"\\x01"',
    '"café"',
    "plain_key",
]


@pytest.mark.parametrize("key", AWKWARD_KEYS)
def test_awkward_keys_read_back_as_the_same_string_key(
    make_theatre: MakeTheatre, run_cli: RunCli, key: str
) -> None:
    """Req 4.1, 4.8: a key that looks like another type, is empty, is the merge key, holds `: `,
    ` #`, spaces, quotes or control characters reads back as the same string key, at the top level
    and inside a nested mapping."""
    config = f"{key}: 1\nm:\n  {key}:\n    {key}: x\n"
    _assert_reads_back(make_theatre, run_cli, Fixture(config))


def test_all_awkward_keys_in_one_mapping_read_back_in_order(
    make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    """Req 4.1, 4.8: a mapping holding every awkward key reads back with the keys in order."""
    config = "".join(f"{key}: {index}\n" for index, key in enumerate(AWKWARD_KEYS))
    _assert_reads_back(make_theatre, run_cli, Fixture(config))


# --- nested collections (3.4, 4.1) ---------------------------------------------------------------

NESTED = """\
a:
  b:
    c: 1
    d: []
    e: {}
    f: [{}, [], [[]], {x: {}}]
l:
  - - k: v
      m: [1, {x: [], "true": "true"}]
    - []
  - {}
  - - - deep
      - {y: "  y\\nz"}
  - {n: {}, o: [[]], p: [{q: [{r: ~}]}]}
empty_map: {}
empty_list: []
last: end
"""


@pytest.mark.parametrize("path", [None, "a", "a.b", "a.b.f", "l", "l.0", "l.3"])
def test_nested_collections_read_back_with_empty_collections(
    make_theatre: MakeTheatre, run_cli: RunCli, path: str | None
) -> None:
    """Req 4.1: nested mappings, lists of lists of mappings and empty mappings and lists inside
    items read back with the same structure, for the whole configuration and for mapping and list
    sections."""
    _assert_reads_back(make_theatre, run_cli, Fixture(NESTED, path=path))


def test_empty_configuration_reads_back_empty(make_theatre: MakeTheatre, run_cli: RunCli) -> None:
    """Req 4.1: an empty configuration prints `{}`, which reads back as an empty configuration."""
    output = _assert_reads_back(make_theatre, run_cli, Fixture("# nothing here\n"))
    assert output == b"{}\n"


# --- YAML includes (3.4) -------------------------------------------------------------------------

INCLUDES_ROOT = """\
name: troupe
agents:
  lead: file:agents/lead.yml
  team:
    - file:agents/a.yml
    - name: inline
      prompt: file:agents/inline.md
    - file:agents/list.yml
  nothing: file:agents/empty.yml
"""

INCLUDE_FILES: TheatreFiles = {
    ".troupe/agents/lead.yml": (
        "name: lead\ndetails: file:profile/p.yml\nprompt: file:lead.md\nlevel: 0o10\n"
    ),
    ".troupe/agents/profile/p.yml": (
        'tags: [x, "true", ~]\nnote: file:note.txt\nempty: {}\n"a: b": " lead"\n'
    ),
    ".troupe/agents/profile/note.txt": "  indented note\nsecond\n\n",
    ".troupe/agents/lead.md": "You lead.\n",
    ".troupe/agents/a.yml": "name: a\nrole: '5'\nprompt: file:a.md\n",
    ".troupe/agents/a.md": "no final newline",
    ".troupe/agents/inline.md": "\n\n  leading blank lines\n",
    ".troupe/agents/list.yml": "- file:a.md\n- [1, 2.5, .inf]\n- {}\n",
    ".troupe/agents/empty.yml": "",
}


@pytest.mark.parametrize("path", [None, "agents", "agents.lead", "agents.team", "agents.team.2"])
def test_yaml_includes_read_back_in_place(
    make_theatre: MakeTheatre, run_cli: RunCli, path: str | None
) -> None:
    """Req 3.4, 4.1, 4.4: YAML includes nested two levels deep, a list item that is an include, an
    include holding a list and an empty include, each with text includes inside, read back as the
    included content in place (no include remains), for the whole configuration and sections."""
    output = _assert_reads_back(
        make_theatre, run_cli, Fixture(INCLUDES_ROOT, files=INCLUDE_FILES, path=path)
    )
    assert b"file:" not in output


# --- LF text includes (3.4, 4.4) -----------------------------------------------------------------

TEXT_INCLUDES = [
    "plain text\n",
    "  leading spaces\nnext\n",
    "\n\n  leading blank lines then spaces\n",
    "\nleading blank line\n",
    "no final newline",
    "  indented, no final newline",
    "several final newlines\n\n\n",
    "  indented\n\n\n",
    "\n",
    "\n\n\n",
    "",
    "\tleading tab\n\tsecond\n",
    "trailing spaces  \nmore \n",
    "   \n  spaces-only first line\n",
    "a: b # not yaml\n- x\n",
    "---\n...\n",
    "#!/bin/sh\necho hi\n",
    "café 日本 😀\n",
    "true",
    "5\n",
]


@pytest.mark.parametrize("text", TEXT_INCLUDES)
def test_lf_text_includes_read_back_as_their_text(
    make_theatre: MakeTheatre, run_cli: RunCli, text: str
) -> None:
    """Req 3.4, 4.1, 4.4: an LF text include (leading spaces, leading blank lines, no final
    newline, several final newlines, only newlines, empty, tabs, trailing spaces) prints as a
    literal block that reads back as exactly its text, as a value, a list item and a value inside a
    list item mapping."""
    config = "t: file:t.txt\nl:\n  - file:t.txt\n  - k: file:t.txt\n    after: 1\n"
    output = _assert_reads_back(
        make_theatre, run_cli, Fixture(config, files={".troupe/t.txt": text})
    )
    assert output.startswith(b"t: |"), output


@pytest.mark.parametrize("text", ["  lead\nx\n", "x\n\n"])
def test_text_include_in_a_list_section_reads_back(
    make_theatre: MakeTheatre, run_cli: RunCli, text: str
) -> None:
    """Req 3.4, 4.1, 4.4: `show` of a list section whose items are text includes, at column 0,
    reads back as the included texts."""
    config = "l:\n  - file:t.txt\n  - k: file:t.txt\n  - - file:t.txt\n"
    fixture = Fixture(config, files={".troupe/t.txt": text}, path="l")
    output = _assert_reads_back(make_theatre, run_cli, fixture)
    assert output.startswith(b"- |"), output


# --- everything together -------------------------------------------------------------------------


def test_combined_configuration_reads_back(make_theatre: MakeTheatre, run_cli: RunCli) -> None:
    """Req 3.4, 4.1, 4.2, 4.3, 4.4, 4.8: one configuration holding every scalar kind, the quoting
    edge cases, the multi-line strings, awkward keys, nested collections and includes reads back
    exactly as a whole."""
    lines = [f"k{index}: {source}" for index, (source, _) in enumerate(SCALAR_KINDS)]
    lines += [f"q{index}: {source}" for index, source in enumerate(QUOTED_STRINGS)]
    lines += [f"ml{index}: {source}" for index, source in enumerate(MULTI_LINE_STRINGS)]
    lines += ["keys:"] + [f"  {key}: {index}" for index, key in enumerate(AWKWARD_KEYS)]
    lines += ["nested:"] + ["  " + line for line in NESTED.splitlines()]
    lines += ["included:"] + ["  " + line for line in INCLUDES_ROOT.splitlines()]
    texts = {f".troupe/t{index}.txt": text for index, text in enumerate(TEXT_INCLUDES)}
    lines += ["texts:"] + [f"  - file:t{index}.txt" for index in range(len(TEXT_INCLUDES))]
    files: TheatreFiles = {**INCLUDE_FILES, **texts}
    _assert_reads_back(make_theatre, run_cli, Fixture("\n".join(lines) + "\n", files=files))
