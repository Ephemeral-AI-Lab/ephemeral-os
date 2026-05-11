"""Unit tests for LSP live-runner workspace binding setup."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from benchmarks.lsp_live_test import runner as runner_mod
from benchmarks.lsp_live_test.scenarios import LspScenario
from tools.core.context import ToolExecutionContextService
from tools.core.results import ToolResult


class _WorkspaceBindingError(RuntimeError):
    kind = "WorkspaceBindingError"


@pytest.mark.asyncio
async def test_lsp_runner_keeps_matching_workspace_base(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    async def fake_call_daemon_api(
        sandbox_id: str,
        op: str,
        args: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        del sandbox_id, kwargs
        calls.append((op, args))
        return {"binding": {"workspace_root": "/testbed"}}

    monkeypatch.setattr(runner_mod, "call_daemon_api", fake_call_daemon_api)

    await runner_mod._ensure_workspace_base("sb-1", "/testbed")

    assert calls == [
        ("api.ensure_workspace_base", {"workspace_root": "/testbed"})
    ]


@pytest.mark.asyncio
async def test_lsp_runner_rebuilds_mismatched_workspace_base(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    async def fake_call_daemon_api(
        sandbox_id: str,
        op: str,
        args: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        del sandbox_id, kwargs
        calls.append((op, args))
        if op == "api.ensure_workspace_base":
            raise _WorkspaceBindingError(
                "workspace binding points at a different workspace: "
                "/ephemeral-os != /testbed"
            )
        return {"binding": {"workspace_root": "/testbed"}}

    monkeypatch.setattr(runner_mod, "call_daemon_api", fake_call_daemon_api)

    await runner_mod._ensure_workspace_base("sb-1", "/testbed")

    assert calls == [
        ("api.ensure_workspace_base", {"workspace_root": "/testbed"}),
        (
            "api.build_workspace_base",
            {"workspace_root": "/testbed", "reset": True},
        ),
    ]


@pytest.mark.asyncio
async def test_lsp_runner_propagates_non_binding_setup_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_call_daemon_api(
        sandbox_id: str,
        op: str,
        args: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        del sandbox_id, op, args, kwargs
        raise RuntimeError("daemon unavailable")

    monkeypatch.setattr(runner_mod, "call_daemon_api", fake_call_daemon_api)

    with pytest.raises(RuntimeError, match="daemon unavailable"):
        await runner_mod._ensure_workspace_base("sb-1", "/testbed")


@pytest.mark.asyncio
async def test_lsp_runner_prewarms_first_python_setup_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    async def fake_execute_tool_call(
        call: Any,
        ctx: ToolExecutionContextService,
    ) -> ToolResult:
        del ctx
        calls.append((call.tool_name, call.args))
        return ToolResult(output='{"diagnostics":[]}')

    monkeypatch.setattr(runner_mod, "_execute_tool_call", fake_execute_tool_call)
    scenario = LspScenario(
        name="warm",
        description="warm",
        setup_files={
            "pyrightconfig.json": "{}",
            "pkg/mod.py": "value = 1\n",
        },
        tool_calls=(),
    )

    elapsed = await runner_mod._prewarm_lsp_session(
        scenario,
        ToolExecutionContextService(cwd=Path("/tmp")),
    )

    assert elapsed >= 0.0
    assert calls == [("lsp.diagnostics", {"file_path": "pkg/mod.py"})]


@pytest.mark.asyncio
async def test_lsp_runner_skips_prewarm_without_python_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_execute_tool_call(*args: Any, **kwargs: Any) -> ToolResult:
        raise AssertionError("prewarm should be skipped")

    monkeypatch.setattr(runner_mod, "_execute_tool_call", fail_execute_tool_call)
    scenario = LspScenario(
        name="warm",
        description="warm",
        setup_files={"README.md": "# demo\n"},
        tool_calls=(),
    )

    assert await runner_mod._prewarm_lsp_session(
        scenario,
        ToolExecutionContextService(cwd=Path("/tmp")),
    ) == 0.0
