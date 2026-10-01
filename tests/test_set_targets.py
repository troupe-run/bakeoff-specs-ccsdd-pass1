"""End-to-end validation of where `troupe config set` writes a change, including new includes (5.2).

Every case runs the real ``troupe`` console script (``set_support``). VALUE is the exact argv
string, written as a Python literal. Success cases assert exit 0, empty stdout, empty stderr, the
exact bytes of the changed file and that no other file changed; failure cases assert the exit
code, empty stdout, the stderr naming PATH and/or the file, and an unchanged theatre.

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

from pathlib import Path

import pytest

from set_support import TreeSnapshot, assert_file_bytes, run_config, run_set, snapshot_tree
from support import CliResult, MakeTheatre

ROOT = ".troupe/troupe.yml"


def _assert_success(result: CliResult) -> None:
    assert (result.exit_code, result.stdout, result.stderr) == (0, b"", ""), result.stderr


def _assert_only_changed(
    theatre: Path, before: TreeSnapshot, relative: str, expected: str | bytes
) -> None:
    """Assert that ``relative`` holds exactly ``expected`` and every other file is unchanged."""
    assert_file_bytes(theatre / relative, expected)
    after = snapshot_tree(theatre)
    assert set(after) == set(before)
    assert {k: v for k, v in after.items() if k != relative} == {
        k: v for k, v in before.items() if k != relative
    }


def _assert_refused(
    theatre: Path,
    before: TreeSnapshot,
    result: CliResult,
    code: int,
    *,
    file: str,
    path: str,
    names: tuple[str, ...] = (),
) -> None:
    """Assert exit ``code``, empty stdout, one stderr line ``<file>: <PATH>: <reason>``, unchanged.

    ``file`` is the theatre-relative file; the message starts with it under the ``--theatre``
    directory, followed by the PATH field ``path``; ``names`` must appear in the reason.
    """
    assert result.exit_code == code, (result.exit_code, result.stderr)
    assert result.stdout == b""
    assert result.stderr.endswith("\n") and result.stderr.count("\n") == 1, result.stderr
    prefix = f"{theatre / file}: {path}: "
    assert result.stderr.startswith(prefix), (prefix, result.stderr)
    reason = result.stderr[len(prefix) :]
    for name in names:
        assert name in reason, (name, result.stderr)
    assert snapshot_tree(theatre) == before


# ---- 2.1, 2.5, 2.6: values reached through a YAML include ----

AGENTS_ROOT = "model: gpt\nagents:\n  coder: file:agents/coder.yaml\n"
CODER = "model: small\ntemperature: 0.2\n"
CODER_FILE = ".troupe/agents/coder.yaml"


def _agents_theatre(make_theatre: MakeTheatre) -> Path:
    return make_theatre(AGENTS_ROOT, files={CODER_FILE: CODER})


def test_existing_value_in_yaml_include_is_changed_in_the_included_file(
    make_theatre: MakeTheatre,
) -> None:
    """Req 2.1, 2.6: `agents.coder.model` is changed in coder.yaml only; troupe.yml unchanged."""
    theatre = _agents_theatre(make_theatre)
    before = snapshot_tree(theatre)

    result = run_set(theatre, "agents.coder.model", "large")

    _assert_success(result)
    _assert_only_changed(theatre, before, CODER_FILE, "model: large\ntemperature: 0.2\n")


def test_new_key_below_yaml_include_is_added_in_the_included_file(
    make_theatre: MakeTheatre,
) -> None:
    """Req 3.1, 2.6: `agents.coder.top_p` is appended to the mapping in coder.yaml."""
    theatre = _agents_theatre(make_theatre)
    before = snapshot_tree(theatre)

    result = run_set(theatre, "agents.coder.top_p", "0.9")

    _assert_success(result)
    _assert_only_changed(
        theatre, before, CODER_FILE, "model: small\ntemperature: 0.2\ntop_p: 0.9\n"
    )


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("{model: x}", id="mapping"),
        pytest.param("file:agents/other.yaml", id="other-include"),
    ],
)
def test_changing_a_yaml_include_root_is_refused(make_theatre: MakeTheatre, value: str) -> None:
    """Req 2.5, 16.3: replacing `agents.coder` (include root) exits 3 naming PATH, coder.yaml."""
    theatre = make_theatre(
        AGENTS_ROOT, files={CODER_FILE: CODER, ".troupe/agents/other.yaml": "model: z\n"}
    )
    before = snapshot_tree(theatre)

    result = run_set(theatre, "agents.coder", value)

    _assert_refused(theatre, before, result, 3, file=CODER_FILE, path="agents.coder")


# ---- 2.2, 2.3, 2.4: text includes ----

PROMPT_ROOT = "prompt: file:prompt.txt\n"
PROMPT_FILE = ".troupe/prompt.txt"


@pytest.mark.parametrize(
    ("old", "expected"),
    [
        pytest.param(b"old\r\nline\r\n", b"new\r\ntext\r\n", id="crlf"),
        pytest.param(b"old\n", b"new\ntext\n", id="lf"),
    ],
)
def test_string_over_text_include_replaces_the_target_content(
    make_theatre: MakeTheatre, old: bytes, expected: bytes
) -> None:
    """Req 2.2, 2.3: the text file gets VALUE exactly (CRLF kept when every break is CRLF)."""
    theatre = make_theatre(PROMPT_ROOT, files={PROMPT_FILE: old})
    before = snapshot_tree(theatre)

    result = run_set(theatre, "prompt", '"new\\ntext\\n"')

    _assert_success(result)
    _assert_only_changed(theatre, before, PROMPT_FILE, expected)


def test_non_string_over_text_include_is_refused(make_theatre: MakeTheatre) -> None:
    """Req 2.4, 16.3: `prompt, '5'` on a text include exits 3 naming PATH and prompt.txt."""
    theatre = make_theatre(PROMPT_ROOT, files={PROMPT_FILE: b"old\r\nline\r\n"})
    before = snapshot_tree(theatre)

    result = run_set(theatre, "prompt", "5")

    _assert_refused(theatre, before, result, 3, file=PROMPT_FILE, path="prompt")


# ---- 3.1, 3.2, 3.3: new keys and intermediate mappings ----

NEW_KEYS = [
    pytest.param("a:\n  x: 1\nb: 2\n", "a.y", "2",
                 "a:\n  x: 1\n  y: 2\nb: 2\n", id="nested-key"),
    pytest.param("a:\n  x: 1\nb: 2\n", "a.p.q", "1",
                 "a:\n  x: 1\n  p:\n    q: 1\nb: 2\n", id="intermediate-mapping"),
    pytest.param("# config\n", "a", "1", "# config\na: 1\n", id="comment-only-root"),
    pytest.param("", "a", "1", "a: 1\n", id="empty-root"),
]  # fmt: skip


@pytest.mark.parametrize(("config", "path", "value", "expected"), NEW_KEYS)
def test_new_keys_are_added_in_the_file_holding_the_mapping(
    make_theatre: MakeTheatre, config: str, path: str, value: str, expected: str
) -> None:
    """Req 3.1, 3.2, 3.3: missing keys and intermediates are added to the root file."""
    theatre = make_theatre(config)
    before = snapshot_tree(theatre)

    result = run_set(theatre, path, value)

    _assert_success(result)
    _assert_only_changed(theatre, before, ROOT, expected)


# ---- 2.7, 2.8, 14.8: new includes written by the set ----


@pytest.mark.parametrize(
    ("value", "target", "content", "show_path", "shown"),
    [
        pytest.param(
            "file:inc.yaml", ".troupe/inc.yaml", "k: 1\n", "b.k", b"1\n", id="yaml-include"
        ),
        pytest.param(
            "file:notes.txt", ".troupe/notes.txt", "hi\n", "b", b"hi\n", id="text-include"
        ),
    ],
)
def test_new_include_is_written_as_a_string_and_target_is_unchanged(
    make_theatre: MakeTheatre, value: str, target: str, content: str, show_path: str, shown: bytes
) -> None:
    """Req 2.7: a `file:` VALUE is written as a plain string; the include target is untouched.

    `troupe config show` afterwards reads the include's content at `b` (2.7 read-back).
    """
    theatre = make_theatre("a: 1\n", files={target: content})
    before = snapshot_tree(theatre)

    result = run_set(theatre, "b", value)

    _assert_success(result)
    _assert_only_changed(theatre, before, ROOT, f"a: 1\nb: {value}\n")
    assert_file_bytes(theatre / target, content)
    shown_result = run_config("show", show_path, "--theatre", str(theatre), cwd=theatre)
    assert (shown_result.exit_code, shown_result.stdout, shown_result.stderr) == (0, shown, "")


def test_new_include_with_missing_target_is_refused(make_theatre: MakeTheatre) -> None:
    """Req 14.8, 16.3: `b, 'file:missing.yaml'` exits 3 naming PATH `b` and missing.yaml."""
    theatre = make_theatre("a: 1\n")
    before = snapshot_tree(theatre)

    result = run_set(theatre, "b", "file:missing.yaml")

    _assert_refused(theatre, before, result, 3, file=ROOT, path="b", names=("missing.yaml",))


def test_new_include_leading_into_a_cycle_is_refused(make_theatre: MakeTheatre) -> None:
    """Req 14.8, 16.3: an include back to troupe.yml exits 3 naming `b` and the cycle files."""
    theatre = make_theatre("a: 1\n", files={".troupe/loop.yaml": "back: file:troupe.yml\n"})
    before = snapshot_tree(theatre)

    result = run_set(theatre, "b", "file:loop.yaml")

    _assert_refused(
        theatre, before, result, 3, file=ROOT, path="b", names=("loop.yaml", "troupe.yml")
    )


# ---- 4.1, 4.2, 4.3, 3.4, 3.5, 3.6, 16.3: appends and exit 5 ----


def test_index_equal_to_length_appends_an_item(make_theatre: MakeTheatre) -> None:
    """Req 4.1: `l.2, 'c'` on a two-item block list appends `c` as the last item."""
    theatre = make_theatre("l:\n  - a\n  - b\n")
    before = snapshot_tree(theatre)

    result = run_set(theatre, "l.2", "c")

    _assert_success(result)
    _assert_only_changed(theatre, before, ROOT, "l:\n  - a\n  - b\n  - c\n")


NOT_FOUND = [
    pytest.param("l:\n  - a\n  - b\n", "l.3", "c", id="4.2-index-past-length"),
    pytest.param("l:\n  - a\n  - b\n", "l.2.x", "1", id="4.3-segments-after-append-index"),
    pytest.param("l:\n  - a\n  - b\n", "l.x", "1", id="3.5-key-on-list"),
    pytest.param("n:\n", "n.k", "1", id="3.4-key-below-null"),
    pytest.param("s: 5\n", "s.k", "1", id="3.4-key-below-scalar"),
    pytest.param("m:\n  k: v\n", "m.0", "x", id="3.6-digit-key-on-mapping"),
]


@pytest.mark.parametrize(("config", "path", "value"), NOT_FOUND)
def test_unreachable_paths_exit_5_naming_path_and_file(
    make_theatre: MakeTheatre, config: str, path: str, value: str
) -> None:
    """Req 3.4, 3.5, 3.6, 4.2, 4.3, 16.3: PATH not found / wrong kind exits 5 naming PATH, file."""
    theatre = make_theatre(config)
    before = snapshot_tree(theatre)

    result = run_set(theatre, path, value)

    _assert_refused(theatre, before, result, 5, file=ROOT, path=path)
