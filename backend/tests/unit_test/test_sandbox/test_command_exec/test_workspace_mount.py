"""Workspace replacement mount behavior tests."""

from __future__ import annotations

from pathlib import Path

import pytest

import sandbox.command_exec.workspace.mount as workspace_mount
from sandbox.command_exec.workspace.capture import capture_workspace_upperdir
from sandbox.command_exec.contract.request import CommandExecRequest
from sandbox.command_exec.workspace.mount import WorkspaceReplacementMountSpec
from sandbox.layer_stack.manifest import Manifest


def test_copy_backed_mount_captures_only_workspace_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        workspace_mount,
        "_private_mount_namespace_available",
        lambda: False,
    )
    lower = tmp_path / "lower"
    lower.mkdir()
    (lower / "input.txt").write_text("base\n", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    spec = WorkspaceReplacementMountSpec(
        workspace_root="/testbed",
        lowerdir=str(lower),
        upperdir=str(tmp_path / "upper"),
        workdir=str(tmp_path / "work"),
        scratch_root=str(tmp_path),
    )
    request = CommandExecRequest(
        request_id="req-1",
        workspace_ref=str(tmp_path / "stack"),
        workspace_root="/testbed",
        command=(
            "bash",
            "-lc",
            (
                "cat input.txt; "
                "mkdir -p generated; "
                "printf changed > generated/output.txt; "
                f"printf outside > {outside}"
            ),
        ),
    )
    timings: dict[str, float] = {}

    process = workspace_mount.run_workspace_replaced_command(
        spec=spec,
        request=request,
        run_dir=tmp_path / "run",
        timings=timings,
    )
    changes = capture_workspace_upperdir(
        spec=spec,
        snapshot_manifest=Manifest(version=1, layers=()),
        mounted_workspace_root=process.mounted_workspace_root,
        copy_backed=process.mount_mode == "copy_backed",
        timings=timings,
    )

    assert process.exit_code == 0
    assert Path(process.stdout_ref).read_text(encoding="utf-8") == "base\n"
    assert [change.path for change in changes] == ["generated/output.txt"]
    assert outside.read_text(encoding="utf-8") == "outside"
    assert "command_exec.mount_workspace_s" in timings
    assert "command_exec.run_command_s" in timings


def test_copy_backed_mount_rewrites_absolute_workspace_references(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        workspace_mount,
        "_private_mount_namespace_available",
        lambda: False,
    )
    lower = tmp_path / "lower"
    lower.mkdir()
    spec = WorkspaceReplacementMountSpec(
        workspace_root="/testbed",
        lowerdir=str(lower),
        upperdir=str(tmp_path / "upper"),
        workdir=str(tmp_path / "work"),
        scratch_root=str(tmp_path),
    )
    request = CommandExecRequest(
        request_id="req-1",
        workspace_ref=str(tmp_path / "stack"),
        workspace_root="/testbed",
        command=("bash", "-lc", "printf captured > /testbed/out.txt"),
    )
    timings: dict[str, float] = {}

    process = workspace_mount.run_workspace_replaced_command(
        spec=spec,
        request=request,
        run_dir=tmp_path / "run",
        timings=timings,
    )
    changes = capture_workspace_upperdir(
        spec=spec,
        snapshot_manifest=Manifest(version=1, layers=()),
        mounted_workspace_root=process.mounted_workspace_root,
        copy_backed=process.mount_mode == "copy_backed",
        timings=timings,
    )

    assert process.exit_code == 0
    assert (
        Path(process.mounted_workspace_root) / "out.txt"
    ).read_text(encoding="utf-8") == "captured"
    assert [change.path for change in changes] == ["out.txt"]


def test_copy_backed_mount_rewrites_workspace_env_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        workspace_mount,
        "_private_mount_namespace_available",
        lambda: False,
    )
    lower = tmp_path / "lower"
    lower.mkdir()
    spec = WorkspaceReplacementMountSpec(
        workspace_root="/testbed",
        lowerdir=str(lower),
        upperdir=str(tmp_path / "upper"),
        workdir=str(tmp_path / "work"),
        scratch_root=str(tmp_path),
    )
    request = CommandExecRequest(
        request_id="req-1",
        workspace_ref=str(tmp_path / "stack"),
        workspace_root="/testbed",
        command=("bash", "-lc", "printf env > \"$WORKSPACE_DIR/env.txt\""),
        env={"WORKSPACE_DIR": "/testbed"},
    )

    process = workspace_mount.run_workspace_replaced_command(
        spec=spec,
        request=request,
        run_dir=tmp_path / "run",
        timings={},
    )

    assert process.exit_code == 0
    assert (
        Path(process.mounted_workspace_root) / "env.txt"
    ).read_text(encoding="utf-8") == "env"


def test_workspace_rewrite_preserves_quoted_literals() -> None:
    rewritten = workspace_mount._rewrite_declared_workspace_refs(
        ("bash", "-lc", "printf '%s' '/testbed docs'; cat /testbed/file.txt"),
        workspace_root="/testbed",
        mounted_workspace_root="/tmp/run/workspace",
    )

    assert rewritten[-1] == (
        "printf '%s' '/testbed docs'; cat /tmp/run/workspace/file.txt"
    )


def test_mount_spec_rejects_paths_outside_scratch_root(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="upperdir must be under scratch_root"):
        WorkspaceReplacementMountSpec(
            workspace_root="/testbed",
            lowerdir=str(tmp_path / "lower"),
            upperdir="/tmp/not-owned",
            workdir=str(tmp_path / "work"),
            scratch_root=str(tmp_path),
        )
