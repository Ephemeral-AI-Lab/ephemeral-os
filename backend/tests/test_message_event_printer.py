from message.event_printer import MultiAgentEventPrinter
from message.stream_events import (
    AssistantTurnComplete,
    BackgroundTaskCompleted,
    SystemNotification,
    ToolExecutionCompleted,
    ThinkingDelta,
    ToolExecutionStarted,
)
from providers.types import UsageSnapshot
from message.messages import ConversationMessage, TextBlock


def test_printer_includes_work_id_in_prefix() -> None:
    lines: list[str] = []
    printer = MultiAgentEventPrinter(color=False, sink=lines.append)

    printer.emit(
        ToolExecutionStarted(
            tool_name="pytest",
            tool_input={"k": "value"},
            agent_name="developer",
            work_id="1234567890abcdef1234",
        )
    )

    assert lines == [
        "[developer     ] [1234567890abcdef1234] -> tool_start: pytest({'k': 'value'})"
    ]


def test_printer_keeps_work_id_for_flushed_thinking() -> None:
    lines: list[str] = []
    printer = MultiAgentEventPrinter(color=False, sink=lines.append)

    printer.emit(ThinkingDelta(text="working", agent_name="team_planner", work_id="b88848c71234425a"))
    printer.emit(
        AssistantTurnComplete(
            message=ConversationMessage(role="assistant", content=[TextBlock(text="done")]),
            usage=UsageSnapshot(),
            agent_name="team_planner",
            work_id="b88848c71234425a",
        )
    )

    assert lines == [
        "[team_planner  ] [b88848c71234425a] [thinking] working"
    ]


def test_printer_keeps_full_background_progress_notification_text() -> None:
    lines: list[str] = []
    printer = MultiAgentEventPrinter(color=False, sink=lines.append)
    long_text = (
        'Background task_id="bg_1" status="running" source="engine_progress"\n'
        "Tool: run_subagent\n"
        "Note: Scout pydantic/networks.py to understand URL and network type implementations\n"
        "Run ID: 84a5dde276554528\n"
        "Running for 19s\n"
        "No new output in the last 7s\n"
        "Keep working on any other ready analysis or tool tasks first. "
        "Only wait when this background task is the remaining blocker.\n\n"
        'Background task_id="bg_2" status="running" source="engine_progress"\n'
        "Tool: run_subagent\n"
        "Note: Second task still visible at the end of the notification."
    )

    printer.emit(
        SystemNotification(
            text=long_text,
            category="background_progress",
            agent_name="team_planner",
            work_id="1a0578d4c4dd7f1f14dd",
        )
    )

    assert lines == [
        "[team_planner  ] [1a0578d4c4dd7f1f14dd] "
        f"[system:background_progress] {long_text}"
    ]


def test_printer_emits_atlas_work_log_lines() -> None:
    lines: list[str] = []
    printer = MultiAgentEventPrinter(color=False, sink=lines.append)

    printer.emit(
        ToolExecutionCompleted(
            tool_name="atlas_lookup",
            output="atlas_lookup: use=1 refresh=1 scout=1",
            metadata={
                "lookups": [
                    {
                        "subsystem": "pydantic/networks.py",
                        "action": "use",
                        "staged_artifact_ref": "atlas:pydantic/networks.py:deadbeef",
                        "staleness_reason": None,
                    },
                    {
                        "subsystem": "pydantic/main.py",
                        "action": "refresh",
                        "staged_artifact_ref": None,
                        "staleness_reason": "ledger edit in scope",
                    },
                    {
                        "subsystem": "pydantic/types.py",
                        "action": "scout",
                        "staged_artifact_ref": None,
                        "staleness_reason": None,
                    },
                ]
            },
            agent_name="team_planner",
            work_id="atlas123",
        )
    )

    assert lines == [
        "[team_planner  ] [atlas123] <- tool_done:  atlas_lookup [ok] atlas_lookup: use=1 refresh=1 scout=1",
        "[team_planner  ] [atlas123] [atlas] subsystem=pydantic/networks.py action=use artifact=atlas:pydantic/networks.py:deadbeef",
        "[team_planner  ] [atlas123] [atlas] subsystem=pydantic/main.py action=refresh reason=ledger edit in scope",
        "[team_planner  ] [atlas123] [atlas] subsystem=pydantic/types.py action=scout",
    ]


def test_printer_emits_scout_triggered_atlas_lines_for_background_subagent() -> None:
    lines: list[str] = []
    printer = MultiAgentEventPrinter(color=False, sink=lines.append)

    printer.emit(
        BackgroundTaskCompleted(
            task_id="bg_scout",
            tool_name="run_subagent",
            output=(
                '{"kind":"brief","run_id":"run-1","summary":"Scout summary",'
                '"artifact_ref":"scout:src/auth","payload":{"target_paths":["src/auth"]},'
                '"atlas":{"subsystem":"src/auth","persisted":true,"promoted":true,'
                '"artifact_ref":"scout:src/auth","reason":"run_subagent:scout-complete"}}'
            ),
            agent_name="team_planner",
            work_id="planner123",
        )
    )

    assert len(lines) == 2
    assert lines[0].startswith(
        "[team_planner  ] [planner123] <~ return:     subagent task_id=bg_scout [ok] "
    )
    assert "Scout summary" in lines[0]
    assert lines[1] == (
        "[team_planner  ] [planner123] [atlas] subsystem=src/auth persisted=true "
        "promoted=true artifact=scout:src/auth reason=run_subagent:scout-complete"
    )
