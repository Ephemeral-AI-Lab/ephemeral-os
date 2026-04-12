"""Event schema for TeamRun persistence.

The runtime is transition-driven — every mutation of the Dispatcher DAG
is naturally an event. Persisting that event stream gives us crash
recovery, observability, and replay for free.

Events are append-only and self-describing. ``TeamRunEvent.to_json`` /
``from_json`` form the wire format shared by every ``TeamRunStore``
implementation (jsonl file, SQL row, in-memory null sink).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

EventKind = Literal[
    "team_run_created",
    "team_run_status",
    "work_item_added",
    "work_item_status",
    "artifact_written",
    "budget_update",
    "checkpoint_taken",
    "checkpoint_repo_state",
    "file_changed",
]


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class TeamRunEvent:
    """One durable transition of a TeamRun.

    ``seq`` is assigned by the store at append time (monotonic within a
    run). ``data`` is a free-form JSON-serialisable dict whose shape
    depends on ``kind``; see the ``make_*`` helpers below for canonical
    payloads.
    """

    team_run_id: str
    kind: EventKind
    data: dict[str, Any] = field(default_factory=dict)
    ts: str = field(default_factory=_utcnow_iso)
    seq: int = 0  # populated by store

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, obj: dict[str, Any]) -> "TeamRunEvent":
        return cls(
            team_run_id=obj["team_run_id"],
            kind=obj["kind"],
            data=dict(obj.get("data") or {}),
            ts=obj.get("ts") or _utcnow_iso(),
            seq=int(obj.get("seq") or 0),
        )


# ---- canonical payload builders -----------------------------------------


def make_team_run_created(
    team_run_id: str,
    *,
    session_id: str,
    user_request: str,
    goal: str | None,
    repo_root: str | None,
    sandbox_id: str | None = None,
    budgets: dict[str, Any],
    roster: dict[str, list[str]] | None = None,
) -> TeamRunEvent:
    data: dict[str, Any] = {
        "session_id": session_id,
        "user_request": user_request,
        "goal": goal,
        "repo_root": repo_root,
        "sandbox_id": sandbox_id,
        "budgets": budgets,
    }
    if roster:
        data["roster"] = roster
    return TeamRunEvent(
        team_run_id=team_run_id,
        kind="team_run_created",
        data=data,
    )


def make_team_run_status(team_run_id: str, status: str, **fields: Any) -> TeamRunEvent:
    payload: dict[str, Any] = {"status": status}
    payload.update(fields)
    return TeamRunEvent(
        team_run_id=team_run_id,
        kind="team_run_status",
        data=payload,
    )


def make_work_item_added(team_run_id: str, wi: dict[str, Any]) -> TeamRunEvent:
    return TeamRunEvent(
        team_run_id=team_run_id,
        kind="work_item_added",
        data={"work_item": wi},
    )


def make_work_item_status(
    team_run_id: str,
    wi_id: str,
    status: str,
    **fields: Any,
) -> TeamRunEvent:
    payload: dict[str, Any] = {"wi_id": wi_id, "status": status}
    payload.update(fields)
    return TeamRunEvent(team_run_id=team_run_id, kind="work_item_status", data=payload)



def make_budget_update(
    team_run_id: str,
    *,
    tasks_used: int,
    note_bytes_used: int,
    replans_used: int = 0,
) -> TeamRunEvent:
    return TeamRunEvent(
        team_run_id=team_run_id,
        kind="budget_update",
        data={
            "tasks_used": tasks_used,
            "note_bytes_used": note_bytes_used,
            "replans_used": replans_used,
        },
    )


def make_checkpoint_taken(
    team_run_id: str,
    *,
    checkpoint_id: str,
    sequence: int,
    label: str | None,
) -> TeamRunEvent:
    return TeamRunEvent(
        team_run_id=team_run_id,
        kind="checkpoint_taken",
        data={
            "checkpoint_id": checkpoint_id,
            "sequence": sequence,
            "label": label,
        },
    )


def make_checkpoint_repo_state(
    team_run_id: str,
    *,
    checkpoint_id: str,
    repo_patch: str,
) -> TeamRunEvent:
    return TeamRunEvent(
        team_run_id=team_run_id,
        kind="checkpoint_repo_state",
        data={
            "checkpoint_id": checkpoint_id,
            "repo_patch": repo_patch,
            "repo_patch_bytes": len(repo_patch.encode("utf-8")),
        },
    )


def make_file_changed(
    team_run_id: str,
    *,
    wi_id: str | None,
    path: str,
    op: str,
) -> TeamRunEvent:
    return TeamRunEvent(
        team_run_id=team_run_id,
        kind="file_changed",
        data={"wi_id": wi_id, "path": path, "op": op},
    )


# ---- helpers for serialising runtime dataclasses ------------------------


def task_to_dict(task: Any) -> dict[str, Any]:
    """Serialise a ``Task`` dataclass to a JSON-safe dict.

    Kept here (not on ``Task``) so the runtime dataclass stays a pure
    data container and persistence concerns live in one place.
    """
    from team.models import Task  # local import to avoid cycles

    assert isinstance(task, Task)
    return {
        "id": task.id,
        "team_run_id": task.team_run_id,
        "agent_name": task.agent_name,
        "status": task.status.value,
        "task": task.task,
        "deps": list(task.deps),
        "scope_paths": list(task.scope_paths),
        "cascade_policy": task.cascade_policy,
        "parent_id": task.parent_id,
        "root_id": task.root_id,
        "depth": task.depth,
        "retry_count": task.retry_count,
        "max_retries": task.max_retries,
        "agent_run_id": task.agent_run_id,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "finished_at": task.finished_at.isoformat() if task.finished_at else None,
        "failure_reason": task.failure_reason,
    }


# Backward-compat alias used by dispatcher imports
work_item_to_dict = task_to_dict
