"""Unit coverage for mock probe queue-bridge helpers."""

from __future__ import annotations

import asyncio

import pytest

from test_runner.agent.mock.probe_bridge import _CallToolBridge
from tools._framework.core.results import ToolResult


@pytest.mark.asyncio
async def test_bridge_translates_requested_background_id_for_write_stdin() -> None:
    bridge = _CallToolBridge()
    bridge._background_aliases["requested-bg"] = "cmd_2"  # noqa: SLF001

    pending = asyncio.create_task(
        bridge._call_loop_tool(  # noqa: SLF001
            "write_stdin",
            {"command_session_id": "requested-bg", "chars": "\u0003"},
        )
    )
    kind, tool_name, raw_input, future = await bridge._queue.get()  # noqa: SLF001

    assert kind == "call"
    assert tool_name == "write_stdin"
    assert raw_input == {"command_session_id": "cmd_2", "chars": "\u0003"}

    future.set_result(ToolResult(output="cancelled", is_error=False))
    result = await pending

    assert result.output == "cancelled"


@pytest.mark.asyncio
async def test_bridge_background_await_polls_without_wait_turn() -> None:
    bridge = _CallToolBridge()

    pending = asyncio.create_task(
        bridge._await_command_session_result(  # noqa: SLF001
            command_session_id="cmd_1",
            allow_error=True,
        )
    )
    kind, tool_name, raw_input, future = await bridge._queue.get()  # noqa: SLF001

    assert kind == "call"
    assert tool_name == "write_stdin"
    assert raw_input == {
        "command_session_id": "cmd_1",
        "chars": "",
        "yield_time_ms": 50,
    }

    future.set_result(
        ToolResult(
            output='{"id": "bg_1", "status": "running", "result": "[started]"}',
            is_error=False,
        )
    )
    await asyncio.sleep(0)
    assert bridge._queue.empty()  # noqa: SLF001

    kind, tool_name, raw_input, future = await asyncio.wait_for(
        bridge._queue.get(), 0.2  # noqa: SLF001
    )
    assert kind == "call"
    assert tool_name == "write_stdin"
    assert raw_input == {
        "command_session_id": "cmd_1",
        "chars": "",
        "yield_time_ms": 100,
    }

    future.set_result(
        ToolResult(
            output=(
                '{"status": "ok", "exit_code": 0, '
                '"output": {"stdout": "done", "stderr": ""}}'
            ),
            is_error=False,
        )
    )
    result = await pending

    assert not result.is_error
