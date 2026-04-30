"""ask_resolver blocking helper tool."""

from __future__ import annotations

from pydantic import BaseModel, Field

from agents import get_definition
from tools.core.context import ToolExecutionContextService
from tools.core.decorator import tool
from tools.core.results import TextToolOutput, ToolResult
from tools.submission.hooks import HelperRequestGate


class AskResolverInput(BaseModel):
    issues_to_resolve: list[str] = Field(..., min_length=1)
    issue_context: str = Field(default="")


def _resolver_prompt(
    *, issues_to_resolve: list[str], issue_context: str
) -> str:
    issues = "\n".join(f"- {issue}" for issue in issues_to_resolve)
    return (
        "Resolve the following verifier or evaluator issues.\n\n"
        f"Issues:\n{issues}\n\n"
        f"Context:\n{issue_context}"
    )


@tool(
    name="ask_resolver",
    description=(
        "Ask the resolver helper to address unresolved verifier or evaluator "
        "issues. The resolver may edit files."
    ),
    input_model=AskResolverInput,
    output_model=TextToolOutput,
    pre_hooks=(
        HelperRequestGate(
            "ask_resolver",
            frozenset({"verifier", "evaluator"}),
        ),
    ),
)
async def ask_resolver(
    issues_to_resolve: list[str],
    issue_context: str,
    *,
    context: ToolExecutionContextService,
) -> ToolResult:
    from engine.runtime.lifecycle import run_ephemeral_agent

    runtime_config = context.runtime_config
    if runtime_config is None:
        return ToolResult(
            output="ask_resolver: missing runtime_config in execution context.",
            is_error=True,
        )

    resolver = get_definition("resolver")
    if resolver is None:
        return ToolResult(output="ask_resolver: resolver agent is not registered.", is_error=True)

    result = await run_ephemeral_agent(
        runtime_config,
        _resolver_prompt(
            issues_to_resolve=issues_to_resolve,
            issue_context=issue_context,
        ),
        agent_def=resolver,
        sandbox_id=context.sandbox_id or None,
        persist_agent_run=False,
        extra_tool_metadata=context.services_with_overrides(
            role="resolver",
            agent_type="agent",
        ),
    )
    if result.status == "failed":
        return ToolResult(output=f"ask_resolver: resolver crashed: {result.error}", is_error=True)
    if result.terminal_result is None:
        return ToolResult(
            output="ask_resolver: resolver exited without submit_resolver_result.",
            is_error=True,
        )
    terminal = result.terminal_result
    return ToolResult(
        output=terminal.output,
        is_error=terminal.is_error,
        metadata=dict(terminal.metadata or {}),
    )
