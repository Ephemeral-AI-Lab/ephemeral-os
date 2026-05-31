"""Role-scoped AgentContext XML and guidance tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from task_center._core.outcomes import ExecutionTaskOutcome, records_json, to_record
from task_center._core.primitives import generator_task_id, reducer_task_id
from task_center._core.state import (
    AttemptFailReason,
    AttemptStatus,
    IterationCreationReason,
)
from task_center.context_engine.engine import (
    ContextEngine,
    ContextEngineDeps,
    RecipeScopeError,
)
from task_center.context_engine.scope import ContextScope
from task_center.context_engine.task_guidance import render_task_guidance
from task_center.context_engine.xml import render_context_xml


@pytest.fixture
def deps(workflow_store, iteration_store, attempt_store, task_store) -> ContextEngineDeps:
    return ContextEngineDeps(
        workflow_store=workflow_store,
        iteration_store=iteration_store,
        attempt_store=attempt_store,
        task_store=task_store,
    )


def _workflow(workflow_store, task_center_run_id):
    return workflow_store.insert(
        task_center_run_id=task_center_run_id,
        parent_task_id="root",
        workflow_goal="Build the complete feature.",
    )


def test_planner_context_uses_workflow_shape_and_execution_outcomes(
    deps, workflow_store, iteration_store, attempt_store, task_store, task_center_run_id
) -> None:
    workflow = _workflow(workflow_store, task_center_run_id)
    prior = iteration_store.insert(
        workflow_id=workflow.id,
        sequence_no=1,
        creation_reason=IterationCreationReason.INITIAL,
        iteration_goal="Build storage.",
        attempt_budget=2,
    )
    workflow_store.append_iteration_id(workflow.id, prior.id)
    prior_outcome = ExecutionTaskOutcome(
        status="success",
        role="reducer",
        task_id="attempt1:red:verify_storage",
        outcome="Storage layer is implemented and verified.",
    )
    iteration_store.close_succeeded(
        prior.id,
        outcomes=records_json((prior_outcome,)),
        closed_at=datetime.now(UTC),
    )

    current = iteration_store.insert(
        workflow_id=workflow.id,
        sequence_no=2,
        creation_reason=IterationCreationReason.DEFERRED_GOAL_CONTINUATION,
        iteration_goal="Finish the API and CLI slice.",
        attempt_budget=3,
    )
    workflow_store.append_iteration_id(workflow.id, current.id)
    previous_attempt = attempt_store.insert(
        iteration_id=current.id, attempt_sequence_no=1
    )
    iteration_store.append_attempt_id(current.id, previous_attempt.id)
    gen_id = generator_task_id(previous_attempt.id, "api")
    red_id = reducer_task_id(previous_attempt.id, "verify_api")
    attempt_store.set_generator_task_ids(previous_attempt.id, [gen_id])
    attempt_store.set_reducer_task_ids(previous_attempt.id, [red_id])
    task_store.upsert_task(
        task_id=gen_id,
        task_center_run_id=task_center_run_id,
        role="generator",
        agent_name="executor",
        context_message="Implement API.",
        status="done",
        outcomes=[
            to_record(
                ExecutionTaskOutcome("success", "generator", gen_id, "API endpoints were implemented.")
            )
        ],
        needs=[],
    )
    task_store.upsert_task(
        task_id=red_id,
        task_center_run_id=task_center_run_id,
        role="reducer",
        agent_name="reducer",
        context_message="Verify API.",
        status="failed",
        outcomes=[
            to_record(
                ExecutionTaskOutcome(
                    "failed",
                    "reducer",
                    red_id,
                    "Verification failed because the CLI command still calls the old endpoint.",
                )
            )
        ],
        needs=[gen_id],
    )
    attempt_store.close(
        previous_attempt.id,
        status=AttemptStatus.FAILED,
        fail_reason=AttemptFailReason.TASK_FAILED,
        outcomes=[
            to_record(ExecutionTaskOutcome("success", "generator", gen_id, "API endpoints were implemented.")),
            to_record(
                ExecutionTaskOutcome(
                    "failed",
                    "reducer",
                    red_id,
                    "Verification failed because the CLI command still calls the old endpoint.",
                )
            ),
        ],
    )
    current_attempt = attempt_store.insert(iteration_id=current.id, attempt_sequence_no=2)
    iteration_store.append_attempt_id(current.id, current_attempt.id)

    context = ContextEngine(deps).build(
        "planner",
        ContextScope.for_planner(
            workflow_id=workflow.id,
            iteration_id=current.id,
            attempt_id=current_attempt.id,
        ),
    )
    xml = render_context_xml(context)

    assert '<context role="planner">' in xml
    assert "<workflow>" in xml
    assert "<prior_iterations>" in xml
    assert f'<iteration sequence="{prior.sequence_no}">' in xml
    assert (
        '<task task_id="attempt1:red:verify_storage" role="reducer" status="success">'
        in xml
    )
    assert f'<current_iteration sequence="{current.sequence_no}">' in xml
    assert '<attempt sequence="1" status="failed">' in xml
    assert f'<task task_id="{gen_id}" role="generator" status="success">' in xml
    assert f'<task task_id="{red_id}" role="reducer" status="failed">' in xml
    assert "<outcomes>" not in xml
    assert "planner" not in xml.split("<prior_iterations>", 1)[1]

    guidance = render_task_guidance(context)
    assert "<workflow>: workflow goal and current planning frame" in guidance
    assert "Planner outcomes are omitted" in guidance


def test_generator_context_is_dependencies_plus_assigned_task(
    deps, workflow_store, iteration_store, attempt_store, task_store, task_center_run_id
) -> None:
    workflow = _workflow(workflow_store, task_center_run_id)
    iteration = iteration_store.insert(
        workflow_id=workflow.id,
        sequence_no=1,
        creation_reason=IterationCreationReason.INITIAL,
        iteration_goal=workflow.workflow_goal,
        attempt_budget=2,
    )
    attempt = attempt_store.insert(iteration_id=iteration.id, attempt_sequence_no=1)
    dep_id = generator_task_id(attempt.id, "storage")
    task_id = generator_task_id(attempt.id, "api")
    task_store.upsert_task(
        task_id=dep_id,
        task_center_run_id=task_center_run_id,
        role="generator",
        agent_name="executor",
        context_message="Build storage.",
        status="done",
        outcomes=[to_record(ExecutionTaskOutcome("success", "generator", dep_id, "Storage done."))],
        needs=[],
    )
    task_store.upsert_task(
        task_id=task_id,
        task_center_run_id=task_center_run_id,
        role="generator",
        agent_name="executor",
        context_message="Implement the API endpoints.",
        status="pending",
        outcomes=[],
        needs=[dep_id],
    )

    context = ContextEngine(deps).build(
        "generator",
        ContextScope.for_generator(
            workflow_id=workflow.id,
            iteration_id=iteration.id,
            attempt_id=attempt.id,
            task_id=task_id,
        ),
    )
    xml = render_context_xml(context)

    assert '<context role="generator">' in xml
    assert "<dependencies>" in xml
    assert f'<dependency task_id="{dep_id}">' in xml
    assert f'<assigned_task task_id="{task_id}">' in xml
    assert "Implement the API endpoints." in xml
    assert "<workflow>" not in xml
    assert "<needs>" not in xml
    assert "<assigned_prompt>" not in xml
    assert "Complete <assigned_task> using <dependencies>." in render_task_guidance(context)


def test_dependency_context_preserves_all_execution_outcomes(
    deps, workflow_store, iteration_store, attempt_store, task_store, task_center_run_id
) -> None:
    workflow = _workflow(workflow_store, task_center_run_id)
    iteration = iteration_store.insert(
        workflow_id=workflow.id,
        sequence_no=1,
        creation_reason=IterationCreationReason.INITIAL,
        iteration_goal=workflow.workflow_goal,
        attempt_budget=2,
    )
    attempt = attempt_store.insert(iteration_id=iteration.id, attempt_sequence_no=1)
    dep_id = generator_task_id(attempt.id, "handoff")
    task_id = generator_task_id(attempt.id, "consumer")
    first_child = generator_task_id("child-attempt-1", "api")
    second_child = reducer_task_id("child-attempt-2", "verify_api")
    task_store.upsert_task(
        task_id=dep_id,
        task_center_run_id=task_center_run_id,
        role="generator",
        agent_name="executor",
        context_message="Run child workflow.",
        status="done",
        outcomes=[
            to_record(
                ExecutionTaskOutcome(
                    "success",
                    "generator",
                    first_child,
                    "Child API implementation completed.",
                )
            ),
            to_record(
                ExecutionTaskOutcome(
                    "success",
                    "reducer",
                    second_child,
                    "Child API verification passed.",
                )
            ),
        ],
        needs=[],
    )
    task_store.upsert_task(
        task_id=task_id,
        task_center_run_id=task_center_run_id,
        role="generator",
        agent_name="executor",
        context_message="Consume child workflow results.",
        status="pending",
        outcomes=[],
        needs=[dep_id],
    )

    context = ContextEngine(deps).build(
        "generator",
        ContextScope.for_generator(
            workflow_id=workflow.id,
            iteration_id=iteration.id,
            attempt_id=attempt.id,
            task_id=task_id,
        ),
    )
    xml = render_context_xml(context)

    assert f'<dependency task_id="{dep_id}">' in xml
    assert f'<task task_id="{first_child}" role="generator" status="success">' in xml
    assert f'<task task_id="{second_child}" role="reducer" status="success">' in xml
    assert "Child API implementation completed." in xml
    assert "Child API verification passed." in xml


def test_context_recipe_must_match_scope_role(deps) -> None:
    with pytest.raises(RecipeScopeError, match="cannot build role"):
        ContextEngine(deps).build(
            "planner",
            ContextScope.for_generator(
                workflow_id="workflow",
                iteration_id="iteration",
                attempt_id="attempt",
                task_id="task",
            ),
        )


def test_reducer_context_uses_assigned_task_not_assigned_prompt(
    deps, workflow_store, iteration_store, attempt_store, task_store, task_center_run_id
) -> None:
    workflow = _workflow(workflow_store, task_center_run_id)
    iteration = iteration_store.insert(
        workflow_id=workflow.id,
        sequence_no=1,
        creation_reason=IterationCreationReason.INITIAL,
        iteration_goal=workflow.workflow_goal,
        attempt_budget=2,
    )
    attempt = attempt_store.insert(iteration_id=iteration.id, attempt_sequence_no=1)
    dep_id = generator_task_id(attempt.id, "api")
    task_id = reducer_task_id(attempt.id, "verify_api")
    task_store.upsert_task(
        task_id=dep_id,
        task_center_run_id=task_center_run_id,
        role="generator",
        agent_name="executor",
        context_message="Build API.",
        status="done",
        outcomes=[to_record(ExecutionTaskOutcome("success", "generator", dep_id, "API done."))],
        needs=[],
    )
    task_store.upsert_task(
        task_id=task_id,
        task_center_run_id=task_center_run_id,
        role="reducer",
        agent_name="reducer",
        context_message="Verify the API and CLI slice.",
        status="pending",
        outcomes=[],
        needs=[dep_id],
    )

    context = ContextEngine(deps).build(
        "reducer",
        ContextScope.for_reducer(
            workflow_id=workflow.id,
            iteration_id=iteration.id,
            attempt_id=attempt.id,
            task_id=task_id,
        ),
    )
    xml = render_context_xml(context)

    assert '<context role="reducer">' in xml
    assert f'<assigned_task task_id="{task_id}">' in xml
    assert "Verify the API and CLI slice." in xml
    assert "<assigned_prompt>" not in xml
    assert "<needs>" not in xml
