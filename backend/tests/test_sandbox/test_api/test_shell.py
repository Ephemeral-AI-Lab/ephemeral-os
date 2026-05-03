"""Tests for ``sandbox.api.shell``."""

from __future__ import annotations

import json
import shlex

from sandbox.api.models import RawExecResult, RequestActor, ShellRequest
from sandbox.api.shell import shell
from sandbox.providers.registry import dispose_adapter, register_adapter


class _Adapter:
    name = "shell-api"

    def __init__(self, *, response: dict) -> None:
        self.response = response
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
        payload = json.loads(shlex.split(command)[-1])
        assert payload["op"] == "shell"
        assert payload["args"]["command"] == "pytest -q"
        return RawExecResult(exit_code=0, stdout=json.dumps(self.response))


async def test_shell_delegates_once_and_round_trips_changed_paths() -> None:
    adapter = _Adapter(
        response={
            "result": "ok\n",
            "exit_code": 0,
            "changed_paths": ["/workspace/a.py"],
            "warnings": [],
            "overlay_run_timings": {},
            "overlay_stage_timings": {},
            "conflict": None,
        }
    )
    register_adapter("sb-shell", adapter)
    try:
        result = await shell(
            "sb-shell",
            ShellRequest(
                command="pytest -q",
                cwd="/workspace",
                actor=RequestActor(agent_id="agent-1"),
            ),
        )
    finally:
        dispose_adapter("sb-shell")

    assert result.success is True
    assert result.stdout == "ok\n"
    assert result.changed_paths == ("/workspace/a.py",)
    assert result.conflict is None
    assert len(adapter.calls) == 1


async def test_shell_overlay_or_occ_failure_maps_conflict_info() -> None:
    adapter = _Adapter(
        response={
            "result": "",
            "exit_code": 0,
            "changed_paths": ["/workspace/a.py"],
            "warnings": [],
            "overlay_run_timings": {},
            "overlay_stage_timings": {},
            "conflict": {
                "reason": "overlay_upper_full",
                "conflict_file": "/workspace/a.py",
                "message": "upperdir full",
            },
        }
    )
    register_adapter("sb-shell-conflict", adapter)
    try:
        result = await shell(
            "sb-shell-conflict",
            ShellRequest(
                command="pytest -q",
                cwd="/workspace",
                actor=RequestActor(agent_id="agent-1"),
            ),
        )
    finally:
        dispose_adapter("sb-shell-conflict")

    assert result.success is False
    assert result.status == "error"
    assert result.conflict is not None
    assert result.conflict.reason == "overlay_upper_full"
    assert result.conflict.conflict_file == "/workspace/a.py"
    assert result.conflict.message == "upperdir full"
    assert result.conflict_reason == "upperdir full"
