"""Lifecycle tests for no-OCC snapshot-overlay command execution.

The bulk-growth intermediates inside ``run_dir``
(``workspace/`` and ``work/``) must be reaped after the invocation.
Load-bearing artifacts (``upper/`` with ``content_path`` refs, ``stdout.bin``,
``stderr.bin``) MUST remain readable after return because the daemon overlay
payload carries references into them that downstream consumers read
post-invocation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sandbox.layer_stack import WriteLayerChange, LayerStack
from sandbox.daemon.service.layer_stack_client import LayerStackClient
from sandbox.ephemeral_workspace.shell_contract import CommandExecRequest, ShellProcessResult
from sandbox.ephemeral_workspace._execute_command import execute_command
from sandbox.overlay.layout import LayerPathsLayout


def _source(tmp_path: Path, name: str, content: bytes) -> str:
    path = tmp_path / "sources" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return str(path)


@pytest.mark.asyncio
async def test_no_occ_orchestrator_removes_intermediate_dirs_but_keeps_outputs(
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
    request = CommandExecRequest(
        request_id="request-cleanup",
        workspace_ref=manager.storage_root.as_posix(),
        workspace_root="/workspace",
        command=(
            "bash",
            "-lc",
            "printf new > pkg/value.txt; printf out; printf err >&2",
        ),
        cwd=".",
        env={},
        timeout_seconds=10,
    )

    result = await execute_command(
        request,
        layer_stack=LayerStackClient(manager),
        capture_publisher=None,
        storage_root=manager.storage_root,
        occ_apply=False,
        command_runner=_write_cleanup_runner,
    )

    runtime_root = manager.storage_root / "runtime" / "command_exec"
    run_dirs = list(runtime_root.iterdir())
    assert len(run_dirs) == 1
    run_dir = run_dirs[0]

    # Bulk-growth intermediates must be gone.
    assert not (run_dir / "workspace").exists()
    assert not (run_dir / "work").exists()

    # Load-bearing artifacts MUST still exist and be readable.
    assert (run_dir / "upper").is_dir()
    assert Path(result.stdout_ref).read_text(encoding="utf-8") == "out"
    assert Path(result.stderr_ref).read_text(encoding="utf-8") == "err"

    # content_path refs into upper/ must still be readable.
    assert len(result.workspace_capture.changes) == 1
    change = result.workspace_capture.changes[0]
    assert change.content_path is not None
    assert Path(change.content_path).read_bytes() == b"new"


@pytest.mark.asyncio
async def test_no_occ_orchestrator_cleans_intermediate_dirs_even_on_nonzero_exit(
    tmp_path: Path,
) -> None:
    manager = LayerStack(tmp_path / "stack")
    manager.publish_changes(
        [
            WriteLayerChange(
                path="value.txt",
                source_path=_source(tmp_path, "value.txt", b"x\n"),
            )
        ]
    )
    request = CommandExecRequest(
        request_id="request-fail",
        workspace_ref=manager.storage_root.as_posix(),
        workspace_root="/workspace",
        command=("bash", "-lc", "exit 3"),
        cwd=".",
        env={},
        timeout_seconds=10,
    )

    result = await execute_command(
        request,
        layer_stack=LayerStackClient(manager),
        capture_publisher=None,
        storage_root=manager.storage_root,
        occ_apply=False,
        command_runner=_nonzero_runner,
    )

    runtime_root = manager.storage_root / "runtime" / "command_exec"
    run_dir = next(iter(runtime_root.iterdir()))
    assert not (run_dir / "workspace").exists()
    assert not (run_dir / "work").exists()
    assert result.exit_code == 3


def _write_cleanup_runner(
    *,
    spec: LayerPathsLayout,
    request: CommandExecRequest,
    run_dir: str | Path,
    timings: dict[str, float],
) -> ShellProcessResult:
    del request
    run_path = Path(run_dir)
    target = Path(spec.writes) / "pkg" / "value.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"new")
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


def _nonzero_runner(
    *,
    spec: LayerPathsLayout,
    request: CommandExecRequest,
    run_dir: str | Path,
    timings: dict[str, float],
) -> ShellProcessResult:
    del request
    run_path = Path(run_dir)
    stdout_ref = run_path / "stdout.bin"
    stderr_ref = run_path / "stderr.bin"
    stdout_ref.parent.mkdir(parents=True, exist_ok=True)
    stdout_ref.write_text("", encoding="utf-8")
    stderr_ref.write_text("", encoding="utf-8")
    timings["command_exec.run_command_s"] = 0.0
    return ShellProcessResult(
        exit_code=3,
        stdout_ref=str(stdout_ref),
        stderr_ref=str(stderr_ref),
        mounted_workspace_root=spec.workspace_root,
        mount_mode="private_namespace",
    )
