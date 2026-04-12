"""Unit tests for team.runtime.dispatcher.Dispatcher.

Async tests use a simple ``_run()`` helper that calls ``asyncio.run()``
so no pytest-asyncio plugin is required.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from team.errors import BudgetExceeded
from team.models import (
    AgentResult,
    BudgetConfig,
    BudgetState,
    Plan,
    Task,
    TaskSpec,
    TaskStatus,
    TERMINAL_STATUSES,
)
from team.runtime.dispatcher import Dispatcher


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_AGENT_EXISTS_PATH = "team.planning.validation._agent_exists"
_HAS_ROLE_VALIDATION_PATH = "team.planning.validation._has_role"
_GET_DEFN_VALIDATION_PATH = "team.planning.validation._get_definition"
# has_role is imported locally inside dispatcher methods, so patch at the source
_HAS_ROLE_REGISTRY_PATH = "agents.registry.has_role"


def _run(coro):
    """Run a coroutine synchronously."""
    return asyncio.run(coro)


def _make_dispatcher(
    max_tasks: int = 50,
    max_plan_size: int = 50,
    max_depth: int = 4,
) -> Dispatcher:
    budgets = BudgetConfig(max_tasks=max_tasks, max_plan_size=max_plan_size, max_depth=max_depth)
    state = BudgetState()
    return Dispatcher(team_run_id="T1", budgets=budgets, budget_state=state)


def _task(
    id_: str,
    deps: list[str] | None = None,
    agent_name: str = "developer",
    depth: int = 0,
    parent_id: str | None = None,
    root_id: str | None = None,
) -> Task:
    return Task(
        id=id_,
        team_run_id="T1",
        agent_name=agent_name,
        status=TaskStatus.PENDING,
        task=f"task {id_}",
        deps=deps or [],
        depth=depth,
        parent_id=parent_id,
        root_id=root_id or id_,
    )


def _agent_mock(role: str = "developer"):
    class _Defn:
        agent_type = "agent"

    _Defn.role = role
    return _Defn()


# ---------------------------------------------------------------------------
# add_work_item
# ---------------------------------------------------------------------------


def test_add_work_item_adds_task_to_graph():
    async def _test():
        disp = _make_dispatcher()
        task = _task("A")
        await disp.add_work_item(task)
        assert "A" in disp.graph
        assert disp.graph["A"] is task

    _run(_test())


def test_add_work_item_increments_tasks_used():
    async def _test():
        disp = _make_dispatcher()
        assert disp.budget_state.tasks_used == 0
        await disp.add_work_item(_task("A"))
        assert disp.budget_state.tasks_used == 1
        await disp.add_work_item(_task("B"))
        assert disp.budget_state.tasks_used == 2

    _run(_test())


def test_task_with_no_deps_immediately_promoted_to_ready():
    async def _test():
        disp = _make_dispatcher()
        await disp.add_work_item(_task("A"))
        assert disp.graph["A"].status == TaskStatus.READY

    _run(_test())


def test_task_with_deps_stays_pending():
    async def _test():
        disp = _make_dispatcher()
        await disp.add_work_item(_task("A"))
        await disp.add_work_item(_task("B", deps=["A"]))
        assert disp.graph["B"].status == TaskStatus.PENDING

    _run(_test())


# ---------------------------------------------------------------------------
# pop_ready
# ---------------------------------------------------------------------------


def test_pop_ready_returns_ready_task_id():
    async def _test():
        disp = _make_dispatcher()
        await disp.add_work_item(_task("A"))
        task_id = await disp.pop_ready()
        assert task_id == "A"

    _run(_test())


# ---------------------------------------------------------------------------
# mark_running
# ---------------------------------------------------------------------------


def test_mark_running_sets_status_to_running():
    async def _test():
        disp = _make_dispatcher()
        await disp.add_work_item(_task("A"))
        await disp.pop_ready()
        wi = await disp.mark_running("A", "agent-run-1")
        assert wi.status == TaskStatus.RUNNING
        assert wi.agent_run_id == "agent-run-1"

    _run(_test())


# ---------------------------------------------------------------------------
# complete — simple summary
# ---------------------------------------------------------------------------


def test_complete_with_summary_marks_done():
    async def _test():
        disp = _make_dispatcher()
        await disp.add_work_item(_task("A"))
        await disp.pop_ready()
        await disp.mark_running("A", "AR1")
        with patch(_HAS_ROLE_REGISTRY_PATH, return_value=False):
            new_items = await disp.complete("A", AgentResult(summary="done"))
        assert disp.graph["A"].status == TaskStatus.DONE
        assert new_items == []

    _run(_test())


def test_complete_promotes_dependents_to_ready():
    async def _test():
        disp = _make_dispatcher()
        await disp.add_work_item(_task("A"))
        await disp.add_work_item(_task("B", deps=["A"]))
        await disp.pop_ready()
        await disp.mark_running("A", "AR1")
        with patch(_HAS_ROLE_REGISTRY_PATH, return_value=False):
            await disp.complete("A", AgentResult(summary="done"))
        assert disp.graph["B"].status == TaskStatus.READY

    _run(_test())


# ---------------------------------------------------------------------------
# complete — with submitted_plan creates child tasks
# ---------------------------------------------------------------------------


def test_complete_with_submitted_plan_creates_child_tasks():
    async def _test():
        disp = _make_dispatcher()
        planner = _task("PLANNER", agent_name="team_planner")
        await disp.add_work_item(planner)
        await disp.pop_ready()
        await disp.mark_running("PLANNER", "AR1")

        plan = Plan(tasks=[
            TaskSpec(id="x", task="implement", agent="developer"),
            TaskSpec(id="y", task="verify", agent="developer", deps=["x"]),
        ])

        def _has_role_side_effect(name, role):
            return role == "planner" and name == "team_planner"

        with patch(_HAS_ROLE_REGISTRY_PATH, side_effect=_has_role_side_effect), \
             patch(_AGENT_EXISTS_PATH, return_value=True), \
             patch(_HAS_ROLE_VALIDATION_PATH, return_value=False), \
             patch(_GET_DEFN_VALIDATION_PATH, return_value=_agent_mock()):
            new_items = await disp.complete(
                "PLANNER", AgentResult(summary="", submitted_plan=plan)
            )

        assert len(new_items) == 2
        assert disp.graph["PLANNER"].status == TaskStatus.DONE
        statuses = {wi.status for wi in new_items}
        assert TaskStatus.READY in statuses
        assert TaskStatus.PENDING in statuses

    _run(_test())


# ---------------------------------------------------------------------------
# Budget enforcement
# ---------------------------------------------------------------------------


def test_budget_exceeded_raises_when_adding_beyond_max_tasks():
    async def _test():
        disp = _make_dispatcher(max_tasks=2)
        await disp.add_work_item(_task("A"))
        await disp.add_work_item(_task("B"))
        with pytest.raises(BudgetExceeded):
            await disp.add_work_item(_task("C"))

    _run(_test())


def test_budget_not_exceeded_at_exact_limit():
    async def _test():
        disp = _make_dispatcher(max_tasks=2)
        await disp.add_work_item(_task("A"))
        await disp.add_work_item(_task("B"))
        assert disp.budget_state.tasks_used == 2

    _run(_test())


# ---------------------------------------------------------------------------
# all_terminal
# ---------------------------------------------------------------------------


def test_all_terminal_true_when_all_done():
    async def _test():
        disp = _make_dispatcher()
        await disp.add_work_item(_task("A"))
        await disp.pop_ready()
        await disp.mark_running("A", "AR1")
        with patch(_HAS_ROLE_REGISTRY_PATH, return_value=False):
            await disp.complete("A", AgentResult(summary="done"))
        assert disp.all_terminal() is True

    _run(_test())


def test_all_terminal_false_when_some_pending():
    async def _test():
        disp = _make_dispatcher()
        await disp.add_work_item(_task("A"))
        await disp.add_work_item(_task("B", deps=["A"]))
        # A=READY, B=PENDING — neither is terminal
        assert disp.all_terminal() is False

    _run(_test())


def test_all_terminal_true_for_empty_graph():
    disp = _make_dispatcher()
    assert disp.all_terminal() is True


# ---------------------------------------------------------------------------
# fail — marks FAILED and cascade-cancels dependents
# ---------------------------------------------------------------------------


def test_fail_marks_task_failed():
    async def _test():
        disp = _make_dispatcher()
        await disp.add_work_item(_task("A"))
        await disp.fail("A", "something went wrong")
        assert disp.graph["A"].status == TaskStatus.FAILED
        assert disp.graph["A"].failure_reason == "something went wrong"

    _run(_test())


def test_fail_cascade_cancels_dependents():
    async def _test():
        disp = _make_dispatcher()
        await disp.add_work_item(_task("A"))
        await disp.add_work_item(_task("B", deps=["A"]))
        await disp.add_work_item(_task("C", deps=["B"]))
        await disp.fail("A", "boom")
        assert disp.graph["A"].status == TaskStatus.FAILED
        assert disp.graph["B"].status == TaskStatus.CANCELLED
        assert disp.graph["C"].status == TaskStatus.CANCELLED

    _run(_test())


def test_fail_does_not_cancel_independent_tasks():
    async def _test():
        disp = _make_dispatcher()
        await disp.add_work_item(_task("A"))
        await disp.add_work_item(_task("B"))  # no dependency on A
        await disp.fail("A", "boom")
        assert disp.graph["A"].status == TaskStatus.FAILED
        assert disp.graph["B"].status == TaskStatus.READY  # unaffected

    _run(_test())


# ---------------------------------------------------------------------------
# all_terminal with mixed statuses
# ---------------------------------------------------------------------------


def test_all_terminal_true_with_failed_and_cancelled():
    async def _test():
        disp = _make_dispatcher()
        await disp.add_work_item(_task("A"))
        await disp.add_work_item(_task("B", deps=["A"]))
        await disp.fail("A", "error")
        # A=FAILED, B=CANCELLED
        assert disp.all_terminal() is True

    _run(_test())


# ---------------------------------------------------------------------------
# Duplicate task ID
# ---------------------------------------------------------------------------


def test_add_duplicate_task_id_raises_value_error():
    async def _test():
        disp = _make_dispatcher()
        await disp.add_work_item(_task("A"))
        with pytest.raises(ValueError, match="already exists"):
            await disp.add_work_item(_task("A"))

    _run(_test())
