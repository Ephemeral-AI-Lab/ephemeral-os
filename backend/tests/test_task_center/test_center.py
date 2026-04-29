"""End-to-end tests for ``task_center.runtime.TaskCenter``."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import pytest

from task_center import Status, TaskCenterError, TaskSummary
from task_center.runtime import TaskCenter


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


def _plan_with_final_verifier(*node_ids: str) -> list[dict[str, object]]:
    return [
        *({"id": nid, "deps": [], "role": "executor"} for nid in node_ids),
        {"id": "verify", "deps": list(node_ids), "role": "verifier"},
    ]


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


@pytest.mark.asyncio
async def test_simple_task_failure() -> None:
    async def root_action(tc, tid):
        tc.submit_task_failure(tid, "blocked")

    tc = TaskCenter(spawn_func=_scripted_spawn({"t1": root_action}))
    root = await tc.run_query("can't do it")

    assert root.status is Status.FAILED
    assert _summary_kinds(root.summaries) == ["failure"]


@pytest.mark.asyncio
async def test_plan_driven_happy_path_closes_on_final_verifier() -> None:
    async def root_action(tc, tid):
        tc.request_plan(tid, "decompose")

    async def planner_action(tc, tid):
        tc.submit_full_plan(
            tid,
            [
                {"id": "a", "deps": [], "role": "executor"},
                {"id": "b", "deps": ["a"], "role": "executor"},
                {"id": "verify", "deps": ["a", "b"], "role": "verifier"},
            ],
            {"a": "do a", "b": "do b", "verify": "verify a and b"},
        )

    async def child_action(tc, tid):
        tc.submit_task_success(tid, f"{tid} done")

    async def verify_action(tc, tid):
        tc.submit_verification_success(tid, "all good")

    scripts = {
        "t1": root_action,
        "t2": planner_action,
        "a": child_action,
        "b": child_action,
        "verify": verify_action,
    }
    tc = TaskCenter(spawn_func=_scripted_spawn(scripts))
    root = await tc.run_query("plan it")

    assert root.status is Status.DONE
    assert tc.graph.get("t2").status is Status.DONE
    assert tc.graph.get("a").status is Status.DONE
    assert tc.graph.get("b").status is Status.DONE
    assert tc.graph.get("verify").status is Status.DONE
    assert _summary_kinds(root.summaries) == ["handoff", "child_success"]


@pytest.mark.asyncio
async def test_failure_blocks_final_verifier_and_fails_graph() -> None:
    async def root_action(tc, tid):
        tc.request_plan(tid, "decompose")

    async def planner_action(tc, tid):
        tc.submit_full_plan(
            tid,
            [
                {"id": "a", "deps": [], "role": "executor"},
                {"id": "b", "deps": ["a"], "role": "executor"},
                {"id": "verify", "deps": ["a", "b"], "role": "verifier"},
            ],
            {"a": "do a", "b": "do b", "verify": "verify all work"},
        )

    async def fail_a(tc, tid):
        tc.submit_task_failure(tid, "a failed")

    scripts = {"t1": root_action, "t2": planner_action, "a": fail_a}
    tc = TaskCenter(spawn_func=_scripted_spawn(scripts))
    root = await asyncio.wait_for(tc.run_query("scenario"), timeout=2)

    assert root.status is Status.FAILED
    assert tc.graph.get("a").status is Status.FAILED
    assert tc.graph.get("b").status is Status.FAILED
    assert tc.graph.get("verify").status is Status.FAILED
    assert "dependency_blocked" in _summary_kinds(tc.graph.get("verify").summaries)
    assert "child_failure" in _summary_kinds(root.summaries)


@pytest.mark.asyncio
async def test_final_verifier_failure_uses_fix_executor_then_fails_root() -> None:
    async def root_action(tc, tid):
        tc.request_plan(tid, "decompose")

    async def planner_action(tc, tid):
        tc.submit_full_plan(
            tid,
            _plan_with_final_verifier("a"),
            {"a": "do a", "verify": "verify a"},
        )

    async def child_action(tc, tid):
        tc.submit_task_success(tid, "a done")

    async def verify_action(tc, tid):
        tc.submit_verification_failure(tid, "criteria not met")

    async def fix_action(tc, tid):
        tc.submit_task_failure(tid, "cannot repair")

    scripts = {
        "t1": root_action,
        "t2": planner_action,
        "a": child_action,
        "verify": verify_action,
        "t3": fix_action,
    }
    tc = TaskCenter(spawn_func=_scripted_spawn(scripts))
    root = await tc.run_query("scenario")

    assert root.status is Status.FAILED
    assert tc.graph.get("t2").status is Status.FAILED
    assert tc.graph.get("verify").status is Status.FAILED
    assert "child_failure" in _summary_kinds(root.summaries)


@pytest.mark.asyncio
async def test_nested_plan_success_unblocks_outer_verifier() -> None:
    async def root_action(tc, tid):
        tc.request_plan(tid, "outer plan")

    async def outer_planner(tc, tid):
        tc.submit_full_plan(
            tid,
            _plan_with_final_verifier("x"),
            {"x": "complex work", "verify": "verify x"},
        )

    async def x_action(tc, tid):
        tc.request_plan(tid, "x decompose")

    async def inner_planner(tc, tid):
        tc.submit_full_plan(
            tid,
            [
                {"id": "y", "deps": [], "role": "executor"},
                {"id": "inner_verify", "deps": ["y"], "role": "verifier"},
            ],
            {"y": "do y", "inner_verify": "verify y"},
        )

    async def y_action(tc, tid):
        tc.submit_task_success(tid, "y done")

    async def inner_verify(tc, tid):
        tc.submit_verification_success(tid, "inner ok")

    async def outer_verify(tc, tid):
        tc.submit_verification_success(tid, "outer ok")

    scripts = {
        "t1": root_action,
        "t2": outer_planner,
        "x": x_action,
        "t3": inner_planner,
        "y": y_action,
        "inner_verify": inner_verify,
        "verify": outer_verify,
    }
    tc = TaskCenter(spawn_func=_scripted_spawn(scripts))
    root = await tc.run_query("nested")

    assert root.status is Status.DONE
    assert tc.graph.get("x").status is Status.DONE
    assert tc.graph.get("inner_verify").status is Status.DONE
    assert tc.graph.get("verify").status is Status.DONE


@pytest.mark.asyncio
async def test_nested_plan_failure_blocks_outer_verifier() -> None:
    async def root_action(tc, tid):
        tc.request_plan(tid, "outer plan")

    async def outer_planner(tc, tid):
        tc.submit_full_plan(
            tid,
            _plan_with_final_verifier("x"),
            {"x": "complex work", "verify": "verify x"},
        )

    async def x_action(tc, tid):
        tc.request_plan(tid, "x decompose")

    async def inner_planner(tc, tid):
        tc.submit_full_plan(
            tid,
            [
                {"id": "y", "deps": [], "role": "executor"},
                {"id": "inner_verify", "deps": ["y"], "role": "verifier"},
            ],
            {"y": "do y", "inner_verify": "verify y"},
        )

    async def y_fail(tc, tid):
        tc.submit_task_failure(tid, "y failed")

    scripts = {
        "t1": root_action,
        "t2": outer_planner,
        "x": x_action,
        "t3": inner_planner,
        "y": y_fail,
    }
    tc = TaskCenter(spawn_func=_scripted_spawn(scripts))
    root = await tc.run_query("nested")

    assert root.status is Status.FAILED
    assert tc.graph.get("x").status is Status.FAILED
    assert tc.graph.get("inner_verify").status is Status.FAILED
    assert tc.graph.get("verify").status is Status.FAILED


def test_submit_full_plan_rejects_global_id_collision_before_mutating_planner() -> None:
    tc = TaskCenter()
    root = tc._create_root_executor("root")
    tc._graph.transition(root.id, Status.RUNNING)
    tc.request_plan(root.id, "decompose")
    planner = tc.graph.get("t2")
    tc._graph.transition(planner.id, Status.RUNNING)

    err = tc.submit_full_plan(
        planner.id,
        [
            {"id": root.id, "deps": [], "role": "executor"},
            {"id": "verify", "deps": [root.id], "role": "verifier"},
        ],
        {root.id: "collides with root", "verify": "verify"},
    )

    assert err is not None
    assert err.code == "id_collision"
    assert planner.status is Status.RUNNING
    assert planner.summaries == []


@pytest.mark.asyncio
async def test_role_rejection_both_directions() -> None:
    async def root_action(tc, tid):
        with pytest.raises(TaskCenterError):
            tc.submit_verification_success(tid, "wrong tool")
        tc.request_plan(tid, "go")

    async def planner_action(tc, tid):
        tc.submit_full_plan(
            tid,
            _plan_with_final_verifier("a"),
            {"a": "do a", "verify": "verify a"},
        )

    async def a_action(tc, tid):
        tc.submit_task_success(tid, "a done")

    async def verify_action(tc, tid):
        with pytest.raises(TaskCenterError):
            tc.submit_task_failure(tid, "wrong tool")
        tc.submit_verification_success(tid, "ok")

    scripts = {
        "t1": root_action,
        "t2": planner_action,
        "a": a_action,
        "verify": verify_action,
    }
    tc = TaskCenter(spawn_func=_scripted_spawn(scripts))
    root = await tc.run_query("guard test")
    assert root.status is Status.DONE


@pytest.mark.asyncio
async def test_summary_history_coexists() -> None:
    async def root_action(tc, tid):
        tc.request_plan(tid, "plan it")

    async def planner_action(tc, tid):
        tc.submit_full_plan(
            tid,
            _plan_with_final_verifier("a"),
            {"a": "do a", "verify": "verify a"},
        )

    async def a_action(tc, tid):
        tc.submit_task_success(tid, "a worked")

    async def verify_action(tc, tid):
        tc.submit_verification_success(tid, "looks good")

    scripts = {
        "t1": root_action,
        "t2": planner_action,
        "a": a_action,
        "verify": verify_action,
    }
    tc = TaskCenter(spawn_func=_scripted_spawn(scripts))
    root = await tc.run_query("history")

    assert _summary_kinds(root.summaries) == ["handoff", "child_success"]
    assert _summary_kinds(tc.graph.get("t2").summaries) == []
    assert _summary_kinds(tc.graph.get("a").summaries) == ["success"]
    assert _summary_kinds(tc.graph.get("verify").summaries) == ["success"]


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
    assert tc.graph.get("t2") is second


@pytest.mark.asyncio
async def test_dag_pipelining_launches_unblocked_task() -> None:
    b_can_finish = asyncio.Event()
    c_can_finish = asyncio.Event()
    d_observed: dict[str, str] = {}

    async def root_action(tc, tid):
        tc.request_plan(tid, "plan")

    async def planner_action(tc, tid):
        tc.submit_full_plan(
            tid,
            [
                {"id": "a", "deps": [], "role": "executor"},
                {"id": "b", "deps": [], "role": "executor"},
                {"id": "c", "deps": ["a", "b"], "role": "executor"},
                {"id": "d", "deps": ["a"], "role": "executor"},
                {"id": "verify", "deps": ["a", "b", "c", "d"], "role": "verifier"},
            ],
            {tid_: "..." for tid_ in ("a", "b", "c", "d", "verify")},
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

    async def verify_action(tc, tid):
        tc.submit_verification_success(tid, "all done")

    scripts = {
        "t1": root_action,
        "t2": planner_action,
        "a": a_action,
        "b": b_action,
        "c": c_action,
        "d": d_action,
        "verify": verify_action,
    }
    tc = TaskCenter(spawn_func=_scripted_spawn(scripts))
    root = await tc.run_query("pipelining")

    assert root.status is Status.DONE
    assert d_observed["b_status"] != "done"
    assert d_observed["c_status"] in ("pending", "running")
