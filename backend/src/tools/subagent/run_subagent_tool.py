"""run_subagent — spawn a focused worker subagent and run it to completion.

Synchronous from the parent's POV: the parent's tool call awaits the
subagent's full lifecycle and receives the subagent's terminal-tool output
as its own ``ToolResult``. The subagent must terminate via a registered
terminal tool (typically ``submit_exploration_result``); the engine's
terminal-nudge cycle in ``run_query`` enforces this.

Subagents cannot spawn further subagents — recursion is rejected at
validation time so the focused-worker contract holds.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from tools.core.base import ExecutionMetadata, TextToolOutput, ToolExecutionContext, ToolResult
from tools.core.decorator import tool

logger = logging.getLogger(__name__)


@dataclass
class _ValidatedRunSubagentRequest:
    sub_def: Any


class RunSubagentInput(BaseModel):
    """Runtime input model for run_subagent."""

    agent_name: str = Field(
        ...,
        description="Name of a registered dispatchable subagent.",
    )
    prompt: str = Field(
        ...,
        min_length=1,
        description=(
            "Free-form, fully descriptive task prompt. Include any target "
            "paths, context, and required actions inline — this is the only "
            "channel the subagent receives."
        ),
    )


def _validate_run_subagent_request(
    *,
    agent_name: str,
    prompt: str | None,
    context: ToolExecutionContext,
) -> ToolResult | _ValidatedRunSubagentRequest:
    from agents import get_definition

    parent_cfg = context.metadata.session_config
    if parent_cfg is None:
        return ToolResult(
            output="run_subagent: missing session_config in execution context",
            is_error=True,
        )

    if not isinstance(prompt, str) or not prompt.strip():
        return ToolResult(
            output="run_subagent: `prompt` must be a non-empty string.",
            is_error=True,
        )

    # Recursion gate — subagents are focused workers and may not spawn
    # further subagents. (Today the gate is implicit because background
    # tools are stripped for ``agent_type=="subagent"``; with run_subagent
    # now synchronous we enforce it explicitly.)
    caller_agent_type = context.metadata.get("agent_type")
    if caller_agent_type == "subagent":
        return ToolResult(
            output=(
                "run_subagent: subagents may not spawn further subagents. "
                "This is a hard contract — handle the work directly or "
                "submit your findings via submit_exploration_result."
            ),
            is_error=True,
        )

    sub_def = get_definition(agent_name)
    if sub_def is None:
        return ToolResult(
            output=f"run_subagent: agent '{agent_name}' is not registered.",
            is_error=True,
        )
    if sub_def.agent_type != "subagent":
        return ToolResult(
            output=(
                f"run_subagent: agent '{agent_name}' is not a subagent "
                f"(agent_type={sub_def.agent_type!r}); "
                "only subagent-typed agents may be dispatched here."
            ),
            is_error=True,
        )
    return _ValidatedRunSubagentRequest(sub_def=sub_def)


@tool(
    name="run_subagent",
    description=(
        "Run a registered subagent to completion and return its findings. "
        "The subagent receives `prompt` as its only input and must finish "
        "by calling its terminal tool (typically submit_exploration_result); "
        "that tool's text output is returned as this tool's result."
    ),
    short_description="Run a subagent and return its findings.",
    input_model=RunSubagentInput,
    output_model=TextToolOutput,
    task_type="subagent",
)
async def run_subagent(
    agent_name: str,
    prompt: str,
    *,
    context: ToolExecutionContext,
) -> ToolResult:
    """Spawn a named subagent synchronously and forward its terminal result."""
    from engine.runtime.lifecycle import run_ephemeral_agent

    validation = _validate_run_subagent_request(
        agent_name=agent_name,
        prompt=prompt,
        context=context,
    )
    if isinstance(validation, ToolResult):
        return validation
    sub_def = validation.sub_def

    parent_cfg = context.metadata.session_config
    sandbox_id = context.metadata.sandbox_id or None
    parent_run_id = context.metadata.agent_run_id
    parent_task_id = context.metadata.get("task_id")

    # Tag the spawned subagent's metadata so the recursion gate above and
    # any other agent_type-aware checks see the correct caller type when
    # the subagent itself dispatches tools.
    sub_meta = ExecutionMetadata()
    sub_meta["agent_type"] = "subagent"
    if sub_def.role:
        sub_meta["role"] = sub_def.role

    result = await run_ephemeral_agent(
        parent_cfg,
        prompt,
        agent_def=sub_def,
        sandbox_id=sandbox_id,
        persist_session=False,
        parent_run_id=parent_run_id if isinstance(parent_run_id, str) else None,
        parent_task_id=parent_task_id if isinstance(parent_task_id, str) else None,
        extra_tool_metadata=sub_meta,
    )

    if result.status == "failed":
        return ToolResult(
            output=f"run_subagent: subagent crashed: {result.error}",
            is_error=True,
        )
    if result.terminal_result is None:
        return ToolResult(
            output=(
                "run_subagent: subagent exited without calling a terminal tool "
                "(e.g. submit_exploration_result). The findings were not delivered."
            ),
            is_error=True,
        )
    # Forward the terminal tool's ToolResult verbatim — the parent receives
    # whatever findings text the subagent submitted.
    return result.terminal_result
