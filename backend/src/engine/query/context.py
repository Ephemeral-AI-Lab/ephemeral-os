"""Query loop data model."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from notification import NotificationRule
from prompt.prompt_report_recorder import PromptReportRecorder
from providers.types import SupportsStreamingMessages
from tools import ExecutionMetadata, ToolRegistry, ToolResult


class QueryExitReason(StrEnum):
    """Why the query loop exited."""

    TEXT_RESPONSE = "text_response"      # no tool_uses in response
    TOOL_STOP = "tool_stop"              # terminal tool succeeded
    RESOURCE_LIMIT = "resource_limit"    # budget exhausted or max_tokens


@dataclass(frozen=True)
class _ToolBudgetView:
    """Read-only snapshot of tool-call budget state for notification rules."""

    used: int
    limit: int | None

    @property
    def fraction_used(self) -> float:
        if self.limit is None or self.limit <= 0:
            return 0.0
        return self.used / self.limit


@dataclass
class QueryContext:
    api_client: SupportsStreamingMessages
    tool_registry: ToolRegistry
    cwd: Path
    model: str
    system_prompt: str
    max_tokens: int
    agent_name: str = ""
    run_id: str = ""
    task_center_task_id: str = ""
    tool_call_limit: int | None = None
    tool_calls_used: int = 0
    tool_metadata: ExecutionMetadata | None = None
    enable_background_tasks: bool = False
    terminal_tools: set[str] = field(default_factory=set)
    exit_reason: QueryExitReason | None = None
    terminal_result: ToolResult | None = None
    prompt_report_recorder: PromptReportRecorder | None = None
    # Notification rules evaluated at the top of every turn. See
    # ``notification.dispatch_rules``. Default empty list = disabled.
    notification_rules: list[NotificationRule] = field(default_factory=list)
    # Run-scoped dedup state managed by ``dispatch_rules``: fire_once rule
    # names that have already fired this run.
    notification_fired: set[str] = field(default_factory=set)
    # Free-form per-rule scratchpad keyed by ``rule.name``. Rules own the
    # schema of their own slot (e.g., budget_warning tracks last_fired).
    notification_state: dict[str, Any] = field(default_factory=dict)

    @property
    def tool_budget(self) -> _ToolBudgetView:
        """Read-only view of tool-call budget for notification rule triggers."""
        return _ToolBudgetView(used=self.tool_calls_used, limit=self.tool_call_limit)
