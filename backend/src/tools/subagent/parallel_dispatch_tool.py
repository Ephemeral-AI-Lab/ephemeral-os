"""RunParallelAgentsTool — launch multiple agents in parallel over work items."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from collections import OrderedDict
from typing import Any

from pydantic import BaseModel, Field

from tools.base import BaseTool, ToolExecutionContext, ToolResult

logger = logging.getLogger(__name__)

# Template variable pattern: {{item}}, {{index}}, {{goal}}, {{project_context}}
_WORK_ITEM_RE = re.compile(r"\{\{(item|index|goal|project_context)\}\}")
_MAX_RECOVERY_TEXT_CHARS = 4000
_MAX_RECOVERY_MESSAGES = 6
_MAX_RESULT_SNAPSHOTS = 200


class _BoundedSnapshots(OrderedDict):
    """LRU dict that evicts oldest entries when capacity is reached."""

    def __setitem__(self, key: str, value: str) -> None:
        if key in self:
            self.move_to_end(key)
        super().__setitem__(key, value)
        while len(self) > _MAX_RESULT_SNAPSHOTS:
            self.popitem(last=False)


_PARALLEL_RESULT_SNAPSHOTS: dict[str, str] = _BoundedSnapshots()


def get_parallel_result_snapshot(session_id: str) -> str:
    """Return the last serialized parallel-wave aggregate for a planning session."""
    return _PARALLEL_RESULT_SNAPSHOTS.get(session_id, "")


# ---------------------------------------------------------------------------
# Input model
# ---------------------------------------------------------------------------


class RunParallelAgentsInput(BaseModel):
    """Arguments for parallel agent dispatch."""

    items: list[str] | str | None = Field(
        default=None,
        description="Work items to process. Each item is dispatched to a separate worker agent.",
    )
    agent_name: str = Field(
        default="codebase-explorer",
        description="Name of the worker agent to use for each item.",
    )
    skills: list[str] | None = Field(
        default=None,
        description="Optional skills to attach to worker agents.",
    )
    instructions_template: str = Field(
        default="",
        description=(
            "Prompt template with {{item}}, {{index}}, {{goal}}, {{project_context}} placeholders. "
            "Each worker receives this template with placeholders substituted."
        ),
    )
    instructions: str | None = Field(
        default=None,
        description="Fallback instruction string if no template is provided.",
    )
    goal: str | None = Field(default=None, description="Overall goal for the parallel wave.")
    project_context: str | None = Field(default=None, description="Project context for workers.")
    max_workers: int | None = Field(
        default=None,
        description="Maximum parallel workers. Defaults to number of items.",
    )
    tool_call_limit: int | None = Field(
        default=None,
        description="Tool call limit per worker agent.",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _render_item_prompt(
    template: str, item: str, index: int, goal: str, project_context: str
) -> str:
    def _replacer(m: re.Match) -> str:
        key = m.group(1)
        if key == "item":
            return item
        if key == "index":
            return str(index)
        if key == "goal":
            return goal
        if key == "project_context":
            return project_context
        return m.group(0)

    return _WORK_ITEM_RE.sub(_replacer, template)


def _build_parallel_payload(processed: list[dict[str, Any]], total: int) -> dict[str, Any]:
    success_count = sum(
        1 for row in processed if row.get("status") in ("success", "partial_success")
    )
    partial_success_count = sum(1 for row in processed if row.get("status") == "partial_success")
    error_count = sum(1 for row in processed if row.get("status") in ("error", "timeout"))
    return {
        "results": processed,
        "total": total,
        "success_count": success_count,
        "partial_success_count": partial_success_count,
        "failed_count": error_count,
    }


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


def _coerce_items(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        text = value.strip()
        if text.startswith("[") and text.endswith("]"):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
        return [text]
    return []


def _coerce_int(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return default
    return default


def _normalize_worker_result(
    result: Any, *, item: str, index: int, session_id: str
) -> dict[str, Any]:
    if isinstance(result, Exception):
        return {
            "status": "error",
            "index": index,
            "item": item,
            "session_id": session_id,
            "error": str(result),
        }
    if isinstance(result, dict):
        row = dict(result)
        row.setdefault("index", index)
        row.setdefault("item", item)
        row.setdefault("session_id", session_id)
        return row
    return {
        "status": "error",
        "index": index,
        "item": item,
        "session_id": session_id,
        "error": repr(result),
    }


def _store_parallel_result_snapshot(
    session_id: str | None, *, processed: list[dict[str, Any]], total: int
) -> None:
    if not session_id:
        return
    _PARALLEL_RESULT_SNAPSHOTS[session_id] = json.dumps(
        _build_parallel_payload(processed, total)
    )


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------


class RunParallelAgentsTool(BaseTool):
    """Launch multiple agents in parallel to process a list of work items."""

    name = "run_parallel_agents"
    description = (
        "Launch multiple agents in parallel to process a list of work items. "
        "Each worker receives the same instructions template with {{item}}, {{index}}, "
        "{{goal}}, and {{project_context}} substituted. Returns a list of worker results."
    )
    input_model = RunParallelAgentsInput

    _DEFAULT_TEMPLATE = (
        "Explore only this codebase region: {{item}}\n"
        "Goal: {{goal}}\n"
        "Project context: {{project_context}}\n\n"
        "Stay bounded and finish quickly:\n"
        "- Use at most 8 tool calls.\n"
        "- Prefer list_files, grep_search, read_file_lines, and read_file.\n"
        "- Avoid broad recursive searches outside {{item}}.\n"
        "- Do not inspect unrelated directories.\n"
        "- Return a concise report with: key files, main APIs, likely changelog touchpoints, and notable risks.\n"
        "- Keep the final report under 400 words."
    )

    def __init__(
        self,
        *,
        run_named_agent_fn: Any = None,
        goal: str = "",
        project_context: str = "",
        run_context: dict[str, object] | None = None,
        sandbox_id: str | None = None,
        coordination_store: Any = None,
        phase_outputs: dict[str, dict] | None = None,
    ) -> None:
        self._run_named_agent_fn = run_named_agent_fn
        self._goal = goal
        self._project_context = project_context
        self._run_context = dict(run_context or {})
        self._sandbox_id = sandbox_id
        if self._sandbox_id is None:
            raw_contract = self._run_context.get("workspace_contract")
            if isinstance(raw_contract, dict):
                raw_sandbox_id = raw_contract.get("sandbox_id")
                if isinstance(raw_sandbox_id, str) and raw_sandbox_id.strip():
                    self._sandbox_id = raw_sandbox_id.strip()
        self._coordination_store = coordination_store
        self._phase_outputs = dict(phase_outputs or {})

    def _default_items_from_phase_outputs(self) -> list[str]:
        analyze = self._phase_outputs.get("analyze")
        if not isinstance(analyze, dict):
            return []
        regions = analyze.get("regions")
        if not isinstance(regions, list):
            return []
        items: list[str] = []
        for region in regions:
            if not isinstance(region, dict):
                continue
            path = region.get("path")
            if isinstance(path, str) and path.strip():
                items.append(path.strip())
        return items

    async def _launch_worker(
        self,
        semaphore: asyncio.Semaphore,
        agent_name: str,
        base_instructions: str,
        item: str,
        index: int,
        tool_call_limit: int | None = None,
        session_id: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        async with semaphore:
            resolved_session_id = session_id or str(uuid.uuid4())
            resolved_run_id = run_id or str(uuid.uuid4())
            rendered = _render_item_prompt(
                base_instructions, item, index, self._goal, self._project_context
            )

            try:
                result = await self._run_named_agent_fn(
                    agent_name,
                    rendered,
                    session_id=resolved_session_id,
                    options={
                        "run_id": resolved_run_id,
                        "run_kind": "coordination",
                        "tool_call_limit": tool_call_limit,
                    },
                )
                content = _coerce_result_content(result)
                return {
                    "status": "success",
                    "index": index,
                    "item": item,
                    "session_id": resolved_session_id,
                    "content": content,
                }
            except Exception as exc:
                return {
                    "status": "error",
                    "index": index,
                    "item": item,
                    "session_id": resolved_session_id,
                    "error": str(exc),
                }

    async def execute(
        self, arguments: RunParallelAgentsInput, context: ToolExecutionContext
    ) -> ToolResult:
        if self._run_named_agent_fn is None:
            # Allow injection via context metadata as fallback
            self._run_named_agent_fn = context.metadata.get("run_named_agent_fn")

        if self._run_named_agent_fn is None:
            return ToolResult(
                output=json.dumps({"error": "No agent runner function configured"}),
                is_error=True,
            )

        resolved_items = _coerce_items(arguments.items)
        if not resolved_items:
            resolved_items = self._default_items_from_phase_outputs()

        if not resolved_items:
            return ToolResult(
                output=json.dumps({
                    "results": [],
                    "total": 0,
                    "success_count": 0,
                    "failed_count": 0,
                    "error": "No work items provided and no analyze-phase regions available.",
                }),
                is_error=True,
            )

        rendered_template = arguments.instructions_template or str(arguments.instructions or "")
        if not rendered_template.strip():
            rendered_template = self._DEFAULT_TEMPLATE

        if arguments.goal:
            self._goal = arguments.goal
        if arguments.project_context:
            self._project_context = arguments.project_context

        max_workers_int = max(1, _coerce_int(arguments.max_workers, len(resolved_items)))
        tool_call_limit = (
            _coerce_int(arguments.tool_call_limit, 0) or None
        )

        # Snapshot session for result retrieval
        raw_session_id = self._run_context.get("task_planner_session_id")
        snapshot_session_id = (
            raw_session_id if isinstance(raw_session_id, str) and raw_session_id.strip() else None
        )

        semaphore = asyncio.Semaphore(max_workers_int)
        worker_specs = [
            {
                "item": item,
                "index": idx,
                "session_id": str(uuid.uuid4()),
                "run_id": str(uuid.uuid4()),
            }
            for idx, item in enumerate(resolved_items)
        ]

        tasks = [
            asyncio.create_task(
                self._launch_worker(
                    semaphore=semaphore,
                    agent_name=arguments.agent_name,
                    base_instructions=rendered_template,
                    item=spec["item"],
                    index=spec["index"],
                    session_id=spec["session_id"],
                    run_id=spec["run_id"],
                    tool_call_limit=tool_call_limit,
                )
            )
            for spec in worker_specs
        ]

        try:
            results = await asyncio.gather(*tasks, return_exceptions=True)
        except asyncio.CancelledError:
            # Collect whatever completed
            processed = []
            for spec, task in zip(worker_specs, tasks):
                if task.done() and not task.cancelled():
                    try:
                        worker_result = task.result()
                    except Exception as exc:
                        worker_result = exc
                    processed.append(
                        _normalize_worker_result(
                            worker_result,
                            item=spec["item"],
                            index=spec["index"],
                            session_id=spec["session_id"],
                        )
                    )
                else:
                    task.cancel()
                    processed.append({
                        "status": "timeout",
                        "index": spec["index"],
                        "item": spec["item"],
                        "session_id": spec["session_id"],
                        "error": "Parallel wave interrupted before worker completed.",
                    })
            _store_parallel_result_snapshot(
                snapshot_session_id, processed=processed, total=len(resolved_items)
            )
            raise

        processed = [
            _normalize_worker_result(
                result,
                item=spec["item"],
                index=spec["index"],
                session_id=spec["session_id"],
            )
            for spec, result in zip(worker_specs, results)
        ]
        _store_parallel_result_snapshot(
            snapshot_session_id, processed=processed, total=len(resolved_items)
        )
        return ToolResult(
            output=json.dumps(_build_parallel_payload(processed, len(resolved_items)))
        )
