"""End-to-end tests for ``task_center.center.TaskCenter``.

Covers the verification scenarios in docs/architecture/gan-task-graph-v1.md.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import pytest

from task_center import Status, TaskSummary
from task_center.center import TaskCenter


Action = Callable[[TaskCenter, str], Awaitable[None]]


def _scripted_spawn(scripts: dict[str, Action]):
    async def spawn(task_id: str, tc: TaskCenter, sandbox_id: str | None) -> None:
        del sandbox_id
        action = scripts.get(task_id)
        if action is not None:
            await action(tc, task_id)

    return spawn


def _summary_kinds(summaries: list[TaskSummary]) -> list[str]:
    return [s.kind for s in summaries]


# ----- 1. Simple task success -----


@pytest.mark.asyncio
async def test_simple_task_success() -> None:
    async def root_action(tc, tid):
        tc.submit_task_success(tid, "done")

    tc = TaskCenter(spawn_func=_scripted_spawn({"t1": root_action}))
    root = await tc.run_query("just do it")

    assert root.status is Status.DONE
    assert root.task_center_harness_graph_id is None
    assert _summary_kinds(root.summaries) == ["success"]
    assert tc.graph.harness_graphs == {}


# ----- 2. Simple task failure -----


@pytest.mark.asyncio
async def test_simple_task_failure() -> None:
    async def root_action(tc, tid):
        tc.submit_task_failure(tid, "blocked")

    tc = TaskCenter(spawn_func=_scripted_spawn({"t1": root_action}))
    root = await tc.run_query("can't do it")

    assert root.status is Status.FAILED
    assert _summary_kinds(root.summaries) == ["failure"]


# ----- 3. Plan-driven happy path -----


@pytest.mark.asyncio
async def test_plan_driven_happy_path() -> None:
    async def root_action(tc, tid):
        tc.launch_plan_handoff(tid, "decompose")

    async def planner_action(tc, tid):
        tc.submit_plan_handoff(
            tid,
            [{"id": "a"}, {"id": "b", "deps": ["a"]}],
            {"a": "do a", "b": "do b"},
            "plan a then b",
        )

    async def child_action(tc, tid):
        tc.submit_task_success(tid, f"{tid} done")

    async def eval_action(tc, tid):
        tc.submit_task_success(tid, "all good")

    scripts = {
        "t1": root_action,
        "t2": planner_action,
        "a": child_action,
        "b": child_action,
        "t2-eval": eval_action,
    }
    tc = TaskCenter(spawn_func=_scripted_spawn(scripts))
    root = await tc.run_query("plan it")

    assert root.status is Status.DONE
    assert tc.graph.get("t2").status is Status.DONE
    assert tc.graph.get("a").status is Status.DONE
    assert tc.graph.get("b").status is Status.DONE
    assert tc.graph.get("t2-eval").status is Status.DONE
    # Root summaries: handoff + child_success
    assert "handoff" in _summary_kinds(root.summaries)
    assert "child_success" in _summary_kinds(root.summaries)


# ----- 4. Soft fail -----


@pytest.mark.asyncio
async def test_soft_fail_dependency_blocked() -> None:
    async def root_action(tc, tid):
        tc.launch_plan_handoff(tid, "decompose")

    async def planner_action(tc, tid):
        tc.submit_plan_handoff(
            tid,
            [{"id": "a"}, {"id": "b", "deps": ["a"]}, {"id": "c"}],
            {"a": "do a", "b": "do b", "c": "do c"},
            "a, b dep on a, c standalone",
        )

    async def fail_a(tc, tid):
        tc.submit_task_failure(tid, "a failed")

    async def succeed_c(tc, tid):
        tc.submit_task_success(tid, "c done")

    async def eval_action(tc, tid):
        tc.submit_task_success(tid, "partial ok")

    scripts = {
        "t1": root_action,
        "t2": planner_action,
        "a": fail_a,
        "c": succeed_c,
        "t2-eval": eval_action,
    }
    tc = TaskCenter(spawn_func=_scripted_spawn(scripts))
    root = await asyncio.wait_for(tc.run_query("scenario"), timeout=2)

    assert root.status is Status.DONE
    assert tc.graph.get("a").status is Status.FAILED
    assert tc.graph.get("b").status is Status.FAILED  # dependency-blocked
    assert tc.graph.get("c").status is Status.DONE
    b_summary_kinds = _summary_kinds(tc.graph.get("b").summaries)
    assert "dependency_blocked" in b_summary_kinds
    assert tc.graph.get("t2-eval").status is Status.DONE


# ----- 5. Hard fail -----


@pytest.mark.asyncio
async def test_hard_fail_propagates_to_root() -> None:
    async def root_action(tc, tid):
        tc.launch_plan_handoff(tid, "decompose")

    async def planner_action(tc, tid):
        tc.submit_plan_handoff(
            tid, [{"id": "a"}], {"a": "do a"}, "single child"
        )

    async def child_action(tc, tid):
        tc.submit_task_success(tid, "a done")

    async def eval_action(tc, tid):
        tc.submit_evaluation_failure(tid, "criteria not met")

    scripts = {
        "t1": root_action,
        "t2": planner_action,
        "a": child_action,
        "t2-eval": eval_action,
    }
    tc = TaskCenter(spawn_func=_scripted_spawn(scripts))
    root = await tc.run_query("scenario")

    assert root.status is Status.FAILED
    assert tc.graph.get("t2").status is Status.FAILED  # planner
    assert tc.graph.get("t2-eval").status is Status.FAILED
    assert "child_failure" in _summary_kinds(root.summaries)


# ----- 6. Nested graph recovery -----


@pytest.mark.asyncio
async def test_nested_graph_recovery() -> None:
    """Inner evaluator hard-fails; outer evaluator dispatches with FAILED child."""
    async def root_action(tc, tid):
        tc.launch_plan_handoff(tid, "outer plan")

    async def outer_planner(tc, tid):
        tc.submit_plan_handoff(
            tid, [{"id": "x"}], {"x": "complex work"}, "x"
        )

    async def x_action(tc, tid):
        tc.launch_plan_handoff(tid, "x decompose")

    async def inner_planner(tc, tid):
        tc.submit_plan_handoff(
            tid, [{"id": "y"}], {"y": "do y"}, "y"
        )

    async def y_action(tc, tid):
        tc.submit_task_success(tid, "y done")

    async def inner_eval(tc, tid):
        tc.submit_evaluation_failure(tid, "inner failed")

    async def outer_eval(tc, tid):
        tc.submit_evaluation_failure(tid, "outer also failed because x failed")

    scripts = {
        "t1": root_action,
        "t2": outer_planner,
        "x": x_action,
        "t3": inner_planner,
        "y": y_action,
        "t3-eval": inner_eval,
        "t2-eval": outer_eval,
    }
    tc = TaskCenter(spawn_func=_scripted_spawn(scripts))
    root = await tc.run_query("nested")

    assert root.status is Status.FAILED
    assert tc.graph.get("x").status is Status.FAILED
    assert tc.graph.get("t3-eval").status is Status.FAILED
    assert tc.graph.get("t2-eval").status is Status.FAILED


# ----- 7. Evaluator-driven replan -----


@pytest.mark.asyncio
async def test_evaluator_driven_replan_context() -> None:
    seen_planner_inputs: dict[str, str] = {}

    async def root_action(tc, tid):
        tc.launch_plan_handoff(tid, "first plan")

    async def planner_action(tc, tid):
        seen_planner_inputs[tid] = tc.graph.get(tid).input
        tc.submit_plan_handoff(
            tid, [{"id": "a"}], {"a": "do a"}, "single"
        )

    async def a_action(tc, tid):
        tc.submit_task_success(tid, "a done")

    async def eval_replan(tc, tid):
        tc.launch_plan_handoff(tid, "fix the gap")

    async def recovery_planner(tc, tid):
        seen_planner_inputs[tid] = tc.graph.get(tid).input
        tc.submit_plan_handoff(
            tid, [{"id": "fix"}], {"fix": "fix the gap"}, "recovery"
        )

    async def fix_action(tc, tid):
        tc.submit_task_success(tid, "fixed")

    async def recovery_eval(tc, tid):
        tc.submit_task_success(tid, "now ok")

    scripts = {
        "t1": root_action,
        "t2": planner_action,
        "a": a_action,
        "t2-eval": eval_replan,
        "t3": recovery_planner,
        "fix": fix_action,
        "t3-eval": recovery_eval,
    }
    tc = TaskCenter(spawn_func=_scripted_spawn(scripts))
    root = await tc.run_query("with replan")

    assert root.status is Status.DONE

    # The recovery planner's input should reflect evaluator-driven recovery context.
    recovery_input = seen_planner_inputs["t3"]
    assert "evaluator" in recovery_input  # caller_role
    assert "fix the gap" in recovery_input  # task_detail
    assert "with replan" in recovery_input  # requested_goal == parent_goal
    assert "single" in recovery_input  # prior_planner_handoff text from planner
    assert "a done" in recovery_input  # completed child summary carried


# ----- 8. Graph helpers -----


@pytest.mark.asyncio
async def test_graph_helpers() -> None:
    async def root_action(tc, tid):
        tc.launch_plan_handoff(tid, "decompose")

    async def planner_action(tc, tid):
        tc.submit_plan_handoff(
            tid, [{"id": "a"}], {"a": "do a"}, "single"
        )
        # While inside, snapshot some helper outputs.

    async def a_check(tc, tid):
        # parent_goal for a child executor is the parent task's input
        # (the harness graph parent is the root executor here).
        assert tc.parent_goal(tid) == "the goal"
        # planner_handoff returns the planner's handoff summaries.
        handoffs = tc.planner_handoff(tid)
        assert any(s.kind == "handoff" for s in handoffs)
        tc.submit_task_success(tid, "a done")

    async def eval_action(tc, tid):
        tc.submit_task_success(tid, "ok")

    scripts = {
        "t1": root_action,
        "t2": planner_action,
        "a": a_check,
        "t2-eval": eval_action,
    }
    tc = TaskCenter(spawn_func=_scripted_spawn(scripts))
    root = await tc.run_query("the goal")
    assert root.status is Status.DONE


# ----- 9. Evaluator dispatch under partial failure -----


@pytest.mark.asyncio
async def test_evaluator_dispatch_under_partial_failure() -> None:
    async def root_action(tc, tid):
        tc.launch_plan_handoff(tid, "plan")

    async def planner_action(tc, tid):
        tc.submit_plan_handoff(
            tid, [{"id": "a"}, {"id": "b"}], {"a": "do a", "b": "do b"}, "two"
        )

    async def a_fail(tc, tid):
        tc.submit_task_failure(tid, "a no good")

    async def b_done(tc, tid):
        tc.submit_task_success(tid, "b done")

    async def eval_action(tc, tid):
        # Evaluator is dispatched even though `a` failed.
        graph_id = tc.graph.get(tid).task_center_harness_graph_id
        assert graph_id is not None
        assert tc.is_harness_graph_ready_for_evaluation(graph_id)
        tc.submit_task_success(tid, "partial ok")

    scripts = {
        "t1": root_action,
        "t2": planner_action,
        "a": a_fail,
        "b": b_done,
        "t2-eval": eval_action,
    }
    tc = TaskCenter(spawn_func=_scripted_spawn(scripts))
    root = await tc.run_query("partial scenario")
    assert root.status is Status.DONE
    assert tc.graph.get("a").status is Status.FAILED
    assert tc.graph.get("b").status is Status.DONE


# ----- 10. Role rejection -----


@pytest.mark.asyncio
async def test_role_rejection_both_directions() -> None:
    """Executor cannot call submit_evaluation_failure; evaluator cannot call submit_task_failure."""
    from task_center import TaskCenterError

    async def root_action(tc, tid):
        with pytest.raises(TaskCenterError):
            tc.submit_evaluation_failure(tid, "wrong tool")
        tc.launch_plan_handoff(tid, "go")

    async def planner_action(tc, tid):
        tc.submit_plan_handoff(tid, [{"id": "a"}], {"a": "do a"}, "plan")

    async def a_action(tc, tid):
        tc.submit_task_success(tid, "a done")

    async def eval_action(tc, tid):
        with pytest.raises(TaskCenterError):
            tc.submit_task_failure(tid, "wrong tool")
        tc.submit_task_success(tid, "ok")

    scripts = {
        "t1": root_action,
        "t2": planner_action,
        "a": a_action,
        "t2-eval": eval_action,
    }
    tc = TaskCenter(spawn_func=_scripted_spawn(scripts))
    root = await tc.run_query("guard test")
    assert root.status is Status.DONE


# ----- 11. Summary history -----


@pytest.mark.asyncio
async def test_summary_history_coexists() -> None:
    """All summary kinds coexist on their respective tasks."""
    async def root_action(tc, tid):
        tc.launch_plan_handoff(tid, "plan it")

    async def planner_action(tc, tid):
        tc.submit_plan_handoff(
            tid, [{"id": "a"}], {"a": "do a"}, "planner says do a"
        )

    async def a_action(tc, tid):
        tc.submit_task_success(tid, "a worked")

    async def eval_action(tc, tid):
        tc.submit_task_success(tid, "looks good")

    scripts = {
        "t1": root_action,
        "t2": planner_action,
        "a": a_action,
        "t2-eval": eval_action,
    }
    tc = TaskCenter(spawn_func=_scripted_spawn(scripts))
    root = await tc.run_query("history")

    # Root: handoff (when launching planner) + child_success (when graph closed)
    assert _summary_kinds(root.summaries) == ["handoff", "child_success"]
    # Planner: handoff (when submitting plan)
    assert _summary_kinds(tc.graph.get("t2").summaries) == ["handoff"]
    # Executor child: success
    assert _summary_kinds(tc.graph.get("a").summaries) == ["success"]
    # Evaluator: success
    assert _summary_kinds(tc.graph.get("t2-eval").summaries) == ["success"]


# ----- bonus: agent that exits without a terminal is treated as failure -----


@pytest.mark.asyncio
async def test_agent_without_terminal_is_treated_as_failure() -> None:
    async def root_does_nothing(tc, tid):
        return

    tc = TaskCenter(spawn_func=_scripted_spawn({"t1": root_does_nothing}))
    root = await tc.run_query("no-op")

    assert root.status is Status.FAILED
    assert _summary_kinds(root.summaries) == ["failure"]


@pytest.mark.asyncio
async def test_run_query_passes_sandbox_id() -> None:
    seen: list[tuple[str, str | None]] = []

    async def spawn(task_id: str, tc: TaskCenter, sandbox_id: str | None) -> None:
        seen.append((task_id, sandbox_id))
        tc.submit_task_success(task_id, "done")

    tc = TaskCenter(spawn_func=spawn)
    await tc.run_query("use selected sandbox", sandbox_id="sandbox-123")

    assert seen == [("t1", "sandbox-123")]


@pytest.mark.asyncio
async def test_each_query_gets_fresh_graph() -> None:
    async def root_action(tc, tid):
        tc.submit_task_success(tid, "ok")

    tc = TaskCenter(spawn_func=_scripted_spawn({"t1": root_action, "t2": root_action}))
    first = await tc.run_query("first")
    second = await tc.run_query("second")

    assert first.status is Status.DONE
    assert second.status is Status.DONE
    assert first.id == "t1"
    assert second.id == "t2"
    # Each run gets a fresh graph (only the second run's tasks remain).
    assert tc.graph.get("t2") is second


@pytest.mark.asyncio
async def test_dag_pipelining_launches_unblocked_task() -> None:
    """Task d launches as soon as dependency a is DONE while sibling b runs."""

    b_can_finish = asyncio.Event()
    c_can_finish = asyncio.Event()
    d_observed: dict[str, str] = {}

    async def root_action(tc, tid):
        tc.launch_plan_handoff(tid, "plan")

    async def planner_action(tc, tid):
        tc.submit_plan_handoff(
            tid,
            [
                {"id": "a"},
                {"id": "b"},
                {"id": "c", "deps": ["a", "b"]},
                {"id": "d", "deps": ["a"]},
            ],
            {tid_: "..." for tid_ in ("a", "b", "c", "d")},
            "pipeline",
        )

    async def a_action(tc, tid):
        tc.submit_task_success(tid, "a done")

    async def b_action(tc, tid):
        await b_can_finish.wait()
        tc.submit_task_success(tid, "b done")

    async def c_action(tc, tid):
        await c_can_finish.wait()
        tc.submit_task_success(tid, "c done")

    async def d_action(tc, tid):
        d_observed["b_status"] = tc.graph.get("b").status.value
        d_observed["c_status"] = tc.graph.get("c").status.value
        tc.submit_task_success(tid, "d done")
        b_can_finish.set()
        c_can_finish.set()

    async def eval_action(tc, tid):
        tc.submit_task_success(tid, "all done")

    scripts = {
        "t1": root_action,
        "t2": planner_action,
        "a": a_action,
        "b": b_action,
        "c": c_action,
        "d": d_action,
        "t2-eval": eval_action,
    }
    tc = TaskCenter(spawn_func=_scripted_spawn(scripts))
    root = await tc.run_query("pipelining")

    assert root.status is Status.DONE
    assert d_observed["b_status"] != "done"
    assert d_observed["c_status"] in ("pending", "running")
