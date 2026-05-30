"""TaskCenter entry bootstrap tests.

The entry layer converts a top-level prompt into the root workflow: it seeds the
synthetic root bootstrap generator (``<run_id>:root``), delegates the root
workflow to it (flipping it to ``waiting_workflow``), and the workflow's first
attempt launches the planner.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from task_center import start_task_center_run
from task_center.entry import TaskCenterSandboxProvisioner
from task_center._core.primitives import planner_task_id, root_task_id
from task_center._core.task_state import TaskCenterTaskRole, TaskCenterTaskStatus


@pytest.mark.asyncio
async def test_entry_bootstrap_converts_prompt_to_root_workflow(
    workflow_store,
    iteration_store,
    attempt_store,
    task_store,
    register_test_agents,
) -> None:
    release_runner = None
    asyncio_event: asyncio.Event | None = None

    async def runner(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal release_runner, asyncio_event

        if asyncio_event is None:
            asyncio_event = asyncio.Event()
            release_runner = asyncio_event.set
        await asyncio_event.wait()
        return SimpleNamespace(status="ok")

    runtime_cfg = SimpleNamespace(cwd="/tmp")
    handle = start_task_center_run(
        config=runtime_cfg,
        prompt="solve the user request",
        sandbox_id=None,
        on_agent_event=None,
        task_store=task_store,
        workflow_store=workflow_store,
        iteration_store=iteration_store,
        attempt_store=attempt_store,
        runner=runner,
        sandbox_provisioner=TaskCenterSandboxProvisioner(
            create_fn=lambda **_kwargs: {"id": "sandbox-entry-test"}
        ),
    )
    await asyncio.sleep(0)

    workflow = workflow_store.get(handle.workflow_id)
    iteration = iteration_store.get(handle.iteration_id)
    attempt = attempt_store.get(handle.attempt_id)
    root_task = task_store.get_task(root_task_id(handle.task_center_run_id))
    planner_task = task_store.get_task(planner_task_id(handle.attempt_id))
    run_tasks = task_store.list_tasks_for_run(handle.task_center_run_id)

    assert workflow is not None
    # No origin abstraction anymore: the root workflow links back to the
    # synthetic bootstrap generator via parent_task_id.
    assert workflow.parent_task_id == root_task_id(handle.task_center_run_id)
    assert workflow.workflow_goal == "solve the user request"
    assert iteration is not None
    assert iteration.iteration_goal == "solve the user request"
    assert attempt is not None

    # The synthetic root bootstrap generator is waiting on the root workflow.
    assert root_task is not None
    assert root_task["role"] == TaskCenterTaskRole.GENERATOR.value
    assert root_task["status"] == TaskCenterTaskStatus.WAITING_WORKFLOW.value
    assert root_task["child_workflow_id"] == workflow.id

    # The first attempt launched the planner.
    assert planner_task is not None
    assert planner_task["role"] == TaskCenterTaskRole.PLANNER.value
    assert planner_task["status"] == TaskCenterTaskStatus.RUNNING.value

    roles = sorted(task["role"] for task in run_tasks)
    assert roles == [
        TaskCenterTaskRole.GENERATOR.value,
        TaskCenterTaskRole.PLANNER.value,
    ]

    assert release_runner is not None
    release_runner()
    await handle.launcher.wait_for_idle()
