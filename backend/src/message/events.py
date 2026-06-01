"""Events yielded by the query engine."""

from dataclasses import dataclass, field
from typing import Any

from providers.types import UsageSnapshot
from message.message import Message
from notification import SystemNotification


# Identity fields carried by every StreamEvent:
#   agent_name    — short label of the emitting agent ("coordinator",
#                   "developer-1", "eval_agent", ...). Empty string for
#                   standalone single-agent callers.
#   agent_run_id  — the engine agent-run id (``AgentRunTracker.agent_run_id``)
#                   of the run that produced the event. Distinct per agent run,
#                   so printers/recorders group and indent events by run even
#                   when agents interleave. The persisted Task id is carried
#                   separately on tool metadata as ``task_id``.


@dataclass(frozen=True)
class ThinkingDeltaEvent:
    """Incremental thinking/reasoning content from the model."""

    text: str
    agent_name: str = ""
    agent_run_id: str = ""


@dataclass(frozen=True)
class AssistantTextDeltaEvent:
    """Incremental assistant text."""

    text: str
    agent_name: str = ""
    agent_run_id: str = ""


@dataclass(frozen=True)
class AssistantMessageCompleteEvent:
    """Completed assistant message."""

    message: Message
    usage: UsageSnapshot
    stop_reason: str | None = None
    agent_name: str = ""
    agent_run_id: str = ""


@dataclass(frozen=True)
class ToolUseDeltaEvent:
    """A tool_use content block arrived mid-stream (pre-execution)."""

    tool_use_id: str
    name: str
    input: dict[str, Any]
    agent_name: str = ""
    agent_run_id: str = ""


@dataclass(frozen=True)
class ToolExecutionStartedEvent:
    """The engine is about to execute a tool."""

    tool_name: str
    tool_input: dict[str, Any]
    tool_use_id: str = ""
    agent_name: str = ""
    agent_run_id: str = ""


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
    agent_run_id: str = ""


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
    agent_run_id: str = ""


@dataclass(frozen=True)
class ToolExecutionCancelledEvent:
    """A tool was cancelled by LLM abort signal."""

    tool_use_id: str
    tool_name: str
    reason: str
    agent_name: str = ""
    agent_run_id: str = ""


@dataclass(frozen=True)
class BackgroundTaskStartedEvent:
    """A supervised async tool/session has been launched."""

    task_id: str
    tool_name: str
    tool_input: dict[str, Any]
    agent_name: str = ""
    agent_run_id: str = ""


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
