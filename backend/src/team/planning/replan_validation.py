"""Shared replan validation rules.

The submission tool validation path (pre-submission) and the plan expander
(at-apply, inside ``PlanExpander.apply_replan``) enforce the same
layer-restricted rules.
They live here so the two callers cannot drift apart.

Scope is deliberately narrow: a replanner may only author direct children
under itself. That makes the replanner the recovery gate for downstream tasks
rewired from the failed worker. It can cancel its direct siblings; cascade
handles their descendants and dependents.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from agents.registry import has_role
from team.core.models import TaskSpec
from team.planning.validation import Issue

ALLOWED_REPLAN_DEP_STATUSES = {"done", "ready", "pending"}
CANCELABLE_REPLAN_STATUSES = {"running", "pending", "ready"}

_UNRESOLVED_BLOCKER_RE = re.compile(
    r"(?i)\bClassification\s*:\s*unresolved_blocker\b"
)
_DIAGNOSTICS_DECISION_RE = re.compile(
    r"(?i)\bDiagnostics decision\s*:\s*"
    r"(?:trivial_direct_replan|deep_diagnostics)\b"
)
_PLANNER_HANDOFF_RE = re.compile(
    r"(?i)\bPlanner handoff\s*:\s*"
    r"(?:scope_expansion|planner_redraft)\b"
)


@dataclass
class ReplanValidationResult:
    errors: list[str] = field(default_factory=list)
    origin_task_id: str | None = None
    all_cancelled_ids: set[str] = field(default_factory=set)
    allowed_existing_dep_ids: set[str] = field(default_factory=set)
    allowed_cancel_ids: set[str] = field(default_factory=set)


def _compute_allowed_cancel_ids(
    graph: dict[str, Any],
    *,
    replanner_parent_id: Any,
    replan_task_id: str,
    origin_task_id: str | None,
) -> set[str]:
    """Direct siblings of the replanner that the runtime will accept as cancel targets.

    Filters: same parent as replanner; status in CANCELABLE_REPLAN_STATUSES; not
    the replanner itself, not the origin failed task, not another team_replanner.
    """
    allowed: set[str] = set()
    for tid, task in graph.items():
        if tid == replan_task_id or tid == origin_task_id:
            continue
        if getattr(task, "parent_id", None) != replanner_parent_id:
            continue
        status_value = _status_value(getattr(task, "status", None))
        if status_value not in CANCELABLE_REPLAN_STATUSES:
            continue
        agent_name = str(getattr(task, "agent", "") or "")
        if agent_name == "team_replanner":
            continue
        allowed.add(tid)
    return allowed


def _cascade_ids_for_cancel_root(
    graph: dict[str, Any],
    cancel_root_id: str,
) -> set[str]:
    """Live descendants + dependents of ``cancel_root_id``.

    Thin wrapper over :meth:`team.runtime.task_graph.TaskGraph.compute_cancel_cascade`
    so pre-submission validation and at-apply cancellation traverse via the
    same single-owner implementation.
    """
    from team.runtime.task_graph import TaskGraph

    return TaskGraph(graph).compute_cancel_cascade(cancel_root_id)


def _status_value(status: Any) -> Any:
    return getattr(status, "value", status)


def _replan_spec_contract_errors(spec: TaskSpec, agent_name: str) -> list[str]:
    errors: list[str] = []
    if (
        _UNRESOLVED_BLOCKER_RE.search(spec.detail)
        and not _DIAGNOSTICS_DECISION_RE.search(spec.detail)
    ):
        errors.append(
            "unresolved_blocker requires Diagnostics decision: "
            "trivial_direct_replan or deep_diagnostics"
        )
    if has_role(agent_name, "planner") and not _PLANNER_HANDOFF_RE.search(spec.detail):
        errors.append(
            "team_planner replan children require Planner handoff: "
            "scope_expansion or planner_redraft in spec.detail"
        )
    return errors


def replan_spec_contract_issues(items: list[Any]) -> list[Issue]:
    """Validate replanner-only task spec contracts."""
    issues: list[Issue] = []
    for idx, item in enumerate(items):
        spec = getattr(item, "spec", None)
        if not isinstance(spec, TaskSpec):
            continue
        agent_name = str(getattr(item, "agent", "") or "")
        issues.extend(
            {
                "field": f"tasks[{idx}].spec.detail",
                "msg": f"task '{getattr(item, 'id', '')}': {error}",
            }
            for error in _replan_spec_contract_errors(spec, agent_name)
        )
    return issues


def _depends_on_any(
    graph: dict[str, Any],
    *,
    task_id: str,
    blocked_dep_ids: set[str],
) -> bool:
    """Return True when task_id's dependency chain reaches a blocked dep."""
    task = graph.get(task_id)
    stack = [str(dep_id) for dep_id in getattr(task, "deps", []) or []]
    seen: set[str] = set()
    while stack:
        dep_id = stack.pop()
        if dep_id in blocked_dep_ids:
            return True
        if dep_id in seen:
            continue
        seen.add(dep_id)
        dep_task = graph.get(dep_id)
        if dep_task is not None:
            stack.extend(str(next_dep) for next_dep in getattr(dep_task, "deps", []) or [])
    return False


def validate_replan_rules(
    *,
    graph: dict[str, Any] | None,
    replan_task_id: str,
    cancel_ids: Iterable[str],
) -> ReplanValidationResult:
    """Validate replan cancel targets and compute dep/cancel sets.

    New task specs carry no free-form ``parent_id``; callers stamp every new
    task as a direct child of the replanner. This validator enforces the
    cancel-side rules and exposes ``allowed_existing_dep_ids`` for validating
    new-task dependencies.
    """
    result = ReplanValidationResult()
    if graph is None:
        result.errors.append("submit_replan requires the current task graph for validation")
        return result

    replanner = graph.get(replan_task_id)
    if replanner is None:
        result.errors.append(f"replanner task '{replan_task_id}' not found in graph")
        return result
    origin_task_id = (
        getattr(replanner, "fired_by_task_id", None) if replanner is not None else None
    )
    replanner_parent_id = (
        getattr(replanner, "parent_id", None) if replanner is not None else None
    )
    result.origin_task_id = origin_task_id

    cancel_id_list = list(cancel_ids)
    cancel_id_set = set(cancel_id_list)

    result.allowed_cancel_ids = _compute_allowed_cancel_ids(
        graph,
        replanner_parent_id=replanner_parent_id,
        replan_task_id=replan_task_id,
        origin_task_id=origin_task_id,
    )

    if replan_task_id in cancel_id_set:
        result.errors.append("replanner cannot cancel itself")
    if origin_task_id and origin_task_id in cancel_id_set:
        result.errors.append("replanner cannot cancel the original request_replan task")

    all_cancelled = set(cancel_id_set)
    for cid in cancel_id_list:
        target = graph.get(cid)
        if target is None:
            result.errors.append(f"cancel target '{cid}' not found in graph")
            continue
        if cid == replan_task_id or cid == origin_task_id:
            continue
        target_parent = getattr(target, "parent_id", None)
        if target_parent != replanner_parent_id:
            result.errors.append(
                f"cancel target '{cid}' is not a direct sibling of the replanner "
                f"(replanner.parent_id={replanner_parent_id!r}, "
                f"target.parent_id={target_parent!r}); replanners may only "
                f"cancel their direct siblings"
            )
        status = getattr(target, "status", None)
        status_value = _status_value(status)
        if status is not None and status_value not in CANCELABLE_REPLAN_STATUSES:
            result.errors.append(
                f"cancel target '{cid}' is {status_value}; only running/pending/ready tasks can be cancelled"
            )
        all_cancelled.update(_cascade_ids_for_cancel_root(graph, cid))
    result.all_cancelled_ids = all_cancelled

    if result.errors:
        if result.allowed_cancel_ids:
            allowed_list = ", ".join(sorted(result.allowed_cancel_ids))
            result.errors.append(
                "You are only allowed to cancel these tasks, but consider carefully "
                f"before adding any (default `cancel_ids: []` is always valid): {allowed_list}"
            )
        else:
            result.errors.append(
                "You are only allowed to cancel these tasks, but consider carefully "
                "before adding any (default `cancel_ids: []` is always valid): "
                "<no cancellable siblings — ship cancel_ids: []>"
            )

    excluded_dep_ids: set[str] = {replan_task_id}
    if origin_task_id:
        excluded_dep_ids.add(origin_task_id)
    allowed_existing_dep_ids: set[str] = set()
    for tid, task in graph.items():
        if tid in all_cancelled or tid in excluded_dep_ids:
            continue
        if _depends_on_any(
            graph,
            task_id=tid,
            blocked_dep_ids=excluded_dep_ids,
        ):
            continue
        status_value = _status_value(getattr(task, "status", None))
        if status_value in ALLOWED_REPLAN_DEP_STATUSES:
            allowed_existing_dep_ids.add(tid)
    result.allowed_existing_dep_ids = allowed_existing_dep_ids

    return result
