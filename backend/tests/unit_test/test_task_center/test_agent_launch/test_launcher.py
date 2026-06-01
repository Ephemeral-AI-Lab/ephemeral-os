"""Regression tests for TaskCenter agent launcher scheduling."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from workflow.attempt import AttemptFailReason, AttemptStatus
from workflow.attempt.launch import (
    AgentLaunch,
    AttemptDeps,
    EphemeralAttemptAgentLauncher,
)
from workflow.attempt.orchestrator_registry import AttemptOrchestratorRegistry
from workflow._core.state import IterationCreationReason
from task import AgentRole, TaskStatus
from workflow._core.primitives import generator_task_id, planner_task_id


class _NoopLauncher:
    def launch(self, launch: AgentLaunch) -> None:
        del launch


@pytest.mark.asyncio
async def test_wait_for_idle_prunes_done_tasks_before_next_loop() -> None:
    launcher = EphemeralAttemptAgentLauncher(
        config=SimpleNamespace(),
        deps_provider=lambda: None,
    )
    done_task = asyncio.create_task(asyncio.sleep(0))
    await done_task
    launcher._pending.add(done_task)  # noqa: SLF001 - regression seam

    await asyncio.wait_for(launcher.wait_for_idle(), timeout=0.2)

    assert launcher._pending == set()  # noqa: SLF001 - regression seam


@pytest.mark.asyncio
async def test_missing_orchestrator_exhaustion_closes_attempt(
    workflow_store, iteration_store, attempt_store, task_store, task_center_run_id
) -> None:
    workflow = workflow_store.insert(
        task_center_run_id=task_center_run_id,
        parent_task_id="outer-task",
        workflow_goal="solve",
    )
    iteration = iteration_store.insert(
        workflow_id=workflow.id,
        sequence_no=1,
        creation_reason=IterationCreationReason.INITIAL,
        iteration_goal="solve",
        attempt_budget=1,
    )
    workflow_store.append_iteration_id(workflow.id, iteration.id)
    attempt = attempt_store.insert(iteration_id=iteration.id, attempt_sequence_no=1)
    iteration_store.append_attempt_id(iteration.id, attempt.id)
    task_id = planner_task_id(attempt.id)
    task_store.upsert_task(
        task_id=task_id,
        task_center_run_id=task_center_run_id,
        role=AgentRole.PLANNER.value,
        agent_name="planner",
        context_message="plan",
        status=TaskStatus.RUNNING.value,
        outcomes=[],
        needs=[],
    )
    runtime = AttemptDeps(
        workflow_store=workflow_store,
        iteration_store=iteration_store,
        attempt_store=attempt_store,
        task_store=task_store,
        agent_launcher=_NoopLauncher(),
        orchestrator_registry=AttemptOrchestratorRegistry(),
    )
    launcher = EphemeralAttemptAgentLauncher(
        config=SimpleNamespace(),
        deps_provider=lambda: runtime,
    )

    await launcher._report_unfinished_running_task(  # noqa: SLF001 - regression seam
        AgentLaunch(
            task_id=task_id,
            task_center_run_id=task_center_run_id,
            attempt_id=attempt.id,
            role=AgentRole.PLANNER,
            agent_name="planner",
            context="plan",
            task_guidance="plan the work",
            needs=(),
            workflow_id=workflow.id,
        ),
        summary="Agent run ended without a terminal submission.",
    )

    task = task_store.get_task(task_id)
    refreshed = attempt_store.get(attempt.id)
    assert task is not None
    assert task["status"] == TaskStatus.FAILED.value
    assert refreshed is not None
    assert refreshed.status == AttemptStatus.FAILED
    assert refreshed.fail_reason == AttemptFailReason.TASK_FAILED


@pytest.mark.asyncio
async def test_unowned_generator_exhaustion_persists_attempt_outcome(
    workflow_store, iteration_store, attempt_store, task_store, task_center_run_id
) -> None:
    workflow = workflow_store.insert(
        task_center_run_id=task_center_run_id,
        parent_task_id="outer-task",
        workflow_goal="solve",
    )
    iteration = iteration_store.insert(
        workflow_id=workflow.id,
        sequence_no=1,
        creation_reason=IterationCreationReason.INITIAL,
        iteration_goal="solve",
        attempt_budget=1,
    )
    workflow_store.append_iteration_id(workflow.id, iteration.id)
    attempt = attempt_store.insert(iteration_id=iteration.id, attempt_sequence_no=1)
    iteration_store.append_attempt_id(iteration.id, attempt.id)
    task_id = generator_task_id(attempt.id, "api")
    attempt_store.set_generator_task_ids(attempt.id, [task_id])
    task_store.upsert_task(
        task_id=task_id,
        task_center_run_id=task_center_run_id,
        role=AgentRole.GENERATOR.value,
        agent_name="executor",
        context_message="build api",
        status=TaskStatus.RUNNING.value,
        outcomes=[],
        needs=[],
    )
    runtime = AttemptDeps(
        workflow_store=workflow_store,
        iteration_store=iteration_store,
        attempt_store=attempt_store,
        task_store=task_store,
        agent_launcher=_NoopLauncher(),
        orchestrator_registry=AttemptOrchestratorRegistry(),
    )
    launcher = EphemeralAttemptAgentLauncher(
        config=SimpleNamespace(),
        deps_provider=lambda: runtime,
    )

    await launcher._report_unfinished_running_task(  # noqa: SLF001 - regression seam
        AgentLaunch(
            task_id=task_id,
            task_center_run_id=task_center_run_id,
            attempt_id=attempt.id,
            role=AgentRole.GENERATOR,
            agent_name="executor",
            context="build api",
            task_guidance="build api",
            needs=(),
            workflow_id=workflow.id,
        ),
        summary="Agent run ended without a terminal submission.",
    )

    task = task_store.get_task(task_id)
    refreshed = attempt_store.get(attempt.id)
    assert task is not None
    assert task["status"] == TaskStatus.FAILED.value
    assert refreshed is not None
    assert refreshed.status == AttemptStatus.FAILED
    assert refreshed.fail_reason == AttemptFailReason.TASK_FAILED
    assert len(refreshed.outcomes) == 1
    outcome = refreshed.outcomes[0]
    assert outcome.status == "failed"
    assert outcome.role == "generator"
    assert outcome.task_id == task_id
    assert outcome.outcome == "Agent run ended without a terminal submission."
