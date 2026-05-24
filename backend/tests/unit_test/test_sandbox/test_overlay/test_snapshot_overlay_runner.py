"""Snapshot-overlay tests for the unified command orchestrator path."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from sandbox.layer_stack import WriteLayerChange, LayerStack
from sandbox.daemon.service.layer_stack_client import LayerStackClient
from sandbox.ephemeral_workspace.shell_contract import (
    CommandExecRequest,
    OverlayCapture,
    ShellProcessResult,
)
from sandbox.ephemeral_workspace._execute_command import execute_command
from sandbox.daemon.rpc.dispatcher import dispatch_envelope_async
from sandbox.overlay.layout import LayerPathsLayout


def _source(tmp_path: Path, name: str, content: bytes) -> str:
    path = tmp_path / "sources" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return str(path)


def _request(
    manager: LayerStack,
    *,
    command: tuple[str, ...],
    request_id: str = "request-a",
) -> CommandExecRequest:
    return CommandExecRequest(
        request_id=request_id,
        workspace_ref=manager.storage_root.as_posix(),
        workspace_root="/workspace",
        command=command,
        cwd=".",
        env={},
        timeout_seconds=5,
    )


def test_overlay_capture_timings_are_immutable() -> None:
    capture = OverlayCapture(
        exit_code=0,
        stdout_ref="/tmp/stdout",
        stderr_ref="/tmp/stderr",
        snapshot_version=1,
        changes=(),
        timings={"phase": 1.0},
    )

    with pytest.raises(TypeError):
        capture.timings["phase"] = 2.0


@pytest.mark.asyncio
async def test_orchestrator_overlay_executes_against_leased_manifest_without_publish(
    tmp_path: Path,
) -> None:
    manager = LayerStack(tmp_path / "stack")
    manager.publish_changes(
        [
            WriteLayerChange(
                path="pkg/value.txt",
                source_path=_source(tmp_path, "value.txt", b"old\n"),
            )
        ]
    )
    request = _request(
        manager,
        command=(
            "bash",
            "-lc",
            "printf 'new\\n' > pkg/value.txt; printf out; printf err >&2",
        ),
    )

    result = await execute_command(
        request,
        layer_stack=LayerStackClient(manager),
        capture_publisher=None,
        storage_root=manager.storage_root,
        occ_apply=False,
        command_runner=_write_value_runner,
    )

    assert result.exit_code == 0
    assert result.workspace_capture.snapshot_version == 1
    assert result.stdout == "out"
    assert result.stderr == "err"
    assert Path(result.stdout_ref).read_text(encoding="utf-8") == "out"
    assert Path(result.stderr_ref).read_text(encoding="utf-8") == "err"
    assert manager.read_text("pkg/value.txt") == ("old\n", True)
    assert manager.pinned_layers() == ()
    assert result.occ_result.files == ()

    assert len(result.workspace_capture.changes) == 1
    change = result.workspace_capture.changes[0]
    assert change.path == "pkg/value.txt"
    assert change.kind == "write"
    assert change.content_path is not None
    assert Path(change.content_path).read_bytes() == b"new\n"
    assert change.final_hash == hashlib.sha256(b"new\n").hexdigest()


@pytest.mark.asyncio
async def test_orchestrator_overlay_releases_lease_when_runtime_fails(
    tmp_path: Path,
) -> None:
    manager = LayerStack(tmp_path / "stack")
    manager.publish_changes(
        [
            WriteLayerChange(
                path="pkg/value.txt",
                source_path=_source(tmp_path, "value.txt", b"old\n"),
            )
        ]
    )

    def failing_runner(
        *,
        spec: LayerPathsLayout,
        request: CommandExecRequest,
        run_dir: str | Path,
        timings: dict[str, float],
    ) -> object:
        del spec, request, run_dir, timings
        raise RuntimeError("runtime failed")

    with pytest.raises(RuntimeError, match="runtime failed"):
        await execute_command(
            _request(manager, command=("bash", "-lc", "true")),
            layer_stack=LayerStackClient(manager),
            capture_publisher=None,
            storage_root=manager.storage_root,
            occ_apply=False,
            command_runner=failing_runner,
        )

    assert manager.pinned_layers() == ()


@pytest.mark.asyncio
async def test_overlay_run_handler_supports_layer_stack_snapshot_requests(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = LayerStack(tmp_path / "stack")
    manager.publish_changes(
        [
            WriteLayerChange(
                path="value.txt",
                source_path=_source(tmp_path, "value.txt", b"old\n"),
            )
        ]
    )

    monkeypatch.setattr(
        "sandbox.daemon.handler.overlay._run_overlay_command",
        _write_value_runner,
    )

    result = await dispatch_envelope_async(
        {
            "op": "overlay.run",
            "args": {
                "layer_stack_root": str(manager.storage_root),
                "request_id": "handler-request",
                "command": ["bash", "-lc", "printf new > value.txt"],
                "cwd": ".",
                "env": {},
                "timeout_seconds": 5,
            },
        }
    )

    assert result["exit_code"] == 0
    assert result["snapshot_version"] == 1
    assert manager.read_text("value.txt") == ("old\n", True)
    changes = result["changes"]
    assert len(changes) == 1
    assert changes[0]["path"] == "value.txt"
    assert changes[0]["kind"] == "write"
    assert Path(changes[0]["content_path"]).read_bytes() == b"new"
    assert changes[0]["final_hash"] == hashlib.sha256(b"new").hexdigest()


def _write_value_runner(
    *,
    spec: LayerPathsLayout,
    request: CommandExecRequest,
    run_dir: str | Path,
    timings: dict[str, float],
) -> ShellProcessResult:
    run_path = Path(run_dir)
    upper = Path(spec.writes)
    command_text = " ".join(request.command)
    rel = "pkg/value.txt" if "pkg/value.txt" in command_text else "value.txt"
    content = b"new\n" if rel.startswith("pkg/") else b"new"
    target = upper / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    stdout_ref = run_path / "stdout.bin"
    stderr_ref = run_path / "stderr.bin"
    stdout_ref.parent.mkdir(parents=True, exist_ok=True)
    stdout_ref.write_text("out", encoding="utf-8")
    stderr_ref.write_text("err", encoding="utf-8")
    timings["command_exec.run_command_s"] = 0.0
    return ShellProcessResult(
        exit_code=0,
        stdout_ref=str(stdout_ref),
        stderr_ref=str(stderr_ref),
        mounted_workspace_root=spec.workspace_root,
        mount_mode="private_namespace",
    )
