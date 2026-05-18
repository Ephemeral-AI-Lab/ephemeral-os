"""Shared helpers for Phase 03 submission tool tests."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from task_center.attempt.orchestrator import AttemptOrchestrator
from task_center.attempt.orchestrator_registry import (
    AttemptOrchestratorRegistry,
)
from task_center.attempt.runtime import AgentLaunch, AttemptDeps
from task_center.iteration import IterationManagerRegistry
from task_center.iteration.state import IterationCreationReason
from task_center.task_state import GeneratorSubmission, PlannedGeneratorTask, PlannerSubmission
from task_center._core.primitives import evaluator_task_id, generator_task_id, planner_task_id
from tools._framework.core.context import ToolExecutionContextService
from tools._framework.core.runtime import ExecutionMetadata


@dataclass
class TaskCenterFixture:
    runtime: AttemptDeps
    orchestrator: AttemptOrchestrator
    attempt_id: str
    request_id: str
    iteration_id: str


class FakeLauncher:
    def __init__(self) -> None:
        self.launches: list[AgentLaunch] = []

    def launch(self, launch: AgentLaunch) -> None:
        self.launches.append(launch)


def build_harness_fixture(
    *,
    goal_store: Any,
    iteration_store: Any,
    attempt_store: Any,
    task_store: Any,
    composer: Any,
) -> TaskCenterFixture:
    request = goal_store.insert(
        task_center_run_id="run1",
        requested_by_task_id="outer-task",
        goal="solve the task",
    )
    iteration = iteration_store.insert(
        goal_id=request.id,
        sequence_no=1,
        creation_reason=IterationCreationReason.INITIAL,
        goal="solve the task",
        attempt_budget=2,
    )
    goal_store.append_iteration_id(request.id, iteration.id)
    attempt = attempt_store.insert(iteration_id=iteration.id, attempt_sequence_no=1)
    iteration_store.append_attempt_id(iteration.id, attempt.id)

    launcher = FakeLauncher()
    registry = AttemptOrchestratorRegistry()
    runtime = AttemptDeps(
        goal_store=goal_store,
        iteration_store=iteration_store,
        attempt_store=attempt_store,
        task_store=task_store,
        agent_launcher=launcher,
        orchestrator_registry=registry,
        manager_registry=IterationManagerRegistry(),
        composer=composer,
    )
    orchestrator = AttemptOrchestrator(
        attempt=attempt,
        on_attempt_closed=lambda attempt_id: None,
        runtime=runtime,
    )
    registry.register(orchestrator)
    return TaskCenterFixture(
        runtime=runtime,
        orchestrator=orchestrator,
        attempt_id=attempt.id,
        request_id=request.id,
        iteration_id=iteration.id,
    )


def make_tool_context(
    fixture: TaskCenterFixture,
    task_id: str,
    *,
    messages: list[Any] | None = None,
    role: str | None = "executor",
    agent_type: str | None = None,
) -> ToolExecutionContextService:
    metadata = ExecutionMetadata(
        task_center_task_id=task_id,
        task_center_attempt_id=fixture.attempt_id,
        attempt_runtime=fixture.runtime,
        conversation_messages=list(messages or []),
    )
    if role is not None:
        metadata["role"] = role
    if agent_type is not None:
        metadata["agent_type"] = agent_type
    return ToolExecutionContextService(cwd=Path("/tmp"), services=metadata)


def start_planner(fixture: TaskCenterFixture) -> str:
    fixture.orchestrator.start()
    return planner_task_id(fixture.attempt_id)


def apply_single_generator_plan(fixture: TaskCenterFixture, *, agent_name: str = "executor") -> str:
    planner_id = start_planner(fixture)
    fixture.orchestrator.apply_plan_submission(
        PlannerSubmission(
            attempt_id=fixture.attempt_id,
            planner_task_id=planner_id,
            kind="full",
            plan_spec="spec",
            evaluation_criteria=("criterion",),
            tasks=(
                PlannedGeneratorTask(
                    local_id="a",
                    agent_name=agent_name,
                    deps=(),
                    task_spec="do A",
                ),
            ),
            next_iteration_handoff_goal=None,
            summary="plan",
        )
    )
    return generator_task_id(fixture.attempt_id, "a")


def spawn_evaluator(fixture: TaskCenterFixture) -> str:
    generator_id = apply_single_generator_plan(fixture)
    fixture.orchestrator.apply_generator_submission(
        GeneratorSubmission(
            attempt_id=fixture.attempt_id,
            task_id=generator_id,
            outcome="success",
            summary="done",
            payload={},
        )
    )
    return evaluator_task_id(fixture.attempt_id)
