"""Terminal routing tests for generator and reducer submissions."""

from __future__ import annotations

import pytest

from task_center._core.state import WorkflowStatus
from task_center.attempt import AttemptStage, AttemptStatus
from task_center._core.task_state import TaskCenterTaskStatus
from task_center.submissions import (
    GeneratorSubmission,
    PlannedGeneratorTask,
    PlannedReducerTask,
    PlannerSubmission,
)
from task_center._core.primitives import (
    generator_task_id,
    planner_task_id,
    reducer_task_id,
)
from tools._framework.execution.tool_call import execute_tool_once
from tools.submission.executor import (
    submit_execution_blocker,
    submit_execution_success,
    submit_workflow_handoff,
)
from tools.submission.reducer import (
    submit_reduction_failure,
    submit_reduction_success,
)

from .submission_test_utils import (
    apply_single_generator_plan,
    build_harness_fixture,
    make_tool_context,
    spawn_reducer,
)

pytestmark = pytest.mark.asyncio


async def _noop_emit(event) -> None:
    del event


async def test_submit_execution_success_calls_apply_generator_submission(
    workflow_store, iteration_store, attempt_store, task_store, composer
) -> None:
    fixture = build_harness_fixture(
        workflow_store=workflow_store,
        iteration_store=iteration_store,
        attempt_store=attempt_store,
        task_store=task_store,
        composer=composer,
    )
    generator_id = apply_single_generator_plan(fixture)

    result = await execute_tool_once(
        submit_execution_success,
        {"outcome": "done", "artifacts": ["artifact"]},
        make_tool_context(
            fixture, generator_id, advisor_approves="submit_execution_success"
        ),
        emit=_noop_emit,
    )

    task = task_store.get_task(generator_id)
    assert not result.is_error
    assert result.is_terminal
    assert task is not None
    assert task["status"] == TaskCenterTaskStatus.DONE.value
    assert task["terminal_tool_result"]["generator_role"] == "executor"


async def test_submit_execution_blocker_calls_apply_generator_submission(
    workflow_store, iteration_store, attempt_store, task_store, composer
) -> None:
    fixture = build_harness_fixture(
        workflow_store=workflow_store,
        iteration_store=iteration_store,
        attempt_store=attempt_store,
        task_store=task_store,
        composer=composer,
    )
    generator_id = apply_single_generator_plan(fixture)

    result = await execute_tool_once(
        submit_execution_blocker,
        {"outcome": "blocked by missing dependency"},
        make_tool_context(
            fixture, generator_id, advisor_approves="submit_execution_blocker"
        ),
        emit=_noop_emit,
    )

    task = task_store.get_task(generator_id)
    assert not result.is_error
    assert task is not None
    assert task["status"] == TaskCenterTaskStatus.BLOCKED.value
    assert task["terminal_tool_result"]["generator_role"] == "executor"


async def test_submit_reduction_success_calls_apply_reducer_submission(
    workflow_store, iteration_store, attempt_store, task_store, composer
) -> None:
    fixture = build_harness_fixture(
        workflow_store=workflow_store,
        iteration_store=iteration_store,
        attempt_store=attempt_store,
        task_store=task_store,
        composer=composer,
    )
    reducer_id = spawn_reducer(fixture)

    result = await execute_tool_once(
        submit_reduction_success,
        {"outcome": "reduced"},
        make_tool_context(
            fixture, reducer_id, advisor_approves="submit_reduction_success"
        ),
        emit=_noop_emit,
    )

    attempt = attempt_store.get(fixture.attempt_id)
    assert not result.is_error
    assert attempt is not None
    assert attempt.status == AttemptStatus.PASSED


async def test_submit_reduction_failure_calls_apply_reducer_submission(
    workflow_store, iteration_store, attempt_store, task_store, composer
) -> None:
    fixture = build_harness_fixture(
        workflow_store=workflow_store,
        iteration_store=iteration_store,
        attempt_store=attempt_store,
        task_store=task_store,
        composer=composer,
    )
    reducer_id = spawn_reducer(fixture)

    result = await execute_tool_once(
        submit_reduction_failure,
        {"outcome": "reduction failed"},
        make_tool_context(
            fixture, reducer_id, advisor_approves="submit_reduction_failure"
        ),
        emit=_noop_emit,
    )

    attempt = attempt_store.get(fixture.attempt_id)
    assert not result.is_error
    assert attempt is not None
    assert attempt.status == AttemptStatus.FAILED


async def test_submit_workflow_handoff_starts_delegated_request(
    workflow_store, iteration_store, attempt_store, task_store, composer
) -> None:
    fixture = build_harness_fixture(
        workflow_store=workflow_store,
        iteration_store=iteration_store,
        attempt_store=attempt_store,
        task_store=task_store,
        composer=composer,
    )
    generator_id = apply_single_generator_plan(fixture)

    result = await execute_tool_once(
        submit_workflow_handoff,
        {"goal_handoff": "solve delegated task"},
        make_tool_context(
            fixture, generator_id, advisor_approves="submit_workflow_handoff"
        ),
        emit=_noop_emit,
    )

    task = task_store.get_task(generator_id)
    delegated_request = workflow_store.get(result.metadata["workflow_id"])
    initial_iteration = iteration_store.get(result.metadata["initial_iteration_id"])
    created_attempt = attempt_store.get(result.metadata["initial_attempt_id"])

    assert not result.is_error
    assert result.is_terminal
    assert task is not None
    assert task["status"] == TaskCenterTaskStatus.WAITING_WORKFLOW.value
    assert delegated_request is not None
    assert delegated_request.status == WorkflowStatus.OPEN
    assert delegated_request.parent_task_id == generator_id
    assert delegated_request.workflow_goal == "solve delegated task"
    assert initial_iteration is not None
    assert initial_iteration.workflow_id == delegated_request.id
    assert created_attempt is not None
    assert created_attempt.iteration_id == initial_iteration.id
    assert created_attempt.stage == AttemptStage.PLAN


async def test_submit_workflow_handoff_accepts_any_generator_agent_profile(
    workflow_store, iteration_store, attempt_store, task_store, composer
) -> None:
    from agents import AgentDefinition, AgentRole, register_definition

    register_definition(
        AgentDefinition(
            name="custom_generator",
            description="custom generator for this test",
            tool_call_limit=10,
            role=AgentRole.GENERATOR,
            context_recipe="generator",
            terminals=[
                "submit_workflow_handoff",
                "submit_execution_success",
                "submit_execution_blocker",
            ],
        )
    )

    fixture = build_harness_fixture(
        workflow_store=workflow_store,
        iteration_store=iteration_store,
        attempt_store=attempt_store,
        task_store=task_store,
        composer=composer,
    )
    generator_id = apply_single_generator_plan(
        fixture,
        agent_name="custom_generator",
    )

    result = await execute_tool_once(
        submit_workflow_handoff,
        {"goal_handoff": "delegate broad custom generator work"},
        make_tool_context(
            fixture, generator_id, advisor_approves="submit_workflow_handoff"
        ),
        emit=_noop_emit,
    )

    task = task_store.get_task(generator_id)
    assert not result.is_error
    assert result.is_terminal
    assert task is not None
    assert task["status"] == TaskCenterTaskStatus.WAITING_WORKFLOW.value


async def test_submit_workflow_handoff_child_outcome_updates_outer_generator(
    workflow_store, iteration_store, attempt_store, task_store, composer
) -> None:
    fixture = build_harness_fixture(
        workflow_store=workflow_store,
        iteration_store=iteration_store,
        attempt_store=attempt_store,
        task_store=task_store,
        composer=composer,
    )
    outer_generator_id = apply_single_generator_plan(fixture)

    result = await execute_tool_once(
        submit_workflow_handoff,
        {"goal_handoff": "solve delegated task"},
        make_tool_context(
            fixture,
            outer_generator_id,
            advisor_approves="submit_workflow_handoff",
        ),
        emit=_noop_emit,
    )
    delegated_workflow_id = result.metadata["workflow_id"]
    delegated_attempt_id = result.metadata["initial_attempt_id"]
    delegated_orchestrator = fixture.runtime.orchestrator_registry.get_or_raise(
        delegated_attempt_id
    )
    delegated_planner_id = planner_task_id(delegated_attempt_id)
    delegated_generator_id = generator_task_id(delegated_attempt_id, "delegated")
    delegated_reducer_id = reducer_task_id(delegated_attempt_id, "exit")

    # Drive the delegated attempt to PASSED: plan -> generator success ->
    # reducer success.
    delegated_orchestrator.apply_plan_submission(
        PlannerSubmission(
            attempt_id=delegated_attempt_id,
            planner_task_id=delegated_planner_id,
            kind="completes",
            tasks=(
                PlannedGeneratorTask(
                    local_id="delegated",
                    agent_name="executor",
                    needs=(),
                    task_spec="Do delegated work.",
                ),
            ),
            reducers=(
                PlannedReducerTask(
                    local_id="exit",
                    needs=("delegated",),
                    prompt="Confirm delegated task is complete.",
                ),
            ),
            deferred_goal_for_next_iteration=None,
            outcome="Accepted delegated plan.",
        )
    )
    delegated_orchestrator.apply_generator_submission(
        GeneratorSubmission(
            attempt_id=delegated_attempt_id,
            task_id=delegated_generator_id,
            status="success",
            outcome="Delegated work done.",
            terminal_tool_result={},
        )
    )
    # Reducer success closes the delegated attempt PASSED; closure flows the
    # delegated workflow to SUCCEEDED and resolves the outer generator.
    from task_center.submissions import ReducerSubmission

    delegated_orchestrator.apply_reducer_submission(
        ReducerSubmission(
            attempt_id=delegated_attempt_id,
            task_id=delegated_reducer_id,
            status="success",
            outcome="Delegated task passed.",
            terminal_tool_result={},
        )
    )

    outer_task = task_store.get_task(outer_generator_id)
    outer_attempt = attempt_store.get(fixture.attempt_id)
    delegated_request = workflow_store.get(delegated_workflow_id)

    assert delegated_request is not None
    assert delegated_request.status == WorkflowStatus.SUCCEEDED
    # The outer generator that delegated is resolved off WAITING_WORKFLOW and
    # linked to the child workflow it spawned.
    assert outer_task is not None
    assert outer_task["status"] == TaskCenterTaskStatus.DONE.value
    assert outer_task["terminal_tool_result"]["child_workflow_id"] == delegated_workflow_id
    # The outer attempt remains in its single RUN stage (no EVALUATE stage).
    assert outer_attempt is not None
    assert outer_attempt.stage == AttemptStage.RUN
