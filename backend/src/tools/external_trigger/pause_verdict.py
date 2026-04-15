"""PauseVerdictTool — blocker impact assessment tool for external-trigger runs."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, field_validator

from tools.core.base import BaseTool, ToolExecutionContext, ToolResult


class PauseVerdictInput(BaseModel):
    """Pydantic input model for pause_verdict tool."""

    answer: Literal["YES", "NO"]
    reason: str = ""

    @field_validator("answer", mode="before")
    @classmethod
    def normalize_answer(cls, v: str) -> str:
        v = str(v).strip().upper()
        return v if v in ("YES", "NO") else "NO"


class PauseVerdictTool(BaseTool):
    """Submit assessment of whether a task is affected by a blocker."""

    name = "pause_verdict"
    description = "Submit your assessment of whether this task is affected by the blocker."
    input_model = PauseVerdictInput

    async def execute(self, arguments: BaseModel, context: ToolExecutionContext) -> ToolResult:
        """No-op — the external_trigger runner captures the tool call.

        Side effects (pausing tasks, storing verdicts) are handled by the
        caller (conductor) after the runner returns.
        """
        return ToolResult(output="verdict_accepted")
