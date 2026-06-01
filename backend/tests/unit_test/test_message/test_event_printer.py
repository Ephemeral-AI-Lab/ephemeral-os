import message.event_printer as event_printer
from message.event_printer import MultiAgentEventPrinter
from message.events import (
    AssistantMessageCompleteEvent,
    BackgroundTaskStartedEvent,
    ThinkingDeltaEvent,
    ToolExecutionCompletedEvent,
    ToolExecutionStartedEvent,
)
from notification import SystemNotification
from providers.types import UsageSnapshot
from message.message import Message, TextBlock


def test_printer_includes_run_id_in_prefix() -> None:
    lines: list[str] = []
    printer = MultiAgentEventPrinter(color=False, sink=lines.append)

    printer.emit(
        ToolExecutionStartedEvent(
            tool_name="pytest",
            tool_input={"k": "value"},
            agent_name="developer",
            agent_run_id="1234567890abcdef1234",
        )
    )

    assert lines == [
        "[developer     ] [1234567890abcdef1234] -> tool_start: pytest({'k': 'value'})"
    ]


def test_printer_timestamps_use_module_time(monkeypatch) -> None:
    calls = iter([10.0, 12.5])
    monkeypatch.setattr(event_printer.time, "monotonic", lambda: next(calls))
    lines: list[str] = []
    printer = MultiAgentEventPrinter(
        color=False,
        sink=lines.append,
        timestamps=True,
    )

    printer.emit(
        ToolExecutionStartedEvent(
            tool_name="pytest",
            tool_input={},
            agent_name="developer",
            agent_run_id="run-1",
        )
    )

    assert lines == ["[    2.5s] [developer     ] [run-1] -> tool_start: pytest({})"]


def test_printer_keeps_run_id_for_flushed_thinking() -> None:
    lines: list[str] = []
    printer = MultiAgentEventPrinter(color=False, sink=lines.append)

    printer.emit(
        ThinkingDeltaEvent(text="working", agent_name="analysis_agent", agent_run_id="b88848c71234425a")
    )
    printer.emit(
        AssistantMessageCompleteEvent(
            message=Message(role="assistant", content=[TextBlock(text="done")]),
            usage=UsageSnapshot(),
            agent_name="analysis_agent",
            agent_run_id="b88848c71234425a",
        )
    )

    assert lines == ["[analysis_agent] [b88848c71234425a] [thinking] working"]


def test_printer_renders_structured_shell_error_detail() -> None:
    lines: list[str] = []
    printer = MultiAgentEventPrinter(color=False, sink=lines.append)

    printer.emit(
        ToolExecutionCompletedEvent(
            tool_name="shell",
            output=(
                '{"cwd": "/testbed", "status": "error", '
                '"changed_paths": [], "conflict_reason": null, '
                '"command": "pytest -q", "exit_code": 2, '
                '"stdout": "", "stderr": "", "error": "failed"}'
            ),
            is_error=True,
            agent_name="developer",
            agent_run_id="1234567890abcdef1234",
        )
    )

    assert lines == [
        "[developer     ] [1234567890abcdef1234] "
        "<- tool_done:  shell [ERROR] $ pytest -q -> exit 2",
        "[developer     ] [1234567890abcdef1234] │ failed",
    ]


def test_printer_renders_structured_shell_cmd_error_detail() -> None:
    lines: list[str] = []
    printer = MultiAgentEventPrinter(color=False, sink=lines.append)

    printer.emit(
        ToolExecutionCompletedEvent(
            tool_name="shell",
            output=(
                '{"cwd": "/testbed", "status": "error", '
                '"changed_paths": [], "conflict_reason": null, '
                '"command": "pytest -q", "exit_code": 2, '
                '"stdout": "", "stderr": "failed to collect tests", '
                '"error": "failed to collect tests"}'
            ),
            is_error=True,
            agent_name="developer",
            agent_run_id="1234567890abcdef1234",
        )
    )

    assert lines == [
        "[developer     ] [1234567890abcdef1234] "
        "<- tool_done:  shell [ERROR] $ pytest -q -> exit 2",
        "[developer     ] [1234567890abcdef1234] │ failed to collect tests",
    ]


def test_printer_keeps_plain_shell_error_payload() -> None:
    lines: list[str] = []
    printer = MultiAgentEventPrinter(color=False, sink=lines.append)

    printer.emit(
        ToolExecutionCompletedEvent(
            tool_name="shell",
            output="Execution failed: sandbox unavailable",
            is_error=True,
            agent_name="developer",
            agent_run_id="1234567890abcdef1234",
        )
    )

    assert lines == [
        "[developer     ] [1234567890abcdef1234] "
        "<- tool_done:  shell [ERROR] Execution failed: sandbox unavailable"
    ]


def test_printer_renders_subagent_launch_context() -> None:
    lines: list[str] = []
    printer = MultiAgentEventPrinter(color=False, sink=lines.append)

    printer.emit(
        BackgroundTaskStartedEvent(
            task_id="subagent_1",
            tool_name="run_subagent",
            tool_input={
                "agent_name": "explorer",
                "prompt": "Read alpha.txt\nReturn concise findings.",
            },
            agent_name="executor",
            agent_run_id="t1",
        )
    )

    assert lines == [
        "[executor      ] [t1] >> subagent_start: run_subagent subagent_session_id=subagent_1 "
        'agent_name="explorer" prompt="Read alpha.txt\\nReturn concise findings."'
    ]


def test_printer_keeps_full_background_progress_notification_text() -> None:
    lines: list[str] = []
    printer = MultiAgentEventPrinter(color=False, sink=lines.append)
    long_text = (
        'Background task_id="bg_1" status="running" source="engine_progress"\n'
        "Tool: shell\n"
        "Note: Inspect pydantic/networks.py to understand URL and network type implementations\n"
        "Run ID: 84a5dde276554528\n"
        "Running for 19s\n"
        "No new output in the last 7s\n"
        "Keep working on any other ready analysis or tool tasks first. "
        "Only wait when this background task is the remaining blocker.\n\n"
        'Background task_id="bg_2" status="running" source="engine_progress"\n'
        "Tool: shell\n"
        "Note: Second task still visible at the end of the notification."
    )

    printer.emit(
        SystemNotification(
            text=long_text,
            agent_name="analysis_agent",
            agent_run_id="1a0578d4c4dd7f1f14dd",
        )
    )

    expected = [
        "[analysis_agent] [1a0578d4c4dd7f1f14dd] "
        '[system] Background task_id="bg_1" status="running" source="engine_progress"',
        "[analysis_agent] [1a0578d4c4dd7f1f14dd] │ Tool: shell",
        "[analysis_agent] [1a0578d4c4dd7f1f14dd] │ Note: Inspect pydantic/networks.py to understand URL and network type implementations",
        "[analysis_agent] [1a0578d4c4dd7f1f14dd] │ Run ID: 84a5dde276554528",
        "[analysis_agent] [1a0578d4c4dd7f1f14dd] │ Running for 19s",
        "[analysis_agent] [1a0578d4c4dd7f1f14dd] │ No new output in the last 7s",
        "[analysis_agent] [1a0578d4c4dd7f1f14dd] │ Keep working on any other ready analysis or tool tasks first. Only wait when this background task is the remaining blocker.",
        "[analysis_agent] [1a0578d4c4dd7f1f14dd] │ ",
        '[analysis_agent] [1a0578d4c4dd7f1f14dd] │ Background task_id="bg_2" status="running" source="engine_progress"',
        "[analysis_agent] [1a0578d4c4dd7f1f14dd] │ Tool: shell",
        "[analysis_agent] [1a0578d4c4dd7f1f14dd] │ Note: Second task still visible at the end of the notification.",
    ]

    assert lines == expected
