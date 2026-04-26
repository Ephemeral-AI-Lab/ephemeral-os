from message.event_printer import MultiAgentEventPrinter
from message.stream_events import (
    AssistantTurnComplete,
    BackgroundTaskCompleted,
    ToolExecutionCompleted,
    ToolExecutionStarted,
)
from notification.events import SystemNotification
from providers.types import UsageSnapshot
from message.messages import ConversationMessage, TextBlock, ThinkingBlock


def test_printer_includes_run_id_in_prefix() -> None:
    lines: list[str] = []
    printer = MultiAgentEventPrinter(color=False, sink=lines.append)

    printer.emit(
        ToolExecutionStarted(
            tool_name="pytest",
            tool_input={"k": "value"},
            agent_name="developer",
            run_id="1234567890abcdef1234",
        )
    )

    assert lines == [
        "[developer     ] [1234567890abcdef1234] -> tool_start: pytest({'k': 'value'})"
    ]


def test_printer_keeps_run_id_for_completed_thinking() -> None:
    lines: list[str] = []
    printer = MultiAgentEventPrinter(color=False, sink=lines.append)

    printer.emit(
        AssistantTurnComplete(
            message=ConversationMessage(
                role="assistant",
                content=[ThinkingBlock(text="working"), TextBlock(text="done")],
            ),
            usage=UsageSnapshot(),
            agent_name="analysis_agent",
            run_id="b88848c71234425a",
        )
    )

    assert lines == ["[analysis_agent] [b88848c71234425a] [thinking] working"]


def test_printer_renders_structured_shell_error_detail() -> None:
    lines: list[str] = []
    printer = MultiAgentEventPrinter(color=False, sink=lines.append)

    printer.emit(
        ToolExecutionCompleted(
            tool_name="shell",
            output=(
                '{"cwd": "/testbed", "status": "error", "files_written": 0, '
                '"shells_run": 1, "shell_summaries": [], "shell_outputs": [], '
                '"script_stdout": "", "warnings": [], "error": "failed"}'
            ),
            is_error=True,
            agent_name="developer",
            run_id="1234567890abcdef1234",
        )
    )

    assert lines == [
        "[developer     ] [1234567890abcdef1234] <- tool_done:  shell [ERROR] failed"
    ]


def test_printer_renders_structured_shell_cmd_error_detail() -> None:
    lines: list[str] = []
    printer = MultiAgentEventPrinter(color=False, sink=lines.append)

    printer.emit(
        ToolExecutionCompleted(
            tool_name="shell",
            output=(
                '{"cwd": "/testbed", "status": "error", "files_written": 0, '
                '"shells_run": 1, "shell_summaries": ["$ pytest -q -> exit 2"], '
                '"shell_outputs": [{"command": "pytest -q", "exit_code": 2, '
                '"stdout": "", "stderr": "failed to collect tests"}], '
                '"script_stdout": "", "warnings": [], "error": "failed to collect tests"}'
            ),
            is_error=True,
            agent_name="developer",
            run_id="1234567890abcdef1234",
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
        ToolExecutionCompleted(
            tool_name="shell",
            output="Execution failed: sandbox unavailable",
            is_error=True,
            agent_name="developer",
            run_id="1234567890abcdef1234",
        )
    )

    assert lines == [
        "[developer     ] [1234567890abcdef1234] "
        "<- tool_done:  shell [ERROR] Execution failed: sandbox unavailable"
    ]


def test_printer_renders_background_shell_error_fallback() -> None:
    lines: list[str] = []
    printer = MultiAgentEventPrinter(color=False, sink=lines.append)

    printer.emit(
        BackgroundTaskCompleted(
            task_id="bg_1",
            tool_name="shell",
            output='{"cwd": "/testbed", "status": "error", "shells_run": 1}',
            is_error=True,
            agent_name="developer",
            run_id="1234567890abcdef1234",
        )
    )

    assert lines == [
        "[developer     ] [1234567890abcdef1234] "
        "<< bg_done:    shell [ERROR] status=error shells_run=1"
    ]


def test_printer_keeps_full_background_progress_notification_text() -> None:
    lines: list[str] = []
    printer = MultiAgentEventPrinter(color=False, sink=lines.append)
    long_text = (
        'Background task_id="bg_1" status="running" source="engine_progress"\n'
        "Tool: run_subagent\n"
        "Note: Inspect pydantic/networks.py to understand URL and network type implementations\n"
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
            agent_name="analysis_agent",
            run_id="1a0578d4c4dd7f1f14dd",
        )
    )

    expected = [
        "[analysis_agent] [1a0578d4c4dd7f1f14dd] "
        '[system:background_progress] Background task_id="bg_1" status="running" source="engine_progress"',
        "[analysis_agent] [1a0578d4c4dd7f1f14dd] │ Tool: run_subagent",
        "[analysis_agent] [1a0578d4c4dd7f1f14dd] │ Note: Inspect pydantic/networks.py to understand URL and network type implementations",
        "[analysis_agent] [1a0578d4c4dd7f1f14dd] │ Run ID: 84a5dde276554528",
        "[analysis_agent] [1a0578d4c4dd7f1f14dd] │ Running for 19s",
        "[analysis_agent] [1a0578d4c4dd7f1f14dd] │ No new output in the last 7s",
        "[analysis_agent] [1a0578d4c4dd7f1f14dd] │ Keep working on any other ready analysis or tool tasks first. Only wait when this background task is the remaining blocker.",
        "[analysis_agent] [1a0578d4c4dd7f1f14dd] │ ",
        '[analysis_agent] [1a0578d4c4dd7f1f14dd] │ Background task_id="bg_2" status="running" source="engine_progress"',
        "[analysis_agent] [1a0578d4c4dd7f1f14dd] │ Tool: run_subagent",
        "[analysis_agent] [1a0578d4c4dd7f1f14dd] │ Note: Second task still visible at the end of the notification.",
    ]

    assert lines == expected
