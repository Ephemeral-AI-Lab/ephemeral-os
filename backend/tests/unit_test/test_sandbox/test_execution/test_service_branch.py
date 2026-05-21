"""Unit tests for execute_command capability branching (T5).

Verifies that:
- new_mount_api_supported()=True  → prepare_workspace_snapshot(materialize=False)
                                    → LayerPathsLayout spec built
- new_mount_api_supported()=False → prepare_workspace_snapshot(materialize=True)
                                    → MaterializeLayout (OverlayLayout) spec built
- kill switch EOS_OVERLAY_FORCE_MATERIALIZE=1 forces materialize path
- _drop_transient_lowerdir is a no-op when lowerdir=None
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sandbox.execution.contract import (
    CommandExecRequest,
    EmptyChangesetResult,
    LayerPathsLayout,
    WorkspaceCapturePublishResult,
)
from sandbox.execution.overlay.layout import MaterializeLayout
from sandbox.execution.service import execute_command, _drop_transient_lowerdir


def _make_request(request_id: str = "req-001") -> CommandExecRequest:
    return CommandExecRequest(
        request_id=request_id,
        workspace_ref="ws-ref",
        workspace_root="/workspace",
        command=("echo", "hi"),
    )


def _make_lease(
    *,
    lowerdir: str | None = "/tmp/lower",
    layer_paths: tuple[str, ...] | None = None,
) -> MagicMock:
    lease = MagicMock()
    lease.lease_id = "lease-001"
    lease.manifest_version = 1
    lease.manifest = MagicMock()
    lease.manifest.version = 1
    lease.lowerdir = lowerdir
    lease.layer_paths = layer_paths
    lease.timings = {}
    return lease


def _make_layer_stack(lease: MagicMock, storage_root: Path) -> MagicMock:
    ls = MagicMock()
    ls.storage_root = storage_root
    ls.prepare_workspace_snapshot.return_value = lease
    ls.release_lease.return_value = True
    return ls


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
    """Returns a sync callable that captures the spec and returns a fake process."""
    def runner(*, spec: Any, request: Any, run_dir: Any, timings: Any, **kwargs: Any):
        spec_holder.append(spec)
        result = MagicMock()
        result.exit_code = 0
        result.stdout_ref = str(run_dir / "stdout")
        result.stderr_ref = str(run_dir / "stderr")
        result.mount_mode = "private_namespace"
        Path(result.stdout_ref).parent.mkdir(parents=True, exist_ok=True)
        Path(result.stdout_ref).write_bytes(b"")
        Path(result.stderr_ref).write_bytes(b"")
        return result
    return runner


@pytest.fixture()
def storage_root(tmp_path: Path) -> Path:
    root = tmp_path / "storage"
    root.mkdir()
    return root


@pytest.fixture()
def layer_storage_root(tmp_path: Path) -> Path:
    # Layer paths must be under this root
    ls_root = tmp_path / "layers"
    ls_root.mkdir()
    return ls_root


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


class TestCapabilityBranch:
    def test_namespace_path_when_supported(
        self, storage_root: Path, layer_storage_root: Path, tmp_path: Path
    ) -> None:
        layer_path = layer_storage_root / "L0001"
        layer_path.mkdir()
        lease = _make_lease(lowerdir=None, layer_paths=(str(layer_path),))
        ls = _make_layer_stack(lease, layer_storage_root)
        publisher = _make_capture_publisher()
        spec_holder: list[Any] = []

        with patch(
            "sandbox.execution.service.new_mount_api_supported", return_value=True
        ), patch(
            "sandbox.execution.service.walk_upperdir", return_value=[]
        ):
            _run(
                execute_command(
                    _make_request(),
                    layer_stack=ls,
                    capture_publisher=publisher,
                    storage_root=storage_root,
                    command_runner=_make_process_result(spec_holder),
                )
            )

        ls.prepare_workspace_snapshot.assert_called_once()
        call_kwargs = ls.prepare_workspace_snapshot.call_args.kwargs
        assert call_kwargs["materialize"] is False
        assert len(spec_holder) == 1
        assert isinstance(spec_holder[0], LayerPathsLayout)

    def test_materialize_path_when_not_supported(
        self, storage_root: Path, layer_storage_root: Path, tmp_path: Path
    ) -> None:
        scratch = storage_root / "runtime" / "transient_lowerdir" / "req-001" / "lower"
        scratch.mkdir(parents=True)
        lease = _make_lease(lowerdir=str(scratch), layer_paths=None)
        ls = _make_layer_stack(lease, layer_storage_root)
        publisher = _make_capture_publisher()
        spec_holder: list[Any] = []

        with patch(
            "sandbox.execution.service.new_mount_api_supported", return_value=False
        ), patch(
            "sandbox.execution.service.walk_upperdir", return_value=[]
        ):
            _run(
                execute_command(
                    _make_request(),
                    layer_stack=ls,
                    capture_publisher=publisher,
                    storage_root=storage_root,
                    command_runner=_make_process_result(spec_holder),
                )
            )

        call_kwargs = ls.prepare_workspace_snapshot.call_args.kwargs
        assert call_kwargs["materialize"] is True
        assert len(spec_holder) == 1
        assert isinstance(spec_holder[0], MaterializeLayout)

    def test_materialize_path_when_layer_paths_none_despite_flag(
        self, storage_root: Path, layer_storage_root: Path
    ) -> None:
        """If use_namespace=True but lease.layer_paths is None, fall back to materialize."""
        scratch = storage_root / "runtime" / "transient_lowerdir" / "req-001" / "lower"
        scratch.mkdir(parents=True)
        lease = _make_lease(lowerdir=str(scratch), layer_paths=None)
        ls = _make_layer_stack(lease, layer_storage_root)
        publisher = _make_capture_publisher()
        spec_holder: list[Any] = []

        with patch(
            "sandbox.execution.service.new_mount_api_supported", return_value=True
        ), patch(
            "sandbox.execution.service.walk_upperdir", return_value=[]
        ):
            _run(
                execute_command(
                    _make_request(),
                    layer_stack=ls,
                    capture_publisher=publisher,
                    storage_root=storage_root,
                    command_runner=_make_process_result(spec_holder),
                )
            )

        assert isinstance(spec_holder[0], MaterializeLayout)


class TestDropTransientLowerdirNoneGuard:
    def test_no_op_when_lowerdir_is_none(self, storage_root: Path) -> None:
        lease = MagicMock()
        lease.lowerdir = None
        # Should not raise or log warning
        _drop_transient_lowerdir(lease, storage_root=storage_root)

    def test_no_op_when_lowerdir_missing(self, storage_root: Path) -> None:
        lease = MagicMock(spec=[])  # no lowerdir attribute
        _drop_transient_lowerdir(lease, storage_root=storage_root)


class TestKillSwitch:
    def test_kill_switch_disables_namespace(
        self, storage_root: Path, layer_storage_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("EOS_OVERLAY_FORCE_MATERIALIZE", "1")
        # Re-import so the env var is picked up (probe_supported is cached, but
        # new_mount_api_supported reads the env var on each call)
        from sandbox.execution.overlay.capability import new_mount_api_supported
        assert new_mount_api_supported() is False
