"""Terminal routing tests for generator and evaluator submissions."""

from __future__ import annotations

import pytest

from task_center.goal.state import GoalStatus
from task_center.trial import TrialStage, TrialStatus
from task_center.task_state import (
    EvaluatorSubmission,
    GeneratorSubmission,
    PlannedGeneratorTask,
    PlannerSubmission,
    SpawnReason,
    TaskCenterTaskRole,
    TaskCenterTaskStatus,
)
from task_center._core.types import evaluator_task_id, generator_task_id, planner_task_id
from tools._framework.execution.tool_call import execute_tool_once
from tools.submission.evaluator import (
    submit_evaluation_failure,
    submit_evaluation_success,
)
from tools.submission.executor import submit_execution_handoff
from tools.submission.executor import (
    submit_execution_failure,
    submit_execution_success,
)
from tools.submission.verifier import (
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
    mission_store, episode_store, attempt_store, task_store, composer
) -> None:
    fixture = build_harness_fixture(
        goal_store=mission_store,
        iteration_store=episode_store,
        trial_store=attempt_store,
        task_store=task_store,
        composer=composer,
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
    assert task["status"] == TaskCenterTaskStatus.DONE.value
    assert task["summaries"][-1]["payload"]["generator_role"] == "executor"


async def test_submit_execution_failure_calls_apply_generator_submission(
    mission_store, episode_store, attempt_store, task_store, composer
) -> None:
    fixture = build_harness_fixture(
        goal_store=mission_store,
        iteration_store=episode_store,
        trial_store=attempt_store,
        task_store=task_store,
        composer=composer,
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
    assert task["status"] == TaskCenterTaskStatus.FAILED.value
    assert task["summaries"][-1]["payload"]["reason"] == "blocked"


async def test_submit_verification_success_calls_apply_generator_submission(
    mission_store, episode_store, attempt_store, task_store, composer
) -> None:
    fixture = build_harness_fixture(
        goal_store=mission_store,
        iteration_store=episode_store,
        trial_store=attempt_store,
        task_store=task_store,
        composer=composer,
    )
    generator_id = apply_single_generator_plan(fixture, agent_name="verifier")

    result = await execute_tool_once(
        submit_verification_success,
        {"summary": "verified", "checks": ["pytest"]},
        make_tool_context(fixture, generator_id, role="verifier"),
        emit=_noop_emit,
    )

    task = task_store.get_task(generator_id)
    assert not result.is_error
    assert task is not None
    assert task["summaries"][-1]["payload"]["generator_role"] == "verifier"


async def test_submit_evaluation_success_calls_apply_evaluator_submission(
    mission_store, episode_store, attempt_store, task_store, composer
) -> None:
    fixture = build_harness_fixture(
        goal_store=mission_store,
        iteration_store=episode_store,
        trial_store=attempt_store,
        task_store=task_store,
        composer=composer,
    )
    evaluator_id = spawn_evaluator(fixture)

    result = await execute_tool_once(
        submit_evaluation_success,
        {"summary": "passed", "passed_criteria": ["criterion"]},
        make_tool_context(fixture, evaluator_id),
        emit=_noop_emit,
    )

    attempt = attempt_store.get(fixture.trial_id)
    assert not result.is_error
    assert attempt is not None
    assert attempt.status == TrialStatus.PASSED


async def test_submit_evaluation_failure_calls_apply_evaluator_submission(
    mission_store, episode_store, attempt_store, task_store, composer
) -> None:
    fixture = build_harness_fixture(
        goal_store=mission_store,
        iteration_store=episode_store,
        trial_store=attempt_store,
        task_store=task_store,
        composer=composer,
    )
    evaluator_id = spawn_evaluator(fixture)

    result = await execute_tool_once(
        submit_evaluation_failure,
        {"summary": "failed", "failed_criteria": ["criterion"]},
        make_tool_context(fixture, evaluator_id),
        emit=_noop_emit,
    )

    attempt = attempt_store.get(fixture.trial_id)
    assert not result.is_error
    assert attempt is not None
    assert attempt.status == TrialStatus.FAILED


async def test_submit_execution_handoff_starts_delegated_request(
    mission_store, episode_store, attempt_store, task_store, composer
) -> None:
    fixture = build_harness_fixture(
        goal_store=mission_store,
        iteration_store=episode_store,
        trial_store=attempt_store,
        task_store=task_store,
        composer=composer,
    )
    generator_id = apply_single_generator_plan(fixture)

    result = await execute_tool_once(
        submit_execution_handoff,
        {"goal": "solve delegated task"},
        make_tool_context(fixture, generator_id),
        emit=_noop_emit,
    )

    task = task_store.get_task(generator_id)
    delegated_request = mission_store.get(result.metadata["mission_id"])
    initial_episode = episode_store.get(result.metadata["initial_episode_id"])
    created_attempt = attempt_store.get(result.metadata["initial_attempt_id"])

    assert not result.is_error
    assert result.does_terminate
    assert task is not None
    assert task["status"] == TaskCenterTaskStatus.WAITING_MISSION.value
    assert delegated_request is not None
    assert delegated_request.status == GoalStatus.OPEN
    assert delegated_request.requested_by_task_id == generator_id
    assert delegated_request.goal == "solve delegated task"
    assert initial_episode is not None
    assert initial_episode.goal_id == delegated_request.id
    assert created_attempt is not None
    assert created_attempt.iteration_id == initial_episode.id
    assert created_attempt.stage == TrialStage.PLAN


async def test_submit_execution_handoff_entry_mode_uses_bound_controller(
    mission_store, episode_store, attempt_store, task_store, composer
) -> None:
    from pathlib import Path

    from task_center.trial.orchestrator_registry import (
        TrialOrchestratorRegistry,
    )
    from task_center.trial.runtime import TrialDeps
    from task_center.entry import EntryTaskController
    from task_center.iteration import IterationManagerRegistry
    from tools._framework.core.context import ToolExecutionContextService
    from tools._framework.core.runtime import ExecutionMetadata

    from .submission_test_utils import FakeLauncher

    entry_task_id = "run1:entry"
    task_store.upsert_task(
        task_id=entry_task_id,
        task_center_run_id="run1",
        role=TaskCenterTaskRole.ENTRY_EXECUTOR.value,
        agent_name="entry_executor",
        rendered_prompt="top-level goal",
        status=TaskCenterTaskStatus.RUNNING.value,
        summaries=[],
        needs=[],
        task_center_attempt_id=None,
        spawn_reason=SpawnReason.ENTRY_EXECUTOR.value,
    )
    controller = EntryTaskController(
        task_id=entry_task_id,
        task_center_run_id="run1",
        task_store=task_store,
    )
    runtime = TrialDeps(
        goal_store=mission_store,
        iteration_store=episode_store,
        trial_store=attempt_store,
        task_store=task_store,
        agent_launcher=FakeLauncher(),
        orchestrator_registry=TrialOrchestratorRegistry(),
        manager_registry=IterationManagerRegistry(),
        composer=composer,
        entry_task_controller=controller,
    )
    context = ToolExecutionContextService(
        cwd=Path("/tmp"),
        services=ExecutionMetadata(
            task_center_task_id=entry_task_id,
            task_center_attempt_id=None,
            attempt_runtime=runtime,
            conversation_messages=[],
        ),
    )

    result = await execute_tool_once(
        submit_execution_handoff,
        {"goal": "solve first delegated goal"},
        context,
        emit=_noop_emit,
    )

    entry_task = task_store.get_task(entry_task_id)
    delegated_mission = mission_store.get(result.metadata["mission_id"])

    assert not result.is_error
    assert result.does_terminate
    assert result.metadata["attempt_id"] is None
    assert entry_task is not None
    assert entry_task["status"] == TaskCenterTaskStatus.WAITING_MISSION.value
    assert delegated_mission is not None
    assert delegated_mission.requested_by_task_id == entry_task_id


async def test_submit_execution_handoff_accepts_any_generator_agent_profile(
    mission_store, episode_store, attempt_store, task_store, composer
) -> None:
    from agents import AgentDefinition, AgentKind, register_definition

    register_definition(
        AgentDefinition(
            name="custom_generator",
            description="custom generator for this test",
            agent_kind=AgentKind.EXECUTOR,
            dispatchable_by_planner=True,
            context_recipe="generator",
            terminals=[
                "submit_execution_handoff",
                "submit_execution_success",
                "submit_execution_failure",
            ],
        )
    )

    fixture = build_harness_fixture(
        goal_store=mission_store,
        iteration_store=episode_store,
        trial_store=attempt_store,
        task_store=task_store,
        composer=composer,
    )
    generator_id = apply_single_generator_plan(
        fixture,
        agent_name="custom_generator",
    )

    result = await execute_tool_once(
        submit_execution_handoff,
        {"goal": "delegate broad custom generator work"},
        make_tool_context(fixture, generator_id),
        emit=_noop_emit,
    )

    task = task_store.get_task(generator_id)
    assert not result.is_error
    assert result.does_terminate
    assert task is not None
    assert task["status"] == TaskCenterTaskStatus.WAITING_MISSION.value


async def test_submit_execution_handoff_return_updates_outer_generator(
    mission_store, episode_store, attempt_store, task_store, composer
) -> None:
    fixture = build_harness_fixture(
        goal_store=mission_store,
        iteration_store=episode_store,
        trial_store=attempt_store,
        task_store=task_store,
        composer=composer,
    )
    outer_generator_id = apply_single_generator_plan(fixture)

    result = await execute_tool_once(
        submit_execution_handoff,
        {"goal": "solve delegated task"},
        make_tool_context(fixture, outer_generator_id),
        emit=_noop_emit,
    )
    delegated_attempt_id = result.metadata["initial_attempt_id"]
    delegated_orchestrator = fixture.runtime.orchestrator_registry.get_or_raise(
        delegated_attempt_id
    )
    delegated_planner_id = planner_task_id(delegated_attempt_id)
    delegated_generator_id = generator_task_id(delegated_attempt_id, "delegated")
    delegated_evaluator_id = evaluator_task_id(delegated_attempt_id)

    delegated_orchestrator.apply_plan_submission(
        PlannerSubmission(
            trial_id=delegated_attempt_id,
            planner_task_id=delegated_planner_id,
            kind="full",
            task_specification="Solve delegated task.",
            evaluation_criteria=("delegated task passed",),
            tasks=(
                PlannedGeneratorTask(
                    local_id="delegated",
                    agent_name="executor",
                    deps=(),
                    task_spec="Do delegated work.",
                ),
            ),
            continuation_goal=None,
            summary="Accepted delegated plan.",
        )
    )
    delegated_orchestrator.apply_generator_submission(
        GeneratorSubmission(
            trial_id=delegated_attempt_id,
            task_id=delegated_generator_id,
            outcome="success",
            summary="Delegated work done.",
            payload={},
        )
    )
    delegated_orchestrator.apply_evaluator_submission(
        EvaluatorSubmission(
            trial_id=delegated_attempt_id,
            task_id=delegated_evaluator_id,
            outcome="success",
            summary="Delegated task passed.",
            payload={},
        )
    )

    outer_task = task_store.get_task(outer_generator_id)
    outer_attempt = attempt_store.get(fixture.trial_id)
    delegated_request = mission_store.get(result.metadata["mission_id"])

    assert outer_task is not None
    assert outer_task["status"] == TaskCenterTaskStatus.DONE.value
    assert outer_task["summaries"][-1]["payload"]["mission_closure_report"][
        "final_attempt_id"
    ] == delegated_attempt_id
    assert outer_attempt is not None
    assert outer_attempt.stage == TrialStage.EVALUATE
    assert delegated_request is not None
    assert delegated_request.status == GoalStatus.SUCCEEDED
