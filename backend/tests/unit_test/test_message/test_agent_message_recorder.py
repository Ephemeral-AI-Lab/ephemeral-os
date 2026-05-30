from __future__ import annotations

import json

from message.agent_message_recorder import (
    AgentMessageJsonlRecorder,
    clear_recorder,
    recorder_for_run,
    register_recorder,
)
from message.message import (
    Message,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
)
from message.events import (
    AssistantMessageCompleteEvent,
    AssistantTextDeltaEvent,
    ThinkingDeltaEvent,
    ToolExecutionCompletedEvent,
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
        ThinkingDeltaEvent(text="inspect ", agent_name="executor", agent_run_id="t1")
    )
    recorder.emit(ThinkingDeltaEvent(text="repo", agent_name="executor", agent_run_id="t1"))
    recorder.emit(
        AssistantTextDeltaEvent(
            text="I will run ", agent_name="executor", agent_run_id="t1"
        )
    )
    recorder.emit(
        AssistantTextDeltaEvent(text="tests.", agent_name="executor", agent_run_id="t1")
    )
    recorder.emit(
        AssistantMessageCompleteEvent(
            message=Message(
                role="assistant",
                content=[
                    ToolUseBlock(
                        tool_use_id="toolu_1",
                        name="shell",
                        input={"cmd": "pytest -q"},
                    )
                ],
            ),
            usage=UsageSnapshot(input_tokens=1, output_tokens=2),
            agent_name="executor",
            agent_run_id="t1",
        )
    )
    recorder.emit(
        ToolExecutionCompletedEvent(
            tool_name="shell",
            output="ok",
            tool_use_id="toolu_1",
            agent_name="executor",
            agent_run_id="t1",
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
            "tool_use_id": "toolu_1",
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


def test_initial_messages_preserve_launch_metadata(tmp_path) -> None:
    path = tmp_path / "message.jsonl"
    recorder = AgentMessageJsonlRecorder(path)

    recorder.record_initial_messages(
        system_prompt="system",
        user_prompt="user",
        agent_name="planner",
        run_id="run-1",
        metadata={"active_terminals": ["submit_plan_closes_goal"]},
    )

    records = _read_jsonl(path)
    assert [record["metadata"]["active_terminals"] for record in records] == [
        ["submit_plan_closes_goal"],
        ["submit_plan_closes_goal"],
    ]


def test_assistant_complete_with_full_blocks_does_not_duplicate(tmp_path) -> None:
    """Real-LLM path: AssistantMessageCompleteEvent carries the same thinking/text
    blocks that arrived as deltas. The buffer must be discarded, not flushed,
    so the recorder writes exactly one assistant row per provider turn."""
    path = tmp_path / "message.jsonl"
    recorder = AgentMessageJsonlRecorder(path)

    recorder.emit(ThinkingDeltaEvent(text="plan ", agent_name="a", agent_run_id="r"))
    recorder.emit(ThinkingDeltaEvent(text="step", agent_name="a", agent_run_id="r"))
    recorder.emit(AssistantTextDeltaEvent(text="ok.", agent_name="a", agent_run_id="r"))
    recorder.emit(
        AssistantMessageCompleteEvent(
            message=Message(
                role="assistant",
                content=[
                    ThinkingBlock(text="plan step"),
                    TextBlock(text="ok."),
                    ToolUseBlock(tool_use_id="t1", name="shell", input={"cmd": "ls"}),
                ],
            ),
            usage=UsageSnapshot(),
            agent_name="a",
            agent_run_id="r",
        )
    )
    recorder.flush()

    records = _read_jsonl(path)
    assert len(records) == 1, records
    assert [b["type"] for b in records[0]["content"]] == [
        "thinking",
        "text",
        "tool_use",
    ]


def test_advisor_approval_excluded_from_transcript(tmp_path) -> None:
    """The advisor-gated-terminal approval (the ``ask_advisor`` call AND its
    paired result) is per-call metadata, not a durable transcript row. The
    recorder drops both while keeping surrounding real content."""
    path = tmp_path / "message.jsonl"
    recorder = AgentMessageJsonlRecorder(path)

    # Assistant turn mixing real text with an advisor-approval call: keep the
    # text, drop the ask_advisor tool_use.
    recorder.emit(
        AssistantMessageCompleteEvent(
            message=Message(
                role="assistant",
                content=[
                    TextBlock(text="seeking approval"),
                    ToolUseBlock(
                        tool_use_id="adv-1",
                        name="ask_advisor",
                        input={
                            "tool_name": "submit_execution_success",
                            "tool_payload": {},
                        },
                    ),
                ],
            ),
            usage=UsageSnapshot(),
            agent_name="executor",
            agent_run_id="r",
        )
    )
    # Paired advisor result (helper_role="advisor") -> dropped on the same tool
    # identity, so no orphan tool_result remains.
    recorder.emit(
        ToolExecutionCompletedEvent(
            tool_name="ask_advisor",
            output="approved",
            tool_use_id="adv-1",
            metadata={"helper_role": "advisor", "verdict": "approve"},
            agent_name="executor",
            agent_run_id="r",
        )
    )
    # The real gated terminal call + result are kept.
    recorder.emit(
        AssistantMessageCompleteEvent(
            message=Message(
                role="assistant",
                content=[
                    ToolUseBlock(
                        tool_use_id="t1",
                        name="submit_execution_success",
                        input={},
                    )
                ],
            ),
            usage=UsageSnapshot(),
            agent_name="executor",
            agent_run_id="r",
        )
    )
    recorder.emit(
        ToolExecutionCompletedEvent(
            tool_name="submit_execution_success",
            output="ok",
            tool_use_id="t1",
            agent_name="executor",
            agent_run_id="r",
        )
    )
    recorder.flush()

    records = _read_jsonl(path)
    blocks = [block for record in records for block in record["content"]]
    tool_use_names = {
        block.get("name") for block in blocks if block.get("type") == "tool_use"
    }
    assert "ask_advisor" not in tool_use_names
    assert "submit_execution_success" in tool_use_names
    # The real text in the mixed turn survives.
    assert any(
        block.get("type") == "text" and block.get("text") == "seeking approval"
        for block in blocks
    )
    # No advisor-approval result row leaks.
    assert not any(
        block.get("type") == "tool_result"
        and isinstance(block.get("metadata"), dict)
        and block["metadata"].get("helper_role") == "advisor"
        for block in blocks
    )
    # The real terminal's result IS kept.
    assert any(
        block.get("type") == "tool_result" and block.get("tool_use_id") == "t1"
        for block in blocks
    )


def test_recorder_registry_round_trip(tmp_path) -> None:
    recorder = AgentMessageJsonlRecorder(tmp_path / "message.jsonl")
    register_recorder("agent-a", "run-xyz", recorder)
    try:
        assert recorder_for_run("agent-a", "run-xyz") is recorder
        assert recorder_for_run("agent-a", "") is None
        assert recorder_for_run("agent-a", "other") is None
        assert recorder_for_run("agent-b", "run-xyz") is None
    finally:
        clear_recorder("agent-a", "run-xyz")
    assert recorder_for_run("agent-a", "run-xyz") is None
