"""Tests for pure sandbox helper functions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from tools._framework.core.base import ToolExecutionContextService
from tools.sandbox._lib.tool_context import (
    sandbox_repo_root_from_tool_context,
    sandbox_path_error_message,
    resolve_tool_sandbox_path,
)
from tools.sandbox._lib.file_payloads import (
    MAX_READ_FILE_LINES,
    ReadFileInput,
    build_read_file_result,
)


def _ctx(services=None) -> ToolExecutionContextService:
    return ToolExecutionContextService(cwd=Path("/tmp"), services=services or {})


def test_build_read_file_result_preserves_full_selected_content():
    long_line = "x" * 9_000
    result = build_read_file_result(
        context=_ctx(),
        file_path="/tmp/example.txt",
        content=f"first\n{long_line}\nlast",
        start_line=1,
        end_line=MAX_READ_FILE_LINES,
    )
    payload = json.loads(result.output)

    assert long_line in payload["content"]
    assert payload["content"].endswith("   3: last")
    assert payload["end_line"] == 3


def test_read_file_input_rejects_null_end_line():
    with pytest.raises(ValidationError):
        ReadFileInput.model_validate({"file_path": "/tmp/example.txt", "end_line": None})


def test_read_file_input_rejects_end_line_before_start_line():
    with pytest.raises(ValidationError, match="end_line cannot be smaller"):
        ReadFileInput.model_validate(
            {"file_path": "/tmp/example.txt", "start_line": 10, "end_line": 9}
        )


def test_read_file_input_rejects_ranges_over_200_lines():
    with pytest.raises(ValidationError, match="at most 200 lines"):
        ReadFileInput.model_validate(
            {
                "file_path": "dask/dataframe/utils.py",
                "start_line": 1,
                "end_line": 2_147_483_647,
            }
        )


def test_read_file_input_default_reads_at_most_200_lines():
    parsed = ReadFileInput.model_validate({"file_path": "/tmp/example.txt"})

    assert parsed.start_line == 1
    assert parsed.end_line == MAX_READ_FILE_LINES


def test_read_file_input_omitted_end_line_uses_200_line_window_from_start():
    parsed = ReadFileInput.model_validate(
        {"file_path": "/tmp/example.txt", "start_line": 300}
    )

    assert parsed.start_line == 300
    assert parsed.end_line == 499


def test_read_file_input_schema_makes_end_line_non_nullable():
    end_line_schema = ReadFileInput.model_json_schema()["properties"]["end_line"]

    assert end_line_schema["type"] == "integer"
    assert "anyOf" not in end_line_schema


def test_build_read_file_result_clamps_end_line_past_eof():
    result = build_read_file_result(
        context=_ctx(),
        file_path="/tmp/example.txt",
        content="first\nsecond\nthird",
        start_line=2,
        end_line=100,
    )
    payload = json.loads(result.output)

    assert payload["start_line"] == 2
    assert payload["end_line"] == 3
    assert payload["content"] == "   2: second\n   3: third"


def test_build_read_file_result_caps_selected_content_to_200_lines():
    content = "\n".join(f"line {idx}" for idx in range(1, MAX_READ_FILE_LINES + 50))

    result = build_read_file_result(
        context=_ctx(),
        file_path="/tmp/example.txt",
        content=content,
        start_line=25,
        end_line=1_000,
    )
    payload = json.loads(result.output)

    assert payload["start_line"] == 25
    assert payload["end_line"] == 224
    assert len(payload["content"].splitlines()) == MAX_READ_FILE_LINES


# ---------------------------------------------------------------------------
# sandbox_path_error_message
# ---------------------------------------------------------------------------


def test_sandbox_path_error_message_file_not_found():
    exc = FileNotFoundError("gone")
    assert sandbox_path_error_message(exc, "/some/path") == "Path does not exist: /some/path"


def test_sandbox_path_error_message_contains_no_such_file():
    exc = RuntimeError("No such file or directory")
    result = sandbox_path_error_message(exc, "/x")
    assert result is not None
    assert "/x" in result


def test_sandbox_path_error_message_unrecognized_returns_none():
    assert sandbox_path_error_message(RuntimeError("something totally different"), "/p") is None


# ---------------------------------------------------------------------------
# sandbox_repo_root_from_tool_context
# ---------------------------------------------------------------------------


def test_sandbox_repo_root_from_tool_context_returns_value():
    ctx = _ctx({"repo_root": "/workspace/project"})
    assert sandbox_repo_root_from_tool_context(ctx) == "/workspace/project"


def test_sandbox_repo_root_from_tool_context_returns_empty_when_missing():
    assert sandbox_repo_root_from_tool_context(_ctx()) == ""


# ---------------------------------------------------------------------------
# resolve_tool_sandbox_path
# ---------------------------------------------------------------------------


def test_resolve_path_absolute_unchanged():
    ctx = _ctx({"repo_root": "/workspace"})
    assert resolve_tool_sandbox_path("/abs/path", ctx) == "/abs/path"


def test_resolve_path_relative_joins_cwd():
    ctx = _ctx({"repo_root": "/workspace"})
    assert resolve_tool_sandbox_path("relative/file.py", ctx) == "/workspace/relative/file.py"


def test_resolve_path_relative_no_cwd_unchanged():
    assert resolve_tool_sandbox_path("bare_file.py", _ctx()) == "bare_file.py"
