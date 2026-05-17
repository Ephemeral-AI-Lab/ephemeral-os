"""Tests for tools.sandbox.write_file."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from sandbox.api import WriteFileResult
from tools._framework.core.base import ToolExecutionContextService
import tools.sandbox.write_file as write_file_module
from tools.sandbox.write_file import write_file

from ._helpers import run_tool_safely


class _WriteApi:
    def __init__(self, result: WriteFileResult) -> None:
        self.result = result
        self.calls: list[tuple[str, Any]] = []

    async def write_file(
        self,
        sandbox_id: str,
        request: Any,
        **kwargs: Any,
    ) -> WriteFileResult:
        self.calls.append((sandbox_id, request))
        self.kwargs = kwargs
        return self.result


def _ctx_with_api(api: _WriteApi) -> ToolExecutionContextService:
    return ToolExecutionContextService(
        cwd=Path("/tmp"),
        services={
            "sandbox_id": "sb-1",
            "sandbox_api": api,
            "repo_root": "/ws",
        },
    )


def _run(args: dict[str, Any], ctx: ToolExecutionContextService):
    return asyncio.run(run_tool_safely(write_file, args, context=ctx))


def test_write_file_success_returns_changed_paths_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _WriteApi(
        WriteFileResult(success=True, changed_paths=("/ws/new.py",))
    )
    ctx = _ctx_with_api(api)
    monkeypatch.setattr(write_file_module, "sandbox_api", api)

    result = _run({"file_path": "/ws/new.py", "content": "print('hi')\n"}, ctx)

    assert not result.is_error
    payload = json.loads(result.output)
    assert payload == {
        "status": "written",
        "changed_paths": ["/ws/new.py"],
        "conflict_reason": None,
        "cwd": "/ws",
        "file_path": "/ws/new.py",
        "bytes_written": 12,
    }


def test_write_file_failure_preserves_conflict_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _WriteApi(
        WriteFileResult(
            success=False,
            changed_paths=("/ws/new.py",),
            status="aborted_overlap",
            conflict_reason="concurrent edit overlaps the operation window",
        )
    )
    ctx = _ctx_with_api(api)
    monkeypatch.setattr(write_file_module, "sandbox_api", api)

    result = _run({"file_path": "/ws/new.py", "content": "x"}, ctx)

    assert result.is_error
    payload = json.loads(result.output)
    assert payload == {
        "status": "aborted_overlap",
        "changed_paths": ["/ws/new.py"],
        "conflict_reason": "concurrent edit overlaps the operation window",
    }


def test_write_file_ignores_non_publishable_audit_sink(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _WriteApi(WriteFileResult(success=True, changed_paths=("/ws/new.py",)))
    sink = object()
    ctx = ToolExecutionContextService(
        cwd=Path("/tmp"),
        services={
            "sandbox_id": "sb-1",
            "repo_root": "/ws",
            "sandbox_audit_sink": sink,
        },
    )
    monkeypatch.setattr(write_file_module, "sandbox_api", api)

    result = _run({"file_path": "/ws/new.py", "content": "x"}, ctx)

    assert not result.is_error
    assert api.kwargs == {}
    assert "sandbox_audit_emitted" not in result.metadata


def test_write_file_uses_publishable_audit_sink(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Sink:
        def publish(self, _event: object) -> None:
            return None

    api = _WriteApi(WriteFileResult(success=True, changed_paths=("/ws/new.py",)))
    sink = Sink()
    ctx = ToolExecutionContextService(
        cwd=Path("/tmp"),
        services={
            "sandbox_id": "sb-1",
            "repo_root": "/ws",
            "sandbox_audit_sink": sink,
        },
    )
    monkeypatch.setattr(write_file_module, "sandbox_api", api)

    result = _run({"file_path": "/ws/new.py", "content": "x"}, ctx)

    assert api.kwargs == {"audit_sink": sink}
    assert result.metadata["sandbox_audit_emitted"] is True
