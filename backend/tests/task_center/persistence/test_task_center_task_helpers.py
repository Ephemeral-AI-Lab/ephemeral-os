"""Persistence tests for graph-scoped TaskCenter task helpers."""

from __future__ import annotations


def _upsert(
    task_store,
    *,
    task_id: str,
    graph_id: str | None,
    role: str = "generator",
    status: str = "pending",
    needs: list[str] | None = None,
) -> None:
    task_store.upsert_task(
        task_id=task_id,
        task_center_run_id="run1",
        role=role,
        task_input=f"input-{task_id}",
        status=status,
        summaries=[],
        needs=needs or [],
        task_center_harness_graph_id=graph_id,
    )


def test_get_task_returns_serialized_task(task_store):
    _upsert(task_store, task_id="g1:gen:a", graph_id="g1")

    task = task_store.get_task("g1:gen:a")

    assert task is not None
    assert task["id"] == "g1:gen:a"
    assert task["task_center_harness_graph_id"] == "g1"
    assert task["needs"] == []


def test_list_tasks_for_harness_graph_filters_by_graph_id(task_store):
    _upsert(task_store, task_id="g1:planner", graph_id="g1", role="planner")
    _upsert(task_store, task_id="g2:planner", graph_id="g2", role="planner")

    tasks = task_store.list_tasks_for_harness_graph("g1")

    assert [task["id"] for task in tasks] == ["g1:planner"]


def test_set_task_status_updates_status_and_appends_summary(task_store):
    _upsert(task_store, task_id="g1:gen:a", graph_id="g1")

    updated = task_store.set_task_status(
        "g1:gen:a", status="done", summary={"summary": "ok"}
    )

    assert updated["status"] == "done"
    assert updated["summaries"] == [{"summary": "ok"}]


def test_list_generator_tasks_excludes_planner_and_evaluator(task_store):
    _upsert(task_store, task_id="g1:planner", graph_id="g1", role="planner")
    _upsert(task_store, task_id="g1:gen:a", graph_id="g1", role="generator")
    _upsert(task_store, task_id="g1:evaluator", graph_id="g1", role="evaluator")

    tasks = task_store.list_generator_tasks_for_harness_graph("g1")

    assert [task["id"] for task in tasks] == ["g1:gen:a"]
