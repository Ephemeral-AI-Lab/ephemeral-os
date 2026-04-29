"""Stage 8 — round-trip persistence for the new four-role fields.

The roadmap acceptance is "round-trip persistence for the new fields".
The Task fields are ``fix_target_id`` (Stage 6) and ``spawn_reason``
(Stage 6). The HarnessGraph fields are ``dag_nodes`` (Stage 1),
``plan_shape`` and ``what_to_do_next`` (Stage 3), and ``prior_graph_id``
(Stage 5). Stage 2's ``Status.FIXING`` and the verifier+advisor TaskRole
extensions also round-trip via ``upsert_task``.
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db.models  # noqa: F401  (registers all model tables on Base.metadata)
from db.base import Base
from db.stores.task_center_store import TaskCenterStore


def _make_store() -> TaskCenterStore:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    store = TaskCenterStore()
    store.initialize(sf)
    return store


def _seed_request_and_run(store: TaskCenterStore) -> str:
    store.create_request(
        request_id="req1",
        cwd="/tmp",
        sandbox_id=None,
        request_prompt="x",
    )
    store.create_run(run_id="run1", request_id="req1")
    return "run1"


def test_task_round_trips_fix_target_id_and_spawn_reason() -> None:
    store = _make_store()
    run_id = _seed_request_and_run(store)
    store.upsert_task(
        task_id="run1:fix1",
        run_id=run_id,
        role="executor",
        task_input="fix mode",
        status="ready",
        summaries=[],
        needs=[],
        task_center_harness_graph_id=None,
        fix_target_id="run1:verifier_a",
        spawn_reason="fix_verification",
    )
    rows = store.list_tasks_for_run(run_id)
    assert len(rows) == 1
    row = rows[0]
    assert row["fix_target_id"] == "run1:verifier_a"
    assert row["spawn_reason"] == "fix_verification"


def test_task_round_trips_status_fixing() -> None:
    store = _make_store()
    run_id = _seed_request_and_run(store)
    store.upsert_task(
        task_id="run1:v1",
        run_id=run_id,
        role="verifier",
        task_input="verify",
        status="fixing",
        summaries=[],
        needs=[],
        task_center_harness_graph_id=None,
    )
    rows = store.list_tasks_for_run(run_id)
    assert rows[0]["status"] == "fixing"
    assert rows[0]["role"] == "verifier"


def test_task_round_trips_advisor_role() -> None:
    """Stage 4 surface area: advisor as a TaskRole. Persisted as a string
    column so future store callers can write 'advisor' rows even though
    the live spawn code is deferred."""
    store = _make_store()
    run_id = _seed_request_and_run(store)
    store.upsert_task(
        task_id="run1:advisor1",
        run_id=run_id,
        role="advisor",
        task_input="review proposal",
        status="ready",
        summaries=[],
        needs=[],
        task_center_harness_graph_id=None,
    )
    rows = store.list_tasks_for_run(run_id)
    assert rows[0]["role"] == "advisor"


def test_harness_graph_round_trips_dag_nodes_and_plan_shape() -> None:
    store = _make_store()
    run_id = _seed_request_and_run(store)
    store.upsert_harness_graph(
        graph_id="run1:g1",
        run_id=run_id,
        root_task_id="run1:r",
        planner_task_id="run1:p",
        executor_task_ids=["run1:a", "run1:b"],
        dag_nodes=["run1:a", "run1:b", "run1:v1"],
        plan_shape="full",
        what_to_do_next="",
        prior_graph_id=None,
    )
    rows = store.list_harness_graphs_for_run(run_id)
    assert len(rows) == 1
    row = rows[0]
    assert row["dag_nodes"] == ["run1:a", "run1:b", "run1:v1"]
    assert row["plan_shape"] == "full"
    assert row["what_to_do_next"] == ""
    assert row["prior_graph_id"] is None


def test_harness_graph_round_trips_partial_chain_fields() -> None:
    store = _make_store()
    run_id = _seed_request_and_run(store)
    store.upsert_harness_graph(
        graph_id="run1:g1",
        run_id=run_id,
        root_task_id="run1:r",
        planner_task_id="run1:p1",
        executor_task_ids=["run1:shim"],
        dag_nodes=["run1:shim"],
        plan_shape="partial",
        what_to_do_next="bulk fan-out after shim",
        prior_graph_id=None,
    )
    store.upsert_harness_graph(
        graph_id="run1:g2",
        run_id=run_id,
        root_task_id="run1:r",
        planner_task_id="run1:p2",
        executor_task_ids=["run1:bulk"],
        dag_nodes=["run1:bulk"],
        plan_shape="full",
        what_to_do_next="",
        prior_graph_id="run1:g1",
    )
    rows = store.list_harness_graphs_for_run(run_id)
    g1 = next(r for r in rows if r["id"] == "run1:g1")
    g2 = next(r for r in rows if r["id"] == "run1:g2")
    assert g1["plan_shape"] == "partial"
    assert g1["what_to_do_next"] == "bulk fan-out after shim"
    assert g2["prior_graph_id"] == "run1:g1"
    assert g2["plan_shape"] == "full"


def test_upsert_harness_graph_update_path_preserves_new_fields() -> None:
    """Second upsert with new values should overwrite, not silently drop."""
    store = _make_store()
    run_id = _seed_request_and_run(store)
    # First write — partial.
    store.upsert_harness_graph(
        graph_id="run1:g1",
        run_id=run_id,
        root_task_id="run1:r",
        planner_task_id="run1:p",
        executor_task_ids=[],
        dag_nodes=[],
        plan_shape="partial",
        what_to_do_next="initial directive",
        prior_graph_id=None,
    )
    # Second write — same id, evolved state. Mirrors the lifecycle's
    # repeated _persist_all calls as the graph progresses.
    store.upsert_harness_graph(
        graph_id="run1:g1",
        run_id=run_id,
        root_task_id="run1:r",
        planner_task_id="run1:p",
        executor_task_ids=["run1:a"],
        dag_nodes=["run1:a"],
        plan_shape="full",
        what_to_do_next="",
        prior_graph_id=None,
    )
    rows = store.list_harness_graphs_for_run(run_id)
    assert len(rows) == 1
    row = rows[0]
    assert row["plan_shape"] == "full"
    assert row["what_to_do_next"] == ""
    assert row["dag_nodes"] == ["run1:a"]
