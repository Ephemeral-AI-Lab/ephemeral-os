"""Submission tool integration smoke: plan -> generator -> reducer success."""

from __future__ import annotations

import pytest

from workflow._core.primitives import (
    generator_task_id,
    planner_task_id,
    reducer_task_id,
)
from workflow._core.state import AttemptStatus, IterationCreationReason
from workflow.attempt.launch import AgentLaunch, AttemptDeps
from workflow.attempt.orchestrator import AttemptOrchestrator
from workflow.attempt.orchestrator_registry import (
    AttemptOrchestratorRegistry,
)
from workflow.iteration import OpenIterationCoordinatorRegistry
from tools._framework.core.context import ToolExecutionContextService
from tools._framework.core.runtime import ExecutionMetadata
from tools._framework.execution.tool_call import execute_tool_once
from tools.submission.generator import submit_generator_outcome
from tools.submission.planner import submit_planner_outcome
from tools.submission.reducer import submit_reducer_outcome

from tests.unit_test.test_tools.test_submission._advisor_approval_fixtures import (
    build_advisor_approval_messages,
)

pytestmark = pytest.mark.asyncio


class _FakeLauncher:
    def __init__(self) -> None:
        self.launches: list[AgentLaunch] = []

    def launch(self, launch: AgentLaunch) -> None:
        self.launches.append(launch)


async def _noop_emit(event) -> None:
    del event


def _tool_context(
    runtime: AttemptDeps,
    attempt_id: str,
    task_id: str,
    *,
    role: str = "executor",
    advisor_approves: str | None = None,
):
    messages = (
        build_advisor_approval_messages(tool_name=advisor_approves)
        if advisor_approves is not None
        else []
    )
    metadata = ExecutionMetadata(
        task_center_task_id=task_id,
        task_center_attempt_id=attempt_id,
        attempt_runtime=runtime,
        conversation_messages=messages,
    )
    metadata["role"] = role
    return ToolExecutionContextService(cwd="/tmp", services=metadata)


def _build_runtime(workflow_store, iteration_store, attempt_store, task_store, *, composer):
    workflow = workflow_store.insert(
        task_center_run_id="run1",
        parent_task_id="run1:root",
        workflow_goal="solve task",
    )
    iteration = iteration_store.insert(
        workflow_id=workflow.id,
        sequence_no=1,
        creation_reason=IterationCreationReason.INITIAL,
        iteration_goal="solve task",
        attempt_budget=2,
    )
    workflow_store.append_iteration_id(workflow.id, iteration.id)
    attempt = attempt_store.insert(iteration_id=iteration.id, attempt_sequence_no=1)
    iteration_store.append_attempt_id(iteration.id, attempt.id)
    registry = AttemptOrchestratorRegistry()
    runtime = AttemptDeps(
        workflow_store=workflow_store,
        iteration_store=iteration_store,
        attempt_store=attempt_store,
        task_store=task_store,
        agent_launcher=_FakeLauncher(),
        orchestrator_registry=registry,
        iteration_coordinators=OpenIterationCoordinatorRegistry(),
        composer=composer,
    )
    orchestrator = AttemptOrchestrator(
        attempt=attempt,
        on_attempt_closed=lambda attempt_id: None,
        runtime=runtime,
    )
    registry.register(orchestrator)
    return runtime, orchestrator, attempt.id


async def test_phase03_full_plan_through_reducer_success(
    workflow_store, iteration_store, attempt_store, task_store, composer
) -> None:
    runtime, orchestrator, attempt_id = _build_runtime(
        workflow_store,
        iteration_store,
        attempt_store,
        task_store,
        composer=composer,
    )
    orchestrator.start()

    planner_result = await execute_tool_once(
        submit_planner_outcome,
        {
            "tasks": [{"id": "a", "agent_name": "executor", "needs": []}],
            "task_specs": {"a": "Do the work."},
            "reducers": [{"id": "r", "needs": ["a"], "prompt": "Gate the slice."}],
        },
        _tool_context(
            runtime,
            attempt_id,
            planner_task_id(attempt_id),
            advisor_approves="submit_planner_outcome",
        ),
        emit=_noop_emit,
    )
    generator_result = await execute_tool_once(
        submit_generator_outcome,
        {"status": "success", "outcome": "done"},
        _tool_context(
            runtime,
            attempt_id,
            generator_task_id(attempt_id, "a"),
            advisor_approves="submit_generator_outcome",
        ),
        emit=_noop_emit,
    )
    reducer_result = await execute_tool_once(
        submit_reducer_outcome,
        {"status": "success", "outcome": "gated and passed"},
        _tool_context(
            runtime,
            attempt_id,
            reducer_task_id(attempt_id, "r"),
            role="reducer",
            advisor_approves="submit_reducer_outcome",
        ),
        emit=_noop_emit,
    )

    attempt = attempt_store.get(attempt_id)
    assert not planner_result.is_error
    assert not generator_result.is_error
    assert not reducer_result.is_error
    assert attempt is not None
    assert attempt.status == AttemptStatus.PASSED
