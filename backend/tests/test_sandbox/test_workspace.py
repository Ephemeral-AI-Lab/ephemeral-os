"""Tests for sandbox.workspace."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, AsyncMock

from tools.core.base import ToolExecutionContextService


class TestDiscoverWorkspace:
    def test_returns_project_dir_when_present(self):
        from sandbox.lifecycle.workspace import discover_workspace

        sandbox = MagicMock()
        sandbox.configure_mock(project_dir="/workspace/my-project")
        result = discover_workspace(sandbox)

        assert result == "/workspace/my-project"

    def test_falls_back_to_pwd(self):
        from sandbox.lifecycle.workspace import discover_workspace

        sandbox = MagicMock()
        sandbox.configure_mock(project_dir=None)
        resp = MagicMock()
        resp.configure_mock(exit_code=0, result="/home/daytona\n")
        exec_mock = MagicMock(return_value=resp)
        sandbox.configure_mock(process=MagicMock(exec=exec_mock))

        result = discover_workspace(sandbox)

        assert result == "/home/daytona"
        exec_mock.assert_called_once_with("pwd")

    def test_returns_none_when_both_fail(self):
        from sandbox.lifecycle.workspace import discover_workspace

        sandbox = MagicMock()
        sandbox.configure_mock(project_dir=None)
        exec_mock = MagicMock(side_effect=RuntimeError("broken"))
        sandbox.configure_mock(process=MagicMock(exec=exec_mock))

        result = discover_workspace(sandbox)

        assert result is None


class TestDiscoverWorkspaceAsync:
    @pytest.mark.anyio
    async def test_returns_project_dir_when_present(self):
        from sandbox.lifecycle.workspace import discover_workspace_async

        sandbox = MagicMock()
        sandbox.configure_mock(project_dir="/workspace/my-project")

        result = await discover_workspace_async(sandbox)

        assert result == "/workspace/my-project"

    @pytest.mark.anyio
    async def test_falls_back_to_pwd(self):
        from sandbox.lifecycle.workspace import discover_workspace_async

        sandbox = MagicMock()
        sandbox.configure_mock(project_dir=None)
        resp = MagicMock()
        resp.configure_mock(exit_code=0, result="/home/daytona\n")
        exec_mock = AsyncMock(return_value=resp)
        sandbox.configure_mock(process=MagicMock(exec=exec_mock))

        result = await discover_workspace_async(sandbox)

        assert result == "/home/daytona"

    @pytest.mark.anyio
    async def test_returns_none_when_both_fail(self):
        from sandbox.lifecycle.workspace import discover_workspace_async

        sandbox = MagicMock()
        sandbox.configure_mock(project_dir=None)
        exec_mock = AsyncMock(side_effect=RuntimeError("broken"))
        sandbox.configure_mock(process=MagicMock(exec=exec_mock))

        result = await discover_workspace_async(sandbox)

        assert result is None


class TestCodeIntelligenceRuntime:
    def test_sets_runtime_metadata_and_registers_provider_adapter(self, monkeypatch):
        import sandbox.lifecycle.workspace as workspace_module

        mock_context = ToolExecutionContextService(cwd="/tmp")
        mock_sandbox = MagicMock()
        registered = []

        def fake_register(sandbox_id):
            registered.append(sandbox_id)

        monkeypatch.setattr(
            workspace_module, "_register_provider_adapter_if_missing", fake_register
        )

        workspace_module.ensure_code_intelligence_runtime(
            mock_context,
            sandbox_id="sb-123",
            sandbox=mock_sandbox,
            workspace_root="/workspace",
        )

        assert mock_context["daytona_sandbox"] is mock_sandbox
        assert mock_context["repo_root"] == "/workspace"
        assert mock_context["exec_cwd"] == "/workspace"
        assert "ci_service" not in mock_context
        assert registered == ["sb-123"]

    def test_respects_existing_repo_root(self):
        from sandbox.lifecycle.workspace import ensure_code_intelligence_runtime

        mock_context = ToolExecutionContextService(cwd="/tmp", services={
            "repo_root": "/testbed",
            "ci_workspace_root": "/ci-root",
        })
        mock_sandbox = MagicMock()

        ensure_code_intelligence_runtime(
            mock_context,
            sandbox_id=None,
            sandbox=mock_sandbox,
            workspace_root="/workspace",
        )

        assert mock_context["repo_root"] == "/testbed"
        assert mock_context["exec_cwd"] == "/testbed"
        assert "ci_service" not in mock_context


class TestProviderAdapterRegistration:
    """Workspace context registers provider adapters without API handles."""

    def test_registers_daytona_provider_adapter(self):
        from sandbox.lifecycle.workspace import _register_provider_adapter_if_missing
        from sandbox.providers.daytona.adapter import DaytonaProviderAdapter
        from sandbox.providers.registry import dispose_adapter, get_adapter

        sandbox_id = "workspace-provider-registration"
        dispose_adapter(sandbox_id)

        _register_provider_adapter_if_missing(sandbox_id)

        assert isinstance(get_adapter(sandbox_id), DaytonaProviderAdapter)
        dispose_adapter(sandbox_id)

    def test_context_runtime_does_not_attach_legacy_api_handles(self, monkeypatch):
        from sandbox.lifecycle.workspace import ensure_code_intelligence_runtime
        from sandbox.providers.registry import dispose_adapter

        sandbox_id = "workspace-no-legacy-api"
        dispose_adapter(sandbox_id)
        mock_context = ToolExecutionContextService(cwd="/tmp")

        ensure_code_intelligence_runtime(
            mock_context,
            sandbox_id=sandbox_id,
            sandbox=MagicMock(),
            workspace_root="/workspace",
        )

        assert mock_context.get("sandbox_api") is None
        assert mock_context.get("sandbox_transport") is None
        dispose_adapter(sandbox_id)
