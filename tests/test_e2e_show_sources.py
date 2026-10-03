"""``troupe config show [PATH] --sources`` end to end through the real ``troupe`` script (task 4.4).

The comprehensive proof of the source annotations: every line holding a scalar ends with exactly
`` # from <file>`` (5.1) and no other line does (5.2); the file is the root file, the YAML include
the scalar is written in, or the included text file for a text include (5.3); labels are relative to
the theatre directory as given, with ``/`` separators and ``..`` only at the start for an include
outside it, and a relative theatre argument gives the same labels as an absolute one (5.4);
removing the suffixes gives the output without ``--sources`` byte for byte (5.5). On the command
line the flag goes before or after PATH, and PATH may be omitted (2.7); a scalar with
``--sources`` prints as a YAML document of its own with the comment on its line, a literal block
header for a text include (2.6); a text include that a literal block cannot reproduce (CRLF, a
control character) is still a literal block holding the file's text byte for byte (4.5).

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

import re
from pathlib import Path

import pytest

from support import CliResult, MakeTheatre, RunCli

THEATRE = "t"
"""The theatre directory name under ``tmp_path``; ``../shared`` then sits next to it."""

ROOT = """\
# root configuration: comments, quoting and number spellings are not carried into the output
name: "demo"
retries: 5
flag: 'true'
nothing:
agents:
  lead: file:agents/lead.yml
  notes: file:notes.txt
  shared: file:../../shared/p.md
crew:
  - ada
  - name: bob
    age: 0x10
  - []
empty_map: {}
empty_list: []
texts:
  crlf: file:crlf.txt
  bell: file:bell.txt
"""

FILES: dict[str, str | bytes] = {
    ".troupe/agents/lead.yml": "name: alice\nprofile: file:profile.yml\ntools: [grep, sed]\n",
    ".troupe/agents/profile.yml": "level: 3\nbio: null\n",
    ".troupe/notes.txt": b"line one\nline two\n",
    "../shared/p.md": b"shared prompt\n",
    ".troupe/crlf.txt": b"a\r\nb\r\n",
    ".troupe/bell.txt": b"bell\x07here\n",
}
"""A YAML include in a subdirectory holding a second-level include, LF, CRLF and control-character
text includes, and a text include outside the theatre directory (``<tmp>/shared/p.md``)."""

ROOT_LABEL = b" # from .troupe/troupe.yml"
LEAD_LABEL = b" # from .troupe/agents/lead.yml"
PROFILE_LABEL = b" # from .troupe/agents/profile.yml"

AGENTS_SOURCES = (
    b"lead:\n"
    b"  name: alice" + LEAD_LABEL + b"\n"
    b"  profile:\n"
    b"    level: 3" + PROFILE_LABEL + b"\n"
    b"    bio: null" + PROFILE_LABEL + b"\n"
    b"  tools:\n"
    b"    - grep" + LEAD_LABEL + b"\n"
    b"    - sed" + LEAD_LABEL + b"\n"
    b"notes: | # from .troupe/notes.txt\n"
    b"  line one\n"
    b"  line two\n"
    b"shared: | # from ../shared/p.md\n"
    b"  shared prompt\n"
)

TEXTS_SOURCES = (
    b"crlf: | # from .troupe/crlf.txt\n"
    b"  a\r\n"
    b"  b\r\n"
    b"bell: | # from .troupe/bell.txt\n"
    b"  bell\x07here\n"
)

WHOLE_SOURCES = (
    b"name: demo" + ROOT_LABEL + b"\n"
    b"retries: 5" + ROOT_LABEL + b"\n"
    b"flag: 'true'" + ROOT_LABEL + b"\n"
    b"nothing: null" + ROOT_LABEL + b"\n"
    b"agents:\n"
    + b"".join(b"  " + line + b"\n" for line in AGENTS_SOURCES.splitlines())
    + b"crew:\n"
    b"  - ada" + ROOT_LABEL + b"\n"
    b"  - name: bob" + ROOT_LABEL + b"\n"
    b"    age: 16" + ROOT_LABEL + b"\n"
    b"  - []\n"
    b"empty_map: {}\n"
    b"empty_list: []\n"
    b"texts:\n"
    b"  crlf: | # from .troupe/crlf.txt\n"
    b"    a\r\n"
    b"    b\r\n"
    b"  bell: | # from .troupe/bell.txt\n"
    b"    bell\x07here\n"
)
"""The whole configuration with ``--sources``, written out byte for byte."""

SUFFIX = re.compile(rb" # from [^\n]*$", re.MULTILINE)


def _theatre(make_theatre: MakeTheatre) -> Path:
    return make_theatre(ROOT, files=FILES, name=THEATRE)


def _show(run_cli: RunCli, theatre: Path, *args: str) -> CliResult:
    return run_cli("config", "show", *args, "--theatre", str(theatre))


def _stdout(result: CliResult) -> bytes:
    assert (result.exit_code, result.stderr) == (0, ""), result.stderr
    return result.stdout


def _assert_prints(result: CliResult, expected: bytes) -> None:
    assert _stdout(result) == expected, result.stdout.decode("utf-8", "replace")


def test_whole_configuration_with_sources_prints_exact_labelled_bytes(
    make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    """Req 5.1, 5.2, 5.3, 5.4, 2.7: `show --sources` without PATH labels every scalar line with its
    file (root, YAML include, second-level include, text include, `../shared/p.md` outside the
    theatre) after exactly one space, and no `key:`, `{}`/`[]` or literal content line."""
    theatre = _theatre(make_theatre)
    _assert_prints(_show(run_cli, theatre, "--sources"), WHOLE_SOURCES)


def test_section_with_sources_prints_exact_labelled_bytes(
    make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    """Req 5.1, 5.2, 5.3, 5.4: a section mixing a YAML include and text includes labels its
    scalars with the files they come from; a text include's label is on its `|` header only."""
    theatre = _theatre(make_theatre)
    _assert_prints(_show(run_cli, theatre, "agents", "--sources"), AGENTS_SOURCES)


def test_every_comment_has_exactly_one_space_before_the_hash(
    make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    """Req 5.1, 5.2: every comment is ` # from ` preceded by a non-space, never two spaces, and
    no line other than a scalar line carries a `#`."""
    theatre = _theatre(make_theatre)
    lines = _stdout(_show(run_cli, theatre, "--sources")).split(b"\n")
    labelled = [line for line in lines if b"#" in line]
    assert len(labelled) == 16
    for line in labelled:
        head, _, label = line.partition(b" # from ")
        assert label and not head.endswith(b" ") and b"#" not in head + label, line


@pytest.mark.parametrize(
    "path",
    [None, "agents", "agents.lead", "crew", "texts"],
    ids=["whole", "agents", "agents.lead", "crew", "texts"],
)
def test_removing_the_suffixes_gives_the_output_without_sources(
    make_theatre: MakeTheatre, run_cli: RunCli, path: str | None
) -> None:
    """Req 5.5: once every ` # from <file>` suffix is removed, the `--sources` output of the whole
    configuration or of a section PATH equals the output without `--sources` byte for byte."""
    theatre = _theatre(make_theatre)
    args = () if path is None else (path,)
    labelled = _stdout(_show(run_cli, theatre, *args, "--sources"))
    plain = _stdout(_show(run_cli, theatre, *args))
    assert SUFFIX.search(labelled) is not None
    assert SUFFIX.search(plain) is None
    assert SUFFIX.sub(b"", labelled) == plain


@pytest.mark.parametrize("path", ["agents", "retries", "agents.notes"])
def test_sources_before_or_after_path_prints_the_same_bytes(
    make_theatre: MakeTheatre, run_cli: RunCli, path: str
) -> None:
    """Req 2.7: `show PATH --sources` and `show --sources PATH` print identical bytes."""
    theatre = _theatre(make_theatre)
    after = _stdout(_show(run_cli, theatre, path, "--sources"))
    before = _stdout(_show(run_cli, theatre, "--sources", path))
    assert b" # from " in after
    assert before == after


@pytest.mark.parametrize(
    ("path", "with_sources", "without_sources"),
    [
        ("retries", b"5" + ROOT_LABEL + b"\n", b"5\n"),
        ("name", b"demo" + ROOT_LABEL + b"\n", b"demo\n"),
        ("flag", b"'true'" + ROOT_LABEL + b"\n", b"true\n"),
        ("nothing", b"null" + ROOT_LABEL + b"\n", b"null\n"),
        ("crew.1.age", b"16" + ROOT_LABEL + b"\n", b"16\n"),
        ("agents.lead.name", b"alice" + LEAD_LABEL + b"\n", b"alice\n"),
        ("agents.lead.profile.level", b"3" + PROFILE_LABEL + b"\n", b"3\n"),
        (
            "agents.notes",
            b"| # from .troupe/notes.txt\n  line one\n  line two\n",
            b"line one\nline two\n",
        ),
        ("agents.shared", b"| # from ../shared/p.md\n  shared prompt\n", b"shared prompt\n"),
    ],
)
def test_scalar_with_sources_prints_a_labelled_yaml_document(
    make_theatre: MakeTheatre,
    run_cli: RunCli,
    path: str,
    with_sources: bytes,
    without_sources: bytes,
) -> None:
    """Req 2.6, 5.1, 5.3, 2.1: `show <scalar> --sources` prints the scalar's YAML form alone (a
    string needing quotes quoted, a text include as a root literal block) with the comment on the
    scalar's line or the block header; without `--sources` it prints `get`'s raw form."""
    theatre = _theatre(make_theatre)
    _assert_prints(_show(run_cli, theatre, path, "--sources"), with_sources)
    _assert_prints(_show(run_cli, theatre, path), without_sources)
    _assert_prints(run_cli("config", "get", path, "--theatre", str(theatre)), without_sources)


def test_crlf_and_control_character_text_includes_stay_byte_exact_literal_blocks(
    make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    """Req 4.5, 5.1, 5.2: a CRLF text include and one holding `\\x07` print as literal blocks
    whose content lines keep `\\r\\n` and the control character unchanged, with the comment on the
    header line only."""
    theatre = _theatre(make_theatre)
    _assert_prints(_show(run_cli, theatre, "texts", "--sources"), TEXTS_SOURCES)
    _assert_prints(
        _show(run_cli, theatre, "texts.crlf", "--sources"),
        b"| # from .troupe/crlf.txt\n  a\r\n  b\r\n",
    )
    _assert_prints(
        _show(run_cli, theatre, "texts.bell", "--sources"),
        b"| # from .troupe/bell.txt\n  bell\x07here\n",
    )


@pytest.mark.parametrize("path", [None, "agents"], ids=["whole", "agents"])
def test_relative_theatre_argument_gives_the_same_labels_as_an_absolute_one(
    make_theatre: MakeTheatre, run_cli: RunCli, tmp_path: Path, path: str | None
) -> None:
    """Req 5.4: `--theatre t` from the parent directory, `.`, `../t` or no `--theatre` from the
    theatre and `..` from `.troupe` give the same `/`-separated labels as an absolute path."""
    theatre = _theatre(make_theatre)
    args = ("config", "show", *(() if path is None else (path,)), "--sources")
    absolute = _stdout(run_cli(*args, "--theatre", str(theatre.resolve())))
    assert b"# from ../shared/p.md\n" in absolute and b"\\" not in absolute
    assert _stdout(run_cli(*args, "--theatre", THEATRE, cwd=tmp_path)) == absolute
    assert _stdout(run_cli(*args, "--theatre", ".", cwd=theatre)) == absolute
    assert _stdout(run_cli(*args, cwd=theatre)) == absolute
    assert _stdout(run_cli(*args, "--theatre", f"../{THEATRE}", cwd=theatre)) == absolute
    assert _stdout(run_cli(*args, "--theatre", "..", cwd=theatre / ".troupe")) == absolute


def test_labels_keep_a_symlinked_directory_unresolved(
    make_theatre: MakeTheatre, run_cli: RunCli, tmp_path: Path
) -> None:
    """Req 5.4: an include reached through a symbolic link is labelled with the path as written
    (`../shared/p.md`), not with the link's target, also when the theatre itself is a link."""
    store = tmp_path / "store" / "deep"
    store.mkdir(parents=True)
    (store / "p.md").write_bytes(b"shared prompt\n")
    (tmp_path / "shared").symlink_to(store, target_is_directory=True)
    theatre = make_theatre("prompt: file:../../shared/p.md\n", name=THEATRE)
    (tmp_path / "alias").symlink_to(theatre, target_is_directory=True)
    expected = b"prompt: | # from ../shared/p.md\n  shared prompt\n"
    _assert_prints(_show(run_cli, theatre, "--sources"), expected)
    _assert_prints(run_cli("config", "show", "--sources", "--theatre", "t", cwd=tmp_path), expected)
    aliased = run_cli("config", "show", "--sources", "--theatre", "alias", cwd=tmp_path)
    _assert_prints(aliased, expected)
