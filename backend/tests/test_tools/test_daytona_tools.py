"""Unit tests for Daytona sandbox tools (daytona_bash, daytona_read_file, etc.).

All tests mock the Daytona SDK sandbox object so no real sandbox is needed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from tools.base import ToolExecutionContext, ToolResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_exec_response(result: str = "", exit_code: int = 0) -> MagicMock:
    resp = MagicMock()
    resp.result = result
    resp.exit_code = exit_code
    return resp


def _make_sandbox(
    *,
    exec_response: MagicMock | None = None,
    download_content: bytes = b"",
    upload_side_effect: Exception | None = None,
    list_files_result: list[Any] | None = None,
    find_files_result: list[Any] | None = None,
    search_files_result: Any = None,
) -> MagicMock:
    """Build a mock sandbox with async process/fs methods."""
    sandbox = MagicMock()

    # process.exec
    sandbox.process.exec = AsyncMock(return_value=exec_response or _make_exec_response())

    # fs.download_file
    sandbox.fs.download_file = AsyncMock(return_value=download_content)

    # fs.upload_file
    if upload_side_effect:
        sandbox.fs.upload_file = AsyncMock(side_effect=upload_side_effect)
    else:
        sandbox.fs.upload_file = AsyncMock()

    # fs.list_files
    sandbox.fs.list_files = AsyncMock(return_value=list_files_result or [])

    # fs.find_files
    sandbox.fs.find_files = AsyncMock(return_value=find_files_result or [])

    # fs.search_files
    if search_files_result is not None:
        sandbox.fs.search_files = AsyncMock(return_value=search_files_result)
    else:
        sr = MagicMock()
        sr.files = []
        sandbox.fs.search_files = AsyncMock(return_value=sr)

    return sandbox


def _make_context(sandbox: MagicMock, cwd: str = "/workspace") -> ToolExecutionContext:
    return ToolExecutionContext(
        cwd=Path(cwd),
        metadata={"daytona_sandbox": sandbox, "daytona_cwd": cwd},
    )


def _make_context_no_sandbox(cwd: str = "/workspace") -> ToolExecutionContext:
    return ToolExecutionContext(cwd=Path(cwd), metadata={})


# ---------------------------------------------------------------------------
# daytona_bash
# ---------------------------------------------------------------------------

class TestDaytonaBash:

    @pytest.mark.asyncio
    async def test_success(self):
        from tools.daytona_toolkit.tools import daytona_bash

        sandbox = _make_sandbox(exec_response=_make_exec_response("hello world", 0))
        ctx = _make_context(sandbox)

        result = await daytona_bash._entrypoint("echo hello", context=ctx)

        assert not result.is_error
        data = json.loads(result.output)
        assert data["stdout"] == "hello world"
        assert data["exit_code"] == 0
        sandbox.process.exec.assert_awaited_once_with("echo hello", cwd="/workspace", timeout=120)

    @pytest.mark.asyncio
    async def test_nonzero_exit_code(self):
        from tools.daytona_toolkit.tools import daytona_bash

        sandbox = _make_sandbox(exec_response=_make_exec_response("not found", 1))
        ctx = _make_context(sandbox)

        result = await daytona_bash._entrypoint("ls /nope", context=ctx)

        assert result.is_error
        data = json.loads(result.output)
        assert data["exit_code"] == 1
        assert result.metadata["exit_code"] == 1

    @pytest.mark.asyncio
    async def test_custom_timeout(self):
        from tools.daytona_toolkit.tools import daytona_bash

        sandbox = _make_sandbox(exec_response=_make_exec_response("ok", 0))
        ctx = _make_context(sandbox)

        await daytona_bash._entrypoint("sleep 5", timeout=30, context=ctx)

        sandbox.process.exec.assert_awaited_once_with("sleep 5", cwd="/workspace", timeout=30)

    @pytest.mark.asyncio
    async def test_exec_exception(self):
        from tools.daytona_toolkit.tools import daytona_bash

        sandbox = _make_sandbox()
        sandbox.process.exec = AsyncMock(side_effect=RuntimeError("connection lost"))
        ctx = _make_context(sandbox)

        result = await daytona_bash._entrypoint("echo hi", context=ctx)

        assert result.is_error
        assert "connection lost" in result.output

    @pytest.mark.asyncio
    async def test_no_sandbox_in_context(self):
        """_get_sandbox raises before the try/except, so RuntimeError propagates."""
        from tools.daytona_toolkit.tools import daytona_bash

        ctx = _make_context_no_sandbox()

        with pytest.raises(RuntimeError, match="No Daytona sandbox"):
            await daytona_bash._entrypoint("echo hi", context=ctx)

    @pytest.mark.asyncio
    async def test_truncates_long_output(self):
        from tools.daytona_toolkit.tools import daytona_bash, _OUTPUT_MAX_CHARS

        long_text = "x" * (_OUTPUT_MAX_CHARS + 1000)
        sandbox = _make_sandbox(exec_response=_make_exec_response(long_text, 0))
        ctx = _make_context(sandbox)

        result = await daytona_bash._entrypoint("cat bigfile", context=ctx)

        data = json.loads(result.output)
        assert len(data["stdout"]) < len(long_text)
        assert "truncated" in data["stdout"]

    @pytest.mark.asyncio
    async def test_empty_output(self):
        from tools.daytona_toolkit.tools import daytona_bash

        sandbox = _make_sandbox(exec_response=_make_exec_response("", 0))
        ctx = _make_context(sandbox)

        result = await daytona_bash._entrypoint("true", context=ctx)

        assert not result.is_error
        data = json.loads(result.output)
        assert data["stdout"] == ""
        assert data["exit_code"] == 0

    @pytest.mark.asyncio
    async def test_none_result(self):
        from tools.daytona_toolkit.tools import daytona_bash

        resp = _make_exec_response("", 0)
        resp.result = None
        sandbox = _make_sandbox(exec_response=resp)
        ctx = _make_context(sandbox)

        result = await daytona_bash._entrypoint("true", context=ctx)

        assert not result.is_error
        data = json.loads(result.output)
        assert data["stdout"] == ""


# ---------------------------------------------------------------------------
# daytona_read_file
# ---------------------------------------------------------------------------

class TestDaytonaReadFile:

    @pytest.mark.asyncio
    async def test_read_full_file(self):
        from tools.daytona_toolkit.tools import daytona_read_file

        content = b"line1\nline2\nline3"
        sandbox = _make_sandbox(download_content=content)
        ctx = _make_context(sandbox)

        result = await daytona_read_file._entrypoint("/workspace/test.py", context=ctx)

        assert not result.is_error
        data = json.loads(result.output)
        assert data["total_lines"] == 3
        assert data["start_line"] == 1
        assert data["end_line"] == 3
        assert "line1" in data["content"]
        assert "line3" in data["content"]

    @pytest.mark.asyncio
    async def test_read_line_range(self):
        from tools.daytona_toolkit.tools import daytona_read_file

        content = b"a\nb\nc\nd\ne"
        sandbox = _make_sandbox(download_content=content)
        ctx = _make_context(sandbox)

        result = await daytona_read_file._entrypoint("/workspace/f.txt", start_line=2, end_line=4, context=ctx)

        data = json.loads(result.output)
        assert data["start_line"] == 2
        assert data["end_line"] == 4
        assert "b" in data["content"]
        assert "d" in data["content"]
        # line 'a' (line 1) should not appear as a numbered line
        assert "   1:" not in data["content"]

    @pytest.mark.asyncio
    async def test_read_file_not_found(self):
        from tools.daytona_toolkit.tools import daytona_read_file

        sandbox = _make_sandbox()
        sandbox.fs.download_file = AsyncMock(side_effect=FileNotFoundError("no such file"))
        ctx = _make_context(sandbox)

        result = await daytona_read_file._entrypoint("/workspace/missing.py", context=ctx)

        assert result.is_error
        assert "no such file" in result.output

    @pytest.mark.asyncio
    async def test_read_string_content(self):
        """download_file might return a string instead of bytes."""
        from tools.daytona_toolkit.tools import daytona_read_file

        sandbox = _make_sandbox()
        sandbox.fs.download_file = AsyncMock(return_value="hello\nworld")
        ctx = _make_context(sandbox)

        result = await daytona_read_file._entrypoint("/workspace/f.txt", context=ctx)

        assert not result.is_error
        data = json.loads(result.output)
        assert data["total_lines"] == 2


# ---------------------------------------------------------------------------
# daytona_write_file
# ---------------------------------------------------------------------------

class TestDaytonaWriteFile:

    @pytest.mark.asyncio
    async def test_write_success(self):
        from tools.daytona_toolkit.tools import daytona_write_file

        sandbox = _make_sandbox()
        ctx = _make_context(sandbox)

        result = await daytona_write_file._entrypoint("/workspace/new.txt", "hello", context=ctx)

        assert not result.is_error
        data = json.loads(result.output)
        assert data["file_path"] == "/workspace/new.txt"
        assert data["bytes_written"] == 5
        sandbox.fs.upload_file.assert_awaited_once_with("/workspace/new.txt", b"hello")

    @pytest.mark.asyncio
    async def test_write_failure(self):
        from tools.daytona_toolkit.tools import daytona_write_file

        sandbox = _make_sandbox(upload_side_effect=OSError("disk full"))
        ctx = _make_context(sandbox)

        result = await daytona_write_file._entrypoint("/workspace/f.txt", "data", context=ctx)

        assert result.is_error
        assert "disk full" in result.output

    @pytest.mark.asyncio
    async def test_write_unicode(self):
        from tools.daytona_toolkit.tools import daytona_write_file

        sandbox = _make_sandbox()
        ctx = _make_context(sandbox)
        text = "cafe\u0301 \u2603"  # café ☃

        result = await daytona_write_file._entrypoint("/workspace/uni.txt", text, context=ctx)

        assert not result.is_error
        data = json.loads(result.output)
        assert data["bytes_written"] == len(text.encode("utf-8"))


# ---------------------------------------------------------------------------
# daytona_list_files
# ---------------------------------------------------------------------------

class TestDaytonaListFiles:

    @pytest.mark.asyncio
    async def test_list_files_success(self):
        from tools.daytona_toolkit.tools import daytona_list_files

        entry1 = MagicMock()
        entry1.name = "file_a.py"
        entry2 = MagicMock()
        entry2.name = "file_b.py"
        sandbox = _make_sandbox(list_files_result=[entry1, entry2])
        ctx = _make_context(sandbox)

        result = await daytona_list_files._entrypoint("/workspace/src", context=ctx)

        assert not result.is_error
        data = json.loads(result.output)
        assert data["directory"] == "/workspace/src"
        assert data["entries"] == ["file_a.py", "file_b.py"]

    @pytest.mark.asyncio
    async def test_list_files_default_cwd(self):
        from tools.daytona_toolkit.tools import daytona_list_files

        sandbox = _make_sandbox(list_files_result=[])
        ctx = _make_context(sandbox, cwd="/home/user")

        result = await daytona_list_files._entrypoint(context=ctx)

        assert not result.is_error
        sandbox.fs.list_files.assert_awaited_once_with("/home/user")

    @pytest.mark.asyncio
    async def test_list_files_error(self):
        from tools.daytona_toolkit.tools import daytona_list_files

        sandbox = _make_sandbox()
        sandbox.fs.list_files = AsyncMock(side_effect=RuntimeError("timeout"))
        ctx = _make_context(sandbox)

        result = await daytona_list_files._entrypoint("/workspace", context=ctx)

        assert result.is_error
        assert "timeout" in result.output

    @pytest.mark.asyncio
    async def test_list_files_none_response(self):
        from tools.daytona_toolkit.tools import daytona_list_files

        sandbox = _make_sandbox()
        sandbox.fs.list_files = AsyncMock(return_value=None)
        ctx = _make_context(sandbox)

        result = await daytona_list_files._entrypoint("/workspace", context=ctx)

        assert not result.is_error
        data = json.loads(result.output)
        assert data["entries"] == []


# ---------------------------------------------------------------------------
# daytona_grep
# ---------------------------------------------------------------------------

class TestDaytonaGrep:

    @pytest.mark.asyncio
    async def test_grep_with_matches(self):
        from tools.daytona_toolkit.tools import daytona_grep

        match = MagicMock()
        match.file = "/workspace/foo.py"
        match.line = 10
        match.content = "import os"
        sandbox = _make_sandbox(find_files_result=[match])
        ctx = _make_context(sandbox)

        result = await daytona_grep._entrypoint("import os", context=ctx)

        assert not result.is_error
        data = json.loads(result.output)
        assert data["total_matches"] == 1
        assert data["matches"][0]["file"] == "/workspace/foo.py"
        assert data["matches"][0]["line"] == 10

    @pytest.mark.asyncio
    async def test_grep_no_matches(self):
        from tools.daytona_toolkit.tools import daytona_grep

        sandbox = _make_sandbox(find_files_result=[])
        ctx = _make_context(sandbox)

        result = await daytona_grep._entrypoint("nonexistent", context=ctx)

        assert not result.is_error
        data = json.loads(result.output)
        assert data["total_matches"] == 0
        assert data["matches"] == []

    @pytest.mark.asyncio
    async def test_grep_error(self):
        from tools.daytona_toolkit.tools import daytona_grep

        sandbox = _make_sandbox()
        sandbox.fs.find_files = AsyncMock(side_effect=RuntimeError("search failed"))
        ctx = _make_context(sandbox)

        result = await daytona_grep._entrypoint("pattern", context=ctx)

        assert result.is_error
        assert "search failed" in result.output

    @pytest.mark.asyncio
    async def test_grep_caps_at_500(self):
        from tools.daytona_toolkit.tools import daytona_grep

        matches = []
        for i in range(600):
            m = MagicMock()
            m.file = f"/workspace/f{i}.py"
            m.line = i
            m.content = f"line {i}"
            matches.append(m)
        sandbox = _make_sandbox(find_files_result=matches)
        ctx = _make_context(sandbox)

        result = await daytona_grep._entrypoint("pattern", context=ctx)

        data = json.loads(result.output)
        assert len(data["matches"]) == 500
        assert data["total_matches"] == 600


# ---------------------------------------------------------------------------
# daytona_glob
# ---------------------------------------------------------------------------

class TestDaytonaGlob:

    @pytest.mark.asyncio
    async def test_glob_success(self):
        from tools.daytona_toolkit.tools import daytona_glob

        sr = MagicMock()
        sr.files = ["/workspace/a.py", "/workspace/b.py"]
        sandbox = _make_sandbox(search_files_result=sr)
        ctx = _make_context(sandbox)

        result = await daytona_glob._entrypoint("*.py", context=ctx)

        assert not result.is_error
        data = json.loads(result.output)
        assert data["total_files"] == 2
        assert "/workspace/a.py" in data["files"]

    @pytest.mark.asyncio
    async def test_glob_fallback_to_find(self):
        """When search_files raises, falls back to process.exec with find."""
        from tools.daytona_toolkit.tools import daytona_glob

        sandbox = _make_sandbox()
        sandbox.fs.search_files = AsyncMock(side_effect=RuntimeError("not supported"))
        sandbox.process.exec = AsyncMock(
            return_value=_make_exec_response("/workspace/x.py\n/workspace/y.py", 0)
        )
        ctx = _make_context(sandbox)

        result = await daytona_glob._entrypoint("*.py", context=ctx)

        assert not result.is_error
        data = json.loads(result.output)
        assert data["total_files"] == 2
        sandbox.process.exec.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_glob_fallback_also_fails(self):
        from tools.daytona_toolkit.tools import daytona_glob

        sandbox = _make_sandbox()
        sandbox.fs.search_files = AsyncMock(side_effect=RuntimeError("fail1"))
        sandbox.process.exec = AsyncMock(side_effect=RuntimeError("fail2"))
        ctx = _make_context(sandbox)

        result = await daytona_glob._entrypoint("*.py", context=ctx)

        assert result.is_error
        assert "fail2" in result.output

    @pytest.mark.asyncio
    async def test_glob_caps_at_500(self):
        from tools.daytona_toolkit.tools import daytona_glob

        sr = MagicMock()
        sr.files = [f"/workspace/f{i}.py" for i in range(700)]
        sandbox = _make_sandbox(search_files_result=sr)
        ctx = _make_context(sandbox)

        result = await daytona_glob._entrypoint("*.py", context=ctx)

        data = json.loads(result.output)
        assert len(data["files"]) == 500
        assert data["total_files"] == 700


# ---------------------------------------------------------------------------
# daytona_edit_file
# ---------------------------------------------------------------------------

class TestDaytonaEditFile:

    @pytest.mark.asyncio
    async def test_edit_success(self):
        from tools.daytona_toolkit.edit_tool import daytona_edit_file

        sandbox = _make_sandbox(download_content=b"hello world")
        ctx = _make_context(sandbox)

        result = await daytona_edit_file._entrypoint(
            file_path="/workspace/f.txt",
            old_text="hello",
            new_text="goodbye",
            context=ctx,
        )

        assert not result.is_error
        data = json.loads(result.output)
        assert data["status"] == "edited"
        sandbox.fs.upload_file.assert_awaited_once()
        written = sandbox.fs.upload_file.call_args[0][1]
        assert b"goodbye world" == written

    @pytest.mark.asyncio
    async def test_edit_text_not_found(self):
        from tools.daytona_toolkit.edit_tool import daytona_edit_file

        sandbox = _make_sandbox(download_content=b"hello world")
        ctx = _make_context(sandbox)

        result = await daytona_edit_file._entrypoint(
            file_path="/workspace/f.txt",
            old_text="MISSING",
            new_text="new",
            context=ctx,
        )

        assert result.is_error
        assert "not found" in result.output

    @pytest.mark.asyncio
    async def test_edit_no_sandbox(self):
        from tools.daytona_toolkit.edit_tool import daytona_edit_file

        ctx = _make_context_no_sandbox()

        result = await daytona_edit_file._entrypoint(
            file_path="/workspace/f.txt",
            old_text="a",
            new_text="b",
            context=ctx,
        )

        assert result.is_error
        assert "No Daytona sandbox" in result.output

    @pytest.mark.asyncio
    async def test_edit_dry_run(self):
        from tools.daytona_toolkit.edit_tool import daytona_edit_file

        sandbox = _make_sandbox(download_content=b"foo bar baz")
        ctx = _make_context(sandbox)

        result = await daytona_edit_file._entrypoint(
            file_path="/workspace/f.txt",
            old_text="bar",
            new_text="qux",
            dry_run=True,
            context=ctx,
        )

        assert not result.is_error
        data = json.loads(result.output)
        assert data["status"] == "dry_run"
        assert "diff" in data
        # Dry run should NOT write
        sandbox.fs.upload_file.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_edit_replaces_first_occurrence_only(self):
        from tools.daytona_toolkit.edit_tool import daytona_edit_file

        sandbox = _make_sandbox(download_content=b"aaa bbb aaa")
        ctx = _make_context(sandbox)

        await daytona_edit_file._entrypoint(
            file_path="/workspace/f.txt",
            old_text="aaa",
            new_text="ccc",
            context=ctx,
        )

        written = sandbox.fs.upload_file.call_args[0][1]
        assert written == b"ccc bbb aaa"

    @pytest.mark.asyncio
    async def test_edit_read_failure(self):
        from tools.daytona_toolkit.edit_tool import daytona_edit_file

        sandbox = _make_sandbox()
        sandbox.fs.download_file = AsyncMock(side_effect=OSError("permission denied"))
        ctx = _make_context(sandbox)

        result = await daytona_edit_file._entrypoint(
            file_path="/workspace/f.txt",
            old_text="a",
            new_text="b",
            context=ctx,
        )

        assert result.is_error
        assert "Cannot read file" in result.output


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

class TestHelpers:

    def test_truncate_short(self):
        from tools.daytona_toolkit.tools import _truncate

        assert _truncate("hello") == "hello"

    def test_truncate_long(self):
        from tools.daytona_toolkit.tools import _truncate, _OUTPUT_MAX_CHARS

        text = "x" * (_OUTPUT_MAX_CHARS + 100)
        result = _truncate(text)
        assert len(result) < len(text)
        assert "truncated" in result

    def test_get_sandbox_raises_without_sandbox(self):
        from tools.daytona_toolkit.tools import _get_sandbox

        ctx = _make_context_no_sandbox()
        with pytest.raises(RuntimeError, match="No Daytona sandbox"):
            _get_sandbox(ctx)

    def test_get_cwd_from_metadata(self):
        from tools.daytona_toolkit.tools import _get_cwd

        ctx = ToolExecutionContext(
            cwd=Path("/fallback"),
            metadata={"daytona_cwd": "/workspace/src"},
        )
        assert _get_cwd(ctx) == "/workspace/src"

    def test_get_cwd_fallback_returns_none(self):
        from tools.daytona_toolkit.tools import _get_cwd

        ctx = ToolExecutionContext(cwd=Path("/fallback"), metadata={})
        assert _get_cwd(ctx) is None


# ---------------------------------------------------------------------------
# LspClient._resolve and sandbox exec integration
# ---------------------------------------------------------------------------

class TestLspClientResolve:
    """Test that LspClient._resolve handles both sync and async results."""

    def test_resolve_sync_value(self):
        from code_intelligence.lsp.client import LspClient

        result = LspClient._resolve(42)
        assert result == 42

    def test_resolve_async_coroutine(self):
        from code_intelligence.lsp.client import LspClient

        async def coro():
            return "async_result"

        result = LspClient._resolve(coro())
        assert result == "async_result"

    def test_resolve_none(self):
        from code_intelligence.lsp.client import LspClient

        result = LspClient._resolve(None)
        assert result is None


class TestLspClientSandboxExec:
    """Test LspClient with sync and async sandbox objects."""

    def test_run_python_script_sync_sandbox(self):
        from code_intelligence.lsp.client import LspClient

        resp = MagicMock()
        resp.result = "hello"
        resp.exit_code = 0

        sandbox = MagicMock()
        sandbox.process.exec = MagicMock(return_value=resp)

        client = LspClient(workspace_root="/workspace", sandbox=sandbox)
        output = client._run_python_script("print('hello')")

        assert output == "hello"
        sandbox.process.exec.assert_called_once()

    def test_run_python_script_async_sandbox(self):
        from code_intelligence.lsp.client import LspClient

        resp = MagicMock()
        resp.result = "async_hello"
        resp.exit_code = 0

        sandbox = MagicMock()

        async def async_exec(*args, **kwargs):
            return resp

        sandbox.process.exec = async_exec

        client = LspClient(workspace_root="/workspace", sandbox=sandbox)
        output = client._run_python_script("print('hello')")

        assert output == "async_hello"

    def test_run_python_script_sandbox_error(self):
        from code_intelligence.lsp.client import LspClient

        sandbox = MagicMock()
        sandbox.process.exec = MagicMock(side_effect=RuntimeError("boom"))

        client = LspClient(workspace_root="/workspace", sandbox=sandbox)
        output = client._run_python_script("print('hello')")

        assert output == ""

    def test_check_python_backend_sync(self):
        from code_intelligence.lsp.client import LspClient

        resp = MagicMock()
        resp.exit_code = 0

        sandbox = MagicMock()
        sandbox.process.exec = MagicMock(return_value=resp)

        client = LspClient(workspace_root="/workspace", sandbox=sandbox)
        assert client._check_python_backend() is True

    def test_check_python_backend_async(self):
        from code_intelligence.lsp.client import LspClient

        resp = MagicMock()
        resp.exit_code = 0

        sandbox = MagicMock()

        async def async_exec(*args, **kwargs):
            return resp

        sandbox.process.exec = async_exec

        client = LspClient(workspace_root="/workspace", sandbox=sandbox)
        assert client._check_python_backend() is True

    def test_check_typescript_backend_async(self):
        from code_intelligence.lsp.client import LspClient

        resp = MagicMock()
        resp.exit_code = 0

        sandbox = MagicMock()

        async def async_exec(*args, **kwargs):
            return resp

        sandbox.process.exec = async_exec

        client = LspClient(workspace_root="/workspace", sandbox=sandbox)
        assert client._check_typescript_backend() is True

    def test_check_python_backend_failure(self):
        from code_intelligence.lsp.client import LspClient

        resp = MagicMock()
        resp.exit_code = 1

        sandbox = MagicMock()
        sandbox.process.exec = MagicMock(return_value=resp)

        client = LspClient(workspace_root="/workspace", sandbox=sandbox)
        assert client._check_python_backend() is False

    def test_run_python_script_none_result(self):
        from code_intelligence.lsp.client import LspClient

        resp = MagicMock()
        resp.result = None
        resp.exit_code = 0

        sandbox = MagicMock()
        sandbox.process.exec = MagicMock(return_value=resp)

        client = LspClient(workspace_root="/workspace", sandbox=sandbox)
        output = client._run_python_script("pass")

        assert output == ""
