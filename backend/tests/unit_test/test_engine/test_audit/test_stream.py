"""Tests for engine-owned stream audit translation."""

from __future__ import annotations

from engine.audit import events
from engine.audit.stream import audit_events_from_stream_event
from message.stream_events import ToolExecutionCompleted, ToolExecutionStarted


def test_tool_started_stream_event_maps_to_engine_audit_event() -> None:
    emitted = audit_events_from_stream_event(
        ToolExecutionStarted(
            tool_name="edit_file",
            tool_input={"file_path": "a.py", "old_text": "x", "new_text": "y"},
            tool_id="toolu_1",
            agent_name="executor",
            run_id="agent-run-1",
        ),
        task_center_run_id="run-1",
        metadata={
            "task_center_task_id": "task-1",
            "task_center_attempt_id": "attempt-1",
            "sandbox_id": "sb-1",
        },
    )

    assert len(emitted) == 1
    event = emitted[0]
    assert event.source == "engine"
    assert event.type == events.TOOL_STARTED
    assert event.node.task_center_run_id == "run-1"
    assert event.node.task_center_task_id == "task-1"
    assert event.node.attempt_id == "attempt-1"
    assert event.node.agent_name == "executor"
    assert event.node.agent_run_id == "agent-run-1"
    assert event.node.sandbox_id == "sb-1"
    assert event.node.tool_name == "edit_file"
    assert event.node.tool_id == "toolu_1"
    assert event.payload["input_shape"] == {
        "file_path": "str",
        "old_text": "str",
        "new_text": "str",
    }
    assert event.payload["input_redacted"] == {
        "file_path": "<redacted>",
        "old_text": "<redacted>",
        "new_text": "<redacted>",
    }
    assert str(event.payload["input_digest"]).startswith("sha256:")
    assert event.payload["input_bytes"] > 0


def test_tool_completed_stream_event_preserves_domain_timings_as_metadata() -> None:
    emitted = audit_events_from_stream_event(
        ToolExecutionCompleted(
            tool_name="shell",
            output='{"status": "ok"}',
            is_error=False,
            tool_id="toolu_2",
            metadata={
                "status": "ok",
                "timings": {"api.shell.total_s": 0.2, "occ.apply.total_s": 0.1},
            },
            does_terminate=False,
            agent_name="executor",
            run_id="agent-run-2",
        ),
        metadata={
            "task_center_run_id": "run-1",
            "task_center_task_id": "task-2",
            "agent_run_id": "metadata-agent-run",
            "tool_id": "metadata-tool-id",
        },
    )

    event = emitted[0]
    assert event.type == events.TOOL_COMPLETED
    assert event.node.task_center_run_id == "run-1"
    assert event.node.agent_run_id == "agent-run-2"
    assert event.node.tool_id == "toolu_2"
    assert event.payload["status"] == "ok"
    assert event.payload["is_error"] is False
    assert event.payload["metadata"] == {
        "status": "ok",
        "domain_timings": {
            "api.shell.total_s": 0.2,
            "occ.apply.total_s": 0.1,
        },
    }
    assert event.payload["timings"] == {}


def test_tool_error_stream_event_maps_to_failed() -> None:
    emitted = audit_events_from_stream_event(
        ToolExecutionCompleted(
            tool_name="write_file",
            output="failed",
            is_error=True,
            tool_id="toolu_3",
        ),
    )

    assert emitted[0].type == events.TOOL_FAILED
    assert emitted[0].payload["status"] == "error"
    assert emitted[0].payload["error_kind"] == "tool_result_error"


def test_unsupported_stream_event_is_ignored() -> None:
    assert audit_events_from_stream_event(object()) == ()
