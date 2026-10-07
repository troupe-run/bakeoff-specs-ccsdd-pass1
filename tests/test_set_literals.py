"""Writing include-like VALUE strings and the ``--include`` option (config-remote-schema).

``set`` marks VALUE before target resolution (task 3.5): without ``--include`` every include-like
string of VALUE is written tagged ``!literal`` unless the schema gives its own path
``format: uri``; with ``--include`` it is written plain and loads as an include. ``--include`` is a
``store_true`` flag under the same rules as ``--in-include`` and ``--expand-alias``. Every test
runs the real console script against real files and, for remote URLs, the local
``remote_server`` fixture.

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

import json
from pathlib import Path
from typing import Final

import pytest

from remote_support import RemoteServer
from set_support import assert_file_bytes, run_config, snapshot_tree
from support import CliResult, MakeTheatre
from troupe.config.constants import CONFIG_DIR_NAME, HISTORY_STORE_NAME
from troupe.config.errors import ExitCode

CONFIG_NAMES: Final = ["troupe.yml", "troupe.yaml"]

URI_SCHEMA: Final = json.dumps({"properties": {"hook": {"type": "string", "format": "uri"}}})
"""A schema giving ``hook`` the format ``uri``."""


def _set(theatre: Path, *args: str) -> CliResult:
    return run_config("set", *args, "--theatre", str(theatre), cwd=theatre)


def _get(theatre: Path, path: str) -> bytes:
    result = run_config("get", path, "--theatre", str(theatre), cwd=theatre)
    assert (result.exit_code, result.stderr) == (0, ""), result.stderr
    return result.stdout


def _history(theatre: Path) -> list[dict[str, object]]:
    result = run_config("history", "--theatre", str(theatre), cwd=theatre)
    assert (result.exit_code, result.stderr) == (0, ""), result.stderr
    return [json.loads(line) for line in result.stdout.decode("utf-8").splitlines()]


def _history_state(theatre: Path) -> dict[str, bytes]:
    directory = theatre / CONFIG_DIR_NAME
    return {p.name: p.read_bytes() for p in sorted(directory.glob(f"{HISTORY_STORE_NAME}*"))}


def _ok(result: CliResult) -> None:
    assert (result.exit_code, result.stdout, result.stderr) == (0, b"", ""), result.stderr


# ---- 8.1, 9.3: tagged without --include, an include with it ----


@pytest.mark.parametrize("config_name", CONFIG_NAMES)
@pytest.mark.parametrize(
    ("path", "value", "expected"),
    [
        pytest.param(
            "hook", "https://example.invalid/x", "hook: !literal https://example.invalid/x\n"
        ),
        pytest.param("notes", "file:notes.md", "notes: !literal file:notes.md\n"),
        pytest.param("b", "file:inc.yaml", "b: !literal file:inc.yaml\n"),
        pytest.param(
            "a", "{b: http://example.invalid/y}", "a:\n  b: !literal http://example.invalid/y\n"
        ),
    ],
)
def test_set_writes_an_include_like_string_tagged_literal(
    make_theatre: MakeTheatre, config_name: str, path: str, value: str, expected: str
) -> None:
    """Req 8.1, 10.2: without ``--include`` an include-like VALUE string (alone or nested in a
    flow mapping) is written ``!literal`` + one space + the string, read back as that string, and
    no include file is read (``inc.yaml`` and ``notes.md`` exist and stay untouched)."""
    theatre = make_theatre(
        "x: 1\n",
        config_name=config_name,
        files={"inc.yaml": "inner: 1\n", "notes.md": "notes\n"},
    )

    _ok(_set(theatre, path, value))

    assert_file_bytes(theatre / ".troupe" / config_name, "x: 1\n" + expected)
    assert (theatre / "inc.yaml").read_bytes() == b"inner: 1\n"


def test_set_literal_reads_back_as_the_string(make_theatre: MakeTheatre) -> None:
    """Req 8.1: the tagged string reads back as the string itself, not an include."""
    theatre = make_theatre("x: 1\n", files={".troupe/inc.yaml": "inner: 1\n"})

    _ok(_set(theatre, "b", "file:inc.yaml"))

    assert _get(theatre, "b") == b"file:inc.yaml\n"


@pytest.mark.parametrize("config_name", CONFIG_NAMES)
def test_set_include_writes_a_local_include_plain(
    make_theatre: MakeTheatre, config_name: str
) -> None:
    """Req 9.3, 9.6: ``set --include b file:inc.yaml`` writes the include string plain, so it
    loads the file, and records the set's events like any other set."""
    theatre = make_theatre(
        "x: 1\n", config_name=config_name, files={".troupe/inc.yaml": "inner: 1\n"}
    )

    _ok(_set(theatre, "--include", "b", "file:inc.yaml"))

    assert_file_bytes(theatre / ".troupe" / config_name, "x: 1\nb: file:inc.yaml\n")
    assert _get(theatre, "b.inner") == b"1\n"
    sets = [line for line in _history(theatre) if line["op"] == "set"]
    assert [(line["path"], line["new"]) for line in sets] == [("b.inner", 1)]


def test_set_include_writes_a_remote_include_fetched_on_read_back(
    make_theatre: MakeTheatre, remote_server: RemoteServer
) -> None:
    """Req 9.3, 9.6: ``set --include shared URL`` writes the URL plain; the read-back fetches it
    and ``get`` reads the remote content through it."""
    url = remote_server.route("/team.yml", "lead: alice\n")
    theatre = make_theatre("x: 1\n")

    _ok(_set(theatre, "--include", "shared", url))

    assert_file_bytes(theatre / ".troupe" / "troupe.yml", f"x: 1\nshared: {url}\n")
    assert remote_server.paths() == ["/team.yml"]
    assert _get(theatre, "shared.lead") == b"alice\n"


def test_set_include_of_an_unfetchable_remote_url_is_refused_naming_the_url(
    make_theatre: MakeTheatre, remote_server: RemoteServer
) -> None:
    """Req 9.4: ``set --include team URL`` with a URL that cannot be fetched exits 3, writes
    nothing to stdout, names PATH and the URL on stderr and changes no file."""
    missing = remote_server.url("/missing.yml")
    theatre = make_theatre("x: 1\n")
    before = snapshot_tree(theatre)

    result = _set(theatre, "--include", "team", missing)

    assert result.exit_code == ExitCode.WRITE_REFUSED, result.stderr
    assert result.stdout == b""
    assert result.stderr.startswith(f"{theatre / '.troupe' / 'troupe.yml'}: team: ")
    assert missing in result.stderr
    assert snapshot_tree(theatre) == before


def test_set_include_writes_a_ref_mapping_as_an_include(make_theatre: MakeTheatre) -> None:
    """Req 9.3, 8.6: with ``--include`` a ``$ref`` mapping VALUE is written plain and loads as a
    mapping-form include with its override."""
    theatre = make_theatre("x: 1\n", files={".troupe/inc.yaml": "a: 1\nb: 2\n"})

    _ok(_set(theatre, "--include", "s", "{$ref: file:inc.yaml, b: 3}"))

    assert_file_bytes(
        theatre / ".troupe" / "troupe.yml", "x: 1\ns:\n  $ref: file:inc.yaml\n  b: 3\n"
    )
    assert (_get(theatre, "s.a"), _get(theatre, "s.b")) == (b"1\n", b"3\n")


def test_set_of_a_ref_mapping_without_include_is_refused(make_theatre: MakeTheatre) -> None:
    """Req 8.6: without ``--include`` a ``$ref`` mapping VALUE exits 3 naming PATH and mentioning
    ``--include``, with no file changed."""
    theatre = make_theatre("x: 1\n", files={".troupe/inc.yaml": "a: 1\n"})
    before = snapshot_tree(theatre)

    result = _set(theatre, "s", "{$ref: file:inc.yaml}")

    assert (result.exit_code, result.stdout) == (ExitCode.WRITE_REFUSED, b""), result.stderr
    assert "s: " in result.stderr and "--include" in result.stderr
    assert snapshot_tree(theatre) == before


# ---- 8.2: schema format uri ----


def test_set_writes_a_schema_uri_string_plain(make_theatre: MakeTheatre) -> None:
    """Req 8.2: a VALUE string whose path the schema gives ``format: uri`` is written untagged
    and reads back as the string (no request is made)."""
    theatre = make_theatre("x: 1\n", files={".troupe/troupe.schema.json": URI_SCHEMA})

    _ok(_set(theatre, "hook", "https://example.invalid/x"))

    assert_file_bytes(theatre / ".troupe" / "troupe.yml", "x: 1\nhook: https://example.invalid/x\n")
    assert _get(theatre, "hook") == b"https://example.invalid/x\n"


# ---- 9.5: --include without include strings ----


def test_set_include_without_include_strings_is_an_ordinary_set(
    make_theatre: MakeTheatre,
) -> None:
    """Req 9.5: ``--include`` with a VALUE holding no include string sets it exactly as without
    the option."""
    with_option = make_theatre("x: 1\n", name="with")
    without = make_theatre("x: 1\n", name="without")

    _ok(_set(with_option, "--include", "x", "[a, {b: 2}]"))
    _ok(_set(without, "x", "[a, {b: 2}]"))

    root = Path(".troupe") / "troupe.yml"
    assert (with_option / root).read_bytes() == (without / root).read_bytes()
    assert (without / root).read_bytes() == b"x:\n  - a\n  - b: 2\n"


# ---- 9.1: positions and combinations ----


@pytest.mark.parametrize(
    "layout",
    [
        pytest.param(("--include", "P", "V"), id="before"),
        pytest.param(("P", "--include", "V"), id="between"),
        pytest.param(("P", "V", "--include"), id="after"),
        pytest.param(("--include", "--reason", "R", "P", "V"), id="with-reason"),
        pytest.param(("P", "--include", "V", "--in-include"), id="with-in-include"),
        pytest.param(("--expand-alias", "P", "V", "--include"), id="with-expand-alias"),
        pytest.param(
            ("--in-include", "P", "--include", "V", "--reason=R", "--expand-alias"), id="all"
        ),
        pytest.param(("P", "--include", "V", "--include"), id="twice"),
    ],
)
def test_include_anywhere_together_with_the_other_options(
    make_theatre: MakeTheatre, layout: tuple[str, ...]
) -> None:
    """Req 9.1, 9.6: ``--include`` takes no value, stands before, between or after PATH and VALUE
    (also twice) and combines with ``--reason``, ``--in-include`` and ``--expand-alias``; the
    include is written plain and the reason recorded."""
    theatre = make_theatre("x: 1\n", files={".troupe/inc.yaml": "inner: 1\n"})
    names = {"R": "why", "--reason=R": "--reason=why", "P": "b", "V": "file:inc.yaml"}

    _ok(_set(theatre, *(names.get(arg, arg) for arg in layout)))

    assert_file_bytes(theatre / ".troupe" / "troupe.yml", "x: 1\nb: file:inc.yaml\n")
    sets = [line for line in _history(theatre) if line["op"] == "set"]
    reason = "why" if "R" in layout or "--reason=R" in layout else None
    assert [(line["path"], line["new"], line["reason"]) for line in sets] == [
        ("b.inner", 1, reason)
    ]


# ---- 9.2: --include=TEXT ----


@pytest.mark.parametrize("attached", ["x", "", "true"])
def test_include_with_a_value_is_a_usage_error(make_theatre: MakeTheatre, attached: str) -> None:
    """Req 9.2: ``--include=TEXT`` exits 2 with empty stdout and a usage message on stderr,
    leaves every file byte-identical and creates no history state."""
    theatre = make_theatre("x: 1\n", files={".troupe/inc.yaml": "inner: 1\n"})
    before = snapshot_tree(theatre)

    result = _set(theatre, "b", "file:inc.yaml", f"--include={attached}")

    assert (result.exit_code, result.stdout) == (2, b"")
    assert result.stderr == (
        f"troupe config set: argument --include: ignored explicit argument {attached!r}\n"
    )
    assert snapshot_tree(theatre) == before
    assert _history_state(theatre) == {}


def test_set_help_mentions_the_include_option(make_theatre: MakeTheatre) -> None:
    """Req 9.1: ``set --help`` lists ``--include`` without a metavar."""
    theatre = make_theatre("x: 1\n")
    result = run_config("set", "--help", cwd=theatre)
    assert (result.exit_code, result.stderr) == (0, ""), result.stderr
    text = result.stdout.decode("utf-8")
    assert "--include " in text or "--include\n" in text
    assert "--include=" not in text and "--include TEXT" not in text


# ---- 8.3: quoting styles, comments and line breaks (byte for byte) ----


@pytest.mark.parametrize(
    ("before", "path", "value", "after"),
    [
        pytest.param(
            "# head\na: 'old'  # c\nb: \"keep\"\n",
            "a",
            "https://example.invalid/x",
            "# head\na: !literal 'https://example.invalid/x' # c\nb: \"keep\"\n",
            id="single-quoted-with-comment",
        ),
        pytest.param(
            "a: 'keep'\nb: \"old\"  # c\n\n# tail\n",
            "b",
            "file:inc.yaml",
            "a: 'keep'\nb: !literal \"file:inc.yaml\" # c\n\n# tail\n",
            id="double-quoted-with-comment",
        ),
        pytest.param(
            "a: old                         # c\n",
            "a",
            "file:inc.yaml",
            "a: !literal file:inc.yaml      # c\n",
            id="plain-keeps-comment-column",
        ),
        pytest.param(
            "a: 1\n",
            "a",
            "'file:x: y'",
            "a: !literal 'file:x: y'\n",
            id="needs-quotes",
        ),
        pytest.param(
            "a: 1\n",
            "a",
            '"https://example.invalid/x\\nsecond"',
            'a: !literal "https://example.invalid/x\\nsecond"\n',
            id="line-break-double-quoted",
        ),
        pytest.param(
            "a: |\n  old\nb: 2\n",
            "a",
            '"https://example.invalid/x\\nsecond"',
            'a: !literal "https://example.invalid/x\\nsecond"\nb: 2\n',
            id="over-literal-block-never-a-block",
        ),
        pytest.param(
            "a: >\n  old\nb: 2\n",
            "a",
            "https://example.invalid/x",
            "a: !literal https://example.invalid/x\nb: 2\n",
            id="over-folded-block-on-its-line",
        ),
    ],
)
def test_tagged_string_keeps_the_style_rules_and_every_other_byte(
    make_theatre: MakeTheatre, before: str, path: str, value: str, after: str
) -> None:
    """Req 8.3, 10.1: a tagged string is written on its line in plain, single- or double-quoted
    style by config-set-writeback's rules (double-quoted with a line break, never a block
    scalar); comments, blank lines and the other entries keep their bytes, and the string reads
    back unchanged."""
    theatre = make_theatre(before, files={".troupe/inc.yaml": "inner: 1\n"})

    _ok(_set(theatre, path, value))

    assert_file_bytes(theatre / ".troupe" / "troupe.yml", after)
    assert (theatre / ".troupe" / "inc.yaml").read_bytes() == b"inner: 1\n"
    assert sorted(p.name for p in (theatre / ".troupe").iterdir() if p.is_file()) == sorted(
        ["troupe.yml", "inc.yaml", *_history_state(theatre)]
    )


# ---- 8.1: nested flow VALUEs, tagged per string ----


@pytest.mark.parametrize(
    ("before", "path", "value", "after"),
    [
        pytest.param(
            "m: {c: 1}\n",
            "m",
            "{b: https://example.invalid/x, k: [file:a.yaml, 2]}",
            "m: {b: !literal https://example.invalid/x, k: [!literal file:a.yaml, 2]}\n",
            id="flow-mapping-replaced-in-flow",
        ),
        pytest.param(
            "x: 1\n",
            "a",
            "[https://example.invalid/x, plain, file:b.yaml]",
            "x: 1\na:\n  - !literal https://example.invalid/x\n  - plain\n"
            "  - !literal file:b.yaml\n",
            id="block-list",
        ),
        pytest.param(
            "x: 1\n",
            "a",
            "{https://example.invalid/k: file:v.yaml}",
            "x: 1\na:\n  https://example.invalid/k: !literal file:v.yaml\n",
            id="keys-never-tagged",
        ),
    ],
)
def test_nested_include_strings_are_tagged_one_by_one(
    make_theatre: MakeTheatre, before: str, path: str, value: str, after: str
) -> None:
    """Req 8.1: every include-like string value inside a flow mapping or list VALUE is tagged on
    its own (keys are not), in block or flow style by config-set-writeback's rules; ``show``
    prints them as plain strings."""
    theatre = make_theatre(before)

    _ok(_set(theatre, path, value))

    assert_file_bytes(theatre / ".troupe" / "troupe.yml", after)
    shown = run_config("show", path, "--theatre", str(theatre), cwd=theatre)
    assert (shown.exit_code, shown.stderr) == (0, ""), shown.stderr
    assert b"!literal" not in shown.stdout


def test_show_prints_written_literals_as_their_strings(make_theatre: MakeTheatre) -> None:
    """Req 8.1: after ``set a '[https://…, file:b.yaml]'`` the whole ``show`` output holds the
    two strings untagged, and ``get`` of each item prints the string."""
    theatre = make_theatre("x: 1\n")

    _ok(_set(theatre, "a", "[https://example.invalid/x, file:b.yaml]"))

    shown = run_config("show", "--theatre", str(theatre), cwd=theatre)
    assert (shown.exit_code, shown.stderr) == (0, ""), shown.stderr
    assert shown.stdout == b"x: 1\na:\n  - https://example.invalid/x\n  - file:b.yaml\n"
    assert _get(theatre, "a.1") == b"file:b.yaml\n"


# ---- 8.2: schema format uri at nested paths ----

NESTED_URI_SCHEMA: Final = json.dumps(
    {
        "properties": {
            "hooks": {"type": "array", "items": {"type": "string", "format": "uri"}},
            "env": {
                "type": "object",
                "additionalProperties": {"type": "string", "format": "uri"},
            },
            "hook": {"type": "string", "format": "uri"},
        }
    }
)
"""``format: uri`` reached through ``items``, ``additionalProperties`` and ``properties``."""


@pytest.mark.parametrize(
    ("path", "value", "expected"),
    [
        pytest.param(
            "hooks",
            "[https://example.invalid/a, file:b.yaml]",
            "hooks:\n  - https://example.invalid/a\n  - file:b.yaml\n",
            id="items",
        ),
        pytest.param(
            "env",
            "{A: https://example.invalid/a, B: file:b.yaml}",
            "env:\n  A: https://example.invalid/a\n  B: file:b.yaml\n",
            id="additional-properties",
        ),
        pytest.param(
            "other",
            "{hook: https://example.invalid/a}",
            "other:\n  hook: !literal https://example.invalid/a\n",
            id="same-key-elsewhere-is-tagged",
        ),
    ],
)
def test_schema_uri_through_items_and_additional_properties_is_written_plain(
    make_theatre: MakeTheatre, path: str, value: str, expected: str
) -> None:
    """Req 8.2: a string whose own path the schema gives ``format: uri`` (through ``items`` or
    ``additionalProperties``) is written untagged and read back as the string, with no file
    read; a string at a path the schema does not mark is still tagged."""
    theatre = make_theatre("x: 1\n", files={".troupe/troupe.schema.json": NESTED_URI_SCHEMA})

    _ok(_set(theatre, path, value))

    assert_file_bytes(theatre / ".troupe" / "troupe.yml", "x: 1\n" + expected)
    shown = run_config("show", "--theatre", str(theatre), cwd=theatre)
    assert (shown.exit_code, shown.stderr) == (0, ""), shown.stderr
    assert shown.stdout == ("x: 1\n" + expected.replace("!literal ", "")).encode("utf-8")


def test_schema_uri_string_is_plain_beside_a_tagged_sibling(make_theatre: MakeTheatre) -> None:
    """Req 8.1, 8.2: in one flow mapping VALUE the ``format: uri`` string is written plain and
    its unmarked sibling is tagged."""
    theatre = make_theatre("x: 1\n", files={".troupe/troupe.schema.json": URI_SCHEMA})

    _ok(_set(theatre, "hook", "https://example.invalid/h"))
    _ok(_set(theatre, "y", "{hook: https://example.invalid/h}"))

    assert_file_bytes(
        theatre / ".troupe" / "troupe.yml",
        "x: 1\nhook: https://example.invalid/h\ny:\n  hook: !literal https://example.invalid/h\n",
    )


# ---- 8.4, 8.5: replacing an existing !literal ----


@pytest.mark.parametrize(
    ("before", "value", "after", "new"),
    [
        pytest.param(
            "a: !literal file:old.yaml  # c\n",
            "https://example.invalid/new",
            "a: !literal https://example.invalid/new # c\n",
            "https://example.invalid/new",
            id="include-like-stays-tagged",
        ),
        pytest.param(
            "a: &anc !literal 'file:old.yaml'\nb: 1\n",
            "file:new.yaml",
            "a: &anc !literal 'file:new.yaml'\nb: 1\n",
            "file:new.yaml",
            id="anchor-before-the-tag-stays",
        ),
        pytest.param(
            'a: !literal "file:old.yaml"\n',
            "plain",
            'a: "plain"\n',
            "plain",
            id="ordinary-string-drops-the-tag",
        ),
        pytest.param(
            "a: !literal file:old.yaml\n",
            "42",
            "a: 42\n",
            42,
            id="number-drops-the-tag",
        ),
    ],
)
def test_set_over_a_literal_replaces_the_whole_scalar_and_records_the_strings(
    make_theatre: MakeTheatre, before: str, value: str, after: str, new: object
) -> None:
    """Req 8.4, 9.6: VALUE over an existing ``!literal`` string replaces the scalar with its tag;
    the new string is tagged only if it is include-like, an anchor written before the tag stays,
    and history records the old and new values as plain strings (no tag, no include read)."""
    theatre = make_theatre(before)
    imported = _history(theatre)

    _ok(_set(theatre, "a", value))

    assert_file_bytes(theatre / ".troupe" / "troupe.yml", after)
    sets = [line for line in _history(theatre) if line["op"] == "set"]
    assert [(line["path"], line["old"], line["new"]) for line in sets] == [
        ("a", "file:old.yaml", new)
    ]
    assert next(line for line in imported if line["path"] == "a")["new"] == "file:old.yaml"


@pytest.mark.parametrize(
    ("before", "value"),
    [
        pytest.param("a: !literal 'file:x.yaml'  # c\n", "file:x.yaml", id="single-quoted"),
        pytest.param("a: &k !literal https://example.invalid/x\n", "https://example.invalid/x"),
    ],
)
def test_set_of_the_same_literal_string_is_a_no_op(
    make_theatre: MakeTheatre, before: str, value: str
) -> None:
    """Req 8.5: VALUE equal to the current ``!literal`` string (any style, with an anchor)
    changes no byte and records no event."""
    theatre = make_theatre(before)
    events = _history(theatre)  # the first command imports the file
    before_tree = snapshot_tree(theatre)

    _ok(_set(theatre, "a", value))

    assert snapshot_tree(theatre) == before_tree
    assert _history(theatre) == events


# ---- 8.4: --expand-alias over an alias of a !literal anchor ----


def test_expand_alias_copy_of_a_literal_anchor_keeps_the_tag(make_theatre: MakeTheatre) -> None:
    """Req 8.4 (config-aliases): ``--expand-alias`` over an alias of a mapping holding a
    ``!literal`` string copies the string with its tag, so the copy still reads as the string
    and no include is read."""
    theatre = make_theatre(
        "b: &x {k: !literal file:a.yaml}\nc: *x\n", files={".troupe/a.yaml": "inner: 1\n"}
    )

    _ok(_set(theatre, "c.j", "z", "--expand-alias"))

    assert_file_bytes(
        theatre / ".troupe" / "troupe.yml",
        "b: &x {k: !literal file:a.yaml}\n"
        "c:  # expanded from *x by troupe\n"
        "  k: !literal file:a.yaml\n"
        "  j: z\n",
    )
    assert _get(theatre, "c.k") == b"file:a.yaml\n"


# ---- 8.7: a tag in VALUE ----


@pytest.mark.parametrize("value", ["!literal file:x.yaml", "{a: !literal https://example.invalid}"])
def test_value_with_a_literal_tag_is_a_usage_error(make_theatre: MakeTheatre, value: str) -> None:
    """Req 8.7: VALUE holding an explicit tag, ``!literal`` included, exits 2 with empty stdout,
    a message naming PATH and the tag, and no file or history state changed."""
    theatre = make_theatre("a: 1\n")
    before = snapshot_tree(theatre)

    result = _set(theatre, "a", value)

    assert (result.exit_code, result.stdout) == (2, b""), result.stderr
    assert result.stderr.startswith("a: ") and "'!literal'" in result.stderr
    assert snapshot_tree(theatre) == before
    assert _history_state(theatre) == {}


# ---- 8.8: text include content ----


def test_include_like_text_include_content_is_written_untagged(make_theatre: MakeTheatre) -> None:
    """Req 8.8: VALUE that becomes the new content of a text include is written to the text file
    as the string, untagged; the include line in the configuration keeps its bytes."""
    theatre = make_theatre("p:\n  t: file:notes.md\n", files={".troupe/notes.md": "old\n"})

    _ok(_set(theatre, "p.t", "https://example.invalid/x"))

    assert_file_bytes(theatre / ".troupe" / "troupe.yml", "p:\n  t: file:notes.md\n")
    assert_file_bytes(theatre / ".troupe" / "notes.md", "https://example.invalid/x")
    assert _get(theatre, "p.t") == b"https://example.invalid/x\n"


# ---- 9.3, 9.4, 9.6: --include end to end ----


def test_set_include_of_a_remote_mapping_fetches_once_and_records_each_leaf(
    make_theatre: MakeTheatre, remote_server: RemoteServer
) -> None:
    """Req 9.3, 9.6, 10.3: ``set --include`` of a remote YAML include in a commented file writes
    the URL plain (comments keep their bytes), fetches it once during the set, records one `set`
    event per leaf, and ``show`` prints the remote content in place."""
    url = remote_server.route("/team.yml", "lead: alice\nsize: 3\n")
    theatre = make_theatre("# team settings\nx: 1  # one\n")

    _ok(_set(theatre, "team", url, "--include", "--reason", "add team"))

    assert_file_bytes(
        theatre / ".troupe" / "troupe.yml", f"# team settings\nx: 1  # one\nteam: {url}\n"
    )
    assert remote_server.paths() == ["/team.yml"]
    sets = [line for line in _history(theatre) if line["op"] == "set"]
    assert sorted((line["path"], line["new"], line["reason"]) for line in sets) == [
        ("team.lead", "alice", "add team"),
        ("team.size", 3, "add team"),
    ]
    shown = run_config("show", "--theatre", str(theatre), cwd=theatre)
    assert (shown.exit_code, shown.stderr) == (0, ""), shown.stderr
    assert shown.stdout == b"x: 1\nteam:\n  lead: alice\n  size: 3\n"


def test_set_include_writes_local_and_remote_strings_of_one_value_plain(
    make_theatre: MakeTheatre, remote_server: RemoteServer
) -> None:
    """Req 9.3: with ``--include`` every include string of a flow VALUE is written plain, local
    and remote alike, and both load as includes."""
    url = remote_server.route("/r.txt", "remote text\n")
    theatre = make_theatre("x: 1\n", files={".troupe/inc.yaml": "inner: 1\n"})

    _ok(_set(theatre, "s", f"{{loc: file:inc.yaml, rem: {url}}}", "--include"))

    assert_file_bytes(
        theatre / ".troupe" / "troupe.yml", f"x: 1\ns:\n  loc: file:inc.yaml\n  rem: {url}\n"
    )
    assert (_get(theatre, "s.loc.inner"), _get(theatre, "s.rem")) == (b"1\n", b"remote text\n")


@pytest.mark.parametrize("failure", ["refused", "500", "bad-yaml"])
def test_set_include_of_a_remote_url_that_cannot_load_changes_no_file(
    make_theatre: MakeTheatre, remote_server: RemoteServer, failure: str
) -> None:
    """Req 9.4, 10.1: a remote include written by ``set --include`` that cannot be fetched
    (refused connection, HTTP 500) or loaded (bad YAML) exits 3 with empty stdout, a message
    naming the file, PATH and the URL, and no file or history state changed."""
    if failure == "refused":
        with RemoteServer() as closed:
            url = closed.url("/team.yml")
    elif failure == "500":
        url = remote_server.route("/team.yml", "x", status=500)
    else:
        url = remote_server.route("/team.yml", "a: [1\n")
    theatre = make_theatre("x: 1  # keep\n")
    _history(theatre)
    before = snapshot_tree(theatre)
    state = _history_state(theatre)

    result = _set(theatre, "team", url, "--include")

    assert (result.exit_code, result.stdout) == (ExitCode.WRITE_REFUSED, b""), result.stderr
    assert result.stderr.startswith(f"{theatre / '.troupe' / 'troupe.yml'}: team: ")
    assert url in result.stderr
    assert snapshot_tree(theatre) == before
    assert _history_state(theatre) == state
