"""Terminal routing tests for generator and evaluator submissions."""

from __future__ import annotations

import pytest

from task_center.complex_task.request import ComplexTaskRequestStatus
from task_center.harness_graph.graph import HarnessGraphStage, HarnessGraphStatus
from task_center.task import HarnessTaskStatus
from tools.core.tool_execution import execute_tool_once
from tools.submission.main_agent.evaluator import (
    submit_evaluation_failure,
    submit_evaluation_success,
)
from tools.submission.main_agent.generator.executor import (
    request_complex_task_solution,
    submit_execution_failure,
    submit_execution_success,
)
from tools.submission.main_agent.generator.verifier import (
    submit_verification_success,
)

from .submission_test_utils import (
    apply_single_generator_plan,
    build_harness_fixture,
    make_tool_context,
    spawn_evaluator,
)

pytestmark = pytest.mark.asyncio


async def _noop_emit(event) -> None:
    del event


async def test_submit_execution_success_calls_apply_generator_submission(
    request_store, segment_store, graph_store, task_store
) -> None:
    fixture = build_harness_fixture(
        request_store=request_store,
        segment_store=segment_store,
        graph_store=graph_store,
        task_store=task_store,
    )
    generator_id = apply_single_generator_plan(fixture)

    result = await execute_tool_once(
        submit_execution_success,
        {"summary": "done", "artifacts": ["artifact"]},
        make_tool_context(fixture, generator_id),
        emit=_noop_emit,
    )

    task = task_store.get_task(generator_id)
    assert not result.is_error
    assert result.does_terminate
    assert task is not None
    assert task["status"] == HarnessTaskStatus.DONE.value
    assert task["summaries"][-1]["payload"]["generator_role"] == "executor"


async def test_submit_execution_failure_calls_apply_generator_submission(
    request_store, segment_store, graph_store, task_store
) -> None:
    fixture = build_harness_fixture(
        request_store=request_store,
        segment_store=segment_store,
        graph_store=graph_store,
        task_store=task_store,
    )
    generator_id = apply_single_generator_plan(fixture)

    result = await execute_tool_once(
        submit_execution_failure,
        {"summary": "failed", "reason": "blocked", "details": ["detail"]},
        make_tool_context(fixture, generator_id),
        emit=_noop_emit,
    )

    task = task_store.get_task(generator_id)
    assert not result.is_error
    assert task is not None
    assert task["status"] == HarnessTaskStatus.FAILED.value
    assert task["summaries"][-1]["payload"]["reason"] == "blocked"


async def test_submit_verification_success_calls_apply_generator_submission(
    request_store, segment_store, graph_store, task_store
) -> None:
    fixture = build_harness_fixture(
        request_store=request_store,
        segment_store=segment_store,
        graph_store=graph_store,
        task_store=task_store,
    )
    generator_id = apply_single_generator_plan(fixture, agent_name="verifier")

    result = await execute_tool_once(
        submit_verification_success,
        {"summary": "verified", "checks": ["pytest"]},
        make_tool_context(fixture, generator_id),
        emit=_noop_emit,
    )

    task = task_store.get_task(generator_id)
    assert not result.is_error
    assert task is not None
    assert task["summaries"][-1]["payload"]["generator_role"] == "verifier"


async def test_submit_evaluation_success_calls_apply_evaluator_submission(
    request_store, segment_store, graph_store, task_store
) -> None:
    fixture = build_harness_fixture(
        request_store=request_store,
        segment_store=segment_store,
        graph_store=graph_store,
        task_store=task_store,
    )
    evaluator_id = spawn_evaluator(fixture)

    result = await execute_tool_once(
        submit_evaluation_success,
        {"summary": "passed", "passed_criteria": ["criterion"]},
        make_tool_context(fixture, evaluator_id),
        emit=_noop_emit,
    )

    graph = graph_store.get(fixture.graph_id)
    assert not result.is_error
    assert graph is not None
    assert graph.status == HarnessGraphStatus.PASSED


async def test_submit_evaluation_failure_calls_apply_evaluator_submission(
    request_store, segment_store, graph_store, task_store
) -> None:
    fixture = build_harness_fixture(
        request_store=request_store,
        segment_store=segment_store,
        graph_store=graph_store,
        task_store=task_store,
    )
    evaluator_id = spawn_evaluator(fixture)

    result = await execute_tool_once(
        submit_evaluation_failure,
        {"summary": "failed", "failed_criteria": ["criterion"]},
        make_tool_context(fixture, evaluator_id),
        emit=_noop_emit,
    )

    graph = graph_store.get(fixture.graph_id)
    assert not result.is_error
    assert graph is not None
    assert graph.status == HarnessGraphStatus.FAILED


async def test_request_complex_task_solution_starts_nested_request(
    request_store, segment_store, graph_store, task_store
) -> None:
    fixture = build_harness_fixture(
        request_store=request_store,
        segment_store=segment_store,
        graph_store=graph_store,
        task_store=task_store,
    )
    generator_id = apply_single_generator_plan(fixture)

    result = await execute_tool_once(
        request_complex_task_solution,
        {"goal": "solve nested task"},
        make_tool_context(fixture, generator_id),
        emit=_noop_emit,
    )

    task = task_store.get_task(generator_id)
    nested_request = request_store.get(result.metadata["complex_task_request_id"])
    nested_segment = segment_store.get(result.metadata["initial_segment_id"])
    created_harness_graph = graph_store.get(result.metadata["initial_harness_graph_id"])

    assert not result.is_error
    assert result.does_terminate
    assert task is not None
    assert task["status"] == HarnessTaskStatus.WAITING_COMPLEX_TASK.value
    assert nested_request is not None
    assert nested_request.status == ComplexTaskRequestStatus.OPEN
    assert nested_request.requested_by_task_id == generator_id
    assert nested_request.goal == "solve nested task"
    assert nested_segment is not None
    assert nested_segment.complex_task_request_id == nested_request.id
    assert created_harness_graph is not None
    assert created_harness_graph.task_segment_id == nested_segment.id
    assert created_harness_graph.stage == HarnessGraphStage.PLANNING
