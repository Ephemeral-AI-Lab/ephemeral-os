"""Tests for sandbox.workspace."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, AsyncMock


class TestDiscoverWorkspace:
    def test_returns_project_dir_when_present(self):
        from sandbox.workspace import discover_workspace

        sandbox = MagicMock()
        sandbox.configure_mock(project_dir="/workspace/my-project")
        result = discover_workspace(sandbox)

        assert result == "/workspace/my-project"

    def test_falls_back_to_pwd(self):
        from sandbox.workspace import discover_workspace

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
        from sandbox.workspace import discover_workspace

        sandbox = MagicMock()
        sandbox.configure_mock(project_dir=None)
        exec_mock = MagicMock(side_effect=RuntimeError("broken"))
        sandbox.configure_mock(process=MagicMock(exec=exec_mock))

        result = discover_workspace(sandbox)

        assert result is None


class TestDiscoverWorkspaceAsync:
    @pytest.mark.anyio
    async def test_returns_project_dir_when_present(self):
        from sandbox.workspace import discover_workspace_async

        sandbox = MagicMock()
        sandbox.configure_mock(project_dir="/workspace/my-project")

        result = await discover_workspace_async(sandbox)

        assert result == "/workspace/my-project"

    @pytest.mark.anyio
    async def test_falls_back_to_pwd(self):
        from sandbox.workspace import discover_workspace_async

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
        from sandbox.workspace import discover_workspace_async

        sandbox = MagicMock()
        sandbox.configure_mock(project_dir=None)
        exec_mock = AsyncMock(side_effect=RuntimeError("broken"))
        sandbox.configure_mock(process=MagicMock(exec=exec_mock))

        result = await discover_workspace_async(sandbox)

        assert result is None


class TestInjectCodeIntelligence:
    def test_injects_ci_service(self, monkeypatch):
        from sandbox.workspace import inject_code_intelligence

        mock_context = MagicMock()
        mock_context.metadata = {}
        mock_sandbox = MagicMock()
        mock_svc = MagicMock()

        def fake_get_ci(sandbox_id, workspace_root, sandbox):
            return mock_svc

        import sys
        import types

        fake_ci_module = types.ModuleType("code_intelligence.routing.service")
        fake_ci_module.get_code_intelligence = fake_get_ci
        monkeypatch.setitem(sys.modules, "code_intelligence.routing.service", fake_ci_module)

        inject_code_intelligence(mock_context, "sb-123", mock_sandbox, "/workspace")

        assert mock_context.metadata["ci_service"] == mock_svc

    def test_skips_when_ci_import_fails(self, monkeypatch):
        from sandbox.workspace import inject_code_intelligence

        mock_context = MagicMock()
        mock_context.metadata = {}
        mock_sandbox = MagicMock()

        import sys

        monkeypatch.setitem(sys.modules, "code_intelligence.routing.service", None)

        inject_code_intelligence(mock_context, "sb-123", mock_sandbox, "/workspace")

        assert "ci_service" not in mock_context.metadata
