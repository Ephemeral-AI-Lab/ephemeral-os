"""Tests for shared sandbox command result rendering."""

from __future__ import annotations

import asyncio
import json

import pytest

from engine.background.task_supervisor import BackgroundTaskSupervisor
from sandbox.shared.models import CommandOutput, ExecCommandResult
from tools._framework.core.context import ToolExecutionContextService
from tools.sandbox._lib.pty_command_tool import (
    command_tool_result,
    recover_pty_result_from_supervisor,
)


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
    assert result.metadata == {
        "status": "timed_out",
        "pty_session_id": "pty_1",
        "changed_paths": [],
        "changed_path_kinds": {},
        "mutation_source": "",
        "conflict_reason": None,
    }
    assert json.loads(result.output) == {
        "status": "timed_out",
        "exit_code": 124,
        "output": {"stdout": "", "stderr": "timeout\n"},
        "stdout": "",
        "stderr": "timeout\n",
        "changed_paths": [],
        "changed_path_kinds": {},
        "mutation_source": "",
        "conflict_reason": None,
        "pty_session_id": "pty_1",
    }


@pytest.mark.asyncio
async def test_pty_not_found_recovers_supervisor_terminal_result() -> None:
    supervisor = BackgroundTaskSupervisor()
    supervisor.register_pty_command(
        pty_session_id="pty_2",
        sandbox_id="sb-1",
        agent_id="agent-1",
        command="printf done",
    )
    supervisor.mark_pty_result_reported_by_tool(
        pty_session_id="pty_2",
        result={
            "status": "ok",
            "exit_code": 0,
            "output": {"stdout": "done\n", "stderr": ""},
        },
    )
    missing = ExecCommandResult(
        success=False,
        status="error",
        exit_code=None,
        output=CommandOutput(stderr="pty_session_not_found"),
    )

    recovered = recover_pty_result_from_supervisor(
        ToolExecutionContextService(
            cwd=".",
            services={"background_task_manager": supervisor},
        ),
        missing,
        pty_session_id="pty_2",
    )

    assert recovered.status == "ok"
    assert recovered.exit_code == 0
    assert recovered.pty_session_id == "pty_2"
    assert recovered.output.stdout == "done\n"
    await asyncio.sleep(0)
