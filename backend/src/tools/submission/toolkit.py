"""Submission toolkit — terminal actions for team-mode agents.

Replaces the former posthook toolkit. Tools write structured data to
``context.metadata``; the executor reads it after the runner returns.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from agents.registry import get_definition
from team.planning.validation import validate_plan
from tools.core.base import BaseTool, BaseToolkit, ToolExecutionContext, ToolResult


# ---------------------------------------------------------------------------
# Helpers (ported from posthook/toolkit.py)
# ---------------------------------------------------------------------------


async def _post_submission_note(
    context: ToolExecutionContext,
    *,
    content: str,
    scope_paths: list[str] | None = None,
    tags: list[str] | None = None,
) -> None:
    tc = context.metadata.get("task_center")
    if tc is None:
        return
    from team.models import Note

    await tc.notes.post(
        Note(
            id=str(uuid.uuid4()),
            task_id=context.metadata.get("work_item_id", ""),
            agent_name=context.metadata.get("agent_name", ""),
            content=content,
            timestamp=time.time(),
            paths=list(scope_paths or context.metadata.get("write_scope") or []),
            tags=tags or [],
        )
    )


async def _freshness_submission_gate(context: ToolExecutionContext, *, action: str) -> ToolResult | None:
    """Reject terminal submissions when the task context has gone stale."""
    from tools.context.freshness import check_freshness

    report = await check_freshness(context)
    if not report.stale:
        return None
    return ToolResult(
        output=(
            f"Error: `{action}` is blocked because your task context changed since the "
            "last acknowledged baseline. Call `context_changed_since()` now, refresh with "
            "`read_task_note(...)` or targeted rereads if needed, then either retry the "
            f"submission or call `submit_task_summary(type='fail')`. "
            f"Scope changes by others: {report.scope_changes_by_others}, "
            f"New dependency notes: {report.new_dep_notes}, "
            f"New sibling completions: {report.new_sibling_completions}."
        ),
        is_error=True,
    )


def _resolve_agent_name(agent_value: str, roster: dict[str, list[str]]) -> str:
    candidate = agent_value.strip()
    if not candidate:
        return candidate
    if get_definition(candidate) is not None:
        return candidate
    role_matches = roster.get(candidate)
    if role_matches:
        return str(role_matches[0])
    return candidate


def _resolve_plan_tasks(
    raw_tasks: list[dict[str, Any]],
    roster: dict[str, list[str]],
) -> list[dict[str, Any]]:
    resolved: list[dict[str, Any]] = []
    for item in raw_tasks:
        data = dict(item)
        data["agent"] = _resolve_agent_name(str(data.get("agent") or ""), roster)
        resolved.append(data)
    return resolved


def _roster_from_context(context: ToolExecutionContext) -> dict[str, list[str]]:
    roster = context.metadata.get("roster")
    if not isinstance(roster, dict):
        return {}
    return {
        str(role): [str(agent_name) for agent_name in agent_names if isinstance(agent_name, str)]
        for role, agent_names in roster.items()
        if isinstance(agent_names, list)
    }


async def _known_external_dep_ids(context: ToolExecutionContext) -> set[str] | None:
    known = context.metadata.get("known_external_dep_ids")
    if isinstance(known, set):
        return {str(item) for item in known}
    if isinstance(known, list):
        return {str(item) for item in known}
    tc = context.metadata.get("task_center")
    store = getattr(tc, "store", None) if tc is not None else None
    if store is None or not hasattr(store, "get_task_ids"):
        return None
    return {str(item) for item in await store.get_task_ids()}


def _note_budget_issues(
    tasks: list[dict[str, Any]],
    *,
    max_note_bytes: int | None,
) -> list[str]:
    if not max_note_bytes or max_note_bytes <= 0:
        return []
    issues: list[str] = []
    for item in tasks:
        task_id = str(item.get("id") or "<unknown>")
        task_text = str(item.get("task") or "")
        size = len(task_text.encode("utf-8"))
        if size > max_note_bytes:
            issues.append(
                f"task '{task_id}' is {size} bytes, exceeds max_note_bytes={max_note_bytes}"
            )
    return issues


# ---------------------------------------------------------------------------
# SubmitTaskSummaryTool — terminal for non-planner agents
# ---------------------------------------------------------------------------


class SubmitTaskSummaryInput(BaseModel):
    type: Literal["success", "fail"] = Field(
        ...,
        description=(
            "Outcome type. 'success' = task completed successfully. "
            "'fail' = task cannot be completed (triggers replan)."
        ),
    )
    content: str = Field(
        ...,
        min_length=1,
        description=(
            "Summary of work done. For success: describe what was accomplished "
            "and files changed. For fail: describe what went wrong and why "
            "a replan is needed."
        ),
    )


class SubmitTaskSummaryTool(BaseTool):
    name = "submit_task_summary"
    description = (
        "Submit your task outcome. Call with type='success' when work is done, "
        "or type='fail' when the task cannot be completed and needs replanning. "
        "This is your terminal action — the agent loop ends after this call."
    )
    input_model = SubmitTaskSummaryInput
    tool_types = frozenset({"normal"})

    async def execute(self, arguments: BaseModel, context: ToolExecutionContext) -> ToolResult:
        assert isinstance(arguments, SubmitTaskSummaryInput)

        # Write to metadata for executor to read after runner returns
        context.metadata["task_summary"] = arguments.content
        context.metadata["task_summary_type"] = arguments.type

        # Audit trail note
        tag = "implementation" if arguments.type == "success" else "warning"
        await _post_submission_note(context, content=arguments.content, tags=[tag])
        return ToolResult(output="Summary submitted.")


# ---------------------------------------------------------------------------
# SubmitPlanTool — terminal for planners AND replanners
# ---------------------------------------------------------------------------


class SubmitPlanInput(BaseModel):
    add_tasks: list[dict] = Field(
        default_factory=list,
        description=(
            "List of TaskSpec dicts. Each must have: "
            "id (unique string), task (prose instruction — this is the agent's sole briefing), "
            "agent (agent name or role hint, e.g. 'developer', 'team_planner', 'validator'), "
            "deps (list of task ids this depends on, default []), "
            "scope_paths (file/dir hints for OCC and note scoping, default []), "
            "cascade_policy ('cancel' | 'retry_first' | 'continue', default 'cancel')."
        ),
    )
    remove_tasks: list[str] = Field(
        default_factory=list,
        description=(
            "Task IDs to cancel (replanner only). Cancelling a node cancels its "
            "entire subtree. Targets can be atomic or expandable, running or pending."
        ),
    )
    rationale: str | None = Field(
        default=None,
        description="Why this decomposition was chosen — helps replanners if tasks fail.",
    )

    @model_validator(mode="before")
    @classmethod
    def accept_tasks_alias(cls, values: Any) -> Any:
        """Accept 'tasks' as an alias for 'add_tasks' for planner backward compat."""
        if isinstance(values, dict):
            if "tasks" in values and "add_tasks" not in values:
                values["add_tasks"] = values.pop("tasks")
        return values


class SubmitPlanTool(BaseTool):
    name = "submit_plan"
    description = (
        "Submit a plan. Planners: provide add_tasks with the full decomposition. "
        "Replanners: provide add_tasks for new corrective tasks and remove_tasks "
        "for task IDs to cancel. Each task's 'task' field is the agent's sole "
        "briefing — write clear, actionable prose."
    )
    input_model = SubmitPlanInput
    tool_types = frozenset({"normal"})

    async def execute(self, arguments: BaseModel, context: ToolExecutionContext) -> ToolResult:
        assert isinstance(arguments, SubmitPlanInput)
        from team.models import Plan, ReplanPlan

        roster = _roster_from_context(context)
        resolved_tasks = _resolve_plan_tasks(arguments.add_tasks, roster)
        role = str(context.metadata.get("role") or "")
        is_replanner = role == "replanner"

        if is_replanner:
            # Replanner path: build ReplanPlan
            try:
                replan = ReplanPlan.from_dict({
                    "add_tasks": resolved_tasks,
                    "cancel_ids": arguments.remove_tasks,
                })
            except (TypeError, ValueError) as exc:
                return ToolResult(output=f"Error: invalid replan payload: {exc}", is_error=True)

            freshness_gate = await _freshness_submission_gate(context, action="submit_plan(replan)")
            if freshness_gate is not None:
                return freshness_gate

            note_content = (
                f"Replanner submitted plan: {len(replan.add_tasks)} new task(s), "
                f"{len(replan.cancel_ids)} cancelled."
            )
            await _post_submission_note(context, content=note_content, tags=["refactor"])

            # Write to metadata for executor
            context.metadata["resolved_plan"] = replan
            context.metadata["plan_is_replan"] = True
            return ToolResult(
                output=f"Replan accepted ({len(replan.add_tasks)} new tasks, {len(replan.cancel_ids)} cancelled).",
            )
        else:
            # Planner path: build Plan
            try:
                plan = Plan.from_dict({"tasks": resolved_tasks, "rationale": arguments.rationale})
            except (TypeError, ValueError) as exc:
                return ToolResult(output=f"Error: invalid plan payload: {exc}", is_error=True)

            allow_empty = bool(context.metadata.get("allow_empty_plan"))
            max_plan_size = int(context.metadata.get("max_plan_size", 50) or 50)
            known_ext_deps = await _known_external_dep_ids(context)
            issues = validate_plan(
                plan,
                max_plan_size=max_plan_size,
                allow_empty=allow_empty,
                known_external_deps=known_ext_deps,
            )

            max_tasks = int(context.metadata.get("max_tasks", 0) or 0)
            tasks_used = int(context.metadata.get("tasks_used", 0) or 0)
            if max_tasks and tasks_used + len(plan.tasks) > max_tasks:
                issues.append({
                    "field": "tasks",
                    "msg": f"plan would exceed max_tasks={max_tasks} (used={tasks_used}, adding={len(plan.tasks)})",
                })
            max_depth = int(context.metadata.get("max_depth", 0) or 0)
            task_depth = int(context.metadata.get("task_depth", 0) or 0)
            if max_depth and plan.tasks and (task_depth + 1) > max_depth:
                issues.append({
                    "field": "tasks",
                    "msg": f"plan would exceed max_depth={max_depth} from current depth={task_depth}",
                })

            note_budget_issues = _note_budget_issues(
                resolved_tasks,
                max_note_bytes=int(context.metadata.get("max_note_bytes", 0) or 0),
            )
            issues.extend({"field": "tasks", "msg": msg} for msg in note_budget_issues)

            if issues:
                message = "; ".join(str(issue.get("msg") or "invalid plan") for issue in issues)
                return ToolResult(output=f"Error: {message}", is_error=True)

            freshness_gate = await _freshness_submission_gate(context, action="submit_plan()")
            if freshness_gate is not None:
                return freshness_gate

            summary = f"Submitted plan with {len(plan.tasks)} task(s)."
            if arguments.rationale:
                summary += f"\nRationale: {arguments.rationale.strip()}"
            await _post_submission_note(context, content=summary, tags=["architecture"])

            # Write to metadata for executor
            context.metadata["resolved_plan"] = plan
            context.metadata["plan_is_replan"] = False
            return ToolResult(output=f"Plan accepted ({len(plan.tasks)} tasks).")


# ---------------------------------------------------------------------------
# SubmissionToolkit
# ---------------------------------------------------------------------------


class SubmissionToolkit(BaseToolkit):
    """Terminal submission tools for team-mode agents.

    Registered in the main tool loop. The query loop's ``terminal_tools``
    set (populated from TeamDefinition) causes the loop to exit when one
    of these tools is called.
    """

    @classmethod
    def from_context(cls, ctx: object) -> SubmissionToolkit:
        return cls(
            name="submission",
            description="Terminal submission tools (submit_task_summary, submit_plan).",
            tools=[SubmitTaskSummaryTool(), SubmitPlanTool()],
        )
