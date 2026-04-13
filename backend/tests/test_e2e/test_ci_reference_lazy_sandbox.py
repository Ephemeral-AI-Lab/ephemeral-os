# ruff: noqa
"""E2E: ci_query_references lazy sandbox attach for planner agents.

Exercises the bug where team_planner calls ci_query_references but has
no ``daytona_sandbox`` in metadata — only ``sandbox_id`` and ``daytona_cwd``.
Both local and remote ripgrep fallbacks silently fail, returning
"No references found" for symbols that clearly exist.

The fix adds ``_resolve_sandbox`` which lazily attaches the sandbox
from ``sandbox_id`` so the remote ripgrep fallback can work.

Run with: pytest tests/test_e2e/test_ci_reference_lazy_sandbox.py -v
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tools.core.base import ToolExecutionContext
from tools.ci_toolkit.query_tools import (
    ci_query_references,
    _resolve_sandbox,
)

pytestmark = [pytest.mark.e2e]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ctx(metadata: dict | None = None) -> ToolExecutionContext:
    return ToolExecutionContext(cwd=Path("/tmp"), metadata=metadata or {})


def _make_rg_sandbox(rg_output: str) -> MagicMock:
    """Create a mock sandbox whose process.exec returns ripgrep output."""
    sandbox = MagicMock()
    sandbox.process.exec = AsyncMock(
        return_value=MagicMock(exit_code=0, result=rg_output),
    )
    return sandbox


def _svc_stub(*, workspace_root: str = "/testbed", initialized: bool = True) -> MagicMock:
    """Create a stub CI service that returns empty from find_references."""
    svc = MagicMock()
    svc.is_initialized = initialized
    svc.workspace_root = workspace_root
    svc.find_references.return_value = []
    svc.lsp_client.connected = True
    return svc


# ---------------------------------------------------------------------------
# _resolve_sandbox unit tests
# ---------------------------------------------------------------------------


class TestResolveSandbox:
    """Unit tests for the lazy sandbox attach helper."""

    async def test_returns_existing_sandbox(self):
        """If daytona_sandbox is already in metadata, return it directly."""
        sandbox = MagicMock()
        ctx = _ctx({"daytona_sandbox": sandbox})
        result = await _resolve_sandbox(ctx)
        assert result is sandbox

    async def test_lazy_attach_from_sandbox_id(self):
        """Resolves sandbox from sandbox_id and caches it in metadata."""
        sandbox = MagicMock()
        ctx = _ctx({"sandbox_id": "sb-lazy-001"})

        with patch("tools.ci_toolkit.query_tools.get_daytona_sandbox", return_value=None):
            with patch(
                "sandbox.async_client.get_async_sandbox",
                new_callable=AsyncMock,
                return_value=sandbox,
            ):
                result = await _resolve_sandbox(ctx)

        assert result is sandbox
        assert ctx.metadata["daytona_sandbox"] is sandbox

    async def test_returns_none_without_sandbox_id(self):
        """Returns None when neither sandbox nor sandbox_id is available."""
        ctx = _ctx({})
        result = await _resolve_sandbox(ctx)
        assert result is None

    async def test_returns_none_on_attach_failure(self):
        """Returns None gracefully when get_async_sandbox raises."""
        ctx = _ctx({"sandbox_id": "sb-fail"})

        with patch("tools.ci_toolkit.query_tools.get_daytona_sandbox", return_value=None):
            with patch(
                "sandbox.async_client.get_async_sandbox",
                new_callable=AsyncMock,
                side_effect=RuntimeError("connection refused"),
            ):
                result = await _resolve_sandbox(ctx)

        assert result is None

    async def test_caches_sandbox_for_subsequent_calls(self):
        """Once lazily attached, second call returns cached sandbox."""
        sandbox = MagicMock()
        ctx = _ctx({"sandbox_id": "sb-cache"})

        with patch("tools.ci_toolkit.query_tools.get_daytona_sandbox", return_value=None):
            with patch(
                "sandbox.async_client.get_async_sandbox",
                new_callable=AsyncMock,
                return_value=sandbox,
            ) as mock_get:
                first = await _resolve_sandbox(ctx)

        # Metadata now has daytona_sandbox — second call returns it directly
        second = await _resolve_sandbox(ctx)
        assert first is second is sandbox
        mock_get.assert_called_once()  # only resolved once


# ---------------------------------------------------------------------------
# Integration: ci_query_references with lazy sandbox attach
# ---------------------------------------------------------------------------


class TestCIQueryReferencesLazySandbox:
    """Simulates the exact team_planner scenario where daytona_sandbox is
    missing from metadata but sandbox_id and daytona_cwd are set."""

    async def test_planner_context_finds_references_via_lazy_sandbox(self):
        """Core bug fix: planner has sandbox_id but no daytona_sandbox.

        The remote ripgrep fallback should lazily attach the sandbox and
        find references instead of returning "No references found".
        """
        svc = _svc_stub(workspace_root="/testbed")

        rg_output = (
            "/testbed/src/engine.py:10:class Engine:\n"
            "/testbed/src/runner.py:5:from engine import Engine\n"
            "/testbed/src/main.py:20:    engine = Engine(config)\n"
        )
        sandbox = _make_rg_sandbox(rg_output)

        # Planner context: has sandbox_id and daytona_cwd but NO daytona_sandbox
        ctx = _ctx({
            "ci_service": svc,
            "sandbox_id": "sb-planner",
            "daytona_cwd": "/testbed",
            "agent_name": "team_planner",
        })

        with patch("tools.ci_toolkit.query_tools.get_ci_service", return_value=svc):
            with patch("tools.ci_toolkit.query_tools.get_daytona_sandbox", return_value=None):
                with patch(
                    "sandbox.async_client.get_async_sandbox",
                    new_callable=AsyncMock,
                    return_value=sandbox,
                ):
                    result = await ci_query_references.execute(
                        ci_query_references.input_model(
                            file_path="/testbed/src/engine.py",
                            symbol="Engine",
                        ),
                        ctx,
                    )

        assert not result.is_error
        data = json.loads(result.output)
        assert data["total_references"] >= 2
        refs = data["references"]
        files = [r["file"] for r in refs]
        assert any("runner.py" in f for f in files), f"Expected runner.py in {files}"
        assert any("main.py" in f for f in files), f"Expected main.py in {files}"

    async def test_planner_config_symbol_references(self):
        """Planner searching for 'config' — the other symbol from the bug report."""
        svc = _svc_stub(workspace_root="/testbed")

        rg_output = (
            "/testbed/src/config.py:1:config = {}\n"
            "/testbed/src/main.py:3:from config import config\n"
            "/testbed/src/engine.py:8:    def __init__(self, config):\n"
        )
        sandbox = _make_rg_sandbox(rg_output)

        ctx = _ctx({
            "ci_service": svc,
            "sandbox_id": "sb-planner",
            "daytona_cwd": "/testbed",
            "agent_name": "team_planner",
        })

        with patch("tools.ci_toolkit.query_tools.get_ci_service", return_value=svc):
            with patch("tools.ci_toolkit.query_tools.get_daytona_sandbox", return_value=None):
                with patch(
                    "sandbox.async_client.get_async_sandbox",
                    new_callable=AsyncMock,
                    return_value=sandbox,
                ):
                    result = await ci_query_references.execute(
                        ci_query_references.input_model(
                            file_path="/testbed/src/config.py",
                            symbol="config",
                        ),
                        ctx,
                    )

        assert not result.is_error
        data = json.loads(result.output)
        assert data["total_references"] >= 2

    async def test_falls_back_to_no_references_when_sandbox_unavailable(self):
        """When sandbox_id is also missing, falls through to 'No references found'."""
        svc = _svc_stub(workspace_root="/testbed")

        # No sandbox_id, no daytona_sandbox, workspace is remote
        ctx = _ctx({
            "ci_service": svc,
            "agent_name": "team_planner",
        })

        with patch("tools.ci_toolkit.query_tools.get_ci_service", return_value=svc):
            result = await ci_query_references.execute(
                ci_query_references.input_model(
                    file_path="/testbed/src/engine.py",
                    symbol="Engine",
                ),
                ctx,
            )

        assert "No references found" in result.output

    async def test_cold_ci_with_lazy_sandbox_reports_cold_status(self):
        """When CI is cold AND sandbox attach fails, reports cold status."""
        svc = _svc_stub(workspace_root="/testbed", initialized=False)
        svc.lsp_client.connected = False

        ctx = _ctx({
            "ci_service": svc,
            "sandbox_id": "sb-cold",
            "daytona_cwd": "/testbed",
        })

        with patch("tools.ci_toolkit.query_tools.get_ci_service", return_value=svc):
            with patch("tools.ci_toolkit.query_tools.get_daytona_sandbox", return_value=None):
                with patch(
                    "sandbox.async_client.get_async_sandbox",
                    new_callable=AsyncMock,
                    side_effect=RuntimeError("sandbox not ready"),
                ):
                    result = await ci_query_references.execute(
                        ci_query_references.input_model(
                            file_path="/testbed/src/engine.py",
                            symbol="Engine",
                        ),
                        ctx,
                    )

        data = json.loads(result.output)
        assert data["status"] == "cold"
        assert data["lsp_connected"] is False

    async def test_sandbox_cached_across_multiple_reference_calls(self):
        """Once lazily attached, subsequent ci_query_references calls reuse it."""
        svc = _svc_stub(workspace_root="/testbed")

        sandbox = _make_rg_sandbox("/testbed/src/a.py:1:Engine\n")

        ctx = _ctx({
            "ci_service": svc,
            "sandbox_id": "sb-reuse",
            "daytona_cwd": "/testbed",
        })

        with patch("tools.ci_toolkit.query_tools.get_ci_service", return_value=svc):
            with patch(
                "sandbox.async_client.get_async_sandbox",
                new_callable=AsyncMock,
                return_value=sandbox,
            ) as mock_get:
                # First call — lazy attach
                r1 = await ci_query_references.execute(
                    ci_query_references.input_model(
                        file_path="/testbed/src/engine.py",
                        symbol="Engine",
                    ),
                    ctx,
                )

                # Second call — should reuse cached sandbox
                r2 = await ci_query_references.execute(
                    ci_query_references.input_model(
                        file_path="/testbed/src/config.py",
                        symbol="config",
                    ),
                    ctx,
                )

        assert not r1.is_error
        assert not r2.is_error
        # get_async_sandbox called once (first call), not twice
        mock_get.assert_called_once_with("sb-reuse")


# ---------------------------------------------------------------------------
# Live sandbox test (requires Daytona credentials)
# ---------------------------------------------------------------------------


from engine.testing.eval_agent import EvalAgent
from tests.test_e2e.conftest import (
    create_eval_agent,
    create_test_sandbox,
    delete_test_sandbox,
    populate_sandbox_files,
)


@pytest.fixture(scope="module")
def live_sandbox_id():
    if not EvalAgent.has_daytona():
        pytest.skip("Daytona credentials required")
    sb = create_test_sandbox("ci-ref-lazy")
    populate_sandbox_files(sb["id"])
    yield sb["id"]
    delete_test_sandbox(sb["id"])


@pytest.mark.live
@pytest.mark.asyncio
class TestLiveLazySandboxReferences:
    """Live sandbox tests for lazy sandbox attach in ci_query_references."""

    async def test_live_references_without_daytona_sandbox_in_metadata(self, live_sandbox_id):
        """Live: ci_query_references finds references when only sandbox_id is set.

        Simulates the exact planner scenario against a real Daytona sandbox.
        """
        from sandbox.service import SandboxService
        from sandbox.workspace import discover_workspace, inject_code_intelligence

        svc_client = SandboxService()
        sandbox = svc_client.get_sandbox_object(live_sandbox_id)
        workspace_root = discover_workspace(sandbox) or "/home/daytona"

        # Inject CI service (builds the symbol index)
        context = MagicMock()
        context.metadata = {}
        inject_code_intelligence(context, live_sandbox_id, sandbox, workspace_root)

        ci_svc = context.metadata.get("ci_service")
        assert ci_svc is not None
        ci_svc.symbol_index.ensure_built(wait=True, timeout=60.0)

        # Build a planner-like context: sandbox_id + daytona_cwd but NO daytona_sandbox
        planner_ctx = _ctx({
            "ci_service": ci_svc,
            "sandbox_id": live_sandbox_id,
            "daytona_cwd": workspace_root,
            "agent_name": "team_planner",
        })

        result = await ci_query_references.execute(
            ci_query_references.input_model(
                file_path=f"{workspace_root}/src/main.py",
                symbol="App",
            ),
            planner_ctx,
        )

        assert not result.is_error
        data = json.loads(result.output)
        # The populate_sandbox_files helper puts an App class in src/main.py
        # which should be referenced somewhere
        assert data["total_references"] >= 1, (
            f"Expected references for 'App', got: {result.output}"
        )

    async def test_live_agent_uses_ci_query_references(self, live_sandbox_id):
        """Live agent test: verify ci_query_references works through EvalAgent.

        Creates an agent with code_intelligence + sandbox_operations toolkits,
        then strips daytona_sandbox from metadata to simulate the planner
        scenario where only sandbox_id is present. The lazy attach should
        re-resolve the sandbox and return references.
        """
        if not EvalAgent.has_all():
            pytest.skip("LLM + Daytona credentials required")

        agent = create_eval_agent(
            sandbox_id=live_sandbox_id,
            toolkits=["sandbox_operations", "code_intelligence"],
            system_prompt=(
                "You have a remote sandbox with Python files in src/. "
                "When asked to find references to a symbol, you MUST use "
                "ci_query_references. Do NOT use daytona_grep or any other "
                "search tool. ONLY use ci_query_references. Be concise."
            ),
        )

        # Simulate the planner scenario: strip daytona_sandbox from metadata
        # but keep sandbox_id so the lazy attach can resolve it.
        meta = agent._query_context.tool_metadata
        meta["daytona_sandbox"] = None

        result = await agent.invoke(
            "Use ci_query_references with file_path='src/main.py' and "
            "symbol='App' to find all references to the App class."
        )

        completed = result.tools_completed()
        ci_calls = [e for e in completed if e.tool_name == "ci_query_references"]
        assert ci_calls, (
            f"Expected ci_query_references to be called, but agent used: "
            f"{[t.name for t in result.tool_calls]}"
        )
        for call in ci_calls:
            assert "No references found" not in (call.output or ""), (
                f"ci_query_references returned empty (lazy sandbox attach failed): "
                f"{call.output}"
            )
