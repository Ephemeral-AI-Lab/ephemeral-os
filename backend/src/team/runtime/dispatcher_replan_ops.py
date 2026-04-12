from __future__ import annotations

from typing import TYPE_CHECKING

from team.errors import BudgetExceeded, InvalidPlan
from team.models import ReplanRequest, Task, TaskSpec, TaskStatus
from team.persistence.events import make_work_item_added, make_work_item_status, work_item_to_dict
from team.planning.validation import _has_cycle
from team.runtime.dispatcher_mutation_ops import cascade_cancel

if TYPE_CHECKING:
    from team.runtime.dispatcher import Dispatcher


def should_reattach_failed_verifier(failed_wi: Task) -> bool:
    from agents.registry import has_role
    return has_role(failed_wi.agent_name, "reviewer") and failed_wi.status == TaskStatus.FAILED


def _replan_adds_replacement_validator(add_tasks: list[TaskSpec]) -> bool:
    from agents.registry import has_role

    return any(
        has_role(spec.agent, "reviewer")
        for spec in add_tasks
    )


def build_replan_verifier_deps(
    dispatcher: "Dispatcher",
    failed_wi: Task,
    *,
    new_item_ids: list[str],
    cancelled_ids: set[str],
) -> list[str]:
    deps: list[str] = []
    seen: set[str] = set()
    for dep_id in [*failed_wi.deps, *new_item_ids]:
        if dep_id in seen or dep_id in cancelled_ids:
            continue
        dep = dispatcher.graph.get(dep_id)
        if dep is not None and dep.status == TaskStatus.CANCELLED:
            continue
        deps.append(dep_id)
        seen.add(dep_id)
    return deps


def apply_replan_unlocked(
    dispatcher: "Dispatcher",
    *,
    replan_task_id: str,
    add_tasks: list[TaskSpec],
    cancel_ids: list[str],
    target_depth: int,
    target_parent_id: str | None,
    target_root_id: str,
) -> dict[str, int]:
    """Inner replan logic — caller must already hold dispatcher.lock."""
    for cid in cancel_ids:
        wi = dispatcher.graph.get(cid)
        if wi is None:
            raise InvalidPlan(f"cancel target {cid} not found")
        if wi.parent_id != target_parent_id:
            raise InvalidPlan(
                f"cancel target {cid} has parent {wi.parent_id!r}, "
                f"but replan is scoped to parent {target_parent_id!r}"
            )
        if wi.status not in (TaskStatus.PENDING, TaskStatus.READY):
            raise InvalidPlan(
                f"cancel target {cid} is {wi.status.value}; "
                f"can only cancel PENDING or READY items"
            )

    local_to_new: dict[str, str] = {}
    for spec in add_tasks:
        lid = spec.id
        if lid:
            if lid in local_to_new:
                raise InvalidPlan(f"duplicate id '{lid}'")
            local_to_new[lid] = dispatcher.new_id()
    new_items: list[Task] = []
    for spec in add_tasks:
        lid = spec.id
        new_id = local_to_new.get(lid, dispatcher.new_id()) if lid else dispatcher.new_id()
        resolved_deps: list[str] = []
        for d in spec.deps:
            if d in local_to_new:
                resolved_deps.append(local_to_new[d])
            elif d in dispatcher.graph:
                resolved_deps.append(d)
            else:
                raise InvalidPlan(f"replan dep '{d}' is not a local alias or existing graph id")
        new_items.append(
            Task(
                id=new_id,
                team_run_id=dispatcher.team_run_id,
                agent_name=spec.agent,
                status=TaskStatus.PENDING,
                task=spec.task,
                deps=resolved_deps,
                scope_paths=list(spec.scope_paths),
                cascade_policy=spec.cascade_policy,
                parent_id=target_parent_id,
                root_id=target_root_id,
                depth=target_depth,
            )
        )
    if dispatcher.budget_state.tasks_used + len(new_items) > dispatcher.budgets.max_tasks:
        raise BudgetExceeded("max_tasks would be exceeded by replan")
    cancelled_set = set(cancel_ids)
    verifier_reset_deps: list[str] | None = None
    replacing_failed_verifier = _replan_adds_replacement_validator(add_tasks)
    failed_wi = dispatcher.graph.get(replan_task_id)
    if failed_wi is not None and should_reattach_failed_verifier(failed_wi) and not replacing_failed_verifier:
        verifier_reset_deps = build_replan_verifier_deps(
            dispatcher,
            failed_wi,
            new_item_ids=[nwi.id for nwi in new_items],
            cancelled_ids=cancelled_set,
        )
    combined_adj: dict[str, list[str]] = {}
    for wi_id_key, wi in dispatcher.graph.items():
        if wi_id_key not in cancelled_set:
            combined_adj[wi_id_key] = list(wi.deps)
    for nwi in new_items:
        combined_adj[nwi.id] = list(nwi.deps)
    if verifier_reset_deps is not None:
        combined_adj[failed_wi.id] = list(verifier_reset_deps)

    if _has_cycle(combined_adj):
        raise InvalidPlan("replan would create a cycle in the combined graph")
    for cid in cancel_ids:
        wi = dispatcher.graph[cid]
        dispatcher._mark_cancelled(wi, f"cancelled_by_replan_{replan_task_id}")
        cascade_cancel(dispatcher, cid)
    for nwi in new_items:
        dispatcher.graph[nwi.id] = nwi
        dispatcher.budget_state.tasks_used += 1
        dispatcher._emit(make_work_item_added(dispatcher.team_run_id, work_item_to_dict(nwi)))
    if verifier_reset_deps is not None:
        failed_wi.status = TaskStatus.PENDING
        failed_wi.deps = list(verifier_reset_deps)
        failed_wi.agent_run_id = None
        failed_wi.started_at = None
        failed_wi.finished_at = None
        failed_wi.failure_reason = None
        dispatcher._emit(make_work_item_status(dispatcher.team_run_id, failed_wi.id, "pending"))
    if new_items:
        dispatcher._emit_budget()
    dispatcher._promote_ready_work_items()
    return {"added": len(new_items), "cancelled": len(cancel_ids)}


async def request_replan(
    dispatcher: "Dispatcher",
    *,
    wi_id: str,
    request: "ReplanRequest",
) -> Task:
    from agents.registry import find_by_role
    from team.models import ReplanRequest
    async with dispatcher.lock:
        wi = dispatcher.graph[wi_id]
        if wi.status != TaskStatus.RUNNING:
            raise RuntimeError(f"replan: {wi_id} is {wi.status.value}, not RUNNING")
        if dispatcher.budget_state.replans_used >= dispatcher.budgets.max_replans_per_run:
            raise BudgetExceeded("max_replans_per_run reached")
        dispatcher._mark_failed(wi, f"replan_requested: {request.reason}")
        for other in list(dispatcher.graph.values()):
            if (
                other.parent_id == wi.parent_id
                and other.id != wi_id
                and other.status in (TaskStatus.PENDING, TaskStatus.READY)
            ):
                dispatcher._mark_cancelled(other, f"cancelled_by_replan_from_{wi_id}")
                cascade_cancel(dispatcher, other.id)
        cascade_cancel(dispatcher, wi_id)
        done_sibling_ids = [
            other.id
            for other in dispatcher.graph.values()
            if other.parent_id == wi.parent_id
            and other.id != wi_id
            and other.status == TaskStatus.DONE
        ]
        replanners = find_by_role("replanner")
        if not replanners:
            raise RuntimeError("no agent with role='replanner' is registered")
        replanner_agent_name = replanners[0].name
        replanner_id = dispatcher.new_id()
        replanner = Task(
            id=replanner_id,
            team_run_id=dispatcher.team_run_id,
            agent_name=replanner_agent_name,
            status=TaskStatus.PENDING,
            task=f"Replan: {wi.agent_name} failed on task {wi_id}: {request.reason}"
                + (f"\nSuggestion: {request.suggestion}" if request.suggestion else ""),
            deps=done_sibling_ids,
            scope_paths=list(wi.scope_paths),
            parent_id=wi.parent_id,
            root_id=wi.root_id,
            depth=wi.depth,
        )
        dispatcher.graph[replanner_id] = replanner
        dispatcher.budget_state.tasks_used += 1
        dispatcher.budget_state.replans_used += 1
        dispatcher._emit(make_work_item_added(dispatcher.team_run_id, work_item_to_dict(replanner)))
        dispatcher._emit_budget()
        if dispatcher._compute_readiness(replanner):
            dispatcher._promote_to_ready(replanner)
        return replanner
