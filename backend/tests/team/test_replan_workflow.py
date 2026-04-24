from __future__ import annotations

from types import SimpleNamespace

import pytest

from agents.registry import get_definition
from team.definitions import register_all as register_team_builtins
from team.core.models import (
    BudgetConfig,
    BudgetState,
    Task,
    TaskDefinition,
    TaskStatus,
)
from .helpers import make_task as _task
from .helpers import structured_spec as _spec
from team.persistence.task_store import TaskStore
from team.task_center import TaskCenter
from tools.core.base import ToolExecutionContext
from tools.submission import SubmitReplanTool


if get_definition("developer") is None:
    register_team_builtins()


class _FakeSessionFactory:
    def __call__(self):
        class _Ctx:
            async def __aenter__(self_inner):
                return None

            async def __aexit__(self_inner, *args):
                return False

        return _Ctx()


@pytest.mark.asyncio
async def test_submit_replan_inserts_new_tasks_as_replanner_children():
    task_center = SimpleNamespace(
        posted=[],
        notes=None,
        graph={
            "replanner": _task(
                "replanner",
                agent_name="team_replanner",
                status=TaskStatus.RUNNING,
            ),
            "sibling": _task("sibling", status=TaskStatus.EXPANDED),
        },
    )

    async def _post(note):
        task_center.posted.append(note)

    task_center.notes = SimpleNamespace(post=_post)
    ctx = ToolExecutionContext(
        cwd="/tmp",
        metadata={
            "task_center": task_center,
            "work_item_id": "replanner",
            "agent_name": "team_replanner",
            "role": "replanner",
        },
    )

    result = await SubmitReplanTool().execute(
        SubmitReplanTool.input_model(
            new_tasks=[
                {
                    "id": "repair",
                    "agent": "developer",
                    "spec": _spec("Repair under the replanner."),
                    "scope_paths": ["src/b.py"],
                },
                {
                    "id": "child",
                    "agent": "developer",
                    "spec": _spec("Repair under the replanner."),
                    "scope_paths": ["src/a.py"],
                },
            ],
            cancel_ids=[],
        ),
        ctx,
    )

    assert result.is_error is False, result.output
    replan = ctx.metadata["resolved_plan"]
    assert [task.parent_id for task in replan.add_tasks] == [
        "replanner",
        "replanner",
    ]


@pytest.mark.asyncio
async def test_submit_replan_rejects_cancel_of_non_direct_sibling():
    task_center = SimpleNamespace(
        posted=[],
        notes=SimpleNamespace(post=lambda note: None),
        graph={
            "replanner": _task(
                "replanner",
                agent_name="team_replanner",
                status=TaskStatus.RUNNING,
            ),
            "sibling": _task("sibling", status=TaskStatus.EXPANDED),
            "nested": _task("nested", parent_id="sibling", status=TaskStatus.READY),
        },
    )
    ctx = ToolExecutionContext(
        cwd="/tmp",
        metadata={
            "task_center": task_center,
            "work_item_id": "replanner",
            "agent_name": "team_replanner",
            "role": "replanner",
        },
    )

    result = await SubmitReplanTool().execute(
        SubmitReplanTool.input_model(cancel_ids=["nested"]),
        ctx,
    )

    assert result.is_error is True
    assert "not a direct sibling" in result.output


@pytest.mark.asyncio
async def test_submit_replan_rejects_cancel_outside_siblings():
    task_center = SimpleNamespace(
        posted=[],
        notes=SimpleNamespace(post=lambda note: None),
        graph={
            "replanner": _task(
                "replanner",
                agent_name="team_replanner",
                status=TaskStatus.RUNNING,
            ),
            "outside": _task("outside", parent_id="other-parent", status=TaskStatus.READY),
        },
    )
    ctx = ToolExecutionContext(
        cwd="/tmp",
        metadata={
            "task_center": task_center,
            "work_item_id": "replanner",
            "agent_name": "team_replanner",
            "role": "replanner",
        },
    )

    result = await SubmitReplanTool().execute(
        SubmitReplanTool.input_model(cancel_ids=["outside"]),
        ctx,
    )

    assert result.is_error is True
    assert "not a direct sibling" in result.output


@pytest.mark.asyncio
async def test_submit_replan_rejects_self_original_and_terminal_cancel_ids():
    task_center = SimpleNamespace(
        posted=[],
        notes=SimpleNamespace(post=lambda note: None),
        graph={
            "failed": _task("failed", status=TaskStatus.REQUEST_REPLAN),
            "replanner": _task(
                "replanner",
                agent_name="team_replanner",
                status=TaskStatus.RUNNING,
                fired_by_task_id="failed",
            ),
            "done": _task("done", status=TaskStatus.DONE),
        },
    )
    ctx = ToolExecutionContext(
        cwd="/tmp",
        metadata={
            "task_center": task_center,
            "work_item_id": "replanner",
            "agent_name": "team_replanner",
            "role": "replanner",
        },
    )

    result = await SubmitReplanTool().execute(
        SubmitReplanTool.input_model(cancel_ids=["replanner", "failed", "done"]),
        ctx,
    )

    assert result.is_error is True
    assert "replanner cannot cancel itself" in result.output
    assert "replanner cannot cancel the original request_replan task" in result.output
    assert "cancel target 'done' is done; cannot cancel" in result.output


@pytest.mark.asyncio
async def test_submit_replan_rejects_dep_on_rewired_downstream_task():
    task_center = SimpleNamespace(
        posted=[],
        notes=SimpleNamespace(post=lambda note: None),
        graph={
            "failed": _task("failed", status=TaskStatus.REQUEST_REPLAN),
            "replanner": _task(
                "replanner",
                agent_name="team_replanner",
                status=TaskStatus.RUNNING,
                fired_by_task_id="failed",
            ),
            "downstream": _task(
                "downstream",
                status=TaskStatus.PENDING,
                deps=["replanner"],
            ),
        },
    )
    ctx = ToolExecutionContext(
        cwd="/tmp",
        metadata={
            "task_center": task_center,
            "work_item_id": "replanner",
            "agent_name": "team_replanner",
            "role": "replanner",
        },
    )

    result = await SubmitReplanTool().execute(
        SubmitReplanTool.input_model(
            new_tasks=[
                {
                    "id": "repair",
                    "agent": "developer",
                    "spec": _spec("Invalidly wait for downstream work blocked on R."),
                    "deps": ["downstream"],
                    "scope_paths": ["src/a.py"],
                }
            ],
            cancel_ids=[],
        ),
        ctx,
    )

    assert result.is_error is True
    assert "unknown dep 'downstream'" in result.output


# request_replan idempotency, replanner insertion/rewiring, and replan
# same-depth child insertion are covered by pure in-memory tests in
# tests/team/test_task_graph.py (``TestPlanRequestReplan`` and
# ``TestApplyReplan``). The old SQL-mocking tests have been retired with the
# displaced ``TaskStore.request_replan`` / ``apply_replan_atomic`` methods.


@pytest.mark.asyncio
async def test_replanner_context_includes_root_cause_trace_and_rewired_dependents():
    tc = TaskCenter(
        session_factory=_FakeSessionFactory(),
        team_run_id="run-1",
        budgets=BudgetConfig(),
        budget_state=BudgetState(),
    )
    tc.graph.update(
        {
            "parent": _task("parent", agent_name="team_planner", status=TaskStatus.EXPANDED),
            "dep": _task("dep", status=TaskStatus.DONE),
            "failed": Task(
                id="failed",
                team_run_id="run-1",
                agent="developer",
                status=TaskStatus.REQUEST_REPLAN,
                spec=_spec("Fix the parser."),
                deps=["dep"],
                scope_paths=["src/parser.py"],
                parent_id="parent",
                root_id="root",
                depth=1,
                failure_reason="replan_requested: parser failure",
            ),
            "replanner": Task(
                id="replanner",
                team_run_id="run-1",
                agent="team_replanner",
                status=TaskStatus.READY,
                spec=_spec("Replan failed parser task."),
                scope_paths=["src/parser.py"],
                parent_id="parent",
                root_id="root",
                depth=1,
                fired_by_task_id="failed",
            ),
            "downstream": _task(
                "downstream",
                status=TaskStatus.PENDING,
                deps=["replanner"],
            ),
        }
    )
    context = await tc.context.context_for(tc.graph["replanner"])

    assert "## Replan root cause trace" in context
    assert "Original task: failed" in context
    assert "Fix the parser." in context
    assert "replan_requested: parser failure" in context
    assert "downstream (pending); deps: replanner" in context
