"""``request_replan`` tool — signals that the dispatcher should summon a replanner."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from team.models import ReplanRequest
from tools.core.base import ToolExecutionContext
from tools.posthook.base import SubmitPosthookTool


class RequestReplanInput(BaseModel):
    reason: str = Field(
        ...,
        description="Brief summary of why replanning is needed.",
        min_length=1,
    )
    context: str = Field(
        ...,
        description=(
            "Detailed failure analysis with error output, clustered by root cause."
        ),
        min_length=1,
    )
    suggestion: str = Field(
        "",
        description="Optional hint for the replanner about what should happen next.",
    )


class RequestReplanTool(SubmitPosthookTool):
    name: str = "request_replan"
    description: str = (
        "Request that the dispatcher summon a replanner to redraft the DAG at "
        "the current depth level. Use when the failure is systemic and the "
        "current work item's task definition itself is flawed."
    )
    input_model = RequestReplanInput
    default_metadata_key: str = "submitted_summary"

    def _build_payload(
        self, arguments: BaseModel, context: ToolExecutionContext
    ) -> tuple[Any, str | None]:
        assert isinstance(arguments, RequestReplanInput)
        return (
            ReplanRequest(
                reason=arguments.reason,
                context=arguments.context,
                suggestion=arguments.suggestion,
            ),
            None,
        )

    def _accepted_message(self, payload: Any) -> str:
        assert isinstance(payload, ReplanRequest)
        return "Replan request accepted. Dispatcher will summon a replanner."
