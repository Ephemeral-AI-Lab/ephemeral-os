"""No-follow file operation guards for shared tool primitives."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from sandbox._shared.tool_primitives import file_ops


def test_open_no_follow_rejects_intermediate_symlink(tmp_path: Path) -> None:
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    (target_dir / "secret.txt").write_text("secret", encoding="utf-8")
    link = tmp_path / "link"
    link.symlink_to(target_dir, target_is_directory=True)

    with pytest.raises(ValueError, match="refusing to follow symlink"):
        file_ops.read_bytes_no_follow(link / "secret.txt")


def test_open_no_follow_uses_openat2_when_available(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target.txt"
    target.write_text("ok", encoding="utf-8")
    calls: list[tuple[str, int, int]] = []

    def fake_openat2(path: str, flags: int, mode: int) -> int:
        calls.append((path, flags, mode))
        return os.open(path, flags)

    monkeypatch.setattr(file_ops, "_openat2_no_symlinks", fake_openat2)

    assert file_ops.read_bytes_no_follow(target) == b"ok"
    assert calls == [(str(target), os.O_RDONLY, 0o666)]
