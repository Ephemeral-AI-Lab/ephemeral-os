from __future__ import annotations

from types import SimpleNamespace

import pytest

from agents.registry import get_definition
from team.builtins import register_all as register_team_builtins
from team.models import BudgetConfig, BudgetState, Task, TaskStatus
from team.runtime.context_builder import build_work_item_metadata
from tools.core.base import ToolExecutionContext
from tools.posthook.toolkit import SubmitPlanTool


if get_definition("developer") is None:
    register_team_builtins()


class _AsyncTaskCenterStub:
    def __init__(self) -> None:
        self.notes = []

    async def post(self, note) -> None:
        self.notes.append(note)


class _AsyncDispatcherStub:
    def __init__(self, known_ids: set[str] | None = None) -> None:
        self._known_ids = known_ids or set()

    async def known_task_ids(self) -> set[str]:
        return set(self._known_ids)


def test_build_work_item_metadata_enables_team_runtime_flags():
    task = Task(
        id="task-1",
        team_run_id="run-1",
        agent_name="developer",
        status=TaskStatus.PENDING,
        task="implement auth",
        deps=["dep-1", "dep-2"],
        scope_paths=["src/auth"],
        depth=2,
    )
    team_run = SimpleNamespace(
        id="run-1",
        sandbox_id="sbx-1",
        project_context=SimpleNamespace(repo_root="/repo"),
        coordination_metadata={"require_declared_shell_outputs": True},
        task_center=object(),
        dispatcher=object(),
        arbiter=None,
        file_change_store=None,
        budgets=BudgetConfig(max_tasks=12, max_depth=4, max_plan_size=6, max_note_bytes=2048),
        budget_state=BudgetState(tasks_used=3, note_bytes_used=128, replans_used=1),
        root_work_item_id="root-1",
        roster={"developer": ["developer"]},
    )

    meta = build_work_item_metadata(team_run, task)

    assert meta["posthook_enabled"] is True
    assert meta["team_mode_enabled"] is True
    assert meta["task_deps"] == ["dep-1", "dep-2"]
    assert meta["task_parent_id"] is None
    assert meta["task_depth"] == 2
    assert meta["dispatcher"] is team_run.dispatcher
    assert meta["task_center"] is team_run.task_center
    assert meta["max_plan_size"] == 6
    assert meta["max_tasks"] == 12
    assert meta["max_depth"] == 4
    assert meta["max_note_bytes"] == 2048
    assert meta["tasks_used"] == 3
    assert meta["note_bytes_used"] == 128
    assert meta["replans_used"] == 1


@pytest.mark.asyncio
async def test_submit_plan_resolves_roster_role_hints():
    task_center = _AsyncTaskCenterStub()
    dispatcher = _AsyncDispatcherStub()
    ctx = ToolExecutionContext(
        cwd="/tmp",
        metadata={
            "task_center": task_center,
            "dispatcher": dispatcher,
            "work_item_id": "planner-task",
            "agent_name": "team_planner",
            "allow_empty_plan": False,
            "roster": {"reviewer": ["validator"]},
            "max_plan_size": 8,
            "max_tasks": 20,
            "tasks_used": 1,
            "max_depth": 4,
            "task_depth": 0,
            "max_note_bytes": 10_000,
        },
    )

    tool = SubmitPlanTool()
    result = await tool.execute(
        tool.input_model(
            tasks=[
                {"id": "impl", "task": "Implement the API", "agent": "developer"},
                {
                    "id": "review",
                    "task": "Validate the API changes",
                    "agent": "reviewer",
                    "deps": ["impl"],
                    "cascade_policy": "continue",
                },
            ],
            rationale="Implementation then review.",
        ),
        ctx,
    )

    assert result.is_error is False
    submitted = ctx.metadata["submitted_output"]
    assert submitted.tasks[1].agent == "validator"
    assert len(task_center.notes) == 1
    assert "Submitted plan with 2 task(s)." in task_center.notes[0].content


@pytest.mark.asyncio
async def test_submit_plan_rejects_oversize_task_notes():
    task_center = _AsyncTaskCenterStub()
    dispatcher = _AsyncDispatcherStub()
    ctx = ToolExecutionContext(
        cwd="/tmp",
        metadata={
            "task_center": task_center,
            "dispatcher": dispatcher,
            "work_item_id": "planner-task",
            "agent_name": "team_planner",
            "allow_empty_plan": False,
            "max_plan_size": 8,
            "max_tasks": 20,
            "tasks_used": 1,
            "max_depth": 4,
            "task_depth": 0,
            "max_note_bytes": 16,
        },
    )

    tool = SubmitPlanTool()
    result = await tool.execute(
        tool.input_model(
            tasks=[
                {
                    "id": "oversize",
                    "task": "This task description is intentionally too large.",
                    "agent": "developer",
                }
            ]
        ),
        ctx,
    )

    assert result.is_error is True
    assert "max_note_bytes" in result.output
    assert ctx.metadata.get("submitted_output") is None
    assert task_center.notes == []
