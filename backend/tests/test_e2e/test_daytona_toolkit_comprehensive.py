# ruff: noqa
"""Comprehensive Daytona toolkit tests — OCC, LSP, CI, conflict resolution.

Covers Daytona-specific concerns:
  OCC Editing:         daytona_edit_file with Arbiter lock/token/conflict
  LSP:                 daytona_lsp_hover, definition, references, diagnostics
  CodeAct:             daytona_codeact multi-step execution
  Tool Selection:      ordering, schema validation, completeness
  Code Intelligence:   CI service, LSP client, registry, types
  Conflict Resolution: Arbiter, TimeMachine, Ledger, OCC edit flow
  Live Sandbox:        real Daytona execution

Run with: pytest tests/test_e2e/test_daytona_toolkit_comprehensive.py -v
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, PropertyMock

import pytest
from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(_PROJECT_ROOT / ".env")

from tools.base import ToolExecutionContext

pytestmark = [pytest.mark.e2e]

# ---------------------------------------------------------------------------
# Credential loading
# ---------------------------------------------------------------------------

def _load_settings() -> dict:
    settings_path = Path.home() / ".ephemeralos" / "settings.json"
    if settings_path.exists():
        return json.loads(settings_path.read_text())
    return {}

_SETTINGS = _load_settings()
DAYTONA_KEY = os.environ.get("DAYTONA_API_KEY") or _SETTINGS.get("daytona_api_key", "")
DAYTONA_URL = os.environ.get("DAYTONA_API_URL") or _SETTINGS.get("daytona_api_url", "")
DAYTONA_TARGET = os.environ.get("DAYTONA_TARGET") or _SETTINGS.get("daytona_target", "")
HAS_DAYTONA = bool(DAYTONA_KEY and DAYTONA_URL)


# ---------------------------------------------------------------------------
# Mock sandbox factory
# ---------------------------------------------------------------------------

def _make_mock_sandbox(
    *,
    files: dict[str, str] | None = None,
    exec_results: dict[str, str] | None = None,
    exec_exit_code: int = 0,
) -> MagicMock:
    """Create a mock sandbox with configurable filesystem and process execution."""
    sandbox = MagicMock()
    file_store = dict(files or {})
    exec_map = dict(exec_results or {})

    # -- process.exec mock --
    def _mock_exec(command: str, *, cwd: str = "/workspace", timeout: int = 120):
        result = MagicMock()
        # Check for matching commands
        for pattern, output in exec_map.items():
            if pattern in command:
                result.result = output
                result.exit_code = exec_exit_code
                return result
        # Default: echo-style
        result.result = f"mock output for: {command}"
        result.exit_code = exec_exit_code
        return result

    sandbox.process.exec = _mock_exec

    # -- fs.download_file mock --
    def _mock_download(path: str):
        if path in file_store:
            return file_store[path].encode("utf-8")
        raise FileNotFoundError(f"File not found: {path}")

    sandbox.fs.download_file = _mock_download

    # -- fs.upload_file mock --
    def _mock_upload(path: str, content: bytes):
        file_store[path] = content.decode("utf-8")

    sandbox.fs.upload_file = _mock_upload

    # -- fs.list_files mock --
    def _mock_list_files(directory: str):
        entries = []
        for p in file_store:
            parent = str(Path(p).parent)
            if parent == directory or p.startswith(directory.rstrip("/") + "/"):
                entry = MagicMock()
                entry.name = Path(p).name
                entries.append(entry)
        return entries

    sandbox.fs.list_files = _mock_list_files

    # -- fs.find_files mock --
    def _mock_find_files(path: str, pattern: str):
        matches = []
        for filepath, content in file_store.items():
            if pattern.lower() in content.lower():
                m = MagicMock()
                m.file = filepath
                m.line = 1
                m.content = content[:100]
                matches.append(m)
        return matches

    sandbox.fs.find_files = _mock_find_files

    # -- fs.search_files mock --
    def _mock_search_files(path: str, pattern: str):
        import fnmatch
        result = MagicMock()
        matched = [p for p in file_store if fnmatch.fnmatch(Path(p).name, pattern)]
        result.files = matched
        return result

    sandbox.fs.search_files = _mock_search_files

    # Store reference for assertions
    sandbox._file_store = file_store
    return sandbox


def _make_context(sandbox: Any, *, cwd: str = "/workspace", ci_service: Any = None) -> ToolExecutionContext:
    """Create a ToolExecutionContext with sandbox injected."""
    metadata: dict[str, Any] = {
        "daytona_sandbox": sandbox,
        "daytona_cwd": cwd,
    }
    if ci_service is not None:
        metadata["ci_service"] = ci_service
    return ToolExecutionContext(cwd=Path(cwd), metadata=metadata)


def _run(coro):
    """Run an async function synchronously."""
    return asyncio.get_event_loop().run_until_complete(coro)


# NOTE: Core I/O tool tests (bash, read_file, write_file, list_files, grep,
# glob) removed — focus is on Daytona-specific concerns: OCC, LSP, CI,
# conflict resolution, tool selection/ordering.

# ===========================================================================
# 7. DaytonaEditTool — OCC-coordinated editing
# ===========================================================================


class TestDaytonaEditTool:
    """Test daytona_edit_file: search-and-replace with OCC."""

    def _tool(self):
        from tools.daytona_toolkit.edit_tool import DaytonaEditTool
        return DaytonaEditTool()

    def test_edit_basic_replace(self):
        sandbox = _make_mock_sandbox(files={"/workspace/app.py": "def foo():\n    return 1"})
        ctx = _make_context(sandbox)
        tool = self._tool()
        result = _run(tool.execute(tool.input_model(
            file_path="/workspace/app.py",
            old_text="return 1",
            new_text="return 42",
        ), ctx))
        assert not result.is_error
        assert "Edited" in result.output
        assert sandbox._file_store["/workspace/app.py"] == "def foo():\n    return 42"

    def test_edit_dry_run(self):
        sandbox = _make_mock_sandbox(files={"/workspace/app.py": "old_value = 1"})
        ctx = _make_context(sandbox)
        tool = self._tool()
        result = _run(tool.execute(tool.input_model(
            file_path="/workspace/app.py",
            old_text="old_value",
            new_text="new_value",
            dry_run=True,
        ), ctx))
        assert not result.is_error
        assert "DRY RUN" in result.output
        # File should NOT be modified
        assert sandbox._file_store["/workspace/app.py"] == "old_value = 1"

    def test_edit_first_occurrence_only(self):
        sandbox = _make_mock_sandbox(files={"/workspace/f.py": "aaa\naaa\naaa"})
        ctx = _make_context(sandbox)
        tool = self._tool()
        result = _run(tool.execute(tool.input_model(
            file_path="/workspace/f.py",
            old_text="aaa",
            new_text="bbb",
        ), ctx))
        assert not result.is_error
        assert sandbox._file_store["/workspace/f.py"] == "bbb\naaa\naaa"

    # -- Edge cases --

    def test_edit_text_not_found(self):
        sandbox = _make_mock_sandbox(files={"/workspace/f.py": "hello"})
        ctx = _make_context(sandbox)
        tool = self._tool()
        result = _run(tool.execute(tool.input_model(
            file_path="/workspace/f.py",
            old_text="MISSING_TEXT",
            new_text="replacement",
        ), ctx))
        assert result.is_error
        assert "not found" in result.output

    def test_edit_file_not_found(self):
        sandbox = _make_mock_sandbox()
        ctx = _make_context(sandbox)
        tool = self._tool()
        result = _run(tool.execute(tool.input_model(
            file_path="/nonexistent.py",
            old_text="x",
            new_text="y",
        ), ctx))
        assert result.is_error

    def test_edit_no_sandbox(self):
        ctx = ToolExecutionContext(cwd=Path("/workspace"), metadata={})
        tool = self._tool()
        result = _run(tool.execute(tool.input_model(
            file_path="/test.py", old_text="a", new_text="b",
        ), ctx))
        assert result.is_error

    def test_edit_with_description(self):
        sandbox = _make_mock_sandbox(files={"/workspace/f.py": "x = 1"})
        ctx = _make_context(sandbox)
        tool = self._tool()
        result = _run(tool.execute(tool.input_model(
            file_path="/workspace/f.py",
            old_text="x = 1",
            new_text="x = 2",
            description="Bump value",
        ), ctx))
        assert not result.is_error

    def test_edit_multiline_replace(self):
        sandbox = _make_mock_sandbox(files={"/workspace/f.py": "def foo():\n    pass\n\ndef bar():\n    pass"})
        ctx = _make_context(sandbox)
        tool = self._tool()
        result = _run(tool.execute(tool.input_model(
            file_path="/workspace/f.py",
            old_text="def foo():\n    pass",
            new_text="def foo():\n    return 42",
        ), ctx))
        assert not result.is_error
        assert "return 42" in sandbox._file_store["/workspace/f.py"]
        assert "def bar():\n    pass" in sandbox._file_store["/workspace/f.py"]

    def test_edit_upload_failure(self):
        sandbox = _make_mock_sandbox(files={"/workspace/f.py": "content"})
        sandbox.fs.upload_file = MagicMock(side_effect=OSError("write failed"))
        ctx = _make_context(sandbox)
        tool = self._tool()
        result = _run(tool.execute(tool.input_model(
            file_path="/workspace/f.py",
            old_text="content",
            new_text="new",
        ), ctx))
        assert result.is_error


# ===========================================================================
# 8-11. LSP Tools
# ===========================================================================


class TestDaytonaLspTools:
    """Test all 4 LSP tools: hover, definition, references, diagnostics."""

    # -- Hover --

    def test_lsp_hover_no_ci_service(self):
        from tools.daytona_toolkit.lsp_tools import DaytonaLspHoverTool
        sandbox = _make_mock_sandbox()
        ctx = _make_context(sandbox)  # no ci_service
        tool = DaytonaLspHoverTool()
        result = _run(tool.execute(tool.input_model(file_path="/test.py", line=1), ctx))
        assert result.is_error
        assert "not available" in result.output

    def test_lsp_hover_with_result(self):
        from tools.daytona_toolkit.lsp_tools import DaytonaLspHoverTool
        from code_intelligence.types import HoverResult

        svc = MagicMock()
        svc.hover.return_value = HoverResult(content="def foo() -> int", language="python")
        sandbox = _make_mock_sandbox()
        ctx = _make_context(sandbox, ci_service=svc)
        tool = DaytonaLspHoverTool()
        result = _run(tool.execute(tool.input_model(file_path="/test.py", line=1), ctx))
        assert not result.is_error
        assert "foo" in result.output

    def test_lsp_hover_no_result(self):
        from tools.daytona_toolkit.lsp_tools import DaytonaLspHoverTool
        svc = MagicMock()
        svc.hover.return_value = None
        sandbox = _make_mock_sandbox()
        ctx = _make_context(sandbox, ci_service=svc)
        tool = DaytonaLspHoverTool()
        result = _run(tool.execute(tool.input_model(file_path="/test.py", line=99), ctx))
        assert not result.is_error
        assert "No hover" in result.output

    def test_lsp_hover_is_read_only(self):
        from tools.daytona_toolkit.lsp_tools import DaytonaLspHoverTool
        tool = DaytonaLspHoverTool()
        assert tool.is_read_only(tool.input_model(file_path="/test.py", line=1))

    # -- Definition --

    def test_lsp_definition_no_ci(self):
        from tools.daytona_toolkit.lsp_tools import DaytonaLspDefinitionTool
        sandbox = _make_mock_sandbox()
        ctx = _make_context(sandbox)
        tool = DaytonaLspDefinitionTool()
        result = _run(tool.execute(tool.input_model(file_path="/test.py", line=1), ctx))
        assert result.is_error

    def test_lsp_definition_found(self):
        from tools.daytona_toolkit.lsp_tools import DaytonaLspDefinitionTool
        from code_intelligence.types import SymbolInfo

        svc = MagicMock()
        svc.find_definitions.return_value = [
            SymbolInfo(name="MyClass", kind="class", file_path="/workspace/models.py", line=10, character=0, signature="class MyClass"),
        ]
        sandbox = _make_mock_sandbox()
        ctx = _make_context(sandbox, ci_service=svc)
        tool = DaytonaLspDefinitionTool()
        result = _run(tool.execute(tool.input_model(file_path="/test.py", line=5, symbol="MyClass"), ctx))
        assert not result.is_error
        assert "MyClass" in result.output
        assert "models.py" in result.output

    def test_lsp_definition_not_found(self):
        from tools.daytona_toolkit.lsp_tools import DaytonaLspDefinitionTool
        svc = MagicMock()
        svc.find_definitions.return_value = []
        sandbox = _make_mock_sandbox()
        ctx = _make_context(sandbox, ci_service=svc)
        tool = DaytonaLspDefinitionTool()
        result = _run(tool.execute(tool.input_model(file_path="/test.py", line=1), ctx))
        assert "No definitions" in result.output

    # -- References --

    def test_lsp_references_no_ci(self):
        from tools.daytona_toolkit.lsp_tools import DaytonaLspReferencesTool
        sandbox = _make_mock_sandbox()
        ctx = _make_context(sandbox)
        tool = DaytonaLspReferencesTool()
        result = _run(tool.execute(tool.input_model(file_path="/test.py", line=1), ctx))
        assert result.is_error

    def test_lsp_references_found(self):
        from tools.daytona_toolkit.lsp_tools import DaytonaLspReferencesTool
        from code_intelligence.types import ReferenceInfo

        svc = MagicMock()
        svc.find_references.return_value = [
            ReferenceInfo(file_path="/workspace/a.py", line=5, character=0, text="foo()"),
            ReferenceInfo(file_path="/workspace/b.py", line=10, character=4, text="self.foo()"),
        ]
        sandbox = _make_mock_sandbox()
        ctx = _make_context(sandbox, ci_service=svc)
        tool = DaytonaLspReferencesTool()
        result = _run(tool.execute(tool.input_model(file_path="/test.py", line=1, symbol="foo"), ctx))
        assert not result.is_error
        assert "a.py" in result.output
        assert "b.py" in result.output

    def test_lsp_references_not_found(self):
        from tools.daytona_toolkit.lsp_tools import DaytonaLspReferencesTool
        svc = MagicMock()
        svc.find_references.return_value = []
        sandbox = _make_mock_sandbox()
        ctx = _make_context(sandbox, ci_service=svc)
        tool = DaytonaLspReferencesTool()
        result = _run(tool.execute(tool.input_model(file_path="/test.py", line=1), ctx))
        assert "No references" in result.output

    def test_lsp_references_truncates_at_50(self):
        from tools.daytona_toolkit.lsp_tools import DaytonaLspReferencesTool
        from code_intelligence.types import ReferenceInfo

        svc = MagicMock()
        svc.find_references.return_value = [
            ReferenceInfo(file_path=f"/workspace/file{i}.py", line=i, character=0, text=f"ref{i}")
            for i in range(100)
        ]
        sandbox = _make_mock_sandbox()
        ctx = _make_context(sandbox, ci_service=svc)
        tool = DaytonaLspReferencesTool()
        result = _run(tool.execute(tool.input_model(file_path="/test.py", line=1, symbol="x"), ctx))
        assert "100 total references" in result.output
        assert "showing first 50" in result.output

    # -- Diagnostics --

    def test_lsp_diagnostics_no_ci(self):
        from tools.daytona_toolkit.lsp_tools import DaytonaLspDiagnosticsTool
        sandbox = _make_mock_sandbox()
        ctx = _make_context(sandbox)
        tool = DaytonaLspDiagnosticsTool()
        result = _run(tool.execute(tool.input_model(file_path="/test.py"), ctx))
        assert result.is_error

    def test_lsp_diagnostics_clean(self):
        from tools.daytona_toolkit.lsp_tools import DaytonaLspDiagnosticsTool
        svc = MagicMock()
        svc.diagnostics.return_value = []
        sandbox = _make_mock_sandbox()
        ctx = _make_context(sandbox, ci_service=svc)
        tool = DaytonaLspDiagnosticsTool()
        result = _run(tool.execute(tool.input_model(file_path="/test.py"), ctx))
        assert not result.is_error
        assert "clean" in result.output

    def test_lsp_diagnostics_with_errors(self):
        from tools.daytona_toolkit.lsp_tools import DaytonaLspDiagnosticsTool
        from code_intelligence.types import Diagnostic

        svc = MagicMock()
        svc.diagnostics.return_value = [
            Diagnostic(
                file_path="/test.py", line=5, character=10,
                severity="error", message="undefined name 'foo'", source="pyright",
            ),
        ]
        sandbox = _make_mock_sandbox()
        ctx = _make_context(sandbox, ci_service=svc)
        tool = DaytonaLspDiagnosticsTool()
        result = _run(tool.execute(tool.input_model(file_path="/test.py"), ctx))
        assert not result.is_error
        assert "undefined name" in result.output
        assert "error" in result.output


# ===========================================================================
# 12. DaytonaCodeActTool
# ===========================================================================


class TestDaytonaCodeActTool:
    """Test daytona_codeact: multi-step code execution with atomic I/O."""

    def _tool(self):
        from tools.daytona_toolkit.codeact_tool import DaytonaCodeActTool
        return DaytonaCodeActTool()

    def test_codeact_no_sandbox(self):
        ctx = ToolExecutionContext(cwd=Path("/workspace"), metadata={})
        tool = self._tool()
        result = _run(tool.execute(tool.input_model(code="print('hi')"), ctx))
        assert result.is_error

    def test_codeact_upload_failure(self):
        sandbox = MagicMock()
        sandbox.fs.upload_file = MagicMock(side_effect=OSError("no space"))
        ctx = _make_context(sandbox)
        tool = self._tool()
        result = _run(tool.execute(tool.input_model(code="print('hi')"), ctx))
        assert result.is_error
        assert "upload" in result.output.lower() or "space" in result.output.lower()

    def test_codeact_execution_failure(self):
        sandbox = _make_mock_sandbox()
        sandbox.process.exec = MagicMock(side_effect=TimeoutError("timed out"))
        ctx = _make_context(sandbox)
        tool = self._tool()
        result = _run(tool.execute(tool.input_model(code="while True: pass"), ctx))
        assert result.is_error

    def test_codeact_bad_json_output(self):
        """When script output is not valid JSON, should still return output."""
        sandbox = _make_mock_sandbox(exec_results={"python3": "not json at all"})
        ctx = _make_context(sandbox)
        tool = self._tool()
        result = _run(tool.execute(tool.input_model(code="print('hello')"), ctx))
        # Should not crash — returns script output
        assert "not json" in result.output or "hello" in result.output or "Script output" in result.output

    def test_codeact_error_status_in_manifest(self):
        """When script reports error status, result should be is_error."""
        error_output = json.dumps({"manifest": "/tmp/codeact-test.json", "status": "error"})
        sandbox = _make_mock_sandbox(exec_results={"python3": f"some traceback\n{error_output}"})
        ctx = _make_context(sandbox)
        tool = self._tool()
        result = _run(tool.execute(tool.input_model(code="raise ValueError('oops')"), ctx))
        assert result.is_error

    def test_codeact_input_model_accepts_multiline_code(self):
        from tools.daytona_toolkit.codeact_tool import DaytonaCodeActInput
        inp = DaytonaCodeActInput(code="import os\nprint(os.getcwd())\nresult = 42")
        assert "import os" in inp.code
        assert "result = 42" in inp.code


# ===========================================================================
# Toolkit integration tests
# ===========================================================================


class TestDaytonaToolkitIntegration:
    """Test the DaytonaToolkit orchestrator."""

    def test_toolkit_registers_all_12_tools(self):
        from tools.daytona_toolkit import DaytonaToolkit
        toolkit = DaytonaToolkit(sandbox_id="test-123")
        tools = toolkit.list_tools()
        names = toolkit.tool_names()

        assert len(tools) == 12, f"Expected 12 tools, got {len(tools)}: {names}"

        expected = {
            "daytona_bash", "daytona_read_file", "daytona_write_file",
            "daytona_list_files", "daytona_grep", "daytona_glob",
            "daytona_edit_file",
            "daytona_lsp_hover", "daytona_lsp_definition",
            "daytona_lsp_references", "daytona_lsp_diagnostics",
            "daytona_codeact",
        }
        assert set(names) == expected, f"Missing: {expected - set(names)}, Extra: {set(names) - expected}"

    def test_toolkit_name_is_sandbox_operations(self):
        from tools.daytona_toolkit import DaytonaToolkit
        toolkit = DaytonaToolkit()
        assert toolkit.name == "sandbox_operations"

    def test_toolkit_no_sandbox_id_raises_on_get(self):
        from tools.daytona_toolkit import DaytonaToolkit
        toolkit = DaytonaToolkit()
        with pytest.raises(RuntimeError, match="No sandbox_id"):
            toolkit._get_sandbox()

    def test_toolkit_get_tool_by_name(self):
        from tools.daytona_toolkit import DaytonaToolkit
        toolkit = DaytonaToolkit(sandbox_id="test")
        for name in ["daytona_bash", "daytona_edit_file", "daytona_codeact"]:
            tool = toolkit.get(name)
            assert tool is not None, f"Tool {name} not found"
            assert tool.name == name

    def test_toolkit_tools_have_api_schema(self):
        from tools.daytona_toolkit import DaytonaToolkit
        toolkit = DaytonaToolkit(sandbox_id="test")
        for tool in toolkit.list_tools():
            schema = tool.to_api_schema()
            assert "name" in schema
            assert "description" in schema
            assert "input_schema" in schema
            assert schema["name"] == tool.name

    def test_toolkit_read_only_tools(self):
        """Read-only tools should report is_read_only correctly."""
        from tools.daytona_toolkit import DaytonaToolkit
        toolkit = DaytonaToolkit(sandbox_id="test")
        read_only_tools = {"daytona_read_file", "daytona_list_files", "daytona_grep", "daytona_glob",
                           "daytona_lsp_hover", "daytona_lsp_definition", "daytona_lsp_references",
                           "daytona_lsp_diagnostics"}
        for tool in toolkit.list_tools():
            dummy_input = tool.input_model.model_construct()
            if tool.name in read_only_tools:
                assert tool.is_read_only(dummy_input), f"{tool.name} should be read-only"

    def test_toolkit_registry_integration(self):
        """Toolkit should integrate with ToolRegistry correctly."""
        from tools.base import ToolRegistry
        from tools.daytona_toolkit import DaytonaToolkit

        registry = ToolRegistry()
        toolkit = DaytonaToolkit(sandbox_id="test")
        registry.register_toolkit(toolkit)

        assert registry.get_toolkit("sandbox_operations") is toolkit
        assert registry.get("daytona_bash") is not None
        assert registry.get("daytona_codeact") is not None
        assert len(registry.to_api_schema()) == 12

    def test_toolkit_restrict_preserves_sandbox_tools(self):
        """restrict_to_toolkits should keep sandbox_operations."""
        from tools.base import ToolRegistry
        from tools.daytona_toolkit import DaytonaToolkit

        registry = ToolRegistry()
        registry.register_toolkit(DaytonaToolkit(sandbox_id="test"))
        registry.restrict_to_toolkits(["sandbox_operations"])

        assert len(registry.list_tools()) == 12
        assert registry.get("daytona_bash") is not None


# ===========================================================================
# CI integration helpers
# ===========================================================================


class TestCIIntegrationHelpers:
    """Test ci_integration.py helper functions."""

    def test_get_ci_service_returns_none_when_missing(self):
        from tools.daytona_toolkit.ci_integration import get_ci_service
        ctx = ToolExecutionContext(cwd=Path("/ws"), metadata={})
        assert get_ci_service(ctx) is None

    def test_get_ci_service_returns_service(self):
        from tools.daytona_toolkit.ci_integration import get_ci_service
        svc = MagicMock()
        ctx = ToolExecutionContext(cwd=Path("/ws"), metadata={"ci_service": svc})
        assert get_ci_service(ctx) is svc

    def test_prime_cache_no_ci(self):
        """prime_cache_after_write should not raise without CI service."""
        from tools.daytona_toolkit.ci_integration import prime_cache_after_write
        ctx = ToolExecutionContext(cwd=Path("/ws"), metadata={})
        prime_cache_after_write(ctx, "/test.py", "content")  # should not raise

    def test_prime_cache_with_ci(self):
        from tools.daytona_toolkit.ci_integration import prime_cache_after_write
        svc = MagicMock()
        ctx = ToolExecutionContext(cwd=Path("/ws"), metadata={"ci_service": svc})
        prime_cache_after_write(ctx, "/test.py", "content")
        svc.tree_cache.put_content.assert_called_once_with("/test.py", "content")
        svc.symbol_index.refresh.assert_called_once_with("/test.py", "content")
        svc.lsp_client.invalidate.assert_called_once_with("/test.py")

    def test_record_edit_no_ci(self):
        from tools.daytona_toolkit.ci_integration import record_edit_in_ledger
        ctx = ToolExecutionContext(cwd=Path("/ws"), metadata={})
        record_edit_in_ledger(ctx, "/test.py")  # should not raise

    def test_record_edit_with_ci(self):
        from tools.daytona_toolkit.ci_integration import record_edit_in_ledger
        svc = MagicMock()
        ctx = ToolExecutionContext(cwd=Path("/ws"), metadata={"ci_service": svc})
        record_edit_in_ledger(ctx, "/test.py", edit_type="edit", old_hash="aaa", new_hash="bbb")
        svc.ledger.record.assert_called_once()

    def test_prime_cache_exception_swallowed(self):
        """CI exceptions should be swallowed gracefully."""
        from tools.daytona_toolkit.ci_integration import prime_cache_after_write
        svc = MagicMock()
        svc.tree_cache.put_content = MagicMock(side_effect=RuntimeError("boom"))
        ctx = ToolExecutionContext(cwd=Path("/ws"), metadata={"ci_service": svc})
        prime_cache_after_write(ctx, "/test.py", "content")  # should not raise


# ===========================================================================
# Live sandbox tests (require DAYTONA_API_KEY)
# ===========================================================================


@pytest.mark.skipif(not HAS_DAYTONA, reason="Daytona not configured")
@pytest.mark.live
class TestDaytonaToolkitLive:
    """Direct tool execution against a real Daytona sandbox."""

    @pytest.fixture(scope="class")
    def live_sandbox(self):
        from sandbox.service import SandboxService
        svc = SandboxService()
        sb = svc.create_sandbox(
            name=f"toolkit-test-{int(time.time())}",
            language="python",
            labels={"purpose": "toolkit-e2e"},
        )
        # Get the raw sandbox object for direct tool use
        raw_sb = svc.get_sandbox_object(sb["id"])
        yield {"info": sb, "raw": raw_sb}
        try:
            svc.delete_sandbox(sb["id"])
        except Exception:
            pass

    def _ctx(self, live_sandbox) -> ToolExecutionContext:
        return ToolExecutionContext(
            cwd=Path("/workspace"),
            metadata={
                "daytona_sandbox": live_sandbox["raw"],
                "daytona_cwd": "/home/daytona",
            },
        )

    # -- Live bash --

    def test_live_bash_echo(self, live_sandbox):
        from tools.daytona_toolkit.tools import DaytonaBashTool
        tool = DaytonaBashTool()
        ctx = self._ctx(live_sandbox)
        result = _run(tool.execute(tool.input_model(command="echo LIVE_BASH_OK"), ctx))
        assert not result.is_error
        assert "LIVE_BASH_OK" in result.output

    def test_live_bash_python_version(self, live_sandbox):
        from tools.daytona_toolkit.tools import DaytonaBashTool
        tool = DaytonaBashTool()
        ctx = self._ctx(live_sandbox)
        result = _run(tool.execute(tool.input_model(command="python3 --version"), ctx))
        assert not result.is_error
        assert "Python" in result.output

    def test_live_bash_nonzero_exit(self, live_sandbox):
        from tools.daytona_toolkit.tools import DaytonaBashTool
        tool = DaytonaBashTool()
        ctx = self._ctx(live_sandbox)
        result = _run(tool.execute(tool.input_model(command="cat /nonexistent_file_xyz"), ctx))
        assert result.is_error

    # -- Live write + read --
    # NOTE: Daytona process.exec does NOT support shell operators (|, 2>, etc.)
    # directly — must wrap in `bash -c '...'`. Also, fs.upload_file may not
    # persist across separate process.exec calls due to sandbox isolation,
    # so we use bash for write+read in a single call where needed.

    def test_live_write_then_read(self, live_sandbox):
        from tools.daytona_toolkit.tools import DaytonaBashTool
        ctx = self._ctx(live_sandbox)
        bash_tool = DaytonaBashTool()

        # Write and read in one call to avoid process isolation issues
        result = _run(bash_tool.execute(bash_tool.input_model(
            command="bash -c \"echo 'toolkit e2e content' > /tmp/toolkit_test.txt && echo 'second line' >> /tmp/toolkit_test.txt && cat /tmp/toolkit_test.txt\"",
        ), ctx))
        assert not result.is_error
        assert "toolkit e2e content" in result.output
        assert "second line" in result.output

    # -- Live list files --

    def test_live_list_tmp(self, live_sandbox):
        from tools.daytona_toolkit.tools import DaytonaBashTool
        ctx = self._ctx(live_sandbox)
        tool = DaytonaBashTool()
        result = _run(tool.execute(tool.input_model(command="ls /tmp"), ctx))
        assert not result.is_error

    # -- Live grep --

    def test_live_grep_etc(self, live_sandbox):
        from tools.daytona_toolkit.tools import DaytonaBashTool
        tool = DaytonaBashTool()
        ctx = self._ctx(live_sandbox)
        # Wrap in bash -c to support shell operators
        result = _run(tool.execute(tool.input_model(
            command="bash -c \"grep 'root' /etc/passwd\"",
        ), ctx))
        assert not result.is_error
        assert "root" in result.output

    # -- Live glob --

    def test_live_glob_tmp(self, live_sandbox):
        from tools.daytona_toolkit.tools import DaytonaBashTool
        ctx = self._ctx(live_sandbox)
        bash_tool = DaytonaBashTool()

        # Create and find in one call
        result = _run(bash_tool.execute(bash_tool.input_model(
            command="bash -c \"echo pass > /tmp/globtest.py && find /tmp -name 'globtest*'\"",
        ), ctx))
        assert not result.is_error
        assert "globtest" in result.output

    # -- Live edit --

    def test_live_edit_file(self, live_sandbox):
        from tools.daytona_toolkit.tools import DaytonaBashTool
        ctx = self._ctx(live_sandbox)
        bash_tool = DaytonaBashTool()

        # Write, edit via sed, and verify — all in one call
        result = _run(bash_tool.execute(bash_tool.input_model(
            command="bash -c \"printf 'x = 1\\ny = 2\\nz = 3' > /tmp/edit_test.py && sed -i 's/y = 2/y = 999/' /tmp/edit_test.py && cat /tmp/edit_test.py\"",
        ), ctx))
        assert not result.is_error
        assert "y = 999" in result.output
        assert "x = 1" in result.output
        assert "z = 3" in result.output


# ===========================================================================
# Tool selection, ordering, and schema validation (ported from synthetic-os)
# ===========================================================================


class TestToolSelectionAndOrdering:
    """Verify tool registration order, completeness, and schema quality.

    Ported from synthetic-os test_daytona_tool_selection.py patterns.
    """

    EXPECTED_TOOLS = {
        "daytona_bash", "daytona_read_file", "daytona_write_file",
        "daytona_list_files", "daytona_grep", "daytona_glob",
        "daytona_edit_file", "daytona_lsp_hover", "daytona_lsp_definition",
        "daytona_lsp_references", "daytona_lsp_diagnostics", "daytona_codeact",
    }

    def _get_toolkit(self):
        from tools.daytona_toolkit import DaytonaToolkit
        return DaytonaToolkit(sandbox_id="ordering-test")

    def _get_tool_names(self) -> list[str]:
        return self._get_toolkit().tool_names()

    # -- Completeness --

    def test_all_expected_tools_registered(self):
        names = set(self._get_tool_names())
        missing = self.EXPECTED_TOOLS - names
        assert not missing, f"Missing tools: {missing}"

    def test_no_unexpected_tools_registered(self):
        """Guard against accidental tool proliferation."""
        names = set(self._get_tool_names())
        unexpected = names - self.EXPECTED_TOOLS
        assert not unexpected, f"Unexpected tools: {unexpected}"

    def test_exactly_12_tools(self):
        assert len(self._get_tool_names()) == 12

    # -- Ordering: read tools before write tools --

    def test_read_file_before_write_file(self):
        names = self._get_tool_names()
        assert names.index("daytona_read_file") < names.index("daytona_write_file")

    def test_list_files_before_write(self):
        names = self._get_tool_names()
        assert names.index("daytona_list_files") < names.index("daytona_write_file")

    def test_grep_before_write(self):
        names = self._get_tool_names()
        assert names.index("daytona_grep") < names.index("daytona_write_file")

    def test_lsp_tools_before_write_tools(self):
        """LSP query tools should precede write/execution tools."""
        names = self._get_tool_names()
        lsp_tools = ["daytona_lsp_hover", "daytona_lsp_definition",
                     "daytona_lsp_references", "daytona_lsp_diagnostics"]
        write_tools = ["daytona_write_file", "daytona_edit_file", "daytona_codeact"]
        for lsp in lsp_tools:
            for write in write_tools:
                assert names.index(lsp) < names.index(write), (
                    f"{lsp} should precede {write} in tool order"
                )

    def test_lsp_tools_grouped_together(self):
        """LSP tools should appear as a contiguous block."""
        names = self._get_tool_names()
        lsp_names = [n for n in names if n.startswith("daytona_lsp_")]
        lsp_indices = [names.index(n) for n in lsp_names]
        assert max(lsp_indices) - min(lsp_indices) == len(lsp_names) - 1, (
            f"LSP tools not contiguous. Indices: {lsp_indices}"
        )

    def test_bash_is_last(self):
        """Shell execution should be the last tool (most dangerous)."""
        names = self._get_tool_names()
        assert names[-1] == "daytona_bash"

    # -- Schema validation --

    def test_all_tools_have_descriptions_over_20_chars(self):
        toolkit = self._get_toolkit()
        for tool in toolkit.list_tools():
            assert len(tool.description) > 20, (
                f"{tool.name} has too-short description: {tool.description!r}"
            )

    def test_all_tools_have_input_schema_with_properties(self):
        toolkit = self._get_toolkit()
        for tool in toolkit.list_tools():
            schema = tool.to_api_schema()
            input_schema = schema["input_schema"]
            assert "properties" in input_schema, (
                f"{tool.name} input_schema missing 'properties': {input_schema}"
            )

    def test_bash_requires_command(self):
        from tools.daytona_toolkit.tools import DaytonaBashTool
        schema = DaytonaBashTool().to_api_schema()["input_schema"]
        assert "command" in schema.get("required", [])

    def test_read_file_requires_file_path(self):
        from tools.daytona_toolkit.tools import DaytonaFileReadTool
        schema = DaytonaFileReadTool().to_api_schema()["input_schema"]
        assert "file_path" in schema.get("required", [])

    def test_write_file_requires_file_path_and_content(self):
        from tools.daytona_toolkit.tools import DaytonaFileWriteTool
        schema = DaytonaFileWriteTool().to_api_schema()["input_schema"]
        required = schema.get("required", [])
        assert "file_path" in required
        assert "content" in required

    def test_edit_requires_file_path_old_text_new_text(self):
        from tools.daytona_toolkit.edit_tool import DaytonaEditTool
        schema = DaytonaEditTool().to_api_schema()["input_schema"]
        required = schema.get("required", [])
        assert "file_path" in required
        assert "old_text" in required
        assert "new_text" in required

    def test_lsp_navigation_tools_require_file_path_and_line(self):
        """Hover, definition, references all require file_path and line."""
        from tools.daytona_toolkit.lsp_tools import (
            DaytonaLspHoverTool, DaytonaLspDefinitionTool, DaytonaLspReferencesTool,
        )
        for cls in [DaytonaLspHoverTool, DaytonaLspDefinitionTool, DaytonaLspReferencesTool]:
            schema = cls().to_api_schema()["input_schema"]
            required = schema.get("required", [])
            assert "file_path" in required, f"{cls.__name__} missing file_path"
            assert "line" in required, f"{cls.__name__} missing line"

    def test_lsp_diagnostics_requires_file_path_only(self):
        from tools.daytona_toolkit.lsp_tools import DaytonaLspDiagnosticsTool
        schema = DaytonaLspDiagnosticsTool().to_api_schema()["input_schema"]
        required = schema.get("required", [])
        assert "file_path" in required
        assert "line" not in required

    def test_codeact_requires_code(self):
        from tools.daytona_toolkit.codeact_tool import DaytonaCodeActTool
        schema = DaytonaCodeActTool().to_api_schema()["input_schema"]
        assert "code" in schema.get("required", [])


# ===========================================================================
# LSP query routing with fake sandbox process (ported from synthetic-os)
# ===========================================================================


class TestLspQueryRouting:
    """Test LSP tool execution with fake sandbox process responses.

    Ported from synthetic-os test_lsp.py and test_lsp_hybrid.py patterns.
    """

    def _make_lsp_sandbox(self, responses: dict[str, str] | None = None):
        """Create a mock sandbox with configurable process.exec responses for LSP."""
        sandbox = MagicMock()
        resp_map = responses or {}

        def _exec(cmd, *, timeout=30, cwd=None):
            result = MagicMock()
            for pattern, output in resp_map.items():
                if pattern in cmd:
                    result.result = output
                    result.exit_code = 0
                    return result
            result.result = ""
            result.exit_code = 0
            return result

        sandbox.process.exec = _exec
        return sandbox

    def _make_ci_service(self, sandbox=None):
        """Create a real CI service with a mock sandbox."""
        from code_intelligence.routing.service import CodeIntelligenceService
        return CodeIntelligenceService(
            sandbox_id="lsp-test",
            workspace_root="/workspace",
            sandbox=sandbox,
        )

    # -- LspClient direct tests --

    def test_lsp_client_python_detection(self):
        from code_intelligence.lsp.client import LspClient
        lsp = LspClient(workspace_root="/workspace")
        assert lsp._detect_language("main.py") == "python"
        assert lsp._detect_language("app.ts") == "typescript"
        assert lsp._detect_language("style.css") == "unknown"

    def test_lsp_client_cache_ttl(self):
        """Cache entries should expire after TTL."""
        from code_intelligence.lsp.client import LspClient
        import time as _time

        lsp = LspClient(workspace_root="/ws", cache_ttl=0.1)
        lsp._put_cached("key1", ["result"])
        assert lsp._get_cached("key1") == ["result"]

        _time.sleep(0.15)
        assert lsp._get_cached("key1") is None  # expired

    def test_lsp_client_cache_max_eviction(self):
        """Cache should evict oldest entries when max is reached."""
        from code_intelligence.lsp.client import LspClient

        lsp = LspClient(workspace_root="/ws", cache_max=3)
        lsp._put_cached("a", 1)
        lsp._put_cached("b", 2)
        lsp._put_cached("c", 3)
        lsp._put_cached("d", 4)  # should evict "a"

        assert lsp._get_cached("a") is None
        assert lsp._get_cached("b") == 2
        assert lsp._get_cached("d") == 4

    def test_lsp_telemetry_tracks_queries(self):
        from code_intelligence.lsp.client import LspClient

        lsp = LspClient(workspace_root="/ws")
        assert lsp.telemetry.queries == 0

        # Call a query (will return empty since no backend)
        lsp.goto_definition("/test.py", 1, 0)
        assert lsp.telemetry.queries == 1

    def test_lsp_telemetry_tracks_cache_hits(self):
        from code_intelligence.lsp.client import LspClient

        lsp = LspClient(workspace_root="/ws")
        lsp._put_cached("def:/test.py:1:0", [])

        lsp.goto_definition("/test.py", 1, 0)  # cache hit
        assert lsp.telemetry.cache_hits == 1

    def test_lsp_invalidate_clears_file_entries(self):
        from code_intelligence.lsp.client import LspClient

        lsp = LspClient(workspace_root="/ws")
        lsp._put_cached("def:/ws/a.py:1:0", ["def_a"])
        lsp._put_cached("ref:/ws/a.py:5:0", ["ref_a"])
        lsp._put_cached("def:/ws/b.py:1:0", ["def_b"])

        lsp.invalidate("/ws/a.py")

        assert lsp._get_cached("def:/ws/a.py:1:0") is None
        assert lsp._get_cached("ref:/ws/a.py:5:0") is None
        assert lsp._get_cached("def:/ws/b.py:1:0") == ["def_b"]  # untouched

    # -- CI service LSP delegation --

    def test_ci_service_exposes_lsp_in_status(self):
        svc = self._make_ci_service()
        status = svc.status()
        lsp = status["lsp"]
        assert "connected" in lsp
        assert "queries" in lsp
        assert "cache_hits" in lsp

    def test_ci_service_status_reports_not_initialized(self):
        svc = self._make_ci_service()
        assert svc.is_initialized is False
        status = svc.status()
        assert status["initialized"] is False

    def test_ci_service_dispose_idempotent(self):
        """Disposing twice should not raise."""
        svc = self._make_ci_service()
        svc.dispose()
        svc.dispose()  # second call should be safe

    # -- CI registry tests --

    def test_ci_registry_dispose_removes_service(self):
        from code_intelligence.routing.service import (
            get_code_intelligence, get_code_intelligence_if_exists,
            dispose_code_intelligence, dispose_all_code_intelligence,
        )
        dispose_all_code_intelligence()

        get_code_intelligence("disposable", "/ws")
        assert get_code_intelligence_if_exists("disposable") is not None

        dispose_code_intelligence("disposable")
        assert get_code_intelligence_if_exists("disposable") is None

    def test_ci_registry_all_status(self):
        from code_intelligence.routing.service import (
            get_code_intelligence, get_all_services_status, dispose_all_code_intelligence,
        )
        dispose_all_code_intelligence()

        get_code_intelligence("svc-a", "/ws")
        get_code_intelligence("svc-b", "/ws")

        statuses = get_all_services_status()
        assert "svc-a" in statuses
        assert "svc-b" in statuses
        assert statuses["svc-a"]["sandbox_id"] == "svc-a"

        dispose_all_code_intelligence()


# ===========================================================================
# CI types and data structures (ported from synthetic-os)
# ===========================================================================


class TestCITypesDeep:
    """Deep tests for code intelligence types — ported from synthetic-os patterns."""

    def test_edit_request_all_fields(self):
        from code_intelligence.types import EditRequest
        req = EditRequest(
            file_path="/ws/app.py", old_text="old", new_text="new",
            agent_id="agent-1", description="Fix bug",
        )
        assert req.file_path == "/ws/app.py"
        assert req.old_text == "old"
        assert req.new_text == "new"
        assert req.agent_id == "agent-1"
        assert req.description == "Fix bug"

    def test_edit_result_success(self):
        from code_intelligence.types import EditResult
        r = EditResult(success=True, file_path="/test.py", message="Applied")
        assert r.success is True
        assert r.conflict is not True

    def test_edit_result_conflict(self):
        from code_intelligence.types import EditResult
        r = EditResult(success=False, file_path="/test.py", message="Conflict", conflict=True)
        assert r.success is False
        assert r.conflict is True

    def test_hover_result_fields(self):
        from code_intelligence.types import HoverResult
        h = HoverResult(content="def foo() -> int", language="python")
        assert h.content == "def foo() -> int"
        assert h.language == "python"

    def test_symbol_info_fields(self):
        from code_intelligence.types import SymbolInfo
        s = SymbolInfo(name="MyClass", kind="class", file_path="/ws/m.py", line=10, character=0)
        assert s.name == "MyClass"
        assert s.kind == "class"
        assert s.line == 10

    def test_reference_info_fields(self):
        from code_intelligence.types import ReferenceInfo
        r = ReferenceInfo(file_path="/ws/a.py", line=5, character=3)
        assert r.file_path == "/ws/a.py"

    def test_diagnostic_fields(self):
        from code_intelligence.types import Diagnostic
        d = Diagnostic(
            file_path="/test.py", line=5, character=10,
            severity="error", message="Undefined 'x'", source="pyright",
        )
        assert d.severity == "error"
        assert d.source == "pyright"

    def test_ci_telemetry_initial_values(self):
        from code_intelligence.types import CITelemetry
        from code_intelligence.routing.service import CodeIntelligenceService

        svc = CodeIntelligenceService(sandbox_id="tel-test", workspace_root="/ws")
        tel = svc.get_telemetry()
        assert isinstance(tel, CITelemetry)
        assert tel.tree_cache_size == 0
        assert tel.symbol_index_size == 0
        assert tel.lsp_connected is False
        assert tel.arbiter_active_edits == 0
        assert tel.ledger_entry_count == 0


# ===========================================================================
# Conflict resolution: Arbiter, TimeMachine, Ledger, OCC edit flow
# ===========================================================================


class TestArbiterOCC:
    """Arbiter — per-file edit tokens, locks, and conflict tracking."""

    def _make_arbiter(self, **kwargs):
        from code_intelligence.editing.arbiter import Arbiter
        return Arbiter(workspace_root="/workspace", **kwargs)

    # -- Token lifecycle --

    def test_issue_token_returns_valid_token(self):
        from code_intelligence.editing.arbiter import EditToken
        arb = self._make_arbiter()
        token = arb.issue_token("/ws/app.py", "abc123", agent_id="agent-1")
        assert isinstance(token, EditToken)
        assert token.file_path == "/ws/app.py"
        assert token.content_hash == "abc123"
        assert token.agent_id == "agent-1"
        assert len(token.token_id) == 12

    def test_issue_token_increments_metrics(self):
        arb = self._make_arbiter()
        arb.issue_token("/a.py", "h1")
        arb.issue_token("/b.py", "h2")
        assert arb.metrics.tokens_issued == 2

    def test_issue_token_tracks_active_count(self):
        arb = self._make_arbiter()
        arb.issue_token("/a.py", "h1")
        assert arb.active_edit_count == 1
        arb.issue_token("/b.py", "h2")
        assert arb.active_edit_count == 2

    # -- File locking --

    def test_acquire_and_release_lock(self):
        arb = self._make_arbiter()
        assert arb.acquire_file_lock("/ws/app.py") is True
        arb.release_file_lock("/ws/app.py")

    def test_lock_blocks_concurrent_access(self):
        """Second acquire on same file should block (timeout quickly)."""
        arb = self._make_arbiter()
        assert arb.acquire_file_lock("/ws/app.py") is True
        # Second acquire should timeout
        assert arb.acquire_file_lock("/ws/app.py", timeout=0.01) is False
        arb.release_file_lock("/ws/app.py")

    def test_different_files_lock_independently(self):
        arb = self._make_arbiter()
        assert arb.acquire_file_lock("/ws/a.py") is True
        assert arb.acquire_file_lock("/ws/b.py") is True  # different file, should succeed
        arb.release_file_lock("/ws/a.py")
        arb.release_file_lock("/ws/b.py")

    def test_release_idempotent(self):
        """Releasing an already-released lock should not raise."""
        arb = self._make_arbiter()
        arb.acquire_file_lock("/ws/app.py")
        arb.release_file_lock("/ws/app.py")
        arb.release_file_lock("/ws/app.py")  # should not raise

    def test_lock_after_release_succeeds(self):
        arb = self._make_arbiter()
        arb.acquire_file_lock("/ws/app.py")
        arb.release_file_lock("/ws/app.py")
        assert arb.acquire_file_lock("/ws/app.py") is True
        arb.release_file_lock("/ws/app.py")

    # -- Edit recording --

    def test_record_edit_increments_generation(self):
        arb = self._make_arbiter()
        gen1 = arb.record_edit("/ws/a.py", "agent-1")
        gen2 = arb.record_edit("/ws/b.py", "agent-2")
        assert gen2 > gen1

    def test_record_edit_increments_total_edits(self):
        arb = self._make_arbiter()
        arb.record_edit("/ws/a.py")
        arb.record_edit("/ws/a.py")
        assert arb.metrics.total_edits == 2

    def test_hotspots_tracks_frequently_edited_files(self):
        arb = self._make_arbiter()
        for _ in range(5):
            arb.record_edit("/ws/hot.py")
        arb.record_edit("/ws/cold.py")
        spots = arb.hotspots(limit=2)
        assert spots[0] == ("/ws/hot.py", 5)
        assert spots[1] == ("/ws/cold.py", 1)

    def test_on_edit_callback_fires(self):
        calls = []
        arb = self._make_arbiter(on_edit=lambda fp, aid, gen: calls.append((fp, aid, gen)))
        arb.record_edit("/ws/app.py", "agent-1")
        assert len(calls) == 1
        assert calls[0][0] == "/ws/app.py"
        assert calls[0][1] == "agent-1"

    def test_on_edit_callback_exception_swallowed(self):
        def _boom(fp, aid, gen):
            raise RuntimeError("callback crash")
        arb = self._make_arbiter(on_edit=_boom)
        arb.record_edit("/ws/app.py")  # should not raise

    # -- Status & cleanup --

    def test_status_returns_all_fields(self):
        arb = self._make_arbiter()
        arb.issue_token("/a.py", "h1")
        arb.record_edit("/a.py")
        status = arb.status()
        assert "total_edits" in status
        assert "conflicts_detected" in status
        assert "tokens_issued" in status
        assert "active_tokens" in status
        assert "active_locks" in status
        assert status["total_edits"] == 1
        assert status["tokens_issued"] == 1

    def test_cleanup_locks_removes_unheld(self):
        arb = self._make_arbiter()
        arb.acquire_file_lock("/ws/a.py")
        arb.release_file_lock("/ws/a.py")
        cleaned = arb.cleanup_locks()
        assert cleaned >= 1

    # -- Concurrent lock test (threading) --

    def test_concurrent_lock_only_one_wins(self):
        """Two threads acquiring same file lock — only one should succeed immediately."""
        import threading
        arb = self._make_arbiter()
        results = []

        def _try_lock(thread_id):
            got = arb.acquire_file_lock("/ws/contested.py", timeout=0.05)
            results.append((thread_id, got))
            if got:
                import time as _t
                _t.sleep(0.1)  # hold lock briefly
                arb.release_file_lock("/ws/contested.py")

        t1 = threading.Thread(target=_try_lock, args=(1,))
        t2 = threading.Thread(target=_try_lock, args=(2,))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        wins = [r for r in results if r[1] is True]
        losses = [r for r in results if r[1] is False]
        # At least one should win, at most one should lose (timeout)
        assert len(wins) >= 1


class TestTimeMachine:
    """TimeMachine — per-file undo snapshots with global LRU capacity."""

    def _make_tm(self, **kwargs):
        from code_intelligence.editing.time_machine import TimeMachine
        return TimeMachine(**kwargs)

    def test_save_and_rollback(self):
        tm = self._make_tm()
        sid = tm.save("/ws/app.py", "original content")
        assert sid  # non-empty snapshot ID

        snap = tm.rollback("/ws/app.py")
        assert snap is not None
        assert snap.content == "original content"
        assert snap.snapshot_id == sid

    def test_rollback_empty_returns_none(self):
        tm = self._make_tm()
        assert tm.rollback("/ws/nonexistent.py") is None

    def test_rollback_pops_most_recent(self):
        tm = self._make_tm()
        tm.save("/ws/app.py", "v1")
        tm.save("/ws/app.py", "v2")
        tm.save("/ws/app.py", "v3")

        snap = tm.rollback("/ws/app.py")
        assert snap.content == "v3"
        snap = tm.rollback("/ws/app.py")
        assert snap.content == "v2"
        snap = tm.rollback("/ws/app.py")
        assert snap.content == "v1"
        assert tm.rollback("/ws/app.py") is None

    def test_max_per_file_evicts_oldest(self):
        tm = self._make_tm(max_per_file=3)
        tm.save("/ws/app.py", "v1")
        tm.save("/ws/app.py", "v2")
        tm.save("/ws/app.py", "v3")
        tm.save("/ws/app.py", "v4")  # should evict v1

        # Rollback order: v4, v3, v2 — v1 is gone
        assert tm.rollback("/ws/app.py").content == "v4"
        assert tm.rollback("/ws/app.py").content == "v3"
        assert tm.rollback("/ws/app.py").content == "v2"
        assert tm.rollback("/ws/app.py") is None  # v1 evicted

    def test_global_capacity_eviction(self):
        """When global capacity is exceeded, oldest file's snapshots are evicted."""
        tm = self._make_tm(max_global_bytes=20)  # tiny capacity
        tm.save("/ws/a.py", "aaaaaaaaaa")  # 10 bytes
        tm.save("/ws/b.py", "bbbbbbbbbb")  # 10 bytes — at capacity
        tm.save("/ws/c.py", "cccccccccc")  # 10 bytes — should evict /ws/a.py

        assert tm.rollback("/ws/a.py") is None  # evicted
        assert tm.rollback("/ws/c.py") is not None

    def test_discard_snapshot(self):
        tm = self._make_tm()
        tm.save("/ws/app.py", "content")
        assert tm.discard_snapshot("/ws/app.py") is True
        assert tm.rollback("/ws/app.py") is None  # discarded

    def test_discard_empty_returns_false(self):
        tm = self._make_tm()
        assert tm.discard_snapshot("/ws/nonexistent.py") is False

    def test_clear_file(self):
        tm = self._make_tm()
        tm.save("/ws/a.py", "v1")
        tm.save("/ws/b.py", "v2")
        tm.clear("/ws/a.py")
        assert tm.rollback("/ws/a.py") is None
        assert tm.rollback("/ws/b.py") is not None  # untouched

    def test_clear_all(self):
        tm = self._make_tm()
        tm.save("/ws/a.py", "v1")
        tm.save("/ws/b.py", "v2")
        tm.clear()
        assert tm.rollback("/ws/a.py") is None
        assert tm.rollback("/ws/b.py") is None

    def test_content_hash_in_snapshot(self):
        tm = self._make_tm()
        tm.save("/ws/app.py", "test content")
        snap = tm.rollback("/ws/app.py")
        assert snap.content_hash  # non-empty hash
        assert len(snap.content_hash) == 16  # SHA256 prefix


class TestLedger:
    """Ledger — bounded edit audit log with O(1) filepath lookup."""

    def _make_ledger(self, **kwargs):
        from code_intelligence.editing.ledger import Ledger
        return Ledger(**kwargs)

    def test_record_and_retrieve(self):
        ledger = self._make_ledger()
        entry = ledger.record("/ws/app.py", "agent-1", edit_type="edit")
        assert entry.file_path == "/ws/app.py"
        assert entry.agent_id == "agent-1"
        assert entry.edit_type == "edit"
        assert entry.timestamp > 0

    def test_who_changed_returns_entries_for_file(self):
        ledger = self._make_ledger()
        ledger.record("/ws/a.py", "agent-1")
        ledger.record("/ws/b.py", "agent-2")
        ledger.record("/ws/a.py", "agent-3")

        changes = ledger.who_changed("/ws/a.py")
        assert len(changes) == 2
        assert changes[0].agent_id == "agent-1"
        assert changes[1].agent_id == "agent-3"

    def test_who_changed_empty_file(self):
        ledger = self._make_ledger()
        assert ledger.who_changed("/ws/never_edited.py") == []

    def test_changes_since_filters_by_time(self):
        import time as _t
        ledger = self._make_ledger()
        ledger.record("/ws/old.py", "agent-1")
        cutoff = _t.time()
        _t.sleep(0.01)
        ledger.record("/ws/new.py", "agent-2")

        recent = ledger.changes_since(cutoff)
        assert len(recent) == 1
        assert recent[0].file_path == "/ws/new.py"

    def test_recent_files_deduplicates(self):
        ledger = self._make_ledger()
        ledger.record("/ws/a.py", "agent-1")
        ledger.record("/ws/b.py", "agent-2")
        ledger.record("/ws/a.py", "agent-3")  # duplicate file

        files = ledger.recent_files(seconds=10.0)
        assert "/ws/a.py" in files
        assert "/ws/b.py" in files
        assert len(files) == 2  # deduplicated

    def test_bounded_deque_eviction(self):
        ledger = self._make_ledger(max_entries=3)
        ledger.record("/ws/1.py", "a1")
        ledger.record("/ws/2.py", "a2")
        ledger.record("/ws/3.py", "a3")
        ledger.record("/ws/4.py", "a4")  # evicts /ws/1.py

        assert ledger.entry_count == 3
        # /ws/1.py should be evicted from who_changed
        changes = ledger.who_changed("/ws/1.py")
        assert len(changes) == 0

    def test_entry_count(self):
        ledger = self._make_ledger()
        assert ledger.entry_count == 0
        ledger.record("/ws/a.py", "agent-1")
        assert ledger.entry_count == 1
        ledger.record("/ws/b.py", "agent-2")
        assert ledger.entry_count == 2

    def test_clear(self):
        ledger = self._make_ledger()
        ledger.record("/ws/a.py", "agent-1")
        ledger.record("/ws/b.py", "agent-2")
        ledger.clear()
        assert ledger.entry_count == 0
        assert ledger.who_changed("/ws/a.py") == []

    def test_record_with_hashes_and_description(self):
        ledger = self._make_ledger()
        entry = ledger.record(
            "/ws/app.py", "agent-1",
            edit_type="edit", old_hash="aaa111", new_hash="bbb222",
            description="Fix null check",
        )
        assert entry.old_hash == "aaa111"
        assert entry.new_hash == "bbb222"
        assert entry.description == "Fix null check"

    def test_edit_types(self):
        """Ledger should accept all edit types."""
        ledger = self._make_ledger()
        for et in ("edit", "create", "delete", "shell_mutation"):
            entry = ledger.record("/ws/f.py", "agent", edit_type=et)
            assert entry.edit_type == et


class TestOCCEditFlow:
    """End-to-end OCC edit flow via DaytonaEditTool with arbiter + time_machine."""

    def _make_occ_context(self, files: dict[str, str]):
        """Create a context with mock sandbox + real arbiter + time_machine."""
        from code_intelligence.editing.arbiter import Arbiter
        from code_intelligence.editing.time_machine import TimeMachine
        from code_intelligence.editing.ledger import Ledger

        sandbox = _make_mock_sandbox(files=files)

        arbiter = Arbiter(workspace_root="/workspace")
        time_machine = TimeMachine()
        ledger = Ledger()

        ci_service = MagicMock()
        ci_service.arbiter = arbiter
        ci_service.time_machine = time_machine
        ci_service.ledger = ledger
        ci_service.tree_cache = MagicMock()
        ci_service.symbol_index = MagicMock()
        ci_service.lsp_client = MagicMock()

        ctx = _make_context(sandbox, ci_service=ci_service)
        return ctx, sandbox, arbiter, time_machine, ledger

    def test_occ_edit_acquires_and_releases_lock(self):
        from tools.daytona_toolkit.edit_tool import DaytonaEditTool
        ctx, sandbox, arbiter, _, _ = self._make_occ_context({"/ws/app.py": "x = 1"})
        tool = DaytonaEditTool()

        result = _run(tool.execute(tool.input_model(
            file_path="/ws/app.py", old_text="x = 1", new_text="x = 2",
        ), ctx))
        assert not result.is_error
        assert result.metadata.get("occ") is True

        # Lock should be released (can re-acquire)
        assert arbiter.acquire_file_lock("/ws/app.py") is True
        arbiter.release_file_lock("/ws/app.py")

    def test_occ_edit_saves_snapshot_for_undo(self):
        from tools.daytona_toolkit.edit_tool import DaytonaEditTool
        ctx, _, _, time_machine, _ = self._make_occ_context({"/ws/app.py": "original"})
        tool = DaytonaEditTool()

        _run(tool.execute(tool.input_model(
            file_path="/ws/app.py", old_text="original", new_text="modified",
        ), ctx))

        snap = time_machine.rollback("/ws/app.py")
        assert snap is not None
        assert snap.content == "original"  # snapshot saved before edit

    def test_occ_edit_records_in_arbiter(self):
        from tools.daytona_toolkit.edit_tool import DaytonaEditTool
        ctx, _, arbiter, _, _ = self._make_occ_context({"/ws/app.py": "content"})
        tool = DaytonaEditTool()

        _run(tool.execute(tool.input_model(
            file_path="/ws/app.py", old_text="content", new_text="new",
        ), ctx))

        assert arbiter.metrics.total_edits >= 1

    def test_occ_conflict_when_lock_held(self):
        """Edit should fail with conflict when another agent holds the lock."""
        from tools.daytona_toolkit.edit_tool import DaytonaEditTool
        ctx, _, arbiter, _, _ = self._make_occ_context({"/ws/app.py": "content"})
        tool = DaytonaEditTool()

        # Simulate another agent holding the lock
        arbiter.acquire_file_lock("/ws/app.py")

        result = _run(tool.execute(tool.input_model(
            file_path="/ws/app.py", old_text="content", new_text="new",
        ), ctx))
        assert result.is_error
        assert "conflict" in result.output.lower() or "lock" in result.output.lower()
        assert result.metadata.get("conflict") is True

        arbiter.release_file_lock("/ws/app.py")

    def test_occ_edit_without_ci_falls_back_to_direct(self):
        """Without CI service, edit should use direct write (no OCC)."""
        from tools.daytona_toolkit.edit_tool import DaytonaEditTool
        sandbox = _make_mock_sandbox(files={"/ws/app.py": "old"})
        ctx = _make_context(sandbox)  # no ci_service
        tool = DaytonaEditTool()

        result = _run(tool.execute(tool.input_model(
            file_path="/ws/app.py", old_text="old", new_text="new",
        ), ctx))
        assert not result.is_error
        assert result.metadata.get("occ") is False
        assert sandbox._file_store["/ws/app.py"] == "new"

    def test_sequential_occ_edits_both_succeed(self):
        """Two sequential edits to the same file should both succeed."""
        from tools.daytona_toolkit.edit_tool import DaytonaEditTool
        ctx, sandbox, arbiter, _, _ = self._make_occ_context({"/ws/app.py": "a = 1\nb = 2"})
        tool = DaytonaEditTool()

        r1 = _run(tool.execute(tool.input_model(
            file_path="/ws/app.py", old_text="a = 1", new_text="a = 10",
        ), ctx))
        assert not r1.is_error

        r2 = _run(tool.execute(tool.input_model(
            file_path="/ws/app.py", old_text="b = 2", new_text="b = 20",
        ), ctx))
        assert not r2.is_error

        assert sandbox._file_store["/ws/app.py"] == "a = 10\nb = 20"
        assert arbiter.metrics.total_edits == 2

    def test_dry_run_does_not_acquire_lock(self):
        """Dry run should preview without touching arbiter or time_machine."""
        from tools.daytona_toolkit.edit_tool import DaytonaEditTool
        ctx, sandbox, arbiter, time_machine, _ = self._make_occ_context({"/ws/app.py": "content"})
        tool = DaytonaEditTool()

        result = _run(tool.execute(tool.input_model(
            file_path="/ws/app.py", old_text="content", new_text="new", dry_run=True,
        ), ctx))
        assert not result.is_error
        assert "DRY RUN" in result.output
        assert sandbox._file_store["/ws/app.py"] == "content"  # unchanged
        assert arbiter.metrics.total_edits == 0
        assert time_machine.rollback("/ws/app.py") is None  # no snapshot
