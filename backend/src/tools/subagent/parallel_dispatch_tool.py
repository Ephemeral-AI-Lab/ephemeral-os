"""RunParallelAgentsTool — launch multiple agents in parallel over work items."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from typing import Any, Callable, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from tools.base import BaseTool, ToolExecutionContext, ToolResult

logger = logging.getLogger(__name__)

# Template variable pattern: {{item}}, {{index}}
_TEMPLATE_VAR_RE = re.compile(r"\{\{(item|index)\}\}")


# ---------------------------------------------------------------------------
# Typed protocol for the agent spawn function
# ---------------------------------------------------------------------------


@runtime_checkable
class AgentRunFn(Protocol):
    """Callable that runs a named agent and returns its result."""

    async def __call__(
        self,
        agent_name: str,
        prompt: str,
        *,
        session_id: str | None = None,
        options: dict[str, Any] | None = None,
    ) -> Any: ...


# ---------------------------------------------------------------------------
# Input model
# ---------------------------------------------------------------------------


class RunParallelAgentsInput(BaseModel):
    """Arguments for parallel agent dispatch."""

    items: list[str] = Field(
        description="Work items to process. Each item is dispatched to a separate worker agent.",
    )
    agent_name: str = Field(
        description="Name of the worker agent to use for each item.",
    )
    prompt_template: str = Field(
        description=(
            "Prompt template with {{item}} and {{index}} placeholders. "
            "Each worker receives this template with placeholders substituted."
        ),
    )
    max_workers: int | None = Field(
        default=None,
        description="Maximum parallel workers. Defaults to number of items.",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _render_prompt(template: str, item: str, index: int) -> str:
    def _replacer(m: re.Match) -> str:
        key = m.group(1)
        if key == "item":
            return item
        if key == "index":
            return str(index)
        return m.group(0)

    return _TEMPLATE_VAR_RE.sub(_replacer, template)


def _coerce_result_content(result: Any) -> str:
    if result is None:
        return ""
    if isinstance(result, str):
        return result
    content = getattr(result, "content", None)
    if isinstance(content, str):
        return content
    message = getattr(result, "message", None)
    if message is not None:
        msg_content = getattr(message, "content", None)
        if isinstance(msg_content, str):
            return msg_content
    return repr(result)


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------


class RunParallelAgentsTool(BaseTool):
    """Launch multiple agents in parallel to process a list of work items."""

    name = "run_parallel_agents"
    description = (
        "Launch multiple agents in parallel to process a list of work items. "
        "Each worker receives the prompt template with {{item}} and {{index}} substituted. "
        "Returns a list of worker results."
    )
    input_model = RunParallelAgentsInput

    def __init__(self, *, run_agent_fn: AgentRunFn | None = None) -> None:
        self._run_agent_fn = run_agent_fn

    async def _launch_worker(
        self,
        semaphore: asyncio.Semaphore,
        agent_name: str,
        prompt: str,
        item: str,
        index: int,
    ) -> dict[str, Any]:
        async with semaphore:
            session_id = str(uuid.uuid4())
            try:
                result = await self._run_agent_fn(
                    agent_name,
                    prompt,
                    session_id=session_id,
                )
                return {
                    "status": "success",
                    "index": index,
                    "item": item,
                    "session_id": session_id,
                    "content": _coerce_result_content(result),
                }
            except Exception as exc:
                return {
                    "status": "error",
                    "index": index,
                    "item": item,
                    "session_id": session_id,
                    "error": str(exc),
                }

    async def execute(
        self, arguments: RunParallelAgentsInput, context: ToolExecutionContext
    ) -> ToolResult:
        if self._run_agent_fn is None:
            self._run_agent_fn = context.metadata.get("run_agent_fn")

        if self._run_agent_fn is None:
            return ToolResult(
                output=json.dumps({"error": "No agent runner function configured"}),
                is_error=True,
            )

        if not arguments.items:
            return ToolResult(
                output=json.dumps({"error": "No work items provided"}),
                is_error=True,
            )

        max_workers = max(1, arguments.max_workers or len(arguments.items))
        semaphore = asyncio.Semaphore(max_workers)

        tasks = [
            asyncio.create_task(
                self._launch_worker(
                    semaphore=semaphore,
                    agent_name=arguments.agent_name,
                    prompt=_render_prompt(arguments.prompt_template, item, idx),
                    item=item,
                    index=idx,
                )
            )
            for idx, item in enumerate(arguments.items)
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        processed = []
        for idx, (item, result) in enumerate(zip(arguments.items, results)):
            if isinstance(result, Exception):
                processed.append({
                    "status": "error",
                    "index": idx,
                    "item": item,
                    "error": str(result),
                })
            elif isinstance(result, dict):
                processed.append(result)
            else:
                processed.append({
                    "status": "error",
                    "index": idx,
                    "item": item,
                    "error": repr(result),
                })

        success_count = sum(1 for r in processed if r.get("status") == "success")
        return ToolResult(
            output=json.dumps({
                "results": processed,
                "total": len(arguments.items),
                "success_count": success_count,
                "failed_count": len(arguments.items) - success_count,
            })
        )
