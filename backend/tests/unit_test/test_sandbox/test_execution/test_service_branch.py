"""Unit tests for namespace-only command execution."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from sandbox.ephemeral_workspace._execute_command import execute_command
from sandbox.ephemeral_workspace.shell_contract import (
    CommandExecRequest,
    EmptyChangesetResult,
    ShellProcessResult,
    WorkspaceCapturePublishResult,
)
from sandbox.overlay.layout import LayerPathsLayout


def _make_request(request_id: str = "req-001") -> CommandExecRequest:
    return CommandExecRequest(
        request_id=request_id,
        workspace_ref="ws-ref",
        workspace_root="/workspace",
        command=("echo", "hi"),
    )


def _make_lease(*, layer_paths: tuple[str, ...]) -> MagicMock:
    lease = MagicMock()
    lease.lease_id = "lease-001"
    lease.manifest_version = 1
    lease.manifest = MagicMock()
    lease.manifest.version = 1
    lease.lowerdir = None
    lease.layer_paths = layer_paths
    lease.timings = {}
    return lease


def _make_layer_stack(lease: MagicMock, storage_root: Path) -> MagicMock:
    layer_stack = MagicMock()
    layer_stack.storage_root = storage_root
    layer_stack.prepare_workspace_snapshot.return_value = lease
    layer_stack.release_lease.return_value = True
    return layer_stack


def _make_capture_publisher() -> AsyncMock:
    publisher = AsyncMock()
    publisher.publish_cycle = AsyncMock(
        return_value=WorkspaceCapturePublishResult(
            path_changes=(),
            changeset=EmptyChangesetResult(),
            timings={
                "command_exec.capture_upperdir_s": 0.0,
                "command_exec.occ_apply_s": 0.0,
            },
        )
    )
    publisher.run_maintenance_after_publish = AsyncMock(return_value={})
    return publisher


def _make_process_result(spec_holder: list[Any]) -> Any:
    def runner(*, spec: Any, request: Any, run_dir: Any, timings: Any, **kwargs: Any):
        del request, timings, kwargs
        spec_holder.append(spec)
        stdout_ref = run_dir / "stdout"
        stderr_ref = run_dir / "stderr"
        stdout_ref.parent.mkdir(parents=True, exist_ok=True)
        stdout_ref.write_bytes(b"")
        stderr_ref.write_bytes(b"")
        return ShellProcessResult(
            exit_code=0,
            stdout_ref=str(stdout_ref),
            stderr_ref=str(stderr_ref),
            mounted_workspace_root=spec.workspace_root,
            mount_mode="private_namespace",
        )

    return runner


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_execute_command_prepares_layer_paths_snapshot(
    tmp_path: Path,
) -> None:
    layer_storage_root = tmp_path / "layers"
    layer_path = layer_storage_root / "L0001"
    layer_path.mkdir(parents=True)
    lease = _make_lease(layer_paths=(str(layer_path),))
    layer_stack = _make_layer_stack(lease, layer_storage_root)
    publisher = _make_capture_publisher()
    spec_holder: list[Any] = []

    _run(
        execute_command(
            _make_request(),
            layer_stack=layer_stack,
            capture_publisher=publisher,
            storage_root=tmp_path / "storage",
            command_runner=_make_process_result(spec_holder),
        )
    )

    layer_stack.prepare_workspace_snapshot.assert_called_once_with(
        request_id="req-001",
    )
    assert len(spec_holder) == 1
    assert isinstance(spec_holder[0], LayerPathsLayout)
    assert spec_holder[0].layer_paths == (str(layer_path),)


def test_execute_command_rejects_snapshot_without_layer_paths(tmp_path: Path) -> None:
    lease = _make_lease(layer_paths=())
    lease.layer_paths = None
    layer_stack = _make_layer_stack(lease, tmp_path / "layers")

    try:
        _run(
            execute_command(
                _make_request(),
                layer_stack=layer_stack,
                capture_publisher=_make_capture_publisher(),
                storage_root=tmp_path / "storage",
                command_runner=_make_process_result([]),
            )
        )
    except RuntimeError as exc:
        assert "layer paths" in str(exc)
    else:  # pragma: no cover - assertion clarity
        raise AssertionError("execute_command accepted a snapshot without layer paths")
