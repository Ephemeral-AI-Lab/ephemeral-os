"""ask_advisor — caller-side tool that spawns an advisor and waits for its verdict.

The advisor runs as a transient ``Task(role='advisor')`` dispatched by the
TaskCenter run loop. ``ask_advisor`` polls the task's status until it
terminates (DONE / FAILED), then decodes the verdict from the task's
last summary and returns it.

Polling cadence: 50ms tight-loop sleep. Adequate for advisors that take
seconds; a future iteration may swap in an asyncio.Event keyed on the
advisor's terminal transition.
"""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel, Field

from task_center.harness_agents.advisor.lifecycle import decode_verdict
from task_center.model import Status
from tools.core.base import ToolExecutionContextService, ToolResult
from tools.core.decorator import tool


_POLL_INTERVAL_SECONDS = 0.05


class AskAdvisorInput(BaseModel):
    terminal_tool: str = Field(
        ...,
        min_length=1,
        description="Name of the gated terminal you intend to call next.",
    )
    proposed_input: dict[str, Any] = Field(
        ...,
        description=(
            "The exact payload you would pass to the terminal. Strict-equality "
            "match — any drift between this and the actual terminal call "
            "voids the accept."
        ),
    )
    reason: str = Field(
        ...,
        min_length=1,
        description=(
            "One-paragraph rationale for the proposal. The advisor reads this "
            "alongside your context object to decide accept vs. reject."
        ),
    )
    calling_agent_context: str = Field(
        default="",
        description=(
            "Free-form context the advisor should read alongside the proposal: "
            "evidence, artifacts, prior decisions. Defaults to empty (the "
            "advisor sees only the proposal); enrich this when the proposal "
            "depends on artifacts the advisor cannot otherwise see."
        ),
    )


class AskAdvisorOutput(BaseModel):
    verdict: str = Field(..., description="'accept' or 'reject'.")
    reason: str = Field(..., description="The advisor's rationale.")


@tool(
    name="ask_advisor",
    description=(
        "Consult the advisor before calling a high-stakes terminal "
        "(submit_full_plan, submit_partial_plan, submit_verification_*, "
        "submit_evaluation_*, request_plan). Returns {verdict, reason}. "
        "On accept, you may call the terminal with the EXACT same "
        "proposed_input. On reject, call a different terminal — there is "
        "no rephrase-and-resubmit path."
    ),
    input_model=AskAdvisorInput,
    output_model=AskAdvisorOutput,
)
async def ask_advisor(
    terminal_tool: str,
    proposed_input: dict[str, Any],
    reason: str,
    calling_agent_context: str,
    *,
    context: ToolExecutionContextService,
) -> ToolResult:
    tc = context.get("task_center")
    caller_id = context.get("task_id")
    if tc is None or caller_id is None:
        return ToolResult(
            output="ask_advisor: missing task_center or task_id in metadata",
            is_error=True,
        )

    advisor_task = tc.create_advisor(
        caller_id=caller_id,
        terminal_tool=terminal_tool,
        proposed_input=proposed_input,
        agent_reason=reason,
        calling_agent_context=calling_agent_context,
    )

    # Poll until the advisor terminates. Yields control to the event loop
    # so the dispatcher can run the advisor's coroutine.
    while advisor_task.status not in (Status.DONE, Status.FAILED):
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)
        advisor_task = tc.graph.get(advisor_task.id)

    # Read the verdict from the last summary.
    if not advisor_task.summaries:
        verdict, advisor_reason = (
            "reject",
            "advisor produced no summary",
        )
    else:
        verdict, advisor_reason = decode_verdict(advisor_task.summaries[-1].text)

    return ToolResult(
        output=AskAdvisorOutput(verdict=verdict, reason=advisor_reason).model_dump_json()
    )
