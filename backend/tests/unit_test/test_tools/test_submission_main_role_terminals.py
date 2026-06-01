"""Terminal submission tests for workflow planner/generator/reducer roles."""

from __future__ import annotations

import json

import pytest

from workflow._core.primitives import reducer_task_id
from workflow._core.state import WorkflowStatus
from workflow.attempt import AttemptStatus
from task import TaskStatus
from workflow.submissions import GeneratorSubmission, ReducerSubmission
from tools._framework.core.results import ToolResult
from tools._framework.execution.tool_call import execute_tool_once
from tools.submission.generator import submit_generator_outcome
from tools.submission.planner import submit_planner_outcome
from tools.submission.reducer import submit_reducer_outcome
from tools.workflow import cancel_workflow, check_workflow_status, delegate_workflow

from .submission_test_utils import (
    TaskCenterFixture,
    apply_single_generator_plan,
    build_harness_fixture,
    make_tool_context,
    spawn_reducer,
    start_planner,
)

pytestmark = pytest.mark.asyncio


async def _noop_emit(event) -> None:
    del event


def _json_payload(result: ToolResult) -> dict:
    return json.loads(result.output)


async def _start_delegated_workflow(
    fixture: TaskCenterFixture,
    generator_id: str,
    *,
    goal: str = "solve delegated task",
) -> ToolResult:
    result = await execute_tool_once(
        delegate_workflow,
        {"goal": goal},
        make_tool_context(fixture, generator_id),
        emit=_noop_emit,
    )
    assert not result.is_error
    return result


def _delegated_fixture(
    fixture: TaskCenterFixture, delegated_result: ToolResult
) -> TaskCenterFixture:
    delegated_attempt_id = delegated_result.metadata["initial_attempt_id"]
    delegated_workflow_id = delegated_result.metadata["workflow_id"]
    return TaskCenterFixture(
        runtime=fixture.runtime,
        orchestrator=fixture.runtime.orchestrator_registry.get_or_raise(
            delegated_attempt_id
        ),
        attempt_id=delegated_attempt_id,
        request_id=fixture.request_id,
        workflow_id=delegated_workflow_id,
        iteration_id=delegated_result.metadata["initial_iteration_id"],
        background_manager=fixture.background_manager,
    )


def _build_fixture(
    workflow_store,
    iteration_store,
    attempt_store,
    task_store,
    composer,
) -> TaskCenterFixture:
    return build_harness_fixture(
        workflow_store=workflow_store,
        iteration_store=iteration_store,
        attempt_store=attempt_store,
        task_store=task_store,
        composer=composer,
    )


async def test_submit_generator_outcome_with_success_status_calls_apply_generator_submission(
    workflow_store, iteration_store, attempt_store, task_store, composer
) -> None:
    fixture = _build_fixture(
        workflow_store, iteration_store, attempt_store, task_store, composer
    )
    generator_id = apply_single_generator_plan(fixture)

    result = await execute_tool_once(
        submit_generator_outcome,
        {"status": "success", "outcome": "done; artifact: artifact"},
        make_tool_context(
            fixture, generator_id, advisor_approves="submit_generator_outcome"
        ),
        emit=_noop_emit,
    )

    task = task_store.get_task(generator_id)
    assert not result.is_error
    assert result.is_terminal
    assert task is not None
    assert task["status"] == TaskStatus.DONE.value
    assert task["terminal_tool_result"]["generator_role"] == "executor"


async def test_submit_generator_outcome_failed_status_calls_apply_generator_submission(
    workflow_store, iteration_store, attempt_store, task_store, composer
) -> None:
    fixture = _build_fixture(
        workflow_store, iteration_store, attempt_store, task_store, composer
    )
    generator_id = apply_single_generator_plan(fixture)

    result = await execute_tool_once(
        submit_generator_outcome,
        {"status": "failed", "outcome": "blocked by missing dependency"},
        make_tool_context(
            fixture, generator_id, advisor_approves="submit_generator_outcome"
        ),
        emit=_noop_emit,
    )

    task = task_store.get_task(generator_id)
    assert not result.is_error
    assert task is not None
    assert task["status"] == TaskStatus.FAILED.value
    assert task["terminal_tool_result"]["generator_role"] == "executor"


async def test_submit_reducer_outcome_with_success_status_calls_apply_reducer_submission(
    workflow_store, iteration_store, attempt_store, task_store, composer
) -> None:
    fixture = _build_fixture(
        workflow_store, iteration_store, attempt_store, task_store, composer
    )
    reducer_id = spawn_reducer(fixture)

    result = await execute_tool_once(
        submit_reducer_outcome,
        {"status": "success", "outcome": "reduced"},
        make_tool_context(
            fixture, reducer_id, advisor_approves="submit_reducer_outcome"
        ),
        emit=_noop_emit,
    )

    attempt = attempt_store.get(fixture.attempt_id)
    assert not result.is_error
    assert attempt is not None
    assert attempt.status == AttemptStatus.PASSED


async def test_submit_reducer_outcome_failed_status_calls_apply_reducer_submission(
    workflow_store, iteration_store, attempt_store, task_store, composer
) -> None:
    fixture = _build_fixture(
        workflow_store, iteration_store, attempt_store, task_store, composer
    )
    reducer_id = spawn_reducer(fixture)

    result = await execute_tool_once(
        submit_reducer_outcome,
        {"status": "failed", "outcome": "reduction failed"},
        make_tool_context(
            fixture, reducer_id, advisor_approves="submit_reducer_outcome"
        ),
        emit=_noop_emit,
    )

    attempt = attempt_store.get(fixture.attempt_id)
    assert not result.is_error
    assert attempt is not None
    assert attempt.status == AttemptStatus.FAILED


async def test_delegate_workflow_starts_non_terminal_delegated_workflow(
    workflow_store, iteration_store, attempt_store, task_store, composer
) -> None:
    fixture = _build_fixture(
        workflow_store, iteration_store, attempt_store, task_store, composer
    )
    generator_id = apply_single_generator_plan(fixture)

    result = await _start_delegated_workflow(fixture, generator_id)
    payload = _json_payload(result)

    task = task_store.get_task(generator_id)
    workflow = workflow_store.get(result.metadata["workflow_id"])
    initial_iteration = iteration_store.get(result.metadata["initial_iteration_id"])
    created_attempt = attempt_store.get(result.metadata["initial_attempt_id"])

    assert not result.is_terminal
    assert payload["workflow_task_id"] == "wf_1"
    assert payload["status"] == "running"
    assert task is not None
    assert task["status"] == TaskStatus.RUNNING.value
    assert workflow is not None
    assert workflow.status == WorkflowStatus.OPEN
    assert workflow.parent_task_id == generator_id
    assert workflow.workflow_goal == "solve delegated task"
    assert initial_iteration is not None
    assert initial_iteration.workflow_id == workflow.id
    assert created_attempt is not None
    assert created_attempt.iteration_id == initial_iteration.id
    assert fixture.background_manager.count_by_agent("executor") == 1


async def test_delegate_workflow_rejects_second_open_workflow_for_same_task(
    workflow_store, iteration_store, attempt_store, task_store, composer
) -> None:
    fixture = _build_fixture(
        workflow_store, iteration_store, attempt_store, task_store, composer
    )
    generator_id = apply_single_generator_plan(fixture)
    first = await _start_delegated_workflow(fixture, generator_id)

    second = await execute_tool_once(
        delegate_workflow,
        {"goal": "another delegated task"},
        make_tool_context(fixture, generator_id),
        emit=_noop_emit,
    )

    payload = _json_payload(second)
    assert second.is_error
    assert payload["workflow_task_id"] == first.metadata["workflow_task_id"]
    assert "already outstanding" in payload["message"]


async def test_generator_terminal_rejects_non_generator_tasks(
    workflow_store, iteration_store, attempt_store, task_store, composer
) -> None:
    fixture = _build_fixture(
        workflow_store, iteration_store, attempt_store, task_store, composer
    )
    planner_id = start_planner(fixture)

    result = await execute_tool_once(
        submit_generator_outcome,
        {"status": "success", "outcome": "wrong task kind"},
        make_tool_context(
            fixture,
            planner_id,
            role="planner",
            advisor_approves="submit_generator_outcome",
        ),
        emit=_noop_emit,
    )

    task = task_store.get_task(planner_id)
    assert result.is_error
    assert f"Task {planner_id!r} is not a generator task" in str(result.output)
    assert task is not None
    assert task["status"] == TaskStatus.RUNNING.value


async def test_nested_planner_deferral_prehook_blocks_deferred_goal(
    workflow_store, iteration_store, attempt_store, task_store, composer
) -> None:
    fixture = _build_fixture(
        workflow_store, iteration_store, attempt_store, task_store, composer
    )
    outer_generator_id = apply_single_generator_plan(fixture)
    delegated = await _start_delegated_workflow(fixture, outer_generator_id)
    child = _delegated_fixture(fixture, delegated)

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
            child.runtime.attempt_store.get(child.attempt_id).planner_task_id,
            role="planner",
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


async def test_nested_generator_can_delegate_workflow(
    workflow_store, iteration_store, attempt_store, task_store, composer
) -> None:
    fixture = _build_fixture(
        workflow_store, iteration_store, attempt_store, task_store, composer
    )
    outer_generator_id = apply_single_generator_plan(fixture)
    child = _delegated_fixture(
        fixture, await _start_delegated_workflow(fixture, outer_generator_id)
    )
    child_generator_id = apply_single_generator_plan(child, local_id="nested")

    result = await execute_tool_once(
        delegate_workflow,
        {"goal": "delegate nested generator work"},
        make_tool_context(child, child_generator_id),
        emit=_noop_emit,
    )

    nested = workflow_store.get(result.metadata["workflow_id"])
    task = task_store.get_task(child_generator_id)
    assert not result.is_error
    assert not result.is_terminal
    assert nested is not None
    assert nested.parent_task_id == child_generator_id
    assert task is not None
    assert task["status"] == TaskStatus.RUNNING.value


async def test_delegate_workflow_accepts_any_generator_agent_profile(
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
            allowed_tools=[
                "delegate_workflow",
                "check_workflow_status",
                "cancel_workflow",
            ],
            terminals=["submit_generator_outcome"],
        )
    )

    fixture = _build_fixture(
        workflow_store, iteration_store, attempt_store, task_store, composer
    )
    generator_id = apply_single_generator_plan(
        fixture,
        agent_name="custom_generator",
    )

    result = await execute_tool_once(
        delegate_workflow,
        {"goal": "delegate broad custom generator work"},
        make_tool_context(fixture, generator_id),
        emit=_noop_emit,
    )

    task = task_store.get_task(generator_id)
    assert not result.is_error
    assert not result.is_terminal
    assert task is not None
    assert task["status"] == TaskStatus.RUNNING.value


async def test_workflow_status_delivery_unblocks_generator_terminal(
    workflow_store, iteration_store, attempt_store, task_store, composer
) -> None:
    fixture = _build_fixture(
        workflow_store, iteration_store, attempt_store, task_store, composer
    )
    outer_generator_id = apply_single_generator_plan(fixture)
    delegated = await _start_delegated_workflow(fixture, outer_generator_id)
    child = _delegated_fixture(fixture, delegated)
    child_generator_id = apply_single_generator_plan(child, local_id="delegated")
    child_reducer_id = reducer_task_id(child.attempt_id, "r")

    child.orchestrator.apply_generator_submission(
        GeneratorSubmission(
            attempt_id=child.attempt_id,
            task_id=child_generator_id,
            status="success",
            outcome="Delegated work done.",
            terminal_tool_result={},
        )
    )
    child.orchestrator.apply_reducer_submission(
        ReducerSubmission(
            attempt_id=child.attempt_id,
            task_id=child_reducer_id,
            status="success",
            outcome="Delegated task passed.",
            terminal_tool_result={},
        )
    )

    status_result = await execute_tool_once(
        check_workflow_status,
        {
            "workflow_id": delegated.metadata["workflow_id"],
            "workflow_task_id": delegated.metadata["workflow_task_id"],
        },
        make_tool_context(fixture, outer_generator_id),
        emit=_noop_emit,
    )
    terminal_result = await execute_tool_once(
        submit_generator_outcome,
        {"status": "success", "outcome": "used delegated result"},
        make_tool_context(
            fixture,
            outer_generator_id,
            advisor_approves="submit_generator_outcome",
        ),
        emit=_noop_emit,
    )

    payload = _json_payload(status_result)
    outer_task = task_store.get_task(outer_generator_id)
    workflow = workflow_store.get(delegated.metadata["workflow_id"])
    assert not status_result.is_error
    assert payload["status"] == WorkflowStatus.SUCCEEDED.value
    assert workflow is not None
    assert workflow.status == WorkflowStatus.SUCCEEDED
    assert fixture.background_manager.count_by_agent("executor") == 0
    assert not terminal_result.is_error
    assert terminal_result.is_terminal
    assert outer_task is not None
    assert outer_task["status"] == TaskStatus.DONE.value


async def test_cancel_workflow_releases_generator_terminal_gate(
    workflow_store, iteration_store, attempt_store, task_store, composer
) -> None:
    fixture = _build_fixture(
        workflow_store, iteration_store, attempt_store, task_store, composer
    )
    generator_id = apply_single_generator_plan(fixture)
    delegated = await _start_delegated_workflow(fixture, generator_id)

    blocked = await execute_tool_once(
        submit_generator_outcome,
        {"status": "success", "outcome": "too early"},
        make_tool_context(
            fixture, generator_id, advisor_approves="submit_generator_outcome"
        ),
        emit=_noop_emit,
    )
    cancelled = await execute_tool_once(
        cancel_workflow,
        {
            "workflow_task_id": delegated.metadata["workflow_task_id"],
            "reason": "not needed",
        },
        make_tool_context(fixture, generator_id),
        emit=_noop_emit,
    )
    submitted = await execute_tool_once(
        submit_generator_outcome,
        {"status": "failed", "outcome": "delegated work cancelled"},
        make_tool_context(
            fixture, generator_id, advisor_approves="submit_generator_outcome"
        ),
        emit=_noop_emit,
    )

    workflow = workflow_store.get(delegated.metadata["workflow_id"])
    assert blocked.is_error
    assert "background task(s) are still in flight" in blocked.output
    assert not cancelled.is_error
    assert workflow is not None
    assert workflow.status == WorkflowStatus.CANCELLED
    assert fixture.background_manager.count_by_agent("executor") == 0
    assert not submitted.is_error
    assert submitted.is_terminal
