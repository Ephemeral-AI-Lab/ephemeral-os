"""Unit contracts for overlay writable directory discovery."""

from __future__ import annotations

from pathlib import Path

import pytest

import sandbox.overlay.writable_dirs as writable_dirs_mod


def test_overlay_writable_root_creates_canonical_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tmpfs_parent = tmp_path / "eos-mount-scratch"
    tmpfs_parent.mkdir()
    root = tmpfs_parent / "eos-sandbox-runtime"
    monkeypatch.setattr(writable_dirs_mod, "OVERLAY_WRITABLE_ROOT", root)

    assert writable_dirs_mod.overlay_writable_root() == root
    assert root.is_dir()


def test_overlay_writable_root_fails_when_parent_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "missing-parent" / "eos-sandbox-runtime"
    monkeypatch.setattr(writable_dirs_mod, "OVERLAY_WRITABLE_ROOT", root)

    with pytest.raises(writable_dirs_mod.OverlayWritableRootUnavailable):
        writable_dirs_mod.overlay_writable_root()

    assert not root.parent.exists()
