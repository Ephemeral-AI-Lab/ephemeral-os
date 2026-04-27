"""Tests for pure helpers in sandbox.daytona_utils."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from tools.core.base import ToolExecutionContextService
from tools.daytona_toolkit._file_tool_helpers import (
    MAX_READ_FILE_LINES,
    ReadFileInput,
    build_find_result,
    build_read_file_result,
)
from sandbox.daytona_utils import (
    _get_repo_root,
    _normalized_path,
    _path_error,
    _run_with_recovery,
    _resolve_path,
)
from tools.daytona_toolkit.shell import _build_tool_output


def _ctx(services=None) -> ToolExecutionContextService:
    return ToolExecutionContextService(cwd=Path("/tmp"), services=services or {})


def test_build_tool_output_preserves_all_shell_outputs():
    long_command = "python -c " + repr("x" * 200)
    long_stdout = "start-" + ("x" * 9_000) + "-end"
    long_error = "error-" + ("y" * 1_000)
    shells = [
        {
            "command": f"{long_command}-{idx}",
            "exit_code": 0,
            "stdout": f"{long_stdout}-{idx}",
            "stderr": "",
        }
        for idx in range(4)
    ]

    result = _build_tool_output(
        context=_ctx(),
        status="ok",
        files_written=0,
        shells=shells,
        warnings=[],
        error=long_error,
    )
    payload = json.loads(result.output)

    assert payload["error"] == long_error
    assert payload["shell_summaries"][-1] == f"$ {long_command}-3 -> exit 0"
    assert payload["shell_outputs"] == shells


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


def test_build_find_result_preserves_all_matches_without_truncated_flag():
    matches = [
        {"file": f"/tmp/{idx}.py", "line": idx, "content": f"match {idx}"}
        for idx in range(600)
    ]

    result = build_find_result(
        cwd="/tmp",
        pattern="match",
        path="/tmp",
        matches=matches,
    )
    payload = json.loads(result.output)

    assert len(payload["matches"]) == len(matches)
    assert payload["total_matches"] == len(matches)
    assert "truncated" not in payload


# ---------------------------------------------------------------------------
# _path_error
# ---------------------------------------------------------------------------


def test_path_error_file_not_found():
    exc = FileNotFoundError("gone")
    assert _path_error(exc, "/some/path") == "Path does not exist: /some/path"


def test_path_error_message_contains_no_such_file():
    exc = RuntimeError("No such file or directory")
    result = _path_error(exc, "/x")
    assert result is not None
    assert "/x" in result


def test_path_error_sdk_prefix_colon_suffix():
    exc = RuntimeError("Failed to list files:")
    assert _path_error(exc, "/dir") == "Path does not exist: /dir"


def test_path_error_unrecognized_returns_none():
    assert _path_error(RuntimeError("something totally different"), "/p") is None


def test_path_error_sdk_prefix_without_trailing_colon():
    # SDK prefix but no trailing colon — should NOT match
    exc = RuntimeError("Failed to list files: details here")
    assert _path_error(exc, "/p") is None


# ---------------------------------------------------------------------------
# _get_repo_root
# ---------------------------------------------------------------------------


def test_get_repo_root_returns_value():
    ctx = _ctx({"repo_root": "/workspace/project"})
    assert _get_repo_root(ctx) == "/workspace/project"


def test_get_repo_root_returns_none_when_missing():
    assert _get_repo_root(_ctx()) is None


# ---------------------------------------------------------------------------
# _resolve_path
# ---------------------------------------------------------------------------


def test_resolve_path_absolute_unchanged():
    ctx = _ctx({"repo_root": "/workspace"})
    assert _resolve_path("/abs/path", ctx) == "/abs/path"


def test_resolve_path_relative_joins_cwd():
    ctx = _ctx({"repo_root": "/workspace"})
    assert _resolve_path("relative/file.py", ctx) == "/workspace/relative/file.py"


def test_resolve_path_relative_no_cwd_unchanged():
    assert _resolve_path("bare_file.py", _ctx()) == "bare_file.py"


# ---------------------------------------------------------------------------
# _normalized_path
# ---------------------------------------------------------------------------


def test_normalized_path_preserves_root():
    assert _normalized_path("/") == "/"


def test_normalized_path_strips_trailing_separators():
    assert _normalized_path("/workspace/src/") == "/workspace/src"
    assert _normalized_path("relative/path///") == "relative/path"


# ---------------------------------------------------------------------------
# _run_with_recovery
# ---------------------------------------------------------------------------


async def test_run_with_recovery_uses_context_sandbox():
    sandbox = object()
    seen: list[object] = []

    async def operation(candidate: object) -> str:
        seen.append(candidate)
        return "ok"

    result = await _run_with_recovery(_ctx({"daytona_sandbox": sandbox}), operation)

    assert result == "ok"
    assert seen == [sandbox]


async def test_run_with_recovery_retries_with_recovered_sandbox(monkeypatch):
    original = object()
    recovered = object()
    seen: list[object] = []

    async def fake_recover_sandbox(context, exc):
        assert context["daytona_sandbox"] is original
        assert str(exc) == "container not found"
        return recovered

    async def operation(candidate: object) -> str:
        seen.append(candidate)
        if candidate is original:
            raise RuntimeError("container not found")
        return "ok"

    monkeypatch.setattr("sandbox.daytona_utils._recover_sandbox", fake_recover_sandbox)

    result = await _run_with_recovery(_ctx({"daytona_sandbox": original}), operation)

    assert result == "ok"
    assert seen == [original, recovered]
