from __future__ import annotations

import json

from message.agent_message_recorder import AgentMessageJsonlRecorder
from message.messages import ConversationMessage, ToolUseBlock
from message.stream_events import (
    AssistantMessageComplete,
    AssistantTextDelta,
    ThinkingDelta,
    ToolExecutionCompleted,
)
from providers.types import UsageSnapshot


def _read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_agent_message_recorder_appends_completed_steps(tmp_path) -> None:
    path = tmp_path / "message.jsonl"
    recorder = AgentMessageJsonlRecorder(
        path,
        base_event={"benchmark": "sweevo", "instance_id": "demo"},
    )

    recorder.emit(ThinkingDelta(text="inspect ", agent_name="executor", run_id="t1"))
    recorder.emit(ThinkingDelta(text="repo", agent_name="executor", run_id="t1"))
    recorder.emit(AssistantTextDelta(text="I will run ", agent_name="executor", run_id="t1"))
    recorder.emit(AssistantTextDelta(text="tests.", agent_name="executor", run_id="t1"))
    recorder.emit(
        AssistantMessageComplete(
            message=ConversationMessage(
                role="assistant",
                content=[
                    ToolUseBlock(
                        id="toolu_1",
                        name="shell",
                        input={"cmd": "pytest -q"},
                    )
                ],
            ),
            usage=UsageSnapshot(input_tokens=1, output_tokens=2),
            agent_name="executor",
            run_id="t1",
        )
    )
    recorder.emit(
        ToolExecutionCompleted(
            tool_name="shell",
            output="ok",
            tool_id="toolu_1",
            agent_name="executor",
            run_id="t1",
        )
    )
    recorder.flush()

    records = _read_jsonl(path)
    assert [record["step_type"] for record in records] == [
        "thinking",
        "text",
        "tool_call",
        "tool_result",
    ]
    assert records[0]["content"] == [{"type": "thinking", "text": "inspect repo"}]
    assert records[1]["content"] == [{"type": "text", "text": "I will run tests."}]
    assert records[2]["content"] == [
        {
            "type": "tool_use",
            "id": "toolu_1",
            "name": "shell",
            "input": {"cmd": "pytest -q"},
        }
    ]
    assert records[3]["content"][0]["tool_use_id"] == "toolu_1"
    assert records[3]["content"][0]["content"] == "ok"
    assert all(record["benchmark"] == "sweevo" for record in records)
    assert all(record["agent_name"] == "executor" for record in records)
