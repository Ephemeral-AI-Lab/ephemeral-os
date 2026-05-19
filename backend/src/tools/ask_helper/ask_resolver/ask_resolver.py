"""``ask_resolver`` — blocking edit-capable helper for verifier/evaluator issues.

Same direct-launch shape as ``ask_advisor`` (no composer, no inherited
packet). user_msg_1 carries the parent's verbatim context + task +
filtered transcript (resolver mode); user_msg_2 lists the issues to
resolve and the resolver's task.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from tools._framework.core.context import ToolExecutionContextService
from tools._framework.core.decorator import tool
from tools._framework.core.results import TextToolOutput, ToolResult
from .prompt import get_ask_resolver_description
from tools.ask_helper._lib._compose import (
    HelperMessageError,
    as_initial_message,
    assemble_user_msg_1,
    build_helper_messages,
)


class AskResolverInput(BaseModel):
    issues_to_resolve: list[str] = Field(..., min_length=1)
    issue_context: str = Field(default="")


_RESOLVER_TASK_SECTION = (
    "# Your task\n\n"
    "You are the resolver. Read the issues below, consult the parent "
    "transcript above for the failing tool calls and context, and edit "
    "files as needed to resolve every issue. When done, summarize what "
    "you changed and which issues you resolved via "
    "`submit_resolver_result`."
)


def _build_resolver_user_msg_2(
    *, issues_to_resolve: list[str], issue_context: str
) -> str:
    issues = "\n".join(f"- {issue}" for issue in issues_to_resolve)
    issues_block = f"# Issues to resolve\n\n{issues}"
    if issue_context.strip():
        issues_block += f"\n\n## Additional context\n\n{issue_context.strip()}"
    return f"{issues_block}\n\n{_RESOLVER_TASK_SECTION}"


@tool(
    name="ask_resolver",
    description=get_ask_resolver_description(),
    input_model=AskResolverInput,
    output_model=TextToolOutput,
)
async def ask_resolver(
    issues_to_resolve: list[str],
    issue_context: str,
    *,
    context: ToolExecutionContextService,
) -> ToolResult:
    from engine.api import run_ephemeral_agent

    runtime_config = context.runtime_config
    if runtime_config is None:
        return ToolResult(
            output="ask_resolver: missing runtime_config in execution context.",
            is_error=True,
        )

    try:
        messages = build_helper_messages(
            helper_role="resolver", mode="resolver", context=context
        )
    except HelperMessageError as exc:
        return exc.to_tool_result()

    user_msg_1 = assemble_user_msg_1(messages)
    user_msg_2 = _build_resolver_user_msg_2(
        issues_to_resolve=issues_to_resolve, issue_context=issue_context
    )

    result = await run_ephemeral_agent(
        runtime_config,
        user_msg_2,
        agent_def=messages.helper_agent_def,
        sandbox_id=context.sandbox_id or None,
        persist_agent_run=False,
        extra_tool_metadata=context.services_with_overrides(
            role="resolver",
            agent_type="agent",
        ),
        initial_messages=[as_initial_message(user_msg_1)],
    )
    if result.status == "failed":
        return ToolResult(
            output=f"ask_resolver: resolver crashed: {result.error}",
            is_error=True,
        )
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
