from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from team.models import (
    BudgetConfig,
    BudgetState,
    Task,
    TaskStatus,
    TeamRunStatus,
)
from team.persistence.run_store import TeamRunStore
from team.runtime.dispatcher import Dispatcher
from team.runtime.services import TeamRuntimeServices, build_team_runtime_services

if TYPE_CHECKING:
    from team.persistence.events import TeamRunEvent
    from team.runtime.team_run import TeamRun


def build_resumed_run(
    *,
    team_run_cls: type["TeamRun"],
    store: TeamRunStore,
    team_run_id: str,
    created_event: "TeamRunEvent",
) -> tuple[TeamRuntimeServices, "TeamRun"]:
    meta = created_event.data
    budgets = budget_config_from_event(meta)
    services = build_team_runtime_services(
        team_run_id=team_run_id,
        budgets=budgets,
        budget_state=BudgetState(),
        user_request=meta.get("user_request") or "",
        goal=meta.get("goal"),
        repo_root=meta.get("repo_root") or None,
        event_store=store,
    )
    run = team_run_cls(
        session_id=meta.get("session_id") or "",
        user_request=meta.get("user_request") or "",
        budgets=budgets,
        goal=meta.get("goal"),
        sandbox_id=meta.get("sandbox_id") or None,
        repo_root=meta.get("repo_root") or None,
        team_run_id=team_run_id,
        services=services,
    )
    # Restore roster from the durable event so planner prompts work after resume
    roster_data = meta.get("roster")
    if isinstance(roster_data, dict):
        run.roster = {str(k): list(v) for k, v in roster_data.items()}
    return services, run


def budget_config_from_event(meta: dict[str, Any]) -> BudgetConfig:
    valid_keys = set(BudgetConfig.__dataclass_fields__.keys())
    return BudgetConfig(**{k: v for k, v in dict(meta.get("budgets") or {}).items() if k in valid_keys})


def restore_ready_queue(
    *,
    dispatcher: Dispatcher,
    graph: dict[str, Task],
) -> list[str]:
    ready_order: list[str] = []
    for wi in graph.values():
        if wi.status == TaskStatus.READY:
            dispatcher._ready_queue.put_nowait(wi.id)
            ready_order.append(wi.id)
    return ready_order


def task_from_dict(data: dict[str, Any]) -> Task:
    def _parse_dt(iso: str | None) -> datetime | None:
        return datetime.fromisoformat(iso) if iso else None

    return Task(
        id=data["id"],
        team_run_id=data["team_run_id"],
        agent_name=data["agent_name"],
        status=TaskStatus(data["status"]),
        task=data.get("task", ""),
        deps=list(data.get("deps") or []),
        scope_paths=list(data.get("scope_paths") or []),
        cascade_policy=data.get("cascade_policy", "cancel"),
        parent_id=data.get("parent_id"),
        root_id=data.get("root_id") or "",
        depth=int(data.get("depth") or 0),
        agent_run_id=data.get("agent_run_id"),
        created_at=_parse_dt(data.get("created_at")) or datetime.now(),
        started_at=_parse_dt(data.get("started_at")),
        finished_at=_parse_dt(data.get("finished_at")),
        failure_reason=data.get("failure_reason"),
        retry_count=int(data.get("retry_count") or 0),
        max_retries=int(data.get("max_retries") or 2),
    )


def apply_replayed_event(
    *,
    event: "TeamRunEvent",
    graph: dict[str, Task],
    services: TeamRuntimeServices,
    root_id: str | None,
) -> tuple[str | None, tuple[int, int, int] | None, str | None]:
    last_budget: tuple[int, int, int] | None = None
    final_status: str | None = None
    if event.kind == "work_item_added":
        wi = task_from_dict(event.data["work_item"])
        graph[wi.id] = wi
        if wi.depth == 0 and root_id is None:
            root_id = wi.id
    elif event.kind == "work_item_status":
        wi = graph.get(event.data["wi_id"])
        if wi is not None:
            wi.status = TaskStatus(event.data["status"])
            for key in ("started_at", "finished_at"):
                if key in event.data:
                    iso = event.data.get(key)
                    setattr(wi, key, datetime.fromisoformat(iso) if iso else None)
            if "agent_run_id" in event.data:
                wi.agent_run_id = event.data["agent_run_id"]
            if "failure_reason" in event.data:
                wi.failure_reason = event.data["failure_reason"]
            if "retry_count" in event.data:
                wi.retry_count = int(event.data.get("retry_count") or 0)
            if "max_retries" in event.data:
                wi.max_retries = int(event.data.get("max_retries") or wi.max_retries)
    elif event.kind == "artifact_written":
        pass  # no-op: artifact store removed from new model
    elif event.kind == "budget_update":
        last_budget = (
            int(event.data.get("tasks_used", event.data.get("work_items_used", 0))),
            int(event.data.get("note_bytes_used", event.data.get("artifact_bytes_used", 0))),
            int(event.data.get("replans_used") or 0),
        )
    elif event.kind == "team_run_status":
        status = event.data.get("status")
        if status:
            final_status = TeamRunStatus(status).value
    return root_id, last_budget, final_status
