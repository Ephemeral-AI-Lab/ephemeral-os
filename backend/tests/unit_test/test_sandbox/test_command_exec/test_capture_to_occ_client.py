"""Transient lowerdir cleanup guards used by overlay-backed command paths."""

from __future__ import annotations

from pathlib import Path

from sandbox.ephemeral_workspace.pipeline import _drop_transient_lowerdir
from sandbox.layer_stack.paths import TRANSIENT_LOWERDIR_DIR


def test_drop_transient_lowerdir_removes_matching_path_under_storage_root(
    tmp_path: Path,
) -> None:
    storage_root = tmp_path / "stack"
    lower_parent = storage_root / "runtime" / TRANSIENT_LOWERDIR_DIR / "req-1"
    lower = lower_parent / "lower"
    lower.mkdir(parents=True)

    _drop_transient_lowerdir(
        lower.as_posix(),
        storage_root=storage_root,
        scratch_root=tmp_path / "scratch",
    )

    assert lower_parent.exists() is False


def test_drop_transient_lowerdir_removes_matching_path_under_scratch_root(
    tmp_path: Path,
) -> None:
    scratch_root = tmp_path / "scratch"
    lower_parent = scratch_root / "runtime" / TRANSIENT_LOWERDIR_DIR / "req-1"
    lower = lower_parent / "lower"
    lower.mkdir(parents=True)

    _drop_transient_lowerdir(
        lower.as_posix(),
        storage_root=tmp_path / "stack",
        scratch_root=scratch_root,
    )

    assert lower_parent.exists() is False


def test_drop_transient_lowerdir_refuses_matching_path_outside_owned_roots(
    tmp_path: Path,
) -> None:
    storage_root = tmp_path / "stack"
    scratch_root = tmp_path / "scratch"
    outside_root = tmp_path / "outside"
    lower = outside_root / "runtime" / TRANSIENT_LOWERDIR_DIR / "req-1" / "lower"
    lower.mkdir(parents=True)

    _drop_transient_lowerdir(
        lower.as_posix(),
        storage_root=storage_root,
        scratch_root=scratch_root,
    )

    assert lower.exists()
