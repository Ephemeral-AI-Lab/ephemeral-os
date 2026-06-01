"""Shared helpers for Phase 03 submission tool tests."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from workflow.attempt.orchestrator import AttemptOrchestrator
from workflow.attempt.orchestrator_registry import (
    AttemptOrchestratorRegistry,
)
from workflow.attempt.launch import AgentLaunch, AttemptDeps
from workflow.iteration import OpenIterationCoordinatorRegistry
from workflow._core.state import IterationCreationReason
from workflow.submissions import (
    GeneratorSubmission,
    PlannerSubmission,
)
from workflow._core.primitives import generator_task_id, planner_task_id, reducer_task_id
from engine.background.task_supervisor import BackgroundTaskSupervisor
from task import AgentRole, TaskStatus
from tools._framework.core.context import ToolExecutionContextService
from tools._framework.core.runtime import ExecutionMetadata

from .test_submission._advisor_approval_fixtures import (
    build_advisor_approval_messages,
)


@dataclass
class TaskCenterFixture:
    runtime: AttemptDeps
    orchestrator: AttemptOrchestrator
    attempt_id: str
    request_id: str
    workflow_id: str
    iteration_id: str
    background_manager: BackgroundTaskSupervisor


class FakeLauncher:
    def __init__(self) -> None:
        self.launches: list[AgentLaunch] = []

    def launch(self, launch: AgentLaunch) -> None:
        self.launches.append(launch)


def build_harness_fixture(
    *,
    workflow_store: Any,
    iteration_store: Any,
    attempt_store: Any,
    task_store: Any,
    composer: Any,
) -> TaskCenterFixture:
    workflow = workflow_store.insert(
        request_id="run1",
        parent_task_id="outer-task",
        workflow_goal="solve the task",
    )
    iteration = iteration_store.insert(
        workflow_id=workflow.id,
        sequence_no=1,
        creation_reason=IterationCreationReason.INITIAL,
        iteration_goal="solve the task",
        attempt_budget=2,
    )
    workflow_store.append_iteration_id(workflow.id, iteration.id)
    attempt = attempt_store.insert(
        iteration_id=iteration.id,
        workflow_id=workflow.id,
        attempt_sequence_no=1,
    )
    iteration_store.append_attempt_id(iteration.id, attempt.id)

    launcher = FakeLauncher()
    registry = AttemptOrchestratorRegistry()
    background_manager = BackgroundTaskSupervisor()
    runtime = AttemptDeps(
        workflow_store=workflow_store,
        iteration_store=iteration_store,
        attempt_store=attempt_store,
        task_store=task_store,
        agent_launcher=launcher,
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
    return TaskCenterFixture(
        runtime=runtime,
        orchestrator=orchestrator,
        attempt_id=attempt.id,
        request_id=workflow.request_id,
        workflow_id=workflow.id,
        iteration_id=iteration.id,
        background_manager=background_manager,
    )


def make_tool_context(
    fixture: TaskCenterFixture,
    task_id: str,
    *,
    messages: list[Any] | None = None,
    role: str | None = "executor",
    agent_type: str | None = None,
    advisor_approves: str | None = None,
) -> ToolExecutionContextService:
    """Build a tool execution context for a submission-tool test.

    ``advisor_approves`` accepts a terminal-tool name and prepends a synthetic
    ``ask_advisor`` approval pair to ``conversation_messages`` so the
    ``AdvisorApprovalPreHook`` lets the call through. Tests that explicitly want
    to exercise the unapproved path leave this kwarg unset.
    """
    base_messages: list[Any] = []
    if advisor_approves is not None:
        base_messages.extend(
            build_advisor_approval_messages(tool_name=advisor_approves)
        )
    if messages:
        base_messages.extend(messages)
    metadata = ExecutionMetadata(
        task_id=task_id,
        attempt_id=fixture.attempt_id,
        attempt_runtime=fixture.runtime,
        conversation_messages=base_messages,
    )
    if role is not None:
        metadata["role"] = role
    if agent_type is not None:
        metadata["agent_type"] = agent_type
    metadata.agent_name = role or ""
    metadata.workflow_id = fixture.workflow_id
    metadata.request_id = fixture.request_id
    metadata.background_task_manager = fixture.background_manager
    return ToolExecutionContextService(cwd=Path("/tmp"), services=metadata)


def start_planner(fixture: TaskCenterFixture) -> str:
    fixture.orchestrator.start()
    return planner_task_id(fixture.attempt_id)


def ensure_planner_started(fixture: TaskCenterFixture) -> str:
    attempt = fixture.runtime.attempt_store.get(fixture.attempt_id)
    if attempt is not None and attempt.planner_task_id is not None:
        return attempt.planner_task_id
    return start_planner(fixture)


def _persist_plan_task(
    fixture: TaskCenterFixture,
    *,
    task_id: str,
    role: AgentRole,
    agent_name: str,
    instruction: str,
    needs: list[str],
) -> None:
    fixture.runtime.task_store.upsert_task(
        task_id=task_id,
        request_id=fixture.request_id,
        role=role.value,
        agent_name=agent_name,
        instruction=instruction,
        status=TaskStatus.PENDING.value,
        outcomes=[],
        needs=needs,
        workflow_id=fixture.workflow_id,
        iteration_id=fixture.iteration_id,
        attempt_id=fixture.attempt_id,
    )


def apply_single_generator_plan(
    fixture: TaskCenterFixture,
    *,
    local_id: str = "a",
    reducer_id: str = "r",
    agent_name: str = "executor",
) -> str:
    planner_id = ensure_planner_started(fixture)
    generator_id = generator_task_id(fixture.attempt_id, local_id)
    reducer_task = reducer_task_id(fixture.attempt_id, reducer_id)
    _persist_plan_task(
        fixture,
        task_id=generator_id,
        role=AgentRole.GENERATOR,
        agent_name=agent_name,
        instruction="do A",
        needs=[],
    )
    _persist_plan_task(
        fixture,
        task_id=reducer_task,
        role=AgentRole.REDUCER,
        agent_name="reducer",
        instruction="reduce the result",
        needs=[generator_id],
    )
    fixture.orchestrator.apply_plan_submission(
        PlannerSubmission(
            attempt_id=fixture.attempt_id,
            planner_task_id=planner_id,
            kind="completes",
            generator_task_ids=(generator_id,),
            reducer_task_ids=(reducer_task,),
            deferred_goal_for_next_iteration=None,
        )
    )
    return generator_id


def spawn_reducer(fixture: TaskCenterFixture) -> str:
    """Drive the attempt to the point where its single reducer is RUNNING.

    Submits the generator success so the stage advancer launches the reducer
    (the exit gate). Returns the reducer task id, now running.
    """
    generator_id = apply_single_generator_plan(fixture)
    fixture.orchestrator.apply_generator_submission(
        GeneratorSubmission(
            attempt_id=fixture.attempt_id,
            task_id=generator_id,
            status="success",
            outcome="done",
            terminal_tool_result={},
        )
    )
    return reducer_task_id(fixture.attempt_id, "r")
