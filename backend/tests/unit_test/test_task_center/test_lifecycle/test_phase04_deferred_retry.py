"""End-to-end continuation and retry through the child-workflow pipeline.

Drives starter -> workflow lifecycle -> iteration coordinator -> orchestrator so
that retry, deferred-goal continuation, and final child-workflow resolution are
exercised together. The spawning generator stays ``waiting_workflow`` until the
delegated workflow closes terminally, then resolves DONE (success) or FAILED.
"""

from __future__ import annotations

from task_center.workflow.starter import WorkflowStarter
from task_center._core.primitives import (
    generator_task_id,
    planner_task_id,
    reducer_task_id,
)
from task_center._core.state import (
    AttemptFailReason,
    AttemptStatus,
    IterationCreationReason,
    IterationStatus,
    WorkflowStatus,
)
from task_center.attempt.launch import AgentLaunch, AttemptDeps
from task_center.attempt.orchestrator import AttemptOrchestrator
from task_center.attempt.orchestrator_registry import (
    AttemptOrchestratorRegistry,
)
from task_center.iteration import OpenIterationCoordinatorRegistry
from task_center._core.task_state import TaskCenterTaskStatus
from task_center.submissions import (
    GeneratorSubmission,
    PlannedGeneratorTask,
    PlannedReducerTask,
    PlannerSubmission,
    ReducerSubmission,
)


class _FakeLauncher:
    def __init__(self) -> None:
        self.launches: list[AgentLaunch] = []

    def launch(self, launch: AgentLaunch) -> None:
        self.launches.append(launch)


class _FailOnLaunchNumber(_FakeLauncher):
    def __init__(self, fail_on: int) -> None:
        super().__init__()
        self._fail_on = fail_on

    def launch(self, launch: AgentLaunch) -> None:
        super().launch(launch)
        if len(self.launches) == self._fail_on:
            raise RuntimeError("planned launch failure")


def _build_runtime(
    workflow_store, iteration_store, attempt_store, task_store, *, composer, launcher=None
) -> AttemptDeps:
    return AttemptDeps(
        workflow_store=workflow_store,
        iteration_store=iteration_store,
        attempt_store=attempt_store,
        task_store=task_store,
        agent_launcher=launcher or _FakeLauncher(),
        orchestrator_registry=AttemptOrchestratorRegistry(),
        iteration_coordinators=OpenIterationCoordinatorRegistry(),
        composer=composer,
    )


def _seed_outer_running_generator(
    *, runtime: AttemptDeps, task_center_run_id: str
) -> tuple[str, str]:
    """Seed an outer parent attempt whose generator ``outer`` is RUNNING."""
    outer_workflow = runtime.workflow_store.insert(
        task_center_run_id=task_center_run_id,
        parent_task_id=f"{task_center_run_id}:root",
        workflow_goal="outer goal",
    )
    outer_iteration = runtime.iteration_store.insert(
        workflow_id=outer_workflow.id,
        sequence_no=1,
        creation_reason=IterationCreationReason.INITIAL,
        iteration_goal="outer goal",
        attempt_budget=2,
    )
    runtime.workflow_store.append_iteration_id(outer_workflow.id, outer_iteration.id)
    outer_attempt = runtime.attempt_store.insert(
        iteration_id=outer_iteration.id, attempt_sequence_no=1
    )
    runtime.iteration_store.append_attempt_id(outer_iteration.id, outer_attempt.id)
    orchestrator = AttemptOrchestrator(
        attempt=outer_attempt, on_attempt_closed=lambda _id: None, runtime=runtime
    )
    runtime.orchestrator_registry.register(orchestrator)
    orchestrator.start()
    orchestrator.apply_plan_submission(
        PlannerSubmission(
            attempt_id=outer_attempt.id,
            planner_task_id=planner_task_id(outer_attempt.id),
            kind="completes",
            tasks=(
                PlannedGeneratorTask(
                    local_id="outer", agent_name="executor", needs=(), task_spec="execute outer"
                ),
            ),
            reducers=(PlannedReducerTask(local_id="r", needs=("outer",), prompt="gate"),),
            deferred_goal_for_next_iteration=None,
            outcome="outer plan",
        )
    )
    return generator_task_id(outer_attempt.id, "outer"), outer_attempt.id


def _plan(attempt_id: str, *, deferred_goal: str | None) -> PlannerSubmission:
    return PlannerSubmission(
        attempt_id=attempt_id,
        planner_task_id=planner_task_id(attempt_id),
        kind="defers" if deferred_goal is not None else "completes",
        tasks=(PlannedGeneratorTask("d", "executor", (), "do delegated"),),
        reducers=(PlannedReducerTask("rr", ("d",), "gate delegated"),),
        deferred_goal_for_next_iteration=deferred_goal,
        outcome="delegated plan",
    )


def _drive_delegated_attempt_to_pass(
    *, runtime: AttemptDeps, delegated_attempt_id: str, deferred_goal: str | None
) -> None:
    delegated = runtime.orchestrator_registry.get_or_raise(delegated_attempt_id)
    delegated.apply_plan_submission(_plan(delegated_attempt_id, deferred_goal=deferred_goal))
    delegated.apply_generator_submission(
        GeneratorSubmission(
            attempt_id=delegated_attempt_id,
            task_id=generator_task_id(delegated_attempt_id, "d"),
            status="success",
            outcome="generator ok",
            terminal_tool_result={},
        )
    )
    delegated.apply_reducer_submission(
        ReducerSubmission(
            attempt_id=delegated_attempt_id,
            task_id=reducer_task_id(delegated_attempt_id, "rr"),
            status="success",
            outcome="reducer ok",
            terminal_tool_result={},
        )
    )


def _drive_delegated_attempt_to_fail(*, runtime: AttemptDeps, delegated_attempt_id: str) -> None:
    delegated = runtime.orchestrator_registry.get_or_raise(delegated_attempt_id)
    delegated.apply_plan_submission(_plan(delegated_attempt_id, deferred_goal=None))
    delegated.apply_generator_submission(
        GeneratorSubmission(
            attempt_id=delegated_attempt_id,
            task_id=generator_task_id(delegated_attempt_id, "d"),
            status="failure",
            outcome="generator failed",
            terminal_tool_result={},
        )
    )


def test_delegated_continuation_waits_until_final_iteration(
    workflow_store, iteration_store, attempt_store, task_store, task_center_run_id, composer
) -> None:
    runtime = _build_runtime(
        workflow_store, iteration_store, attempt_store, task_store, composer=composer
    )
    parent_task_id, parent_attempt_id = _seed_outer_running_generator(
        runtime=runtime, task_center_run_id=task_center_run_id
    )
    started = WorkflowStarter(runtime=runtime).start(
        prompt="delegated continuation", parent_task_id=parent_task_id
    )

    # Iteration 1 passes with a continuation goal — parent must remain WAITING.
    _drive_delegated_attempt_to_pass(
        runtime=runtime,
        delegated_attempt_id=started.attempt_id,
        deferred_goal="continue work",
    )
    parent_after_1 = task_store.get_task(parent_task_id)
    assert parent_after_1 is not None
    assert parent_after_1["status"] == TaskCenterTaskStatus.WAITING_WORKFLOW.value
    delegated_after_1 = workflow_store.get(started.workflow_id)
    assert delegated_after_1 is not None
    assert delegated_after_1.status == WorkflowStatus.OPEN
    assert len(delegated_after_1.iteration_ids) == 2

    # Iteration 2 (continuation) closes terminally.
    iteration2_id = delegated_after_1.iteration_ids[1]
    iteration2 = iteration_store.get(iteration2_id)
    assert iteration2 is not None
    assert iteration2.iteration_goal == "continue work"
    iteration2_attempt_id = iteration2.attempt_ids[0]
    _drive_delegated_attempt_to_pass(
        runtime=runtime, delegated_attempt_id=iteration2_attempt_id, deferred_goal=None
    )

    parent_final = task_store.get_task(parent_task_id)
    delegated_final = workflow_store.get(started.workflow_id)
    iteration2_final = iteration_store.get(iteration2_id)
    assert parent_final is not None
    assert parent_final["status"] == TaskCenterTaskStatus.DONE.value
    assert delegated_final is not None
    assert delegated_final.status == WorkflowStatus.SUCCEEDED
    assert iteration2_final is not None
    assert iteration2_final.status == IterationStatus.SUCCEEDED


def test_continuation_startup_failure_reports_continuation_attempt(
    workflow_store, iteration_store, attempt_store, task_store, task_center_run_id, composer
) -> None:
    # Outer attempt: 2 launches; child iter1 planner/gen/reducer: 3 launches;
    # the continuation planner is launch 6 — make it fail.
    launcher = _FailOnLaunchNumber(fail_on=6)
    runtime = _build_runtime(
        workflow_store, iteration_store, attempt_store, task_store, composer=composer, launcher=launcher
    )
    parent_task_id, parent_attempt_id = _seed_outer_running_generator(
        runtime=runtime, task_center_run_id=task_center_run_id
    )
    started = WorkflowStarter(runtime=runtime).start(
        prompt="delegated continuation", parent_task_id=parent_task_id
    )

    _drive_delegated_attempt_to_pass(
        runtime=runtime,
        delegated_attempt_id=started.attempt_id,
        deferred_goal="continue work",
    )

    delegated = workflow_store.get(started.workflow_id)
    assert delegated is not None
    assert delegated.status == WorkflowStatus.FAILED
    iteration2_id = delegated.iteration_ids[1]
    iteration2 = iteration_store.get(iteration2_id)
    assert iteration2 is not None
    failed_attempt_id = iteration2.attempt_ids[0]
    failed_attempt = attempt_store.get(failed_attempt_id)
    assert failed_attempt is not None
    assert failed_attempt.status == AttemptStatus.FAILED
    assert failed_attempt.fail_reason == AttemptFailReason.STARTUP_FAILED

    parent_final = task_store.get_task(parent_task_id)
    assert parent_final is not None
    assert parent_final["status"] == TaskCenterTaskStatus.FAILED.value


def test_delegated_retry_waits_until_final_attempt(
    workflow_store, iteration_store, attempt_store, task_store, task_center_run_id, composer
) -> None:
    runtime = _build_runtime(
        workflow_store, iteration_store, attempt_store, task_store, composer=composer
    )
    parent_task_id, parent_attempt_id = _seed_outer_running_generator(
        runtime=runtime, task_center_run_id=task_center_run_id
    )
    started = WorkflowStarter(runtime=runtime).start(
        prompt="delegated retry", parent_task_id=parent_task_id
    )

    # Attempt 1 fails — coordinator retries inside the same iteration, parent waits.
    _drive_delegated_attempt_to_fail(
        runtime=runtime, delegated_attempt_id=started.attempt_id
    )
    iteration1 = iteration_store.get(started.iteration_id)
    assert iteration1 is not None
    assert len(iteration1.attempt_ids) == 2
    parent_mid = task_store.get_task(parent_task_id)
    assert parent_mid is not None
    assert parent_mid["status"] == TaskCenterTaskStatus.WAITING_WORKFLOW.value
    delegated_mid = workflow_store.get(started.workflow_id)
    assert delegated_mid is not None
    assert delegated_mid.status == WorkflowStatus.OPEN

    # Attempt 2 passes terminally inside the same iteration — final close.
    retry_attempt_id = iteration1.attempt_ids[1]
    _drive_delegated_attempt_to_pass(
        runtime=runtime, delegated_attempt_id=retry_attempt_id, deferred_goal=None
    )

    parent_final = task_store.get_task(parent_task_id)
    delegated_final = workflow_store.get(started.workflow_id)
    iteration1_final = iteration_store.get(started.iteration_id)
    assert parent_final is not None
    assert parent_final["status"] == TaskCenterTaskStatus.DONE.value
    assert delegated_final is not None
    assert delegated_final.status == WorkflowStatus.SUCCEEDED
    assert iteration1_final is not None
    assert iteration1_final.status == IterationStatus.SUCCEEDED
