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

    TEXT_RESPONSE = "text_response"      # no tool_uses (within tolerance)
    TOOL_STOP = "tool_stop"              # terminal tool succeeded
    RESOURCE_LIMIT = "resource_limit"    # overshoot_units > tolerance via tool path
    TERMINAL_REFUSED = "terminal_refused"  # overshoot_units > tolerance via text-only path


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
    max_tolerance_after_max_tool_call: int | None = None
    text_only_no_terminal_turns: int = 0
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

    @property
    def tool_overshoot(self) -> int:
        """Tool calls executed past the soft ``tool_call_limit``. 0 below limit."""
        if self.tool_call_limit is None:
            return 0
        return max(0, self.tool_calls_used - self.tool_call_limit)

    @property
    def overshoot_units(self) -> int:
        """Total 'extra work' the agent has spent beyond its soft budget.

        Sum of (calls past ``tool_call_limit``) + (text-only turns without a
        terminal call). The hard ceiling is
        ``max_tolerance_after_max_tool_call`` compared against this single
        number. Returning text without a terminal burns the same budget as
        making one extra tool call, so the agent cannot game the cap by
        alternating between modes.
        """
        return self.tool_overshoot + self.text_only_no_terminal_turns
