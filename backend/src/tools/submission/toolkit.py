"""Submission toolkit — terminal actions for team-mode agents."""

from __future__ import annotations

import time
import uuid
from typing import Any

from pydantic import BaseModel, Field

from agents.registry import get_definition
from team.planning.validation import validate_plan
from tools.core.base import BaseTool, BaseToolkit, ToolExecutionContext, ToolResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _post_submission_note(
    context: ToolExecutionContext,
    *,
    content: str,
    scope_paths: list[str] | None = None,
) -> None:
    tc = context.metadata.get("task_center")
    if tc is None:
        return
    from team.models import Note

    await tc.post(
        Note(
            id=str(uuid.uuid4()),
            task_id=context.metadata.get("work_item_id", ""),
            agent_name=context.metadata.get("agent_name", ""),
            content=content,
            timestamp=time.time(),
            scope_paths=list(scope_paths or context.metadata.get("write_scope") or []),
        )
    )


async def _check_context_freshness(
    context: ToolExecutionContext,
) -> str:
    """Check if context has gone stale since task started.

    Returns a warning string if context is stale, otherwise empty string.
    This is called before submission to attach staleness metadata.
    """
    import json

    since = context.metadata.get("work_item_started_at", 0)
    task_id = context.metadata.get("work_item_id", "")
    agent_run_id = context.metadata.get("agent_run_id", "")

    scope_changes = 0
    new_dep_notes = 0
    new_sibling_completions = 0

    arbiter = context.metadata.get("arbiter")
    scope_paths = context.metadata.get("write_scope") or []
    if arbiter is not None and scope_paths:
        changes = arbiter.changes_since(since)
        scope_changes = sum(
            1
            for e in changes
            if e.agent_id != agent_run_id
            and any(e.file_path.startswith(p.rstrip("/")) for p in scope_paths)
        )

    tc = context.metadata.get("task_center")
    dispatcher = context.metadata.get("dispatcher")
    if tc is not None:
        task_deps = set(context.metadata.get("task_deps", []))
        if task_deps:
            dep_notes = await tc.read(authors=list(task_deps), since=since)
            new_dep_notes = len(dep_notes)
    if dispatcher is not None and hasattr(dispatcher, "done_sibling_ids"):
        sibling_ids = await dispatcher.done_sibling_ids(
            task_id=task_id,
            parent_id=context.metadata.get("task_parent_id"),
            since=since,
        )
        new_sibling_completions = len(sibling_ids)

    stale = scope_changes > 0 or new_dep_notes > 0 or new_sibling_completions > 0
    if not stale:
        return ""

    return (
        f"\n\n[FRESHNESS WARNING] Your context may be stale since task started. "
        f"Scope changes by others: {scope_changes}, "
        f"New dependency notes: {new_dep_notes}, "
        f"New sibling completions: {new_sibling_completions}. "
        f"Consider re-reading affected files before submitting."
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


async def _known_external_dep_ids(context: ToolExecutionContext) -> set[str] | None:
    known = context.metadata.get("known_external_dep_ids")
    if isinstance(known, set):
        return {str(item) for item in known}
    if isinstance(known, list):
        return {str(item) for item in known}
    dispatcher = context.metadata.get("dispatcher")
    if dispatcher is None or not hasattr(dispatcher, "known_task_ids"):
        return None
    return {str(item) for item in await dispatcher.known_task_ids()}


def _roster_from_context(context: ToolExecutionContext) -> dict[str, list[str]]:
    roster = context.metadata.get("roster")
    if not isinstance(roster, dict):
        return {}
    return {
        str(role): [str(agent_name) for agent_name in agent_names if isinstance(agent_name, str)]
        for role, agent_names in roster.items()
        if isinstance(agent_names, list)
    }


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
# DoneTool
# ---------------------------------------------------------------------------


class DoneInput(BaseModel):
    summary: str = Field(
        ...,
        description="1-3 sentence summary of what you accomplished.",
        min_length=1,
    )


class SubmitSummaryTool(BaseTool):
    name = "done"
    description = "Signal task completion with a summary. Must be called exactly once."
    input_model = DoneInput

    async def execute(self, arguments: BaseModel, context: ToolExecutionContext) -> ToolResult:
        assert isinstance(arguments, DoneInput)
        from team.models import SubmittedSummary

        summary = arguments.summary.strip()
        if not summary:
            return ToolResult(output="Error: summary must be non-empty", is_error=True)

        freshness_warning = await _check_context_freshness(context)
        if freshness_warning:
            summary += freshness_warning

        submission = SubmittedSummary(summary=summary)
        context.metadata["submitted_output"] = submission
        await _post_submission_note(context, content=summary)
        return ToolResult(output=f"Summary accepted ({len(summary)} chars).")


# ---------------------------------------------------------------------------
# SubmitPlanTool
# ---------------------------------------------------------------------------


class SubmitPlanInput(BaseModel):
    tasks: list[dict] = Field(
        ...,
        description="List of TaskSpec dicts with id, task, agent, deps, scope_paths",
    )
    rationale: str | None = Field(
        default=None,
        description="Why this decomposition was chosen",
    )


class SubmitPlanTool(BaseTool):
    name = "submit_plan"
    description = "Submit a plan. Terminal action for planners."
    input_model = SubmitPlanInput

    async def execute(self, arguments: BaseModel, context: ToolExecutionContext) -> ToolResult:
        assert isinstance(arguments, SubmitPlanInput)
        from team.models import Plan

        roster = _roster_from_context(context)
        resolved_tasks = _resolve_plan_tasks(arguments.tasks, roster)
        try:
            plan = Plan.from_dict({"tasks": resolved_tasks, "rationale": arguments.rationale})
        except (TypeError, ValueError) as exc:
            return ToolResult(output=f"Error: invalid plan payload: {exc}", is_error=True)

        allow_empty = bool(context.metadata.get("allow_empty_plan"))
        max_plan_size = int(context.metadata.get("max_plan_size", 50) or 50)
        known_external_dep_ids = await _known_external_dep_ids(context)
        issues = validate_plan(
            plan,
            max_plan_size=max_plan_size,
            allow_empty=allow_empty,
            known_external_deps=known_external_dep_ids,
        )

        max_tasks = int(context.metadata.get("max_tasks", 0) or 0)
        tasks_used = int(context.metadata.get("tasks_used", 0) or 0)
        if max_tasks and tasks_used + len(plan.tasks) > max_tasks:
            issues.append(
                {
                    "field": "tasks",
                    "msg": f"plan would exceed max_tasks={max_tasks} (used={tasks_used}, adding={len(plan.tasks)})",
                }
            )
        max_depth = int(context.metadata.get("max_depth", 0) or 0)
        task_depth = int(context.metadata.get("task_depth", 0) or 0)
        if max_depth and plan.tasks and (task_depth + 1) > max_depth:
            issues.append(
                {
                    "field": "tasks",
                    "msg": f"plan would exceed max_depth={max_depth} from current depth={task_depth}",
                }
            )

        note_budget_issues = _note_budget_issues(
            resolved_tasks,
            max_note_bytes=int(context.metadata.get("max_note_bytes", 0) or 0),
        )
        issues.extend({"field": "tasks", "msg": msg} for msg in note_budget_issues)

        if issues:
            message = "; ".join(str(issue.get("msg") or "invalid plan") for issue in issues)
            return ToolResult(output=f"Error: {message}", is_error=True)

        freshness_warning = await _check_context_freshness(context)

        context.metadata["submitted_output"] = plan
        summary = f"Submitted plan with {len(plan.tasks)} task(s)."
        if arguments.rationale:
            summary += f"\nRationale: {arguments.rationale.strip()}"
        if freshness_warning:
            summary += freshness_warning
        await _post_submission_note(context, content=summary)
        return ToolResult(output=f"Plan accepted ({len(plan.tasks)} tasks).")


# ---------------------------------------------------------------------------
# RequestRetryTool
# ---------------------------------------------------------------------------


class RequestRetryInput(BaseModel):
    reason: str = Field(..., description="Why retry is needed")


class RequestRetryTool(BaseTool):
    name = "request_retry"
    description = "Request a retry of the current task."
    input_model = RequestRetryInput

    async def execute(self, arguments: BaseModel, context: ToolExecutionContext) -> ToolResult:
        assert isinstance(arguments, RequestRetryInput)
        from team.models import RetryRequest

        context.metadata["submitted_output"] = RetryRequest(reason=arguments.reason)
        await _post_submission_note(context, content=f"Requested retry: {arguments.reason}")
        return ToolResult(output="Retry requested.")


# ---------------------------------------------------------------------------
# RequestReplanTool
# ---------------------------------------------------------------------------


class RequestReplanInput(BaseModel):
    reason: str = Field(..., description="Why replan is needed")
    suggestion: str | None = Field(default=None, description="Suggestion for the replanner")


class RequestReplanTool(BaseTool):
    name = "request_replan"
    description = "Request a replan of the current task scope."
    input_model = RequestReplanInput

    async def execute(self, arguments: BaseModel, context: ToolExecutionContext) -> ToolResult:
        assert isinstance(arguments, RequestReplanInput)
        from team.models import ReplanRequest

        context.metadata["submitted_output"] = ReplanRequest(
            reason=arguments.reason,
            suggestion=arguments.suggestion,
        )
        note = f"Requested replan: {arguments.reason}"
        if arguments.suggestion:
            note += f"\nSuggestion: {arguments.suggestion}"
        await _post_submission_note(context, content=note)
        return ToolResult(output="Replan requested.")


# ---------------------------------------------------------------------------
# SubmitReplanTool
# ---------------------------------------------------------------------------


class SubmitReplanInput(BaseModel):
    add_tasks: list[dict] = Field(default_factory=list, description="New tasks to add")
    cancel_ids: list[str] = Field(default_factory=list, description="Task IDs to cancel")


class SubmitReplanTool(BaseTool):
    name = "submit_replan"
    description = "Submit a corrective replan. Terminal action for replanners."
    input_model = SubmitReplanInput

    async def execute(self, arguments: BaseModel, context: ToolExecutionContext) -> ToolResult:
        assert isinstance(arguments, SubmitReplanInput)
        from team.models import ReplanPlan

        replan = ReplanPlan.from_dict(
            {"add_tasks": arguments.add_tasks, "cancel_ids": arguments.cancel_ids}
        )

        freshness_warning = await _check_context_freshness(context)
        note_content = (
            f"Submitted corrective replan with {len(replan.add_tasks)} new task(s) "
            f"and {len(replan.cancel_ids)} cancellation(s)."
        )
        if freshness_warning:
            note_content += freshness_warning

        context.metadata["submitted_output"] = replan
        await _post_submission_note(context, content=note_content)
        return ToolResult(
            output=f"Replan accepted ({len(replan.add_tasks)} new tasks, {len(replan.cancel_ids)} cancelled)."
        )


# ---------------------------------------------------------------------------
# SubmissionToolkit
# ---------------------------------------------------------------------------


class SubmissionToolkit(BaseToolkit):
    """Role-aware toolkit that exposes the appropriate terminal submission tools."""

    @classmethod
    def from_context(cls, ctx: object) -> SubmissionToolkit:
        from agents.registry import get_role

        metadata = getattr(ctx, "metadata", {}) or {}  # type: ignore[union-attr]
        role = metadata.get("role")
        if not isinstance(role, str) or not role.strip():
            agent_name: str = str(metadata.get("agent_name") or "")
            role = get_role(agent_name)

        if role == "planner":
            tools = [SubmitPlanTool()]
        elif role == "replanner":
            tools = [SubmitReplanTool()]
        else:
            tools = [SubmitSummaryTool(), RequestRetryTool(), RequestReplanTool()]
        return cls(
            name="submission",
            description="Terminal submission actions for the current agent role.",
            tools=tools,
        )
