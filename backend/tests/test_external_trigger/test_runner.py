from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from external_trigger.runner import run
from tools.core.base import BaseTool, ToolExecutionContext, ToolResult


class _SubmitInput(BaseModel):
    content: str


class _RetryingTool(BaseTool):
    name = "submit"
    description = "submit"
    input_model = _SubmitInput

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, arguments: BaseModel, context: ToolExecutionContext) -> ToolResult:
        self.calls += 1
        if self.calls == 1:
            return ToolResult(output="Error: revise the submission", is_error=True)
        context.metadata["submitted_output"] = arguments.model_dump()
        return ToolResult(output="accepted")


@pytest.mark.asyncio
async def test_runner_executes_tools_and_retries_on_tool_error(monkeypatch):
    responses = [
        SimpleNamespace(content=[
            SimpleNamespace(type="tool_use", name="submit", input={"content": "first"}, id="tool-1"),
        ]),
        SimpleNamespace(content=[
            SimpleNamespace(type="tool_use", name="submit", input={"content": "second"}, id="tool-2"),
        ]),
    ]

    async def fake_stream_to_response(api_client, request):
        del api_client, request
        return responses.pop(0)

    monkeypatch.setattr("external_trigger.runner._stream_to_response", fake_stream_to_response)

    tool = _RetryingTool()
    context = ToolExecutionContext(cwd=Path("."), metadata={"agent_name": "planner"})
    result = await run(
        agent_name="test:submit",
        messages=[{"role": "assistant", "content": "frozen"}],
        system_prompt="system",
        prompt="submit now",
        tools=[tool],
        api_client=object(),
        execution_context=context,
        execute_tools=True,
        max_turns=3,
    )

    assert result.tool_name == "submit"
    assert result.turns_used == 2
    assert tool.calls == 2
    assert context.metadata["submitted_output"] == {"content": "second"}
    assert any(
        block.get("type") == "tool_result" and block.get("is_error") is True
        for message in result.conversation
        for block in message.get("content", [])
        if isinstance(message.get("content"), list)
    )


@pytest.mark.asyncio
async def test_runner_validation_error_reports_required_fields(monkeypatch):
    responses = [
        SimpleNamespace(content=[
            SimpleNamespace(type="tool_use", name="submit", input={}, id="tool-1"),
        ]),
        SimpleNamespace(content=[
            SimpleNamespace(type="tool_use", name="submit", input={"content": "fixed"}, id="tool-2"),
        ]),
    ]

    async def fake_stream_to_response(api_client, request):
        del api_client, request
        return responses.pop(0)

    monkeypatch.setattr("external_trigger.runner._stream_to_response", fake_stream_to_response)

    result = await run(
        agent_name="test:submit",
        messages=[],
        system_prompt="system",
        prompt="submit now",
        tools=[_RetryingTool()],
        api_client=object(),
        execute_tools=False,
        max_turns=3,
    )

    error_blocks = [
        block
        for message in result.conversation
        for block in message.get("content", [])
        if isinstance(message.get("content"), list)
        and block.get("type") == "tool_result"
        and block.get("is_error") is True
    ]
    assert error_blocks
    assert "Required fields for `submit`: content." in error_blocks[0]["content"]
    assert result.tool_name == "submit"
