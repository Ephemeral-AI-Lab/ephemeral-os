"""Integration through workflow_lifecycle -> coordinator -> orchestrator.

Drives a full planner -> generator -> reducer attempt synchronously (the
launcher is a no-op; submissions are applied directly to the orchestrator) and
asserts that a passing reducer closes the attempt, the iteration, and the root
workflow, routing the close through the run-close handler.
"""

from __future__ import annotations

from workflow._core.primitives import (
    WorkflowLifecycleConfig,
    generator_task_id,
    planner_task_id,
    reducer_task_id,
    root_task_id,
)
from workflow._core.state import (
    AttemptStatus,
    IterationStatus,
    Workflow,
    WorkflowStatus,
)
from workflow.attempt.launch import AgentLaunch, AttemptDeps
from workflow.attempt.orchestrator import AttemptOrchestrator
from workflow.attempt.orchestrator_registry import AttemptOrchestratorRegistry
from workflow.iteration import OpenIterationCoordinatorRegistry
from workflow.submissions import (
    GeneratorSubmission,
    PlannedGeneratorTask,
    PlannedReducerTask,
    PlannerSubmission,
    ReducerSubmission,
)
from workflow.lifecycle import WorkflowLifecycle


class _FakeLauncher:
    def __init__(self) -> None:
        self.launches: list[AgentLaunch] = []

    def launch(self, launch: AgentLaunch) -> None:
        self.launches.append(launch)


def _build(workflow_store, iteration_store, attempt_store, task_store, *, composer):
    launcher = _FakeLauncher()
    orchestrator_registry = AttemptOrchestratorRegistry()
    iteration_coordinators = OpenIterationCoordinatorRegistry()
    runtime = AttemptDeps(
        workflow_store=workflow_store,
        iteration_store=iteration_store,
        attempt_store=attempt_store,
        task_store=task_store,
        agent_launcher=launcher,
        orchestrator_registry=orchestrator_registry,
        iteration_coordinators=iteration_coordinators,
        composer=composer,
    )
    closed_workflows: list[Workflow] = []
    workflow_lifecycle = WorkflowLifecycle(
        workflow_store=workflow_store,
        iteration_store=iteration_store,
        attempt_store=attempt_store,
        iteration_coordinators=iteration_coordinators,
        config=WorkflowLifecycleConfig(default_attempt_budget=2),
        orchestrator_registry=orchestrator_registry,
        run_close_handler=lambda *, child_workflow: closed_workflows.append(child_workflow),
        orchestrator_factory=lambda attempt, on_attempt_closed: AttemptOrchestrator(
            attempt=attempt,
            on_attempt_closed=on_attempt_closed,
            runtime=runtime,
        ),
        task_store=task_store,
    )
    return workflow_lifecycle, iteration_coordinators, orchestrator_registry, closed_workflows


def _plan(attempt_id: str) -> PlannerSubmission:
    return PlannerSubmission(
        attempt_id=attempt_id,
        planner_task_id=planner_task_id(attempt_id),
        kind="completes",
        generators=(PlannedGeneratorTask("a", "executor", (), "do A"),),
        reducers=(PlannedReducerTask("r", ("a",), "gate the slice"),),
        deferred_goal_for_next_iteration=None,
    )


def _generator(attempt_id: str, status: str) -> GeneratorSubmission:
    return GeneratorSubmission(
        attempt_id=attempt_id,
        task_id=generator_task_id(attempt_id, "a"),
        status=status,
        outcome="gen",
        terminal_tool_result={},
    )


def _reducer(attempt_id: str, status: str) -> ReducerSubmission:
    return ReducerSubmission(
        attempt_id=attempt_id,
        task_id=reducer_task_id(attempt_id, "r"),
        status=status,
        outcome="reducer",
        terminal_tool_result={},
    )


def _create_root_workflow(workflow_lifecycle, task_store, run_id):
    # Seed the synthetic root bootstrap generator so the close routes through
    # the run-close handler.
    from task import AgentRole, TaskStatus

    task_store.upsert_task(
        task_id=root_task_id(run_id),
        task_center_run_id=run_id,
        role=AgentRole.GENERATOR.value,
        agent_name=None,
        context_message="",
        status=TaskStatus.RUNNING.value,
        outcomes=[],
        needs=[],
    )
    return workflow_lifecycle.create_workflow(
        task_center_run_id=run_id,
        parent_task_id=root_task_id(run_id),
        workflow_goal="g",
    )


def test_full_plan_execution_success_closes_workflow_succeeded(
    workflow_store,
    iteration_store,
    attempt_store,
    task_store,
    task_center_run_id,
    composer,
):
    workflow_lifecycle, iteration_coordinators, orchestrator_registry, closed = _build(
        workflow_store, iteration_store, attempt_store, task_store, composer=composer
    )
    workflow = _create_root_workflow(workflow_lifecycle, task_store, task_center_run_id)
    iteration, coordinator = workflow_lifecycle.create_iteration_with_coordinator(
        workflow_id=workflow.id
    )
    attempt = coordinator.create_attempt()
    orchestrator = orchestrator_registry.get_or_raise(attempt.id)

    orchestrator.apply_plan_submission(_plan(attempt.id))
    orchestrator.apply_generator_submission(_generator(attempt.id, "success"))
    orchestrator.apply_reducer_submission(_reducer(attempt.id, "success"))

    final_workflow = workflow_store.get(workflow.id)
    final_iteration = iteration_store.get(iteration.id)
    final_attempt = attempt_store.get(attempt.id)
    assert final_workflow is not None and final_iteration is not None
    assert final_attempt is not None
    assert final_workflow.status == WorkflowStatus.SUCCEEDED
    assert final_iteration.status == IterationStatus.SUCCEEDED
    assert final_attempt.status == AttemptStatus.PASSED
    assert iteration_coordinators.get(iteration.id) is None
    assert [w.id for w in closed] == [workflow.id]


def test_generator_failure_retry_then_reducer_success(
    workflow_store,
    iteration_store,
    attempt_store,
    task_store,
    task_center_run_id,
    composer,
):
    workflow_lifecycle, iteration_coordinators, orchestrator_registry, closed = _build(
        workflow_store, iteration_store, attempt_store, task_store, composer=composer
    )
    workflow = _create_root_workflow(workflow_lifecycle, task_store, task_center_run_id)
    iteration, coordinator = workflow_lifecycle.create_iteration_with_coordinator(
        workflow_id=workflow.id
    )
    attempt1 = coordinator.create_attempt()
    orchestrator1 = orchestrator_registry.get_or_raise(attempt1.id)

    orchestrator1.apply_plan_submission(_plan(attempt1.id))
    orchestrator1.apply_generator_submission(_generator(attempt1.id, "failure"))

    # A failed generator fails the reducer's gate -> attempt fails -> retry in
    # the same iteration (budget=2).
    refreshed_iteration = iteration_store.get(iteration.id)
    assert refreshed_iteration is not None
    assert len(refreshed_iteration.attempt_ids) == 2
    attempt2_id = refreshed_iteration.attempt_ids[1]
    orchestrator2 = orchestrator_registry.get_or_raise(attempt2_id)

    orchestrator2.apply_plan_submission(_plan(attempt2_id))
    orchestrator2.apply_generator_submission(_generator(attempt2_id, "success"))
    orchestrator2.apply_reducer_submission(_reducer(attempt2_id, "success"))

    final_workflow = workflow_store.get(workflow.id)
    final_iteration = iteration_store.get(iteration.id)
    final_attempt2 = attempt_store.get(attempt2_id)
    assert final_workflow is not None and final_iteration is not None
    assert final_attempt2 is not None
    assert final_workflow.status == WorkflowStatus.SUCCEEDED
    assert final_iteration.status == IterationStatus.SUCCEEDED
    assert final_attempt2.status == AttemptStatus.PASSED
    assert [w.id for w in closed] == [workflow.id]
