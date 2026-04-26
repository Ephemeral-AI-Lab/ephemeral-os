"""Persistence tests for TaskCenter request/run/task/graph records."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db.models  # noqa: F401
from db.base import Base
from db.stores.agent_run_store import AgentRunStore
from db.stores.task_center_store import TaskCenterStore
from task_center.center import TaskCenter


Action = Callable[[TaskCenter, str], Awaitable[None]]


def _memory_store() -> tuple[TaskCenterStore, AgentRunStore]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    task_center_store = TaskCenterStore()
    task_center_store.initialize(sf)
    agent_run_store = AgentRunStore()
    agent_run_store.initialize(sf)
    return task_center_store, agent_run_store


def _scripted_spawn(scripts: dict[str, Action]):
    async def spawn(task_id: str, tc: TaskCenter, sandbox_id: str | None) -> None:
        del sandbox_id
        action = scripts.get(task_id)
        if action is not None:
            await action(tc, task_id)

    return spawn


@pytest.mark.asyncio
async def test_task_center_persists_request_run_tasks_and_graph() -> None:
    store, _ = _memory_store()
    store.create_request(
        request_id="req1",
        cwd="/repo",
        sandbox_id="sandbox-1",
        request_prompt="do the work",
    )
    store.create_run(run_id="run1", request_id="req1")

    async def root_action(tc: TaskCenter, task_id: str) -> None:
        tc.submit_plan_handoff(
            task_id,
            [{"id": "child"}],
            {"child": {"title": "Child", "spec": "child spec"}},
            "child must pass",
            "handoff context",
        )

    async def child_action(tc: TaskCenter, task_id: str) -> None:
        tc.submit_task_completion(task_id, "child done")

    async def eval_action(tc: TaskCenter, task_id: str) -> None:
        tc.submit_task_completion(task_id, "accepted")

    tc = TaskCenter(
        spawn_func=_scripted_spawn(
            {
                "t1": root_action,
                "child": child_action,
                "t1-eval": eval_action,
            }
        ),
        request_id="req1",
        run_id="run1",
        task_center_store=store,
    )
    root = await tc.run_query("do the work", sandbox_id="sandbox-1")

    assert root.summary == "accepted"
    request = store.get_request("req1")
    assert request is not None
    assert request.request_prompt == "do the work"

    runs = store.list_runs_for_request("req1")
    assert runs == [
        {
            "id": "run1",
            "request_id": "req1",
            "root_task_id": "run1:t1",
            "status": "done",
            "started_at": runs[0]["started_at"],
            "finished_at": runs[0]["finished_at"],
        }
    ]

    tasks = {task["id"]: task for task in store.list_tasks_for_run("run1")}
    assert tasks["run1:t1"]["status"] == "done"
    assert tasks["run1:t1"]["task_input"] == "do the work"
    assert "spec" not in tasks["run1:t1"]
    assert tasks["run1:child"]["task_input"] == "child spec"
    assert tasks["run1:child"]["summary"] == "child done"
    assert tasks["run1:t1-eval"]["role"] == "evaluator"
    assert tasks["run1:t1-eval"]["task_input"].startswith("Validate the parent task")

    graph = {node["task_id"]: node for node in store.list_graph_for_run("run1")}
    assert graph["run1:t1"]["children_ids"] == ["run1:child", "run1:t1-eval"]
    assert graph["run1:t1"]["evaluator_id"] == "run1:t1-eval"
    assert graph["run1:t1"]["acceptance_criteria"] == "child must pass"
    assert graph["run1:t1"]["handoff_note"] == "handoff context"


def test_agent_run_is_one_to_one_with_task() -> None:
    store, agent_runs = _memory_store()
    store.create_request(
        request_id="req1",
        cwd="/repo",
        sandbox_id=None,
        request_prompt="prompt",
    )
    store.create_run(run_id="run1", request_id="req1")
    store.upsert_task(
        task_id="run1:t1",
        run_id="run1",
        role="executor",
        title="Root",
        task_input="prompt",
        status="running",
        summary=None,
    )
    store.upsert_graph_node(
        run_id="run1",
        task_id="run1:t1",
        parent_task_id=None,
        children_ids=[],
        evaluator_id=None,
        acceptance_criteria=None,
        handoff_note=None,
    )

    agent_runs.create_run(
        run_id="agent1",
        task_id="run1:t1",
        agent_name="executor",
    )
    agent_runs.finish_run(
        "agent1",
        message_history=[{"role": "user", "content": "prompt"}],
        terminal_tool_result={"output": "done"},
        token_count=7,
    )

    record = agent_runs.get_run("agent1")
    assert record is not None
    assert record.task_id == "run1:t1"
    assert record.terminal_tool_result == {"output": "done"}
    assert record.token_count == 7
