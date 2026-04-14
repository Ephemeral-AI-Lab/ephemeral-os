from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from team.models import Blocker, BlockerStatus, TeamRunStatus
from team.runtime.conductor import Conductor


class _FakeBlockerStore:
    def __init__(self) -> None:
        self.saved: list[Blocker] = []

    async def save(self, blocker: Blocker) -> None:
        self.saved.append(blocker)


def test_on_fix_failed_fails_run_and_clears_active_blocker():
    blocker_store = _FakeBlockerStore()
    task_center = SimpleNamespace(cancel_paused_tasks=AsyncMock(return_value=2))
    team_run = SimpleNamespace(
        id="run-1",
        task_center=task_center,
        status=TeamRunStatus.RUNNING,
        fail_due_to_blocker=AsyncMock(),
    )
    conductor = Conductor(team_run, blocker_store=blocker_store)
    blocker = Blocker(
        id="blocker-1",
        team_run_id="run-1",
        status=BlockerStatus.FIXING,
        reason="shared import crash",
        root_cause_paths=["pkg/_compat.py"],
        initiating_task_id="task-1",
        fix_task_id="resolver-1",
    )
    conductor._active_blockers[blocker.id] = blocker

    asyncio.run(conductor.on_fix_failed(blocker.id, "resolver exhausted repair options"))

    task_center.cancel_paused_tasks.assert_awaited_once_with(blocker.id)
    team_run.fail_due_to_blocker.assert_awaited_once()
    assert blocker_store.saved[-1].status == BlockerStatus.FAILED
    assert conductor.has_active_blocker() is False


def test_create_blocker_merges_overlapping_paths_into_existing_blocker():
    blocker_store = _FakeBlockerStore()
    team_run = SimpleNamespace(id="run-1", task_center=SimpleNamespace())
    conductor = Conductor(team_run, blocker_store=blocker_store)
    existing = Blocker(
        id="blocker-1",
        team_run_id="run-1",
        status=BlockerStatus.FIXING,
        reason="shared import crash",
        root_cause_paths=["pkg/auth"],
        initiating_task_id="task-1",
        fix_task_id="resolver-1",
    )
    conductor._active_blockers[existing.id] = existing
    conductor._assess_running = AsyncMock()
    conductor._spawn_resolver = AsyncMock()

    blocker = asyncio.run(conductor.create_blocker(
        reason="same shared auth surface still broken",
        root_cause_paths=["pkg/auth/session.py"],
        initiating_task_id="task-2",
        declared_by="task-2",
    ))

    assert blocker is existing
    assert blocker.root_cause_paths == ["pkg/auth", "pkg/auth/session.py"]
    conductor._assess_running.assert_awaited_once_with(existing)
    conductor._spawn_resolver.assert_not_awaited()
    assert blocker_store.saved[-1].root_cause_paths == ["pkg/auth", "pkg/auth/session.py"]


def test_on_fix_complete_resumes_tasks_and_clears_active_blocker():
    blocker_store = _FakeBlockerStore()
    task_center = SimpleNamespace(resume_paused_tasks=AsyncMock(return_value=3))
    team_run = SimpleNamespace(id="run-1", task_center=task_center)
    conductor = Conductor(team_run, blocker_store=blocker_store)
    conductor._spawn_post_fix_replanner = AsyncMock()
    blocker = Blocker(
        id="blocker-1",
        team_run_id="run-1",
        status=BlockerStatus.FIXING,
        reason="shared import crash",
        root_cause_paths=["pkg/_compat.py"],
        initiating_task_id="task-1",
        fix_task_id="resolver-1",
    )
    conductor._active_blockers[blocker.id] = blocker

    asyncio.run(conductor.on_fix_complete(blocker.id, "patched the shared helper"))

    task_center.resume_paused_tasks.assert_awaited_once_with(blocker.id)
    conductor._spawn_post_fix_replanner.assert_awaited_once_with(blocker, "patched the shared helper")
    assert blocker_store.saved[-1].status == BlockerStatus.RESOLVED
    assert blocker_store.saved[-1].fix_summary == "patched the shared helper"
    assert conductor.has_active_blocker() is False


def test_on_fix_failed_falls_back_to_status_when_team_run_has_no_handler():
    blocker_store = _FakeBlockerStore()
    task_center = SimpleNamespace(cancel_paused_tasks=AsyncMock(return_value=1))
    team_run = SimpleNamespace(
        id="run-1",
        task_center=task_center,
        status=TeamRunStatus.RUNNING,
    )
    conductor = Conductor(team_run, blocker_store=blocker_store)
    blocker = Blocker(
        id="blocker-1",
        team_run_id="run-1",
        status=BlockerStatus.FIXING,
        reason="shared import crash",
        root_cause_paths=["pkg/_compat.py"],
        initiating_task_id="task-1",
    )
    conductor._active_blockers[blocker.id] = blocker

    asyncio.run(conductor.on_fix_failed(blocker.id, "no viable repair"))

    assert team_run.status == TeamRunStatus.FAILED
    assert blocker_store.saved[-1].status == BlockerStatus.FAILED


def test_assess_running_emits_pause_assess_events(monkeypatch):
    events: list[dict] = []
    fake_store = SimpleNamespace(
        get_siblings_and_descendants=AsyncMock(return_value=[
            SimpleNamespace(
                id="task-2",
                status="running",
                agent_name="developer",
                agent_run_id="agent-run-2",
                blocker_id=None,
            )
        ]),
    )
    task_center = SimpleNamespace(
        store=fake_store,
        pause_running_task=AsyncMock(return_value=False),
    )
    team_run = SimpleNamespace(
        id="run-1",
        task_center=task_center,
        api_client=object(),
        coordination_metadata={"external_hook_emitter": events.append},
    )
    conductor = Conductor(team_run)
    blocker = Blocker(
        id="blocker-1",
        team_run_id="run-1",
        status=BlockerStatus.ASSESSING,
        reason="shared import crash",
        root_cause_paths=["pkg/_compat.py"],
        initiating_task_id="task-1",
    )

    async def fake_assess_pause(**kwargs):
        from external_trigger.pause_assessment import PauseVerdict

        return PauseVerdict(
            task_id=kwargs["task_id"],
            answer="NO",
            reason="unaffected",
            conversation=[],
        )

    monkeypatch.setattr("external_trigger.pause_assessment.assess_pause", fake_assess_pause)

    asyncio.run(conductor._assess_running(blocker))

    assert events == [
        {
            "event": "external_hook",
            "team_run_id": "run-1",
            "hook": "pause_assess",
            "work_item_id": "task-2",
            "agent": "developer",
            "blocker_id": "blocker-1",
            "status": "started",
        },
        {
            "event": "external_hook",
            "team_run_id": "run-1",
            "hook": "pause_assess",
            "work_item_id": "task-2",
            "agent": "developer",
            "blocker_id": "blocker-1",
            "status": "completed",
            "answer": "NO",
        },
    ]
