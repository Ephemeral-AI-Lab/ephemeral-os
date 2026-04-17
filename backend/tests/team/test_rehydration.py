from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from team.models import Task, TaskStatus
from team.persistence.events import make_task_status, task_to_dict
from team.runtime.rehydration import apply_replayed_event, task_from_dict


def _task() -> Task:
    return Task(
        id="task-1",
        team_run_id="run-1",
        agent_name="developer",
        status=TaskStatus.RUNNING,
        objective="repair shared import",
        deps=["dep-1"],
        scope_paths=["pkg/_compat.py"],
        agent_run_id="agent-run-1",
        created_at=datetime(2026, 4, 14, tzinfo=timezone.utc),
    )


def test_task_serialization_round_trip_preserves_task_fields():
    original = _task()

    payload = task_to_dict(original)
    restored = task_from_dict(payload)

    assert restored.deps == ["dep-1"]
    assert restored.scope_paths == ["pkg/_compat.py"]
    assert restored.agent_run_id == "agent-run-1"


def test_task_from_dict_requires_objective():
    with pytest.raises(ValueError, match="Task payload requires a non-empty 'objective'"):
        task_from_dict(
            {
                "id": "task-1",
                "team_run_id": "run-1",
                "agent_name": "developer",
                "status": "pending",
                "task": "repair shared import",
            }
        )


def test_apply_replayed_event_updates_status_fields():
    task = _task()
    graph = {task.id: task}

    event = make_task_status(
        "run-1",
        task.id,
        TaskStatus.FAILED.value,
        failure_reason="failed once",
    )

    root_id, budget, final_status = apply_replayed_event(
        event=event,
        graph=graph,
        services=SimpleNamespace(),
        root_id=None,
    )

    assert root_id is None
    assert budget is None
    assert final_status is None
    assert graph[task.id].status == TaskStatus.FAILED
    assert graph[task.id].failure_reason == "failed once"


def test_apply_replayed_event_keeps_existing_status_when_event_status_is_unknown():
    task = _task()
    graph = {task.id: task}
    event = SimpleNamespace(kind="task_status", data={"task_id": task.id, "status": "mystery"})

    root_id, budget, final_status = apply_replayed_event(
        event=event,
        graph=graph,
        services=SimpleNamespace(),
        root_id=None,
    )

    assert root_id is None
    assert budget is None
    assert final_status is None
    assert graph[task.id].status == TaskStatus.RUNNING
