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


def test_agent_message_recorder_appends_conversation_messages(tmp_path) -> None:
    path = tmp_path / "message.jsonl"
    recorder = AgentMessageJsonlRecorder(
        path,
        base_event={"benchmark": "sweevo", "instance_id": "demo"},
    )

    recorder.record_initial_messages(
        system_prompt="system",
        user_prompt="user",
        agent_name="executor",
        run_id="t1",
    )
    recorder.emit(
        ThinkingDelta(text="inspect ", agent_name="executor", run_id="t1")
    )
    recorder.emit(ThinkingDelta(text="repo", agent_name="executor", run_id="t1"))
    recorder.emit(
        AssistantTextDelta(
            text="I will run ", agent_name="executor", run_id="t1"
        )
    )
    recorder.emit(
        AssistantTextDelta(text="tests.", agent_name="executor", run_id="t1")
    )
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
    assert [record["role"] for record in records] == [
        "system",
        "user",
        "assistant",
        "assistant",
        "assistant",
        "user",
    ]
    assert all("step_type" not in record for record in records)
    assert all(record.get("event") != "agent_step" for record in records)
    assert records[2]["content"] == [{"type": "thinking", "text": "inspect repo"}]
    assert records[3]["content"] == [{"type": "text", "text": "I will run tests."}]
    assert records[4]["content"] == [
        {
            "type": "tool_use",
            "id": "toolu_1",
            "name": "shell",
            "input": {"cmd": "pytest -q"},
        }
    ]
    assert records[5]["content"][0]["tool_use_id"] == "toolu_1"
    assert records[5]["content"][0]["content"] == "ok"
    assert all(
        record["metadata"]["benchmark"] == "sweevo" for record in records
    )
    assert all(
        record["metadata"]["agent_name"] == "executor" for record in records
    )
