"""Tests for server runtime request-scoped TaskCenter creation."""

from __future__ import annotations

from server.app_factory import RuntimeConfig, RuntimeState
from task_center.runtime import TaskCenter


async def test_create_task_center_returns_request_scoped_instances() -> None:
    runtime = RuntimeState()
    runtime.config = RuntimeConfig(cwd="/tmp")

    async def spawn(task_id: str, tc: TaskCenter, sandbox_id: str | None) -> None:
        del sandbox_id
        tc.submit_task_success(task_id, f"{task_id} done")

    runtime._task_center_spawn_func = spawn

    first = runtime.create_task_center(request_prompt="first", sandbox_id=None)
    second = runtime.create_task_center(request_prompt="second", sandbox_id=None)

    first_root = await first.run_query("first")
    second_root = await second.run_query("second")

    assert first is not second
    assert first_root.summaries[-1].text == "t1 done"
    assert second_root.summaries[-1].text == "t1 done"
    assert first.graph is not second.graph
    assert first.graph.get("t1").input == "first"
    assert second.graph.get("t1").input == "second"
