"""Namespace overlay mount behavior tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from sandbox.ephemeral_workspace.shell_contract import CommandExecRequest
from sandbox.overlay import kernel_mount
from sandbox.overlay.layout import LayerPathsLayout
from sandbox.overlay.namespace import NAMESPACE_CONTROL_REF, NAMESPACE_INFRA_EXIT_CODE


def test_layer_paths_mount_spec_rejects_paths_outside_scratch_root(
    tmp_path: Path,
) -> None:
    layer_root = tmp_path / "layers"
    layer = layer_root / "L1"
    layer.mkdir(parents=True)
    with pytest.raises(
        ValueError,
        match="writes must be strictly under scratch_root",
    ):
        LayerPathsLayout(
            workspace_root="/testbed",
            layer_paths=(str(layer),),
            layer_storage_root=str(layer_root),
            writes="/tmp/not-owned",
            kernel_scratch=str(tmp_path / "work"),
            scratch_root=str(tmp_path),
        )


def test_layer_paths_mount_spec_rejects_layer_outside_storage_root(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="must be under layer_storage_root"):
        LayerPathsLayout(
            workspace_root="/testbed",
            layer_paths=("/etc/passwd",),
            layer_storage_root=str(tmp_path / "layers"),
            writes=str(tmp_path / "upper"),
            kernel_scratch=str(tmp_path / "work"),
            scratch_root=str(tmp_path),
        )


def test_namespace_control_constants_remain_stable() -> None:
    assert NAMESPACE_CONTROL_REF == "namespace-control.json"
    assert NAMESPACE_INFRA_EXIT_CODE == 125


def test_command_request_is_namespace_only() -> None:
    request = CommandExecRequest(
        invocation_id="req-1",
        workspace_ref="/tmp/stack",
        workspace_root="/testbed",
        command=("bash", "-lc", "printf ok"),
    )
    assert request.workspace_root == "/testbed"
    assert request.command == ("bash", "-lc", "printf ok")


def test_namespace_mount_validation_keeps_real_mountpoint_and_fd_backed_sources(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    layer1 = tmp_path / "layer1"
    layer2 = tmp_path / "layer2"
    upperdir = tmp_path / "upper"
    workdir = tmp_path / "work"
    workspace_root.mkdir()
    layer1.mkdir()
    layer2.mkdir()

    inputs = kernel_mount.validate_mount_inputs(
        workspace_root=workspace_root,
        layer_paths=(layer1, layer2),
        upperdir=upperdir,
        workdir=workdir,
    )
    try:
        assert inputs.workspace_root == workspace_root
        assert len(inputs.layer_paths) == 2
        assert all(p.as_posix().startswith("/proc/self/fd/") for p in inputs.layer_paths)
        assert inputs.upperdir.as_posix().startswith("/proc/self/fd/")
        assert inputs.workdir.as_posix().startswith("/proc/self/fd/")
    finally:
        inputs.close()
