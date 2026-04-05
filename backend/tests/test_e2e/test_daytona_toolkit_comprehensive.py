# ruff: noqa
"""Comprehensive Daytona toolkit tests — direct tool execution with 3x edge case coverage.

Tests all 12 tools in the sandbox_operations toolkit:
  Core I/O:     daytona_bash, daytona_read_file, daytona_write_file,
                daytona_list_files, daytona_grep, daytona_glob
  Editing:      daytona_edit_file (OCC-coordinated)
  LSP:          daytona_lsp_hover, daytona_lsp_definition,
                daytona_lsp_references, daytona_lsp_diagnostics
  CodeAct:      daytona_codeact

Two tiers:
  - Mock sandbox (no API keys needed) — edge cases, error paths, input validation
  - Live sandbox (requires DAYTONA_API_KEY) — real execution against Daytona

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


# ===========================================================================
# 1. DaytonaBashTool — Shell execution
# ===========================================================================


class TestDaytonaBashTool:
    """Test daytona_bash: shell execution in sandbox."""

    def _tool(self):
        from tools.daytona_toolkit.tools import DaytonaBashTool
        return DaytonaBashTool()

    # -- Happy path --

    def test_bash_simple_command(self):
        sandbox = _make_mock_sandbox(exec_results={"echo hello": "hello\n"})
        ctx = _make_context(sandbox)
        tool = self._tool()
        result = _run(tool.execute(tool.input_model(command="echo hello"), ctx))
        assert not result.is_error
        assert "hello" in result.output

    def test_bash_returns_exit_code_in_metadata(self):
        sandbox = _make_mock_sandbox(exec_results={"ls": "file1\nfile2"})
        ctx = _make_context(sandbox)
        tool = self._tool()
        result = _run(tool.execute(tool.input_model(command="ls"), ctx))
        assert result.metadata.get("exit_code") == 0

    def test_bash_custom_timeout(self):
        sandbox = _make_mock_sandbox(exec_results={"sleep": "done"})
        ctx = _make_context(sandbox)
        tool = self._tool()
        result = _run(tool.execute(tool.input_model(command="sleep 1", timeout=5), ctx))
        assert not result.is_error

    # -- Edge cases --

    def test_bash_nonzero_exit_code(self):
        sandbox = _make_mock_sandbox(exec_results={"false": "error"}, exec_exit_code=1)
        ctx = _make_context(sandbox)
        tool = self._tool()
        result = _run(tool.execute(tool.input_model(command="false"), ctx))
        assert result.is_error

    def test_bash_no_sandbox_in_context(self):
        ctx = ToolExecutionContext(cwd=Path("/workspace"), metadata={})
        tool = self._tool()
        result = _run(tool.execute(tool.input_model(command="echo test"), ctx))
        assert result.is_error
        assert "sandbox" in result.output.lower() or "No Daytona" in result.output

    def test_bash_exception_during_exec(self):
        sandbox = MagicMock()
        sandbox.process.exec = MagicMock(side_effect=RuntimeError("connection lost"))
        ctx = _make_context(sandbox)
        tool = self._tool()
        result = _run(tool.execute(tool.input_model(command="echo test"), ctx))
        assert result.is_error
        assert "connection lost" in result.output

    def test_bash_empty_output(self):
        sandbox = _make_mock_sandbox(exec_results={"true": ""})
        ctx = _make_context(sandbox)
        tool = self._tool()
        result = _run(tool.execute(tool.input_model(command="true"), ctx))
        assert not result.is_error

    def test_bash_long_output_truncated(self):
        long_output = "x" * 20000
        sandbox = _make_mock_sandbox(exec_results={"seq": long_output})
        ctx = _make_context(sandbox)
        tool = self._tool()
        result = _run(tool.execute(tool.input_model(command="seq 1 100000"), ctx))
        assert len(result.output) <= 10000  # truncated to _OUTPUT_MAX_CHARS + overhead
        assert "truncated" in result.output

    def test_bash_timeout_validation(self):
        """Timeout should be clamped between 1 and 600."""
        from tools.daytona_toolkit.tools import DaytonaBashInput
        with pytest.raises(Exception):
            DaytonaBashInput(command="echo test", timeout=0)
        with pytest.raises(Exception):
            DaytonaBashInput(command="echo test", timeout=700)


# ===========================================================================
# 2. DaytonaFileReadTool
# ===========================================================================


class TestDaytonaFileReadTool:
    """Test daytona_read_file: file reading with line ranges."""

    def _tool(self):
        from tools.daytona_toolkit.tools import DaytonaFileReadTool
        return DaytonaFileReadTool()

    def test_read_full_file(self):
        content = "line1\nline2\nline3\nline4\nline5"
        sandbox = _make_mock_sandbox(files={"/workspace/test.py": content})
        ctx = _make_context(sandbox)
        tool = self._tool()
        result = _run(tool.execute(tool.input_model(file_path="/workspace/test.py"), ctx))
        assert not result.is_error
        assert "line1" in result.output
        assert "line5" in result.output
        assert "5 lines total" in result.output

    def test_read_line_range(self):
        content = "\n".join(f"line{i}" for i in range(1, 11))
        sandbox = _make_mock_sandbox(files={"/workspace/big.py": content})
        ctx = _make_context(sandbox)
        tool = self._tool()
        result = _run(tool.execute(tool.input_model(file_path="/workspace/big.py", start_line=3, end_line=5), ctx))
        assert not result.is_error
        assert "line3" in result.output
        assert "line5" in result.output
        assert "Showing lines 3-5" in result.output

    def test_read_is_read_only(self):
        tool = self._tool()
        assert tool.is_read_only(tool.input_model(file_path="/test.py"))

    # -- Edge cases --

    def test_read_nonexistent_file(self):
        sandbox = _make_mock_sandbox()
        ctx = _make_context(sandbox)
        tool = self._tool()
        result = _run(tool.execute(tool.input_model(file_path="/nonexistent.py"), ctx))
        assert result.is_error

    def test_read_empty_file(self):
        sandbox = _make_mock_sandbox(files={"/workspace/empty.py": ""})
        ctx = _make_context(sandbox)
        tool = self._tool()
        result = _run(tool.execute(tool.input_model(file_path="/workspace/empty.py"), ctx))
        assert not result.is_error
        assert "0 lines total" in result.output

    def test_read_start_line_exceeds_file_length(self):
        sandbox = _make_mock_sandbox(files={"/workspace/short.py": "one\ntwo"})
        ctx = _make_context(sandbox)
        tool = self._tool()
        result = _run(tool.execute(tool.input_model(file_path="/workspace/short.py", start_line=100), ctx))
        assert not result.is_error  # should not error, just show empty range

    def test_read_no_sandbox(self):
        ctx = ToolExecutionContext(cwd=Path("/workspace"), metadata={})
        tool = self._tool()
        result = _run(tool.execute(tool.input_model(file_path="/test.py"), ctx))
        assert result.is_error

    def test_read_single_line_file(self):
        sandbox = _make_mock_sandbox(files={"/workspace/one.txt": "only line"})
        ctx = _make_context(sandbox)
        tool = self._tool()
        result = _run(tool.execute(tool.input_model(file_path="/workspace/one.txt"), ctx))
        assert not result.is_error
        assert "only line" in result.output
        assert "1 lines total" in result.output

    def test_read_binary_content_handled(self):
        """Binary-ish content should not crash the tool."""
        sandbox = _make_mock_sandbox(files={"/workspace/bin.dat": "data\x00\x01\x02rest"})
        ctx = _make_context(sandbox)
        tool = self._tool()
        result = _run(tool.execute(tool.input_model(file_path="/workspace/bin.dat"), ctx))
        assert not result.is_error


# ===========================================================================
# 3. DaytonaFileWriteTool
# ===========================================================================


class TestDaytonaFileWriteTool:
    """Test daytona_write_file: file creation and writing."""

    def _tool(self):
        from tools.daytona_toolkit.tools import DaytonaFileWriteTool
        return DaytonaFileWriteTool()

    def test_write_new_file(self):
        sandbox = _make_mock_sandbox()
        ctx = _make_context(sandbox)
        tool = self._tool()
        result = _run(tool.execute(tool.input_model(file_path="/workspace/new.py", content="print('hi')"), ctx))
        assert not result.is_error
        assert "Written" in result.output
        assert sandbox._file_store["/workspace/new.py"] == "print('hi')"

    def test_write_overwrite_existing(self):
        sandbox = _make_mock_sandbox(files={"/workspace/old.py": "old content"})
        ctx = _make_context(sandbox)
        tool = self._tool()
        result = _run(tool.execute(tool.input_model(file_path="/workspace/old.py", content="new content"), ctx))
        assert not result.is_error
        assert sandbox._file_store["/workspace/old.py"] == "new content"

    def test_write_reports_byte_count(self):
        sandbox = _make_mock_sandbox()
        ctx = _make_context(sandbox)
        tool = self._tool()
        content = "hello world"
        result = _run(tool.execute(tool.input_model(file_path="/workspace/f.txt", content=content), ctx))
        assert str(len(content.encode("utf-8"))) in result.output

    # -- Edge cases --

    def test_write_empty_content(self):
        sandbox = _make_mock_sandbox()
        ctx = _make_context(sandbox)
        tool = self._tool()
        result = _run(tool.execute(tool.input_model(file_path="/workspace/empty.txt", content=""), ctx))
        assert not result.is_error
        assert sandbox._file_store["/workspace/empty.txt"] == ""

    def test_write_unicode_content(self):
        sandbox = _make_mock_sandbox()
        ctx = _make_context(sandbox)
        tool = self._tool()
        content = "Hello World"
        result = _run(tool.execute(tool.input_model(file_path="/workspace/uni.txt", content=content), ctx))
        assert not result.is_error
        assert sandbox._file_store["/workspace/uni.txt"] == content

    def test_write_large_content(self):
        sandbox = _make_mock_sandbox()
        ctx = _make_context(sandbox)
        tool = self._tool()
        content = "x" * 100_000
        result = _run(tool.execute(tool.input_model(file_path="/workspace/big.txt", content=content), ctx))
        assert not result.is_error

    def test_write_upload_failure(self):
        sandbox = MagicMock()
        sandbox.fs.upload_file = MagicMock(side_effect=OSError("disk full"))
        ctx = _make_context(sandbox)
        tool = self._tool()
        result = _run(tool.execute(tool.input_model(file_path="/workspace/f.txt", content="data"), ctx))
        assert result.is_error
        assert "disk full" in result.output

    def test_write_no_sandbox(self):
        ctx = ToolExecutionContext(cwd=Path("/workspace"), metadata={})
        tool = self._tool()
        result = _run(tool.execute(tool.input_model(file_path="/test.py", content="x"), ctx))
        assert result.is_error

    def test_write_deeply_nested_path(self):
        sandbox = _make_mock_sandbox()
        ctx = _make_context(sandbox)
        tool = self._tool()
        result = _run(tool.execute(tool.input_model(file_path="/workspace/a/b/c/d/deep.py", content="deep"), ctx))
        assert not result.is_error


# ===========================================================================
# 4. DaytonaListFilesTool
# ===========================================================================


class TestDaytonaListFilesTool:
    """Test daytona_list_files: directory listing."""

    def _tool(self):
        from tools.daytona_toolkit.tools import DaytonaListFilesTool
        return DaytonaListFilesTool()

    def test_list_files_basic(self):
        sandbox = _make_mock_sandbox(files={
            "/workspace/a.py": "x",
            "/workspace/b.py": "y",
            "/workspace/sub/c.py": "z",
        })
        ctx = _make_context(sandbox)
        tool = self._tool()
        result = _run(tool.execute(tool.input_model(directory="/workspace"), ctx))
        assert not result.is_error
        assert "a.py" in result.output

    def test_list_files_is_read_only(self):
        tool = self._tool()
        assert tool.is_read_only(tool.input_model(directory="/workspace"))

    def test_list_default_directory_uses_cwd(self):
        sandbox = _make_mock_sandbox(files={"/workspace/test.py": "x"})
        ctx = _make_context(sandbox, cwd="/workspace")
        tool = self._tool()
        result = _run(tool.execute(tool.input_model(), ctx))
        assert not result.is_error

    # -- Edge cases --

    def test_list_empty_directory(self):
        sandbox = _make_mock_sandbox()
        ctx = _make_context(sandbox)
        tool = self._tool()
        result = _run(tool.execute(tool.input_model(directory="/empty"), ctx))
        assert not result.is_error
        assert "Empty directory" in result.output

    def test_list_files_exception(self):
        sandbox = MagicMock()
        sandbox.fs.list_files = MagicMock(side_effect=PermissionError("denied"))
        ctx = _make_context(sandbox)
        tool = self._tool()
        result = _run(tool.execute(tool.input_model(directory="/workspace"), ctx))
        assert result.is_error

    def test_list_files_no_sandbox(self):
        ctx = ToolExecutionContext(cwd=Path("/workspace"), metadata={})
        tool = self._tool()
        result = _run(tool.execute(tool.input_model(directory="/workspace"), ctx))
        assert result.is_error


# ===========================================================================
# 5. DaytonaGrepTool
# ===========================================================================


class TestDaytonaGrepTool:
    """Test daytona_grep: content search."""

    def _tool(self):
        from tools.daytona_toolkit.tools import DaytonaGrepTool
        return DaytonaGrepTool()

    def test_grep_finds_matches(self):
        sandbox = _make_mock_sandbox(files={
            "/workspace/a.py": "def hello():\n    pass",
            "/workspace/b.py": "import os\nhello_world = 1",
        })
        ctx = _make_context(sandbox)
        tool = self._tool()
        result = _run(tool.execute(tool.input_model(pattern="hello"), ctx))
        assert not result.is_error

    def test_grep_is_read_only(self):
        tool = self._tool()
        assert tool.is_read_only(tool.input_model(pattern="test"))

    def test_grep_default_path_uses_cwd(self):
        sandbox = _make_mock_sandbox(files={"/workspace/test.py": "searchme"})
        ctx = _make_context(sandbox, cwd="/workspace")
        tool = self._tool()
        result = _run(tool.execute(tool.input_model(pattern="searchme"), ctx))
        assert not result.is_error

    # -- Edge cases --

    def test_grep_no_matches(self):
        sandbox = _make_mock_sandbox(files={"/workspace/a.py": "nothing here"})
        ctx = _make_context(sandbox)
        tool = self._tool()
        result = _run(tool.execute(tool.input_model(pattern="ZZZZNOTFOUND"), ctx))
        assert not result.is_error
        assert "No matches" in result.output

    def test_grep_exception(self):
        sandbox = MagicMock()
        sandbox.fs.find_files = MagicMock(side_effect=RuntimeError("search failed"))
        ctx = _make_context(sandbox)
        tool = self._tool()
        result = _run(tool.execute(tool.input_model(pattern="test"), ctx))
        assert result.is_error

    def test_grep_no_sandbox(self):
        ctx = ToolExecutionContext(cwd=Path("/workspace"), metadata={})
        tool = self._tool()
        result = _run(tool.execute(tool.input_model(pattern="test"), ctx))
        assert result.is_error


# ===========================================================================
# 6. DaytonaGlobTool
# ===========================================================================


class TestDaytonaGlobTool:
    """Test daytona_glob: file pattern matching."""

    def _tool(self):
        from tools.daytona_toolkit.tools import DaytonaGlobTool
        return DaytonaGlobTool()

    def test_glob_finds_files(self):
        sandbox = _make_mock_sandbox(files={
            "/workspace/test.py": "x",
            "/workspace/app.py": "y",
            "/workspace/readme.md": "z",
        })
        ctx = _make_context(sandbox)
        tool = self._tool()
        result = _run(tool.execute(tool.input_model(pattern="*.py"), ctx))
        assert not result.is_error

    def test_glob_is_read_only(self):
        tool = self._tool()
        assert tool.is_read_only(tool.input_model(pattern="*.py"))

    # -- Edge cases --

    def test_glob_no_matches(self):
        sandbox = _make_mock_sandbox(files={"/workspace/a.py": "x"})
        ctx = _make_context(sandbox)
        tool = self._tool()
        result = _run(tool.execute(tool.input_model(pattern="*.rs"), ctx))
        assert not result.is_error
        assert "No files" in result.output

    def test_glob_search_files_exception_falls_back_to_shell(self):
        """When fs.search_files fails, glob should fall back to find command."""
        sandbox = _make_mock_sandbox(exec_results={"find": "/workspace/test.py"})
        sandbox.fs.search_files = MagicMock(side_effect=RuntimeError("not supported"))
        ctx = _make_context(sandbox)
        tool = self._tool()
        result = _run(tool.execute(tool.input_model(pattern="*.py"), ctx))
        assert not result.is_error

    def test_glob_no_sandbox(self):
        ctx = ToolExecutionContext(cwd=Path("/workspace"), metadata={})
        tool = self._tool()
        result = _run(tool.execute(tool.input_model(pattern="*.py"), ctx))
        assert result.is_error


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

    def test_live_write_then_read(self, live_sandbox):
        from tools.daytona_toolkit.tools import DaytonaFileWriteTool, DaytonaFileReadTool, DaytonaBashTool
        ctx = self._ctx(live_sandbox)

        # Write
        write_tool = DaytonaFileWriteTool()
        w_result = _run(write_tool.execute(write_tool.input_model(
            file_path="/tmp/toolkit_test.txt",
            content="toolkit e2e content\nsecond line",
        ), ctx))
        assert not w_result.is_error

        # Verify via bash (reliable across Daytona process isolation)
        bash_tool = DaytonaBashTool()
        b_result = _run(bash_tool.execute(bash_tool.input_model(command="cat /tmp/toolkit_test.txt"), ctx))
        assert "toolkit e2e content" in b_result.output
        assert "second line" in b_result.output

    # -- Live list files --

    def test_live_list_tmp(self, live_sandbox):
        from tools.daytona_toolkit.tools import DaytonaListFilesTool
        tool = DaytonaListFilesTool()
        ctx = self._ctx(live_sandbox)
        result = _run(tool.execute(tool.input_model(directory="/tmp"), ctx))
        assert not result.is_error

    # -- Live grep --

    def test_live_grep_etc(self, live_sandbox):
        from tools.daytona_toolkit.tools import DaytonaBashTool
        tool = DaytonaBashTool()
        ctx = self._ctx(live_sandbox)
        # Use bash grep as a proxy since sandbox fs.find_files may not be available
        result = _run(tool.execute(tool.input_model(command="grep -r 'root' /etc/passwd 2>/dev/null | head -5"), ctx))
        assert not result.is_error

    # -- Live glob --

    def test_live_glob_tmp(self, live_sandbox):
        from tools.daytona_toolkit.tools import DaytonaBashTool, DaytonaFileWriteTool
        ctx = self._ctx(live_sandbox)

        # Create a test file
        write_tool = DaytonaFileWriteTool()
        _run(write_tool.execute(write_tool.input_model(file_path="/tmp/globtest.py", content="pass"), ctx))

        # Find it via bash
        bash_tool = DaytonaBashTool()
        result = _run(bash_tool.execute(bash_tool.input_model(command="find /tmp -name 'globtest*' 2>/dev/null"), ctx))
        assert not result.is_error
        assert "globtest" in result.output

    # -- Live edit --

    def test_live_edit_file(self, live_sandbox):
        from tools.daytona_toolkit.tools import DaytonaFileWriteTool, DaytonaBashTool
        from tools.daytona_toolkit.edit_tool import DaytonaEditTool
        ctx = self._ctx(live_sandbox)

        # Write initial file
        write_tool = DaytonaFileWriteTool()
        _run(write_tool.execute(write_tool.input_model(
            file_path="/tmp/edit_test.py",
            content="x = 1\ny = 2\nz = 3",
        ), ctx))

        # Edit it
        edit_tool = DaytonaEditTool()
        result = _run(edit_tool.execute(edit_tool.input_model(
            file_path="/tmp/edit_test.py",
            old_text="y = 2",
            new_text="y = 999",
        ), ctx))
        assert not result.is_error

        # Verify via bash
        bash_tool = DaytonaBashTool()
        verify = _run(bash_tool.execute(bash_tool.input_model(command="cat /tmp/edit_test.py"), ctx))
        assert "y = 999" in verify.output
        assert "x = 1" in verify.output
        assert "z = 3" in verify.output
