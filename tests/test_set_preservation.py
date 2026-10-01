"""End-to-end validation of byte preservation, line endings, encoding and file identity (5.8).

Every case runs the real ``troupe`` console script (``set_support``). PATH and VALUE are the
exact argv strings, written as Python literals; fixtures and expected contents are the exact
bytes of the files. Each case asserts exit 0, empty stdout and stderr, the exact bytes of every
file of the theatre and that no file was added, removed or renamed.

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

import os
import stat
from pathlib import Path

import pytest

from set_support import TreeSnapshot, assert_file_bytes, run_set, snapshot_tree
from support import CliResult, MakeTheatre

ROOT = ".troupe/troupe.yml"


def _assert_success(result: CliResult) -> None:
    assert (result.exit_code, result.stdout, result.stderr) == (0, b"", ""), result.stderr


def _assert_tree(after: TreeSnapshot, before: TreeSnapshot, changed: dict[str, bytes]) -> None:
    """Assert the files are the same as before (no file added, removed or renamed) and every file
    holds its old bytes except the files in ``changed``, which hold exactly the given bytes."""
    assert set(after) == set(before)
    assert after == {**before, **changed}


def _set_root_and_check(
    make_theatre: MakeTheatre,
    fixture: str | bytes,
    path: str,
    value: str,
    expected: str | bytes,
    *,
    config_name: str = "troupe.yml",
) -> None:
    theatre = make_theatre(fixture, config_name=config_name)
    root = f".troupe/{config_name}"
    before = snapshot_tree(theatre)
    _assert_success(run_set(theatre, path, value))
    assert_file_bytes(theatre / root, expected)
    want = expected.encode("utf-8") if isinstance(expected, str) else expected
    _assert_tree(snapshot_tree(theatre), before, {root: want})


@pytest.mark.parametrize(
    ("fixture", "path", "value", "expected"),
    [
        pytest.param(
            b"a: 1\r\nb:\r\n  c: 2\r\n",
            "b.d",
            '"x\\ny\\n"',
            b"a: 1\r\nb:\r\n  c: 2\r\n  d: |\r\n    x\r\n    y\r\n",
            id="crlf-new-literal-block",
        ),
        pytest.param(b"a: 1\r\nb: 2\r\n", "c", "3", b"a: 1\r\nb: 2\r\nc: 3\r\n", id="crlf-new-key"),
        pytest.param(b"a: 1\r\nb: 2", "c", "3", b"a: 1\r\nb: 2\r\nc: 3", id="crlf-no-final-eol"),
        pytest.param(b"l:\r\n- 1\r\n", "l.1", "2", b"l:\r\n- 1\r\n- 2\r\n", id="crlf-append"),
        pytest.param(
            b"a: 1\nb:\r\n  c: 2\r\nz: 0\n",
            "b.e",
            "[1, 2]",
            b"a: 1\nb:\r\n  c: 2\r\n  e:\r\n    - 1\r\n    - 2\r\nz: 0\n",
            id="crlf-new-collection",
        ),
    ],
)
def test_inserted_lines_use_crlf_in_a_crlf_file(
    make_theatre: MakeTheatre, fixture: bytes, path: str, value: str, expected: bytes
) -> None:
    """Req 13.1, 13.2: every line break the set inserts is CRLF when the file (or the line after
    which it inserts) uses CRLF; a missing final line break stays missing."""
    _set_root_and_check(make_theatre, fixture, path, value, expected)


@pytest.mark.parametrize(
    ("fixture", "path", "value", "expected"),
    [
        pytest.param(b"a: 1\r\nb: 2\n", "c", "3", b"a: 1\r\nb: 2\nc: 3\n", id="after-lf-line"),
        pytest.param(
            b"a: 1\nb:\r\n  c: 2\r\nz: 0\n",
            "b.d",
            "3",
            b"a: 1\nb:\r\n  c: 2\r\n  d: 3\r\nz: 0\n",
            id="after-crlf-line",
        ),
        pytest.param(
            b"a: 1\r\nb: 2\nc: 3",
            "d",
            "4",
            b"a: 1\r\nb: 2\nc: 3\nd: 4",
            id="after-line-without-eol",
        ),
    ],
)
def test_mixed_line_endings_follow_the_line_inserted_after(
    make_theatre: MakeTheatre, fixture: bytes, path: str, value: str, expected: bytes
) -> None:
    """Req 13.1, 13.2: in a file with mixed line endings an inserted line break copies the line
    after which it inserts, LF when that line has none (no final line break is added)."""
    _set_root_and_check(make_theatre, fixture, path, value, expected)


def test_file_without_final_line_break_keeps_none(make_theatre: MakeTheatre) -> None:
    """Req 13.1, 13.2: an LF file without a final line break gets LF before the new line and
    still ends without a line break."""
    _set_root_and_check(make_theatre, "a: 1\nb: 2", "c", "3", "a: 1\nb: 2\nc: 3")


@pytest.mark.parametrize(
    ("fixture", "path", "value", "expected"),
    [
        pytest.param(b"\xef\xbb\xbfa: 1\n", "a", "2", b"\xef\xbb\xbfa: 2\n", id="changed-value"),
        pytest.param(
            b"\xef\xbb\xbfa: 1\r\n",
            "b",
            '"x\\ny\\n"',
            b"\xef\xbb\xbfa: 1\r\nb: |\r\n  x\r\n  y\r\n",
            id="new-key-crlf",
        ),
        pytest.param(
            "\ufeffname: café\n".encode(),
            "name",
            "naïve",
            "\ufeffname: naïve\n".encode(),
            id="non-ascii",
        ),
    ],
)
def test_byte_order_mark_and_utf8_are_kept(
    make_theatre: MakeTheatre, fixture: bytes, path: str, value: str, expected: bytes
) -> None:
    """Req 13.1, 13.3: the written file stays UTF-8 and keeps its byte order mark."""
    _set_root_and_check(make_theatre, fixture, path, value, expected)


RICH = (
    "# Theatre\n"
    "\n"
    'name: "Main stage"   # display\n'
    "agents:\n"
    "  coder:\n"
    "    model: 'gpt'\n"
    "    tools: [read, write]\n"
    "  # reviewer next\n"
    "  reviewer: {model: x}\n"
    "zeta: café\n"
)


@pytest.mark.parametrize("config_name", ["troupe.yml", "troupe.yaml"])
def test_rich_fixture_changes_only_the_requested_scalar(
    make_theatre: MakeTheatre, config_name: str
) -> None:
    """Req 13.1, 13.3, 13.4: on a fixture with comments, blank lines, mixed quoting, flow and
    block collections and non-ASCII text, the diff is exactly `'gpt'` -> `'claude'` (the same
    bytes for `troupe.yml` and `troupe.yaml`)."""
    expected = RICH.replace("'gpt'", "'claude'")
    assert expected.count("'claude'") == 1 and len(expected) == len(RICH) + 3
    _set_root_and_check(
        make_theatre, RICH, "agents.coder.model", "claude", expected, config_name=config_name
    )


RICH_CRLF = (
    b"# Theatre config\r\n"
    b"\r\n"
    b"zeta: caf\xc3\xa9        # last first\r\n"
    b'name: "Main stage"   # display\r\n'
    b"agents:\r\n"
    b"  coder:\r\n"
    b"    model: 'gpt'\r\n"
    b"    tools: [read, write]\r\n"
    b"    prompt: |\r\n"
    b"      Be careful.\r\n"
    b"      Test first.\r\n"
    b"\r\n"
    b"  # reviewer next\r\n"
    b"  reviewer: {model: x, temp: 0.5}\r\n"
    b"notes: >\r\n"
    b"  folded text\r\n"
    b"  here\r\n"
    b"alpha: plain\r\n"
)


@pytest.mark.parametrize(
    ("path", "value", "old", "new"),
    [
        pytest.param(
            "name", "Side stage", b'"Main stage"   #', b'"Side stage"   #', id="quoted-commented"
        ),
        pytest.param(
            "agents.reviewer.temp", "0.7", b"temp: 0.5}", b"temp: 0.7}", id="flow-mapping-value"
        ),
        pytest.param(
            "agents.coder.tools.2",
            "exec",
            b"[read, write]",
            b"[read, write, exec]",
            id="flow-sequence-append",
        ),
        pytest.param(
            "agents.coder.prompt",
            '"Be bold.\\nShip it.\\n"',
            b"      Be careful.\r\n      Test first.\r\n",
            b"      Be bold.\r\n      Ship it.\r\n",
            id="literal-block",
        ),
        pytest.param(
            "agents.coder.timeout",
            "30",
            b"      Test first.\r\n\r\n",
            b"      Test first.\r\n    timeout: 30\r\n\r\n",
            id="new-key-after-block-scalar",
        ),
        pytest.param(
            "omega",
            "{k: v}",
            b"alpha: plain\r\n",
            b"alpha: plain\r\nomega:\r\n  k: v\r\n",
            id="new-block-mapping-at-end",
        ),
    ],
)
def test_rich_crlf_fixture_diff_is_exactly_the_requested_change(
    make_theatre: MakeTheatre, path: str, value: str, old: bytes, new: bytes
) -> None:
    """Req 13.1, 13.2, 13.3: on a CRLF fixture with comments, blank lines, unusual key order,
    mixed quoting, flow and block collections, block scalars and non-ASCII text, exactly the
    requested bytes change and every inserted line ends with CRLF."""
    assert RICH_CRLF.count(old) == 1
    _set_root_and_check(make_theatre, RICH_CRLF, path, value, RICH_CRLF.replace(old, new))


def test_symlinked_root_is_written_through_and_stays_a_link(make_theatre: MakeTheatre) -> None:
    """Req 13.1, 13.4, 13.5: the change is written into the link's target, the link stays a
    symlink pointing at the same target, and no file is added, removed or renamed."""
    theatre = make_theatre(None, files={"shared/real.yml": "a: 1   # c\nb: 2\n"})
    link = theatre / ROOT
    link.parent.mkdir()
    link.symlink_to(Path("..") / "shared" / "real.yml")
    before = snapshot_tree(theatre)
    real_before = os.listdir(theatre / "shared")
    troupe_before = sorted(os.listdir(theatre / ".troupe"))
    _assert_success(run_set(theatre, "a", "5"))
    assert link.is_symlink()
    assert os.readlink(link) == os.path.join("..", "shared", "real.yml")
    assert_file_bytes(theatre / "shared" / "real.yml", "a: 5   # c\nb: 2\n")
    new = b"a: 5   # c\nb: 2\n"
    _assert_tree(snapshot_tree(theatre), before, {ROOT: new, "shared/real.yml": new})
    assert os.listdir(theatre / "shared") == real_before
    assert sorted(n for n in os.listdir(theatre / ".troupe") if not n.startswith("history")) == [
        n for n in troupe_before if not n.startswith("history")
    ]


def test_symlinked_include_is_written_through(make_theatre: MakeTheatre, tmp_path: Path) -> None:
    """Req 13.1, 13.4, 13.5: an include reached through a symlink pointing outside the theatre is
    written in its target; the link stays and the root file keeps its bytes."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "coder.yaml").write_bytes(b"model: small\r\n")
    theatre = make_theatre("agents:\n  coder: file:agents/coder.yaml\n")
    link = theatre / ".troupe" / "agents" / "coder.yaml"
    link.parent.mkdir()
    link.symlink_to(outside / "coder.yaml")
    before = snapshot_tree(theatre)
    _assert_success(run_set(theatre, "agents.coder.model", "large"))
    assert link.is_symlink()
    assert os.readlink(link) == str(outside / "coder.yaml")
    assert_file_bytes(outside / "coder.yaml", b"model: large\r\n")
    assert os.listdir(outside) == ["coder.yaml"]
    _assert_tree(snapshot_tree(theatre), before, {".troupe/agents/coder.yaml": b"model: large\r\n"})


@pytest.mark.parametrize("mode", [0o640, 0o600, 0o664])
def test_permission_bits_of_the_written_file_are_kept(make_theatre: MakeTheatre, mode: int) -> None:
    """Req 13.5: the written file keeps its permission bits."""
    theatre = make_theatre("a: 1\n")
    (theatre / ROOT).chmod(mode)
    _assert_success(run_set(theatre, "a", "2"))
    assert_file_bytes(theatre / ROOT, "a: 2\n")
    assert stat.S_IMODE((theatre / ROOT).stat().st_mode) == mode


def test_permission_bits_of_a_symlinked_target_are_kept(make_theatre: MakeTheatre) -> None:
    """Req 13.5: writing through a symlink keeps the target's permission bits (0o640)."""
    theatre = make_theatre(None, files={"real.yml": "a: 1\n"})
    (theatre / "real.yml").chmod(0o640)
    (theatre / ".troupe").mkdir()
    (theatre / ROOT).symlink_to(theatre / "real.yml")
    _assert_success(run_set(theatre, "a", "2"))
    assert (theatre / ROOT).is_symlink()
    assert_file_bytes(theatre / "real.yml", "a: 2\n")
    assert stat.S_IMODE((theatre / "real.yml").stat().st_mode) == 0o640


INCLUDES: dict[str, str | bytes] = {
    ".troupe/agents/coder.yaml": b"# coder\r\nmodel: 'small'   # cheap\r\ntools: [read]\r\n",
    ".troupe/agents/reviewer.yaml": "\ufeffmodel: big\nnotes: |\n  keep me\n",
    ".troupe/prompts/system.txt": b"Be kind.\r\nNo final newline",
}
INCLUDE_ROOT = (
    "# root\n"
    "agents:\n"
    "  coder: file:agents/coder.yaml\n"
    "  reviewer: file:agents/reviewer.yaml   # second\n"
    "prompt: file:prompts/system.txt\n"
    "level: 1\n"
)


def test_include_files_untouched_when_the_root_changes(make_theatre: MakeTheatre) -> None:
    """Req 13.1, 13.4: a set in the root file leaves every include file byte-identical and adds,
    removes or renames no file."""
    theatre = make_theatre(INCLUDE_ROOT, files=INCLUDES)
    before = snapshot_tree(theatre)
    _assert_success(run_set(theatre, "level", "2"))
    expected = INCLUDE_ROOT.replace("level: 1", "level: 2").encode()
    _assert_tree(snapshot_tree(theatre), before, {ROOT: expected})


def test_only_the_written_include_changes(make_theatre: MakeTheatre) -> None:
    """Req 13.1, 13.2, 13.4: a set inside one CRLF include changes only its value; the root, the
    other YAML include (with BOM) and the text include keep their bytes."""
    theatre = make_theatre(INCLUDE_ROOT, files=INCLUDES)
    (theatre / ".troupe/agents/coder.yaml").chmod(0o640)
    before = snapshot_tree(theatre)
    _assert_success(run_set(theatre, "agents.coder.model", "medium"))
    expected = b"# coder\r\nmodel: 'medium'  # cheap\r\ntools: [read]\r\n"
    _assert_tree(snapshot_tree(theatre), before, {".troupe/agents/coder.yaml": expected})
    assert stat.S_IMODE((theatre / ".troupe/agents/coder.yaml").stat().st_mode) == 0o640


def test_text_include_keeps_crlf_and_other_files(make_theatre: MakeTheatre) -> None:
    """Req 13.1, 13.2, 13.4: a set of a text include writes VALUE's text into the include with the
    file's CRLF line breaks; every other file keeps its bytes."""
    theatre = make_theatre(INCLUDE_ROOT, files=INCLUDES)
    before = snapshot_tree(theatre)
    _assert_success(run_set(theatre, "prompt", '"Be brief.\\nBe right."'))
    _assert_tree(
        snapshot_tree(theatre),
        before,
        {".troupe/prompts/system.txt": b"Be brief.\r\nBe right."},
    )
