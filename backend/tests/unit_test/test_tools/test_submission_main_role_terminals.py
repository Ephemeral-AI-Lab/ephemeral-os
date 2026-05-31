"""Terminal submission tests for generator and reducer roles."""

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
from tools._framework.core.results import ToolResult
from tools._framework.execution.tool_call import execute_tool_once
from tools.submission.generator import (
    submit_generator_outcome,
    submit_workflow_handoff,
)
from tools.submission.planner import submit_planner_outcome
from tools.submission.reducer import submit_reducer_outcome

from .submission_test_utils import (
    TaskCenterFixture,
    apply_single_generator_plan,
    build_harness_fixture,
    make_tool_context,
    spawn_reducer,
)

pytestmark = pytest.mark.asyncio


async def _noop_emit(event) -> None:
    del event


async def _start_delegated_workflow(
    fixture: TaskCenterFixture, generator_id: str
) -> ToolResult:
    result = await execute_tool_once(
        submit_workflow_handoff,
        {"goal_handoff": "solve delegated task"},
        make_tool_context(
            fixture, generator_id, advisor_approves="submit_workflow_handoff"
        ),
        emit=_noop_emit,
    )
    assert not result.is_error
    return result


def _delegated_fixture(
    fixture: TaskCenterFixture, handoff_result: ToolResult
) -> TaskCenterFixture:
    child_attempt_id = handoff_result.metadata["initial_attempt_id"]
    return TaskCenterFixture(
        runtime=fixture.runtime,
        orchestrator=fixture.runtime.orchestrator_registry.get_or_raise(
            child_attempt_id
        ),
        attempt_id=child_attempt_id,
        request_id=handoff_result.metadata["workflow_id"],
        iteration_id=handoff_result.metadata["initial_iteration_id"],
    )


def _single_generator_submission(attempt_id: str) -> PlannerSubmission:
    return PlannerSubmission(
        attempt_id=attempt_id,
        planner_task_id=planner_task_id(attempt_id),
        kind="completes",
        generators=(
            PlannedGeneratorTask(
                local_id="nested",
                agent_name="executor",
                needs=(),
                task_spec="Do nested work.",
            ),
        ),
        reducers=(
            PlannedReducerTask(
                local_id="exit",
                needs=("nested",),
                prompt="Confirm nested work is complete.",
            ),
        ),
        deferred_goal_for_next_iteration=None,
    )


async def test_submit_generator_outcome_with_success_status_calls_apply_generator_submission(
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
        submit_generator_outcome,
        {"status": "success", "outcome": "done; artifact: artifact"},
        make_tool_context(fixture, generator_id, advisor_approves="submit_generator_outcome"),
        emit=_noop_emit,
    )

    task = task_store.get_task(generator_id)
    assert not result.is_error
    assert result.is_terminal
    assert task is not None
    assert task["status"] == TaskCenterTaskStatus.DONE.value
    assert task["terminal_tool_result"]["generator_role"] == "executor"


async def test_submit_generator_outcome_failed_status_calls_apply_generator_submission(
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
        submit_generator_outcome,
        {"status": "failed", "outcome": "blocked by missing dependency"},
        make_tool_context(fixture, generator_id, advisor_approves="submit_generator_outcome"),
        emit=_noop_emit,
    )

    task = task_store.get_task(generator_id)
    assert not result.is_error
    assert task is not None
    assert task["status"] == TaskCenterTaskStatus.FAILED.value
    assert task["terminal_tool_result"]["generator_role"] == "executor"


async def test_submit_reducer_outcome_with_success_status_calls_apply_reducer_submission(
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
        submit_reducer_outcome,
        {"status": "success", "outcome": "reduced"},
        make_tool_context(fixture, reducer_id, advisor_approves="submit_reducer_outcome"),
        emit=_noop_emit,
    )

    attempt = attempt_store.get(fixture.attempt_id)
    assert not result.is_error
    assert attempt is not None
    assert attempt.status == AttemptStatus.PASSED


async def test_submit_reducer_outcome_failed_status_calls_apply_reducer_submission(
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
        submit_reducer_outcome,
        {"status": "failed", "outcome": "reduction failed"},
        make_tool_context(fixture, reducer_id, advisor_approves="submit_reducer_outcome"),
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

    result = await _start_delegated_workflow(fixture, generator_id)

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


async def test_nested_planner_deferral_prehook_blocks_deferred_goal(
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
    child = _delegated_fixture(
        fixture, await _start_delegated_workflow(fixture, outer_generator_id)
    )

    result = await execute_tool_once(
        submit_planner_outcome,
        {
            "tasks": [{"id": "a", "agent_name": "executor", "needs": []}],
            "task_specs": {"a": "Do nested delegated work."},
            "reducers": [
                {
                    "id": "exit",
                    "needs": ["a"],
                    "prompt": "Confirm delegated work is complete.",
                }
            ],
            "deferred_goal_for_next_iteration": "leave this child goal for later",
        },
        make_tool_context(
            child,
            planner_task_id(child.attempt_id),
            advisor_approves="submit_planner_outcome",
        ),
        emit=_noop_emit,
    )

    attempt = attempt_store.get(child.attempt_id)
    assert result.is_error
    assert "nested workflow planners cannot set deferred_goal_for_next_iteration" in str(
        result.output
    )
    assert attempt is not None
    assert not attempt.generator_task_ids


async def test_nested_generator_handoff_prehook_blocks_workflow_handoff(
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
    child = _delegated_fixture(
        fixture, await _start_delegated_workflow(fixture, outer_generator_id)
    )
    child.orchestrator.apply_plan_submission(_single_generator_submission(child.attempt_id))
    child_generator_id = generator_task_id(child.attempt_id, "nested")

    result = await execute_tool_once(
        submit_workflow_handoff,
        {"goal_handoff": "delegate too deeply"},
        make_tool_context(
            child,
            child_generator_id,
            advisor_approves="submit_workflow_handoff",
        ),
        emit=_noop_emit,
    )

    task = task_store.get_task(child_generator_id)
    assert result.is_error
    assert "nested workflow generators cannot call submit_workflow_handoff" in str(
        result.output
    )
    assert task is not None
    assert task["status"] == TaskCenterTaskStatus.RUNNING.value


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
            terminals=["submit_workflow_handoff", "submit_generator_outcome"],
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
            generators=(
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
