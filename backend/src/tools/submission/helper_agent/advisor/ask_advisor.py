"""ask_advisor blocking helper tool."""

from __future__ import annotations

import json

from pydantic import BaseModel, Field

from agents import get_definition
from tools.core.context import ToolExecutionContextService
from tools.core.decorator import tool
from tools.core.results import TextToolOutput, ToolResult
from tools.submission.hooks import HelperRequestGate


class AskAdvisorInput(BaseModel):
    tool_name: str = Field(..., min_length=1)
    tool_payloads: list[dict[str, object]] = Field(default_factory=list)
    prompt: str = Field(..., min_length=1)


def _advisor_prompt(
    *,
    tool_name: str,
    tool_payloads: list[dict[str, object]],
    prompt: str,
) -> str:
    payloads = json.dumps(tool_payloads, indent=2, sort_keys=True)
    return (
        "Review this pending decision or terminal submission.\n\n"
        f"Tool name: {tool_name}\n\n"
        f"Tool payloads:\n{payloads}\n\n"
        f"Prompt:\n{prompt}"
    )


@tool(
    name="ask_advisor",
    description=(
        "Ask the advisor helper for blocking read-only advice before a "
        "terminal submission or decision."
    ),
    input_model=AskAdvisorInput,
    output_model=TextToolOutput,
    pre_hooks=(
        HelperRequestGate(
            "ask_advisor",
            frozenset({"planner", "executor", "verifier", "evaluator"}),
        ),
    ),
)
async def ask_advisor(
    tool_name: str,
    tool_payloads: list[dict[str, object]],
    prompt: str,
    *,
    context: ToolExecutionContextService,
) -> ToolResult:
    from engine.runtime.lifecycle import run_ephemeral_agent

    runtime_config = context.runtime_config
    if runtime_config is None:
        return ToolResult(
            output="ask_advisor: missing runtime_config in execution context.",
            is_error=True,
        )

    advisor = get_definition("advisor")
    if advisor is None:
        return ToolResult(output="ask_advisor: advisor agent is not registered.", is_error=True)

    result = await run_ephemeral_agent(
        runtime_config,
        _advisor_prompt(
            tool_name=tool_name,
            tool_payloads=tool_payloads,
            prompt=prompt,
        ),
        agent_def=advisor,
        sandbox_id=context.sandbox_id or None,
        persist_agent_run=False,
        extra_tool_metadata=context.services_with_overrides(
            role="advisor",
            agent_type="agent",
        ),
    )
    if result.status == "failed":
        return ToolResult(output=f"ask_advisor: advisor crashed: {result.error}", is_error=True)
    if result.terminal_result is None:
        return ToolResult(
            output="ask_advisor: advisor exited without submit_advisor_feedback.",
            is_error=True,
        )
    terminal = result.terminal_result
    return ToolResult(
        output=terminal.output,
        is_error=terminal.is_error,
        metadata=dict(terminal.metadata or {}),
    )
