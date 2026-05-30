"""Persistence tests for attempt-scoped TaskCenter task helpers."""

from __future__ import annotations


def _upsert(
    task_store,
    *,
    task_id: str,
    role: str = "generator",
    status: str = "pending",
    needs: list[str] | None = None,
    outcomes: list[dict] | None = None,
) -> None:
    task_store.upsert_task(
        task_id=task_id,
        task_center_run_id="run1",
        role=role,
        agent_name=role,
        context_message=f"input-{task_id}",
        status=status,
        outcomes=outcomes or [],
        needs=needs or [],
    )


def test_get_task_returns_serialized_task(task_store):
    _upsert(task_store, task_id="g1:gen:a")

    task = task_store.get_task("g1:gen:a")

    assert task is not None
    assert task["task_id"] == "g1:gen:a"
    assert task["agent_name"] == "generator"
    assert task["needs"] == []
    assert task["outcomes"] == []
    assert task["terminal_tool_result"] is None
    assert task["child_workflow_id"] is None


def test_request_and_run_helpers_return_serialized_rows(task_store):
    request = task_store.get_request("req1")
    run = task_store.get_run("run1")

    assert request is not None
    assert request["id"] == "req1"
    assert request["cwd"] == "/tmp"
    assert run is not None
    assert run["id"] == "run1"
    assert run["status"] == "running"


def test_list_tasks_for_attempt_filters_by_attempt_id(task_store):
    _upsert(task_store, task_id="g1:planner", role="planner")
    _upsert(task_store, task_id="g2:planner", role="planner")

    tasks = task_store.list_tasks_for_attempt("g1")

    assert [task["task_id"] for task in tasks] == ["g1:planner"]


def test_set_task_status_replaces_outcomes(task_store):
    _upsert(task_store, task_id="g1:gen:a", outcomes=[{"outcome": "old"}])

    updated = task_store.set_task_status(
        "g1:gen:a", status="done", outcomes=[{"outcome": "new"}]
    )

    assert updated["status"] == "done"
    assert updated["outcomes"] == [{"outcome": "new"}]
