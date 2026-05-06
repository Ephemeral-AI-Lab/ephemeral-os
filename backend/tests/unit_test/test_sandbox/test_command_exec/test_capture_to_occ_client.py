"""Command-exec capture submission tests."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from sandbox.command_exec.result import ShellProcessResult
from sandbox.layer_stack.manifest import Manifest
from sandbox.layer_stack.workspace_base import build_workspace_base
from sandbox.layer_stack.workspace import WorkspaceBinding, write_workspace_binding_atomic
from sandbox.occ.changeset.types import ChangesetResult, FileResult, FileStatus
from sandbox.runtime import command_exec_server
from sandbox.runtime.clients.layer_stack import LayerStackClient


@dataclass(frozen=True)
class _Lease:
    lease_id: str
    manifest_version: int
    root_hash: str
    manifest: Manifest
    lowerdir: str
    cache_hit: bool
    materialized_byte_count: int
    timings: dict[str, float]


class _LayerStackClient:
    def __init__(self, lowerdir: Path) -> None:
        self.lease = _Lease(
            lease_id="lease-1",
            manifest_version=1,
            root_hash="h",
            manifest=Manifest(version=1, layers=()),
            lowerdir=str(lowerdir),
            cache_hit=False,
            materialized_byte_count=0,
            timings={"layer_stack.snapshot_cache.hit": 0.0},
        )
        self.released: list[str] = []

    def prepare_workspace_snapshot(
        self,
        *,
        workspace_ref: str,
        request_id: str,
        ttl_seconds: float | None = None,
        cache_policy: str = "enabled",
    ) -> _Lease:
        del workspace_ref, request_id, ttl_seconds, cache_policy
        return self.lease

    def release_lease(self, *, workspace_ref: str, lease_id: str) -> bool:
        del workspace_ref
        self.released.append(lease_id)
        return True


class _OCCClient:
    def __init__(self, layer_stack: _LayerStackClient) -> None:
        self.layer_stack = layer_stack
        self.paths: list[str] = []
        self.snapshot: object | None = None
        self.atomic: bool | None = None

    async def apply_changeset(
        self,
        typed_changes,
        *,
        snapshot: object | None = None,
        options: object | None = None,
        workspace_ref: str | None = None,
    ) -> ChangesetResult:
        del workspace_ref
        assert self.layer_stack.released == []
        self.paths = [change.path for change in typed_changes]
        self.snapshot = snapshot
        self.atomic = getattr(options, "atomic", None)
        return ChangesetResult(
            files=(
                FileResult(path="generated/output.txt", status=FileStatus.COMMITTED),
            ),
            timings={"occ.apply.total_s": 0.01},
            published_manifest_version=2,
        )


class _Gitignore:
    cache_hits = 0
    cache_misses = 0
    last_materialize_s = 0.0
    last_git_init_s = 0.0


async def test_shell_capture_goes_through_occ_client_before_lease_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    stack = tmp_path / "stack"
    stack.mkdir()
    write_workspace_binding_atomic(
        WorkspaceBinding(
            workspace_root=workspace.as_posix(),
            layer_stack_root=stack.as_posix(),
            active_manifest_version=1,
            active_root_hash="a" * 64,
            base_manifest_version=1,
            base_root_hash="a" * 64,
        )
    )
    lower = tmp_path / "lower"
    lower.mkdir()
    layer_stack = _LayerStackClient(lower)
    occ = _OCCClient(layer_stack)

    def fake_run_workspace_replaced_command(*, spec, request, run_dir, timings):
        del request
        upper = Path(spec.upperdir)
        upper.mkdir(parents=True)
        output = upper / "generated" / "output.txt"
        output.parent.mkdir(parents=True)
        output.write_text("value\n", encoding="utf-8")
        stdout_ref = Path(run_dir) / "stdout.bin"
        stderr_ref = Path(run_dir) / "stderr.bin"
        stdout_ref.write_text("done\n", encoding="utf-8")
        stderr_ref.write_text("", encoding="utf-8")
        timings["command_exec.mount_workspace_s"] = 0.001
        timings["command_exec.run_command_s"] = 0.002
        return ShellProcessResult(
            exit_code=0,
            stdout_ref=str(stdout_ref),
            stderr_ref=str(stderr_ref),
            mounted_workspace_root=str(workspace),
            mount_mode="private_namespace",
        )

    monkeypatch.setattr(
        command_exec_server,
        "run_workspace_replaced_command",
        fake_run_workspace_replaced_command,
    )

    result = await command_exec_server._execute_shell(
        {
            "layer_stack_root": stack.as_posix(),
            "command": "true",
            "cwd": ".",
            "actor_id": "agent-1",
            "description": "unit shell",
        },
        layer_stack=layer_stack,
        occ_client=occ,
        gitignore=_Gitignore(),
        storage_root=stack,
    )

    assert occ.paths == ["generated/output.txt"]
    assert occ.snapshot is layer_stack.lease.manifest
    assert occ.atomic is True
    assert layer_stack.released == ["lease-1"]
    assert result.stdout == "done\n"
    assert result.workspace_capture.snapshot_version == 1


async def test_cache_disabled_shell_uses_transient_lowerdir_and_removes_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "input.txt").write_text("base\n", encoding="utf-8")
    stack = tmp_path / "stack"
    build_workspace_base(workspace_root=workspace, layer_stack_root=stack)
    layer_stack = LayerStackClient(stack)
    captured_lowerdirs: list[Path] = []

    def fake_run_workspace_replaced_command(*, spec, request, run_dir, timings):
        del request
        lowerdir = Path(spec.lowerdir)
        captured_lowerdirs.append(lowerdir)
        assert lowerdir.is_dir()
        assert (lowerdir / "input.txt").read_text(encoding="utf-8") == "base\n"
        Path(spec.upperdir).mkdir(parents=True, exist_ok=True)
        stdout_ref = Path(run_dir) / "stdout.bin"
        stderr_ref = Path(run_dir) / "stderr.bin"
        stdout_ref.write_text("done\n", encoding="utf-8")
        stderr_ref.write_text("", encoding="utf-8")
        timings["command_exec.mount_workspace_s"] = 0.001
        timings["command_exec.run_command_s"] = 0.001
        return ShellProcessResult(
            exit_code=0,
            stdout_ref=str(stdout_ref),
            stderr_ref=str(stderr_ref),
            mounted_workspace_root=str(workspace),
            mount_mode="private_namespace",
        )

    monkeypatch.setattr(
        command_exec_server,
        "run_workspace_replaced_command",
        fake_run_workspace_replaced_command,
    )

    result = await command_exec_server._execute_shell(
        {
            "layer_stack_root": stack.as_posix(),
            "command": "true",
            "cwd": ".",
            "snapshot_cache_policy": "disabled",
        },
        layer_stack=layer_stack,
        occ_client=_OCCClient(_LayerStackClient(tmp_path / "unused-lower")),
        gitignore=_Gitignore(),
        storage_root=stack,
    )

    assert result.exit_code == 0
    assert captured_lowerdirs
    assert captured_lowerdirs[0].exists() is False
    assert result.timings["layer_stack.snapshot_cache.hit"] == 0.0
