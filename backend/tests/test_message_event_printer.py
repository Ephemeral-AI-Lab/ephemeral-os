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

    expected = [
        "[team_planner  ] [1a0578d4c4dd7f1f14dd] "
        '[system:background_progress] Background task_id="bg_1" status="running" source="engine_progress"',
        "[team_planner  ] [1a0578d4c4dd7f1f14dd] │ Tool: run_subagent",
        "[team_planner  ] [1a0578d4c4dd7f1f14dd] │ Note: Scout pydantic/networks.py to understand URL and network type implementations",
        "[team_planner  ] [1a0578d4c4dd7f1f14dd] │ Run ID: 84a5dde276554528",
        "[team_planner  ] [1a0578d4c4dd7f1f14dd] │ Running for 19s",
        "[team_planner  ] [1a0578d4c4dd7f1f14dd] │ No new output in the last 7s",
        "[team_planner  ] [1a0578d4c4dd7f1f14dd] │ Keep working on any other ready analysis or tool tasks first. Only wait when this background task is the remaining blocker.",
        "[team_planner  ] [1a0578d4c4dd7f1f14dd] │ ",
        '[team_planner  ] [1a0578d4c4dd7f1f14dd] │ Background task_id="bg_2" status="running" source="engine_progress"',
        "[team_planner  ] [1a0578d4c4dd7f1f14dd] │ Tool: run_subagent",
        "[team_planner  ] [1a0578d4c4dd7f1f14dd] │ Note: Second task still visible at the end of the notification.",
    ]

    assert lines == expected


def test_printer_truncate_none_keeps_full_tool_done_output() -> None:
    lines: list[str] = []
    printer = MultiAgentEventPrinter(color=False, sink=lines.append, truncate=None)
    long_output = '{\n  "scope_paths": ["dask/dataframe/groupby.py", "dask/dataframe/io/hdf.py"],\n  "details": "' + ("x" * 250) + '"\n}'

    printer.emit(
        ToolExecutionCompleted(
            tool_name="ci_scoped_status",
            output=long_output,
            agent_name="team_planner",
            work_id="2af5cbde-0bae-4f7f-98f1-5aa6d9a13b6c",
        )
    )

    assert lines == [
        "[team_planner  ] [2af5cbde-0bae-4f7f-98f1-5aa6d9a13b6c] <- tool_done:  ci_scoped_status [ok] {",
        '[team_planner  ] [2af5cbde-0bae-4f7f-98f1-5aa6d9a13b6c] │   "scope_paths": ["dask/dataframe/groupby.py", "dask/dataframe/io/hdf.py"],',
        f'[team_planner  ] [2af5cbde-0bae-4f7f-98f1-5aa6d9a13b6c] │   "details": "{"x" * 250}"',
        "[team_planner  ] [2af5cbde-0bae-4f7f-98f1-5aa6d9a13b6c] │ }",
    ]


