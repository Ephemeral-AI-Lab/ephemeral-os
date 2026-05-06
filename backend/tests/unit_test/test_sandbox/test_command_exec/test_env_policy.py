"""cwd policy tests for command-exec workspace replacement."""

from __future__ import annotations

from pathlib import Path

import pytest

from sandbox.command_exec.env import resolve_workspace_cwd


def test_relative_cwd_resolves_inside_mounted_workspace(tmp_path: Path) -> None:
    mounted = tmp_path / "mounted"

    resolved = resolve_workspace_cwd(
        declared_workspace_root="/testbed",
        mounted_workspace_root=mounted,
        cwd="pkg",
    )

    assert resolved == mounted / "pkg"
    assert resolved.is_dir()


def test_absolute_workspace_cwd_is_remapped_to_mount(tmp_path: Path) -> None:
    mounted = tmp_path / "mounted"

    resolved = resolve_workspace_cwd(
        declared_workspace_root="/testbed",
        mounted_workspace_root=mounted,
        cwd="/testbed/pkg",
    )

    assert resolved == mounted / "pkg"
    assert resolved.is_dir()


def test_absolute_cwd_outside_workspace_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="escapes workspace"):
        resolve_workspace_cwd(
            declared_workspace_root="/testbed",
            mounted_workspace_root=tmp_path / "mounted",
            cwd="/tmp",
        )
