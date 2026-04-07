"""RunParallelAgents — launch multiple agents in parallel over work items."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from typing import Any, Protocol, runtime_checkable

from tools.core.base import BaseTool, ToolExecutionContext, ToolResult
from tools.core.decorator import tool

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


async def _launch_worker(
    run_agent_fn: AgentRunFn,
    semaphore: asyncio.Semaphore,
    agent_name: str,
    prompt: str,
    item: str,
    index: int,
) -> dict[str, Any]:
    async with semaphore:
        session_id = str(uuid.uuid4())
        try:
            result = await run_agent_fn(
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


# ---------------------------------------------------------------------------
# Tool factory
# ---------------------------------------------------------------------------


def make_run_parallel_agents_tool(run_agent_fn: AgentRunFn | None = None) -> BaseTool:
    """Create a parallel agent dispatch tool with an optional pre-bound runner."""

    @tool(
        name="run_parallel_agents",
        description=(
            "Launch multiple agents in parallel to process a list of work items. "
            "Each worker receives the prompt template with {{item}} and {{index}} substituted. "
            "Returns a list of worker results."
        ),
    )
    async def run_parallel_agents(
        items: list[str],
        agent_name: str,
        prompt_template: str,
        max_workers: int | None = None,
        *,
        context: ToolExecutionContext,
    ) -> ToolResult:
        """Launch multiple agents in parallel to process work items.

        Args:
            items: Work items to process. Each item is dispatched to a separate worker agent.
            agent_name: Name of the worker agent to use for each item.
            prompt_template: Prompt template with {{item}} and {{index}} placeholders.
            max_workers: Maximum parallel workers. Defaults to number of items.

        Returns:
            results (list): Worker results with status, index, item, content or error
            total (int): Total number of items
            success_count (int): Number of successful workers
            failed_count (int): Number of failed workers
        """
        effective_fn = run_agent_fn or context.metadata.get("run_agent_fn")

        if effective_fn is None:
            return ToolResult(
                output=json.dumps({"error": "No agent runner function configured"}),
                is_error=True,
            )

        if not items:
            return ToolResult(
                output=json.dumps({"error": "No work items provided"}),
                is_error=True,
            )

        workers = max(1, max_workers or len(items))
        semaphore = asyncio.Semaphore(workers)

        tasks = [
            asyncio.create_task(
                _launch_worker(
                    run_agent_fn=effective_fn,
                    semaphore=semaphore,
                    agent_name=agent_name,
                    prompt=_render_prompt(prompt_template, item, idx),
                    item=item,
                    index=idx,
                )
            )
            for idx, item in enumerate(items)
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        processed = []
        for idx, (item, result) in enumerate(zip(items, results)):
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
                "total": len(items),
                "success_count": success_count,
                "failed_count": len(items) - success_count,
            })
        )

    return run_parallel_agents
