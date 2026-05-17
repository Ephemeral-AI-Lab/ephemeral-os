"""Tests for tool execution trace bookkeeping."""

from __future__ import annotations

from tools._framework.core.runtime import ExecutionMetadata
from tools._framework.execution.trace import record_tool_trace


def test_record_tool_trace_ignores_subagent_launches() -> None:
    meta = ExecutionMetadata()

    record_tool_trace(
        meta,
        "run_subagent",
        {"agent_name": "test_subagent", "prompt": "explore pkg/core.py"},
    )
    record_tool_trace(
        meta,
        "run_subagent",
        {"agent_name": "test_subagent", "prompt": "explore pkg/core.py"},
    )

    assert meta.extras == {}


def test_record_tool_trace_counts_note_and_sandbox_reads() -> None:
    meta = ExecutionMetadata()

    record_tool_trace(meta, "read_file_note", {"file_paths": ["pkg/core.py"]})
    record_tool_trace(meta, "shell", {"code": "shell('pytest -q')"})
    record_tool_trace(meta, "read_file", {"file_path": "pkg/core.py"})

    assert meta["_read_file_note_calls"] == 1
    assert meta["_note_read_paths_this_response"] == ["pkg/core.py"]
    assert meta["_shell_calls"] == 1
    assert meta["_read_file_calls"] == 1
    assert meta["_read_paths_this_response"] == ["pkg/core.py"]
