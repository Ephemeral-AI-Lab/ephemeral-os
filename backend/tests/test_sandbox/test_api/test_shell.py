"""Tests for ``sandbox.api.shell``."""

from __future__ import annotations

from sandbox.api import RawExecResult, RequestActor, ShellRequest
from sandbox.api.shell import shell
from sandbox.providers.registry import dispose_adapter, register_adapter


class _RawAdapter:
    name = "raw-shell-api"

    def __init__(self, *, stdout: str = "", exit_code: int = 0) -> None:
        self.stdout = stdout
        self.exit_code = exit_code
        self.calls: list[tuple[str, str, str | None, int | None]] = []

    async def exec(
        self,
        sandbox_id: str,
        command: str,
        *,
        cwd: str | None = None,
        timeout: int | None = None,
    ) -> RawExecResult:
        self.calls.append((sandbox_id, command, cwd, timeout))
        return RawExecResult(
            success=self.exit_code == 0,
            exit_code=self.exit_code,
            stdout=self.stdout,
        )


async def test_shell_routes_read_only_pipeline_to_raw_exec() -> None:
    adapter = _RawAdapter(stdout="2\n")
    register_adapter("sb-shell-readonly", adapter)
    try:
        result = await shell(
            "sb-shell-readonly",
            ShellRequest(
                command="cat pyproject.toml | grep pytest | wc -l",
                cwd="/workspace",
                timeout=12,
                actor=RequestActor(agent_id="agent-1"),
            ),
        )
    finally:
        dispose_adapter("sb-shell-readonly")

    assert result.success is True
    assert result.status == "ok"
    assert result.exit_code == 0
    assert result.stdout == "2\n"
    assert result.changed_paths == ()
    assert adapter.calls == [
        ("sb-shell-readonly", "cat pyproject.toml | grep pytest | wc -l", "/workspace", 12)
    ]


async def test_shell_rejects_mutating_command_without_live_root_runtime() -> None:
    adapter = _RawAdapter(stdout="unused\n")
    register_adapter("sb-shell-mutating", adapter)
    try:
        result = await shell(
            "sb-shell-mutating",
            ShellRequest(
                command="cat pyproject.toml | tee copied.txt",
                cwd="/workspace",
                actor=RequestActor(agent_id="agent-1"),
            ),
        )
    finally:
        dispose_adapter("sb-shell-mutating")

    assert result.success is False
    assert result.exit_code == 1
    assert result.status == "error"
    assert result.changed_paths == ()
    assert result.conflict is not None
    assert result.conflict.reason == "overlay_snapshot_required"
    assert result.conflict_reason == (
        "legacy live-root shell runtime was removed; "
        "shell mutation requests must use the layer-stack snapshot path"
    )
    assert result.warnings == ("legacy live-root shell runtime was removed",)
    assert adapter.calls == []
