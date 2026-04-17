"""Unit tests for the upperdir tar → structured-change walker."""

from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from code_intelligence.routing.upperdir_walker import (
    ChangeKind,
    collect_upperdir_changes,
)


def _add_regular(tf: tarfile.TarFile, path: str, content: bytes, mode: int = 0o644) -> None:
    info = tarfile.TarInfo(name=path)
    info.size = len(content)
    info.mode = mode
    info.type = tarfile.REGTYPE
    tf.addfile(info, io.BytesIO(content))


def _add_whiteout(tf: tarfile.TarFile, path: str) -> None:
    info = tarfile.TarInfo(name=path)
    info.type = tarfile.CHRTYPE
    info.devmajor = 0
    info.devminor = 0
    info.mode = 0o000
    tf.addfile(info)


def _add_opaque_dir(tf: tarfile.TarFile, path: str, mode: int = 0o755) -> None:
    info = tarfile.TarInfo(name=path)
    info.type = tarfile.DIRTYPE
    info.mode = mode
    info.pax_headers = {"SCHILY.xattr.user.overlay.opaque": "y"}
    tf.addfile(info)


def _add_symlink(tf: tarfile.TarFile, path: str, target: str) -> None:
    info = tarfile.TarInfo(name=path)
    info.type = tarfile.SYMTYPE
    info.linkname = target
    info.mode = 0o777
    tf.addfile(info)


def _build_tar(tmp_path: Path, name: str = "audit.tar") -> Path:
    return tmp_path / name


def test_regular_file_emits_modify(tmp_path: Path) -> None:
    tar_path = _build_tar(tmp_path)
    with tarfile.open(tar_path, mode="w", format=tarfile.PAX_FORMAT) as tf:
        _add_regular(tf, "./pkg/new.py", b"x = 1\n")
    changes = collect_upperdir_changes(str(tar_path))
    assert len(changes) == 1
    c = changes[0]
    assert c.kind is ChangeKind.MODIFY
    assert c.path == "pkg/new.py"
    assert c.content == b"x = 1\n"
    assert c.mode == 0o644


def test_whiteout_emits_delete(tmp_path: Path) -> None:
    tar_path = _build_tar(tmp_path)
    with tarfile.open(tar_path, mode="w", format=tarfile.PAX_FORMAT) as tf:
        _add_whiteout(tf, "./pkg/removed.py")
    changes = collect_upperdir_changes(str(tar_path))
    assert len(changes) == 1
    assert changes[0].kind is ChangeKind.DELETE
    assert changes[0].path == "pkg/removed.py"


def test_opaque_dir_emits_opaque_dir(tmp_path: Path) -> None:
    tar_path = _build_tar(tmp_path)
    with tarfile.open(tar_path, mode="w", format=tarfile.PAX_FORMAT) as tf:
        _add_opaque_dir(tf, "./pkg/opaque")
    changes = collect_upperdir_changes(str(tar_path))
    assert len(changes) == 1
    assert changes[0].kind is ChangeKind.OPAQUE_DIR
    assert changes[0].path == "pkg/opaque"


def test_plain_dir_emits_nothing(tmp_path: Path) -> None:
    tar_path = _build_tar(tmp_path)
    with tarfile.open(tar_path, mode="w", format=tarfile.PAX_FORMAT) as tf:
        info = tarfile.TarInfo(name="./pkg")
        info.type = tarfile.DIRTYPE
        info.mode = 0o755
        tf.addfile(info)
    changes = collect_upperdir_changes(str(tar_path))
    assert changes == []


def test_symlink_emits_symlink_change(tmp_path: Path) -> None:
    tar_path = _build_tar(tmp_path)
    with tarfile.open(tar_path, mode="w", format=tarfile.PAX_FORMAT) as tf:
        _add_symlink(tf, "./pkg/link", "../target.py")
    changes = collect_upperdir_changes(str(tar_path))
    assert len(changes) == 1
    assert changes[0].kind is ChangeKind.SYMLINK
    assert changes[0].path == "pkg/link"
    assert changes[0].symlink_target == "../target.py"


def test_git_directory_is_ignored(tmp_path: Path) -> None:
    tar_path = _build_tar(tmp_path)
    with tarfile.open(tar_path, mode="w", format=tarfile.PAX_FORMAT) as tf:
        _add_regular(tf, "./.git/HEAD", b"ref: refs/heads/main\n")
        _add_regular(tf, "./pkg/real.py", b"y = 2\n")
    changes = collect_upperdir_changes(str(tar_path))
    assert [c.path for c in changes] == ["pkg/real.py"]


def test_root_dot_entry_is_skipped(tmp_path: Path) -> None:
    tar_path = _build_tar(tmp_path)
    with tarfile.open(tar_path, mode="w", format=tarfile.PAX_FORMAT) as tf:
        info = tarfile.TarInfo(name=".")
        info.type = tarfile.DIRTYPE
        info.mode = 0o755
        tf.addfile(info)
        _add_regular(tf, "./a.py", b"")
    changes = collect_upperdir_changes(str(tar_path))
    assert [c.path for c in changes] == ["a.py"]


def test_mixed_bag_preserves_order(tmp_path: Path) -> None:
    tar_path = _build_tar(tmp_path)
    with tarfile.open(tar_path, mode="w", format=tarfile.PAX_FORMAT) as tf:
        _add_regular(tf, "./a.py", b"a=1")
        _add_whiteout(tf, "./b.py")
        _add_opaque_dir(tf, "./c")
        _add_symlink(tf, "./d", "a.py")
    changes = collect_upperdir_changes(str(tar_path))
    kinds = [c.kind for c in changes]
    assert kinds == [
        ChangeKind.MODIFY,
        ChangeKind.DELETE,
        ChangeKind.OPAQUE_DIR,
        ChangeKind.SYMLINK,
    ]


def test_custom_ignore_prefix(tmp_path: Path) -> None:
    tar_path = _build_tar(tmp_path)
    with tarfile.open(tar_path, mode="w", format=tarfile.PAX_FORMAT) as tf:
        _add_regular(tf, "./node_modules/x.js", b"")
        _add_regular(tf, "./src/main.py", b"")
    changes = collect_upperdir_changes(
        str(tar_path),
        ignore_prefixes=("node_modules/",),
    )
    assert [c.path for c in changes] == ["src/main.py"]
