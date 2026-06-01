"""Query loop data model."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from notification import NotificationRule
from prompt.prompt_report_recorder import PromptReportRecorder
from providers.types import SupportsStreamingMessages
from tools import ExecutionMetadata, ToolRegistry, ToolResult

if TYPE_CHECKING:
    from engine.query.request import QueryRunRequest
    from message.events import StreamEvent


# A per-agent event source. Called once per loop turn with the current
# ``QueryContext`` and the built ``QueryRunRequest``; yields the same
# ``StreamEvent`` shape the provider client produces. Production leaves this
# unset (``None``) so the loop streams from ``api_client``; the mock harness
# injects a scripted source so a mock agent runs through the real loop.
EventSource = Callable[
    ["QueryContext", "QueryRunRequest"], "AsyncIterator[StreamEvent]"
]


class QueryExitReason(StrEnum):
    """Why the query loop exited."""

    TOOL_STOP = "tool_stop"                              # success: terminal tool submitted
    TERMINAL_NOT_SUBMITTED = "terminal_not_submitted"    # failure: hard ceiling crossed


@dataclass
class QueryContext:
    api_client: SupportsStreamingMessages
    tool_registry: ToolRegistry
    cwd: Path
    model: str
    system_prompt: str
    max_tokens: int
    tool_call_limit: int
    agent_name: str = ""
    agent_run_id: str = ""
    task_id: str = ""
    tool_calls_used: int = 0
    text_only_no_terminal_turns: int = 0
    tool_metadata: ExecutionMetadata | None = None
    enable_background_tasks: bool = False
    terminal_tools: set[str] = field(default_factory=set)
    exit_reason: QueryExitReason | None = None
    terminal_result: ToolResult | None = None
    # Injected per-agent event source. ``None`` (production default) ⇒ the loop
    # streams from ``api_client``; a scripted source ⇒ the agent runs the real
    # loop against mock events. See ``loop._consume_provider_stream``.
    event_source: EventSource | None = None
    prompt_report_recorder: PromptReportRecorder | None = None
    # Notification rules evaluated at the top of every turn. See
    # ``notification.dispatch_rules``. Default empty list = disabled.
    notification_rules: list[NotificationRule] = field(default_factory=list)
    # Run-scoped dedup state managed by ``dispatch_rules``: fire_once rule
    # names that have already fired this run.
    notification_fired: set[str] = field(default_factory=set)
    # Free-form per-rule scratchpad keyed by ``rule.name``. Rules own the
    # schema of their own slot.
    notification_state: dict[str, Any] = field(default_factory=dict)
