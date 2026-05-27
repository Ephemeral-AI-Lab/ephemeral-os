"""Events yielded by the query engine."""

from dataclasses import dataclass, field
from typing import Any

from providers.types import UsageSnapshot
from message.message import Message
from notification import SystemNotification


# Identity fields carried by every StreamEvent:
#   agent_name — short label of the emitting agent ("coordinator",
#                "developer-1", "eval_agent", ...). Empty string for
#                standalone single-agent callers.
#   run_id    — stable identifier for the unit of work that produced the
#                event. For a coordinator's own response this is its run_id;
#                for a dispatched subagent it is the subagent's run_id
#                (distinct from the parent). Lets printers group and
#                indent events by work unit even when agents interleave.


@dataclass(frozen=True)
class ThinkingDeltaEvent:
    """Incremental thinking/reasoning content from the model."""

    text: str
    agent_name: str = ""
    run_id: str = ""


@dataclass(frozen=True)
class AssistantTextDeltaEvent:
    """Incremental assistant text."""

    text: str
    agent_name: str = ""
    run_id: str = ""


@dataclass(frozen=True)
class AssistantMessageCompleteEvent:
    """Completed assistant message."""

    message: Message
    usage: UsageSnapshot
    stop_reason: str | None = None
    agent_name: str = ""
    run_id: str = ""


@dataclass(frozen=True)
class ToolUseDeltaEvent:
    """A tool_use content block arrived mid-stream (pre-execution)."""

    tool_use_id: str
    name: str
    input: dict[str, Any]
    agent_name: str = ""
    run_id: str = ""


@dataclass(frozen=True)
class ToolExecutionStartedEvent:
    """The engine is about to execute a tool."""

    tool_name: str
    tool_input: dict[str, Any]
    tool_use_id: str = ""
    agent_name: str = ""
    run_id: str = ""


@dataclass(frozen=True)
class ToolExecutionCompletedEvent:
    """A tool has finished executing."""

    tool_name: str
    output: str
    is_error: bool = False
    tool_use_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    is_terminal: bool = False
    agent_name: str = ""
    run_id: str = ""


@dataclass(frozen=True)
class ToolExecutionProgressEvent:
    """Progress update from a running tool.

    Emitted during long-running tool execution (e.g., bash commands,
    test runners) so the LLM can see partial output and decide
    whether to continue or abort.
    """

    tool_use_id: str
    tool_name: str
    output: str
    agent_name: str = ""
    run_id: str = ""


@dataclass(frozen=True)
class ToolExecutionCancelledEvent:
    """A tool was cancelled by LLM abort signal."""

    tool_use_id: str
    tool_name: str
    reason: str
    agent_name: str = ""
    run_id: str = ""


@dataclass(frozen=True)
class BackgroundTaskStartedEvent:
    """A tool has been launched as a background task."""

    task_id: str
    tool_name: str
    tool_input: dict[str, Any]
    agent_name: str = ""
    run_id: str = ""


StreamEvent = (
    ThinkingDeltaEvent
    | AssistantTextDeltaEvent
    | AssistantMessageCompleteEvent
    | ToolUseDeltaEvent
    | ToolExecutionStartedEvent
    | ToolExecutionCompletedEvent
    | ToolExecutionProgressEvent
    | ToolExecutionCancelledEvent
    | BackgroundTaskStartedEvent
    | SystemNotification
)
