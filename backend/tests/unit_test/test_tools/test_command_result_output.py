"""Tests for shared sandbox command result rendering."""

from __future__ import annotations

import json

from sandbox.shared.models import CommandOutput, ExecCommandResult
from tools.sandbox._lib.pty_command_tool import command_tool_result


def test_command_tool_result_marks_timeout_as_error() -> None:
    result = command_tool_result(
        ExecCommandResult(
            success=False,
            status="timed_out",
            exit_code=124,
            output=CommandOutput(stderr="timeout\n"),
            pty_session_id="pty_1",
        )
    )

    assert result.is_error is True
    assert result.metadata == {"status": "timed_out", "pty_session_id": "pty_1"}
    assert json.loads(result.output) == {
        "status": "timed_out",
        "exit_code": 124,
        "output": {"stdout": "", "stderr": "timeout\n"},
        "pty_session_id": "pty_1",
    }
