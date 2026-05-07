"""Workspace replacement mount behavior tests."""

from __future__ import annotations

from pathlib import Path

import pytest

import sandbox.command_exec.workspace_mount as workspace_mount
from sandbox.command_exec.capture.upperdir import capture_workspace_upperdir
from sandbox.command_exec.request import CommandExecRequest
from sandbox.command_exec.workspace_mount import WorkspaceReplacementMountSpec
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
        manifest_version=1,
        lease_id="lease-1",
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
        manifest_version=1,
        lease_id="lease-1",
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
