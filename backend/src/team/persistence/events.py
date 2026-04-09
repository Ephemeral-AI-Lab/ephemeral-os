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
) -> TeamRunEvent:
    return TeamRunEvent(
        team_run_id=team_run_id,
        kind="team_run_created",
        data={
            "session_id": session_id,
            "user_request": user_request,
            "goal": goal,
            "repo_root": repo_root,
            "sandbox_id": sandbox_id,
            "budgets": budgets,
        },
    )


def make_team_run_status(team_run_id: str, status: str) -> TeamRunEvent:
    return TeamRunEvent(
        team_run_id=team_run_id,
        kind="team_run_status",
        data={"status": status},
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
    payload.update({k: v for k, v in fields.items() if v is not None})
    return TeamRunEvent(team_run_id=team_run_id, kind="work_item_status", data=payload)


def make_artifact_written(
    team_run_id: str,
    *,
    wi_id: str,
    ref: str,
    size: int,
    payload: Any,
) -> TeamRunEvent:
    return TeamRunEvent(
        team_run_id=team_run_id,
        kind="artifact_written",
        data={"wi_id": wi_id, "ref": ref, "size": size, "payload": payload},
    )


def make_budget_update(
    team_run_id: str,
    *,
    work_items_used: int,
    artifact_bytes_used: int,
) -> TeamRunEvent:
    return TeamRunEvent(
        team_run_id=team_run_id,
        kind="budget_update",
        data={
            "work_items_used": work_items_used,
            "artifact_bytes_used": artifact_bytes_used,
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


def work_item_to_dict(wi: Any) -> dict[str, Any]:
    """Serialise a ``WorkItem`` dataclass to a JSON-safe dict.

    Kept here (not on ``WorkItem``) so the runtime dataclass stays a pure
    data container and persistence concerns live in one place.
    """
    from team.models import WorkItem  # local import to avoid cycles

    assert isinstance(wi, WorkItem)
    return {
        "id": wi.id,
        "team_run_id": wi.team_run_id,
        "agent_name": wi.agent_name,
        "status": wi.status.value,
        "kind": wi.kind.value,
        "deps": list(wi.deps),
        "parent_id": wi.parent_id,
        "root_id": wi.root_id,
        "agent_run_id": wi.agent_run_id,
        "payload": wi.payload,
        "artifact_ref": wi.artifact_ref,
        "timeout_seconds": wi.timeout_seconds,
        "depth": wi.depth,
        "local_id": wi.local_id,
        "briefings": [asdict(b) for b in wi.briefings],
        "dep_artifacts": [asdict(d) for d in wi.dep_artifacts],
        "created_at": wi.created_at.isoformat() if wi.created_at else None,
        "started_at": wi.started_at.isoformat() if wi.started_at else None,
        "finished_at": wi.finished_at.isoformat() if wi.finished_at else None,
        "failure_reason": wi.failure_reason,
    }
