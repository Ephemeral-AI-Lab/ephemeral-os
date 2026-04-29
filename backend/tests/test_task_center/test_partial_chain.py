"""Partial-plan continuation chain driven by final verifier success."""

from __future__ import annotations

from task_center.model import Status
from task_center.runtime import Orchestrator, TaskCenter


def _spawn_orch_running_planner(tc: TaskCenter, root_id: str) -> Orchestrator:
    orch = Orchestrator.spawn(
        tc, root_task_id=root_id, request_plan_note="please plan"
    )
    tc.graph.transition(orch.planner.id, Status.RUNNING)
    return orch


def _drive_dag_to_done(tc: TaskCenter, node_ids: list[str]) -> None:
    remaining = list(node_ids)
    while remaining:
        progressed = False
        for nid in list(remaining):
            task = tc.graph.get(nid)
            if task.status is Status.PENDING and all(
                tc.graph.get(d).status is Status.DONE for d in task.needs
            ):
                tc.graph.transition(nid, Status.READY)
            if task.status is Status.READY:
                tc.graph.transition(nid, Status.RUNNING)
                tc.graph.transition(nid, Status.DONE)
                remaining.remove(nid)
                progressed = True
        if not progressed:
            raise AssertionError(f"DAG drive stuck — remaining: {remaining}")


def test_close_partial_success_marks_planner_done_and_spawns_continuation() -> None:
    tc = TaskCenter()
    root = tc._create_executor(
        input="root goal",
        harness_graph_id=None,
        needs=frozenset(),
        status=Status.READY,
    )
    tc.graph.transition(root.id, Status.RUNNING)
    tc.graph.transition(root.id, Status.HANDOFF)
    orch = _spawn_orch_running_planner(tc, root.id)
    err = orch.materialize_partial_plan(
        task_dep_graphs=[
            {"id": "shim", "deps": [], "role": "executor"},
            {"id": "verify", "deps": ["shim"], "role": "verifier"},
        ],
        task_details={"shim": "build the shim", "verify": "verify shim"},
        what_to_do_next="bulk fan-out after shim lands",
    )
    assert err is None
    _drive_dag_to_done(tc, ["shim"])
    verifier = tc.graph.get("verify")
    tc.graph.transition(verifier.id, Status.READY)
    tc.graph.transition(verifier.id, Status.RUNNING)

    tc.submit_verification_success(verifier.id, "shim verified")

    assert tc.graph.get(orch.planner.id).status is Status.DONE
    assert tc.graph.get(verifier.id).status is Status.DONE
    assert tc.graph.get(root.id).status is Status.HANDOFF
    assert any(s.kind == "segment_success" for s in tc.graph.get(root.id).summaries)
    new_graphs = [
        g
        for g in tc.graph.harness_graphs.values()
        if g.prior_graph_id == orch.graph_id
    ]
    assert len(new_graphs) == 1
    assert new_graphs[0].root_task_id == root.id


def test_partial_chain_terminates_when_segment_full_closes() -> None:
    tc = TaskCenter()
    root = tc._create_executor(
        input="migrate from v1 to v2",
        harness_graph_id=None,
        needs=frozenset(),
        status=Status.READY,
    )
    tc.graph.transition(root.id, Status.RUNNING)
    tc.graph.transition(root.id, Status.HANDOFF)

    seg1 = _spawn_orch_running_planner(tc, root.id)
    seg1.materialize_partial_plan(
        task_dep_graphs=[
            {"id": "shim", "deps": [], "role": "executor"},
            {"id": "verify_shim", "deps": ["shim"], "role": "verifier"},
        ],
        task_details={"shim": "shim", "verify_shim": "verify shim"},
        what_to_do_next="bulk migrate after shim",
    )
    _drive_dag_to_done(tc, ["shim"])
    tc.graph.transition("verify_shim", Status.READY)
    tc.graph.transition("verify_shim", Status.RUNNING)
    tc.submit_verification_success("verify_shim", "shim approved")

    seg2_graph = next(
        g
        for g in tc.graph.harness_graphs.values()
        if g.prior_graph_id == seg1.graph_id
    )
    seg2 = Orchestrator(graph_id=seg2_graph.id, tc=tc)
    tc.graph.transition(seg2.planner.id, Status.RUNNING)
    seg2.materialize_full_plan(
        task_dep_graphs=[
            {"id": "bulk", "deps": [], "role": "executor"},
            {"id": "verify_bulk", "deps": ["bulk"], "role": "verifier"},
        ],
        task_details={"bulk": "bulk migration", "verify_bulk": "verify bulk"},
    )
    _drive_dag_to_done(tc, ["bulk"])
    tc.graph.transition("verify_bulk", Status.READY)
    tc.graph.transition("verify_bulk", Status.RUNNING)
    tc.submit_verification_success("verify_bulk", "migration complete")

    root_task = tc.graph.get(root.id)
    assert root_task.status is Status.DONE
    summary_kinds = [s.kind for s in root_task.summaries]
    assert "segment_success" in summary_kinds
    assert "child_success" in summary_kinds


def test_build_continuation_note_walks_chain() -> None:
    tc = TaskCenter()
    root = tc._create_executor(
        input="ROOT-GOAL-INPUT",
        harness_graph_id=None,
        needs=frozenset(),
        status=Status.READY,
    )
    tc.graph.transition(root.id, Status.RUNNING)
    tc.graph.transition(root.id, Status.HANDOFF)

    seg1 = _spawn_orch_running_planner(tc, root.id)
    seg1.materialize_partial_plan(
        task_dep_graphs=[
            {"id": "a", "deps": [], "role": "executor"},
            {"id": "verify_a", "deps": ["a"], "role": "verifier"},
        ],
        task_details={"a": "a", "verify_a": "verify a"},
        what_to_do_next="DO SEG1 NEXT",
    )
    _drive_dag_to_done(tc, ["a"])
    tc.graph.transition("verify_a", Status.READY)
    tc.graph.transition("verify_a", Status.RUNNING)
    tc.submit_verification_success("verify_a", "SEG1-VERIFY-SUMMARY")

    seg2_graph = next(
        g
        for g in tc.graph.harness_graphs.values()
        if g.prior_graph_id == seg1.graph_id
    )
    seg2 = Orchestrator(graph_id=seg2_graph.id, tc=tc)
    note = seg2.build_continuation_note()
    assert "ROOT-GOAL-INPUT" in note
    assert "DO SEG1 NEXT" in note
    assert "SEG1-VERIFY-SUMMARY" in note
    assert note.startswith("ROOT_GOAL: ")
