"""Tests for tools.sandbox.shell."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from sandbox.api import ShellResult
from sandbox.shared.timing_keys import TimingKey
from tools._framework.core.base import ToolExecutionContextService
import tools.sandbox.shell as shell_module
from tools.sandbox.shell import shell

from ._helpers import run_tool_safely


class _ShellApi:
    def __init__(self, result: ShellResult) -> None:
        self.result = result
        self.calls: list[tuple[str, Any]] = []

    async def shell(
        self,
        sandbox_id: str,
        request: Any,
        **kwargs: Any,
    ) -> ShellResult:
        self.calls.append((sandbox_id, request))
        self.kwargs = kwargs
        return self.result


def _ctx_with_api(api: _ShellApi) -> ToolExecutionContextService:
    return ToolExecutionContextService(
        cwd=Path("/tmp"),
        services={
            "sandbox_id": "sb-1",
            "sandbox_api": api,
            "repo_root": "/ws",
        },
    )


def _run(args: dict[str, Any], ctx: ToolExecutionContextService):
    return asyncio.run(run_tool_safely(shell, args, context=ctx))


def test_shell_success_returns_single_command_output_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _ShellApi(
        ShellResult(
            exit_code=0,
            stdout="ok\n",
            success=True,
            changed_paths=("/ws/a.py",),
        )
    )
    ctx = _ctx_with_api(api)
    monkeypatch.setattr(shell_module, "sandbox_api", api)

    result = _run({"command": "pytest -q"}, ctx)

    assert not result.is_error
    payload = json.loads(result.output)
    assert payload == {
        "cwd": "/ws",
        "status": "ok",
        "changed_paths": ["/ws/a.py"],
        "changed_path_kinds": {},
        "mutation_source": "",
        "conflict_reason": None,
        "command": "pytest -q",
        "exit_code": 0,
        "stdout": "ok\n",
        "stderr": "",
        "error": "",
    }


def test_shell_metadata_normalizes_timing_key_enum_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _ShellApi(
        ShellResult(
            exit_code=0,
            stdout="ok\n",
            success=True,
            timings={TimingKey.PREPARE_TOTAL: 0.1, TimingKey.APPLY_TOTAL: 0.2},
        )
    )
    ctx = _ctx_with_api(api)
    monkeypatch.setattr(shell_module, "sandbox_api", api)

    result = _run({"command": "pytest -q"}, ctx)

    assert result.metadata["timings"] == {
        "occ.prepare.total_s": 0.1,
        "occ.apply.total_s": 0.2,
    }


def test_shell_conflict_returns_conflict_reason_without_legacy_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _ShellApi(
        ShellResult(
            exit_code=0,
            stdout="",
            success=False,
            changed_paths=("/ws/a.py",),
            conflict_reason="overlay upperdir is full",
        )
    )
    ctx = _ctx_with_api(api)
    monkeypatch.setattr(shell_module, "sandbox_api", api)

    result = _run({"command": "python script.py"}, ctx)

    assert result.is_error
    payload = json.loads(result.output)
    assert payload == {
        "cwd": "/ws",
        "status": "error",
        "changed_paths": ["/ws/a.py"],
        "changed_path_kinds": {},
        "mutation_source": "",
        "conflict_reason": "overlay upperdir is full",
        "command": "python script.py",
        "exit_code": 0,
        "stdout": "",
        "stderr": "",
        "error": "sandbox commit aborted: overlay upperdir is full",
    }


def test_shell_uses_publishable_audit_sink(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Sink:
        def publish(self, _event: object) -> None:
            return None

    api = _ShellApi(ShellResult(exit_code=0, stdout="ok\n", success=True))
    sink = Sink()
    ctx = ToolExecutionContextService(
        cwd=Path("/tmp"),
        services={
            "sandbox_id": "sb-1",
            "repo_root": "/ws",
            "sandbox_audit_sink": sink,
        },
    )
    monkeypatch.setattr(shell_module, "sandbox_api", api)

    result = _run({"command": "pytest -q"}, ctx)

    assert api.kwargs == {"audit_sink": sink}
    assert result.metadata["sandbox_audit_emitted"] is True
