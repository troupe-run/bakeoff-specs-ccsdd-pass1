"""Unit tests of the AtomicWriter (task 4.3): ``troupe.config.writeback.atomic``.

Every test writes real files in a temporary directory. Failures are injected at the OS boundary
(``os.replace``, ``os.write``, ``os.fsync``, an unwritable directory) to prove that the target keeps
its previous bytes and that no temporary file is left behind (product-owner decision E).

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

import os
import re
import secrets
import stat
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from set_support import assert_file_bytes, snapshot_tree
from troupe.config.errors import ExitCode, WriteRefusedError
from troupe.config.model import LineEnding, SourceFile
from troupe.config.writeback.atomic import restore_file, write_atomically

TEMP_NAME = re.compile(r"^\.troupe\.yml\.troupe-tmp-[0-9a-f]{16}$")


def _source(path: Path, text: str) -> SourceFile:
    """Return the SourceFile the loader would produce for ``path`` holding ``text``."""
    return SourceFile(path=path, real_path=path.resolve(), text=text, line_ending=LineEnding.LF)


def _make(directory: Path, name: str, data: bytes, mode: int = 0o644) -> Path:
    file = directory / name
    file.write_bytes(data)
    file.chmod(mode)
    return file


def _listing(directory: Path) -> list[str]:
    return sorted(os.listdir(directory))


@pytest.fixture
def unwritable_dir_supported(tmp_path: Path) -> Iterator[None]:
    probe = tmp_path / "probe"
    probe.mkdir()
    probe.chmod(0o555)
    try:
        writable = os.access(probe, os.W_OK)
    finally:
        probe.chmod(0o755)
        probe.rmdir()
    if writable:
        pytest.skip("directory permissions are not enforced for this user (root?)")
    yield


def test_writes_new_bytes_and_keeps_listing(tmp_path: Path) -> None:
    """Req 13.4, 14.3, 14.4: the target holds the new bytes; the listing is otherwise unchanged."""
    target = _make(tmp_path, "troupe.yml", b"a: 1\n")
    other = _make(tmp_path, "other.yaml", b"x: 1\n")
    before = snapshot_tree(tmp_path)

    write_atomically(_source(target, "a: 1\n"), "a: 2\n")

    assert_file_bytes(target, "a: 2\n")
    assert_file_bytes(other, "x: 1\n")
    assert snapshot_tree(tmp_path) == {**before, "troupe.yml": b"a: 2\n"}


def test_keeps_bom_and_crlf_without_translation(tmp_path: Path) -> None:
    """Req 13.3: UTF-8 with the BOM kept; line breaks are written exactly as in the text."""
    target = _make(tmp_path, "troupe.yml", b"\xef\xbb\xbfa: 1\r\n")
    old = "﻿a: 1\r\n"

    write_atomically(_source(target, old), "﻿a: 'é'\r\nb: 2\n")

    assert_file_bytes(target, b"\xef\xbb\xbfa: '\xc3\xa9'\r\nb: 2\n")


@pytest.mark.parametrize("mode", [0o600, 0o640, 0o664, 0o755])
def test_keeps_permission_bits_despite_umask(tmp_path: Path, mode: int) -> None:
    """Req 13.5: the written file keeps the target's permission bits, whatever the umask."""
    target = _make(tmp_path, "troupe.yml", b"a: 1\n", mode)
    previous_umask = os.umask(0o077)
    try:
        write_atomically(_source(target, "a: 1\n"), "a: 2\n")
    finally:
        os.umask(previous_umask)

    assert stat.S_IMODE(target.stat().st_mode) == mode
    assert_file_bytes(target, "a: 2\n")


def test_temp_file_is_hidden_in_target_dir_with_target_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Req 14.3, 13.5: an exclusive hidden temp file next to the real file is renamed over it."""
    target = _make(tmp_path, "troupe.yml", b"a: 1\n", 0o640)
    opened: list[tuple[str, int]] = []
    replaced: list[tuple[str, str, int, bytes]] = []
    real_open = os.open
    real_replace = os.replace

    def spy_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        opened.append((os.fspath(path), flags))
        return real_open(path, flags, *args, **kwargs)

    def spy_replace(src: Any, dst: Any, *args: Any, **kwargs: Any) -> None:
        src_path = Path(os.fspath(src))
        replaced.append(
            (
                os.fspath(src),
                os.fspath(dst),
                stat.S_IMODE(src_path.stat().st_mode),
                src_path.read_bytes(),
            )
        )
        real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "open", spy_open)
    monkeypatch.setattr(os, "replace", spy_replace)

    write_atomically(_source(target, "a: 1\n"), "a: 2\n")

    assert len(opened) == 1
    temp_path, flags = opened[0]
    assert Path(temp_path).parent == tmp_path.resolve()
    assert TEMP_NAME.match(Path(temp_path).name)
    assert flags & os.O_CREAT and flags & os.O_EXCL and flags & os.O_WRONLY
    assert replaced == [(temp_path, os.fspath(target.resolve()), 0o640, b"a: 2\n")]


def test_writes_through_symlink_and_keeps_link(tmp_path: Path) -> None:
    """Req 13.5, 14.4: the link's target is written, the link stays, no extra file anywhere."""
    real_dir = tmp_path / "real"
    link_dir = tmp_path / "links"
    real_dir.mkdir()
    link_dir.mkdir()
    real = _make(real_dir, "troupe.yml", b"a: 1\n", 0o600)
    link = link_dir / "troupe.yml"
    link.symlink_to(real)
    real_listing = _listing(real_dir)
    link_listing = _listing(link_dir)

    write_atomically(_source(link, "a: 1\n"), "a: 2\n")

    assert link.is_symlink()
    assert os.readlink(link) == os.fspath(real)
    assert_file_bytes(real, "a: 2\n")
    assert stat.S_IMODE(real.stat().st_mode) == 0o600
    assert _listing(real_dir) == real_listing
    assert _listing(link_dir) == link_listing


def _fail(*_args: Any, **_kwargs: Any) -> Any:
    raise OSError(5, "Input/output error")


@pytest.mark.parametrize("function", ["replace", "write", "fsync"])
def test_os_failure_leaves_target_and_listing_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, function: str
) -> None:
    """Req 14.3, 14.4, 14.5: an OS failure raises exit 3 naming the file; nothing else changes."""
    target = _make(tmp_path, "troupe.yml", b"a: 1\n")
    _make(tmp_path, "other.yaml", b"x: 1\n")
    before = snapshot_tree(tmp_path)
    listing = _listing(tmp_path)
    displayed = Path("theatre/troupe.yml")
    source = SourceFile(
        path=displayed, real_path=target.resolve(), text="a: 1\n", line_ending=LineEnding.LF
    )
    failing: Callable[..., Any] = _fail
    monkeypatch.setattr(os, function, failing)

    with pytest.raises(WriteRefusedError) as caught:
        write_atomically(source, "a: 2\n")

    monkeypatch.undo()
    assert caught.value.exit_code == ExitCode.WRITE_REFUSED
    assert caught.value.file == displayed
    assert caught.value.path is None
    assert "Input/output error" in caught.value.problem
    assert _listing(tmp_path) == listing
    assert snapshot_tree(tmp_path) == before


def test_unwritable_directory_refuses_and_leaves_no_file(
    tmp_path: Path, unwritable_dir_supported: None
) -> None:
    """Req 14.4, 14.5: a directory that cannot hold the temp file refuses with exit 3."""
    directory = tmp_path / "theatre"
    directory.mkdir()
    target = _make(directory, "troupe.yml", b"a: 1\n")
    before = snapshot_tree(directory)
    directory.chmod(0o555)
    try:
        with pytest.raises(WriteRefusedError) as caught:
            write_atomically(_source(target, "a: 1\n"), "a: 2\n")
    finally:
        directory.chmod(0o755)

    assert caught.value.file == target
    assert caught.value.exit_code == ExitCode.WRITE_REFUSED
    assert snapshot_tree(directory) == before
    assert _listing(directory) == ["troupe.yml"]


def test_unencodable_text_refuses_and_leaves_no_file(tmp_path: Path) -> None:
    """Req 13.3, 14.4: text that is not encodable as UTF-8 is refused before any file is made."""
    target = _make(tmp_path, "troupe.yml", b"a: 1\n")
    before = snapshot_tree(tmp_path)

    with pytest.raises(WriteRefusedError) as caught:
        write_atomically(_source(target, "a: 1\n"), "a: '\ud800'\n")

    assert caught.value.file == target
    assert snapshot_tree(tmp_path) == before


def test_temp_name_collision_is_retried_never_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Req 13.4, 14.4: an existing file with the temp name is never touched by the write."""
    target = _make(tmp_path, "troupe.yml", b"a: 1\n")
    stranger = _make(tmp_path, ".troupe.yml.troupe-tmp-" + "0" * 16, b"keep me\n")
    tokens = iter(["0" * 16, "1" * 16])
    monkeypatch.setattr(secrets, "token_hex", lambda _n: next(tokens))

    write_atomically(_source(target, "a: 1\n"), "a: 2\n")

    assert_file_bytes(target, "a: 2\n")
    assert_file_bytes(stranger, "keep me\n")
    assert _listing(tmp_path) == sorted([".troupe.yml.troupe-tmp-" + "0" * 16, "troupe.yml"])


def test_restore_file_writes_original_text_back(tmp_path: Path) -> None:
    """Req 14.5, 15.6: restore puts the file's original bytes back through the same path."""
    target = _make(tmp_path, "troupe.yml", b"\xef\xbb\xbfa: 1\r\n", 0o640)
    source = _source(target, "﻿a: 1\r\n")
    before = snapshot_tree(tmp_path)
    write_atomically(source, "﻿a: 2\r\n")

    restore_file(source)

    assert snapshot_tree(tmp_path) == before
    assert stat.S_IMODE(target.stat().st_mode) == 0o640


def test_restore_file_through_symlink(tmp_path: Path) -> None:
    """Req 13.5, 14.5: restore writes the link's target and keeps the link."""
    real = _make(tmp_path, "real.yml", b"a: 1\n")
    link = tmp_path / "troupe.yml"
    link.symlink_to(real)
    source = _source(link, "a: 1\n")
    write_atomically(source, "a: 2\n")

    restore_file(source)

    assert link.is_symlink()
    assert_file_bytes(real, "a: 1\n")
    assert _listing(tmp_path) == ["real.yml", "troupe.yml"]


def test_restore_failure_raises_write_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Req 14.5, 14.10: a failing restore raises a write-refused error naming the file."""
    target = _make(tmp_path, "troupe.yml", b"a: 2\n")
    source = _source(target, "a: 1\n")
    monkeypatch.setattr(os, "replace", _fail)

    with pytest.raises(WriteRefusedError) as caught:
        restore_file(source)

    monkeypatch.undo()
    assert caught.value.file == target
    assert_file_bytes(target, "a: 2\n")
    assert _listing(tmp_path) == ["troupe.yml"]
