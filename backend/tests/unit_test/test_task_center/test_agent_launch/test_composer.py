"""Tests for the agent-entry composer."""

from __future__ import annotations

from workflow._core.primitives import generator_task_id
from workflow._core.state import IterationCreationReason
from workflow.agent_launch.entry_messages import AgentEntryMessages
from workflow.context_engine.scope import ContextScope


def _seed_attempt(
    *,
    workflow_store,
    iteration_store,
    attempt_store,
    request_id: str,
):
    workflow = workflow_store.insert(
        request_id=request_id,
        parent_task_id="root",
        workflow_goal="Build the feature.",
    )
    iteration = iteration_store.insert(
        workflow_id=workflow.id,
        sequence_no=1,
        creation_reason=IterationCreationReason.INITIAL,
        iteration_goal=workflow.workflow_goal,
        attempt_budget=2,
    )
    workflow_store.append_iteration_id(workflow.id, iteration.id)
    attempt = attempt_store.insert(iteration_id=iteration.id, attempt_sequence_no=1)
    iteration_store.append_attempt_id(iteration.id, attempt.id)
    return workflow, iteration, attempt


def test_compose_returns_separate_context_and_guidance_rows(
    composer,
    workflow_store,
    iteration_store,
    attempt_store,
    request_id,
) -> None:
    workflow, iteration, attempt = _seed_attempt(
        workflow_store=workflow_store,
        iteration_store=iteration_store,
        attempt_store=attempt_store,
        request_id=request_id,
    )

    messages = composer.compose(
        base_agent_name="planner",
        scope=ContextScope.for_planner(
            workflow_id=workflow.id,
            iteration_id=iteration.id,
            attempt_id=attempt.id,
        ),
    )

    assert isinstance(messages, AgentEntryMessages)
    assert messages.context.startswith('<context role="planner">\n')
    assert "<workflow>" in messages.context
    assert messages.task_guidance is not None
    assert messages.task_guidance.startswith("<Task Guidance>\n")
    assert "<terminal_tool_selection>" in messages.task_guidance


def test_compose_generator_context_uses_new_terminal_names(
    composer,
    workflow_store,
    iteration_store,
    attempt_store,
    task_store,
    request_id,
) -> None:
    workflow, iteration, attempt = _seed_attempt(
        workflow_store=workflow_store,
        iteration_store=iteration_store,
        attempt_store=attempt_store,
        request_id=request_id,
    )
    task_id = generator_task_id(attempt.id, "api")
    task_store.upsert_task(
        task_id=task_id,
        request_id=request_id,
        role="generator",
        agent_name="executor",
        instruction="Implement API.",
        status="pending",
        outcomes=[],
        needs=[],
    )

    messages = composer.compose(
        base_agent_name="executor",
        scope=ContextScope.for_generator(
            workflow_id=workflow.id,
            iteration_id=iteration.id,
            attempt_id=attempt.id,
            task_id=task_id,
        ),
    )

    assert '<context role="generator">' in messages.context
    assert f'<assigned_task task_id="{task_id}">' in messages.context
    assert messages.task_guidance is not None
    assert "submit_generator_outcome" in messages.task_guidance
    assert "submit_execution_success" not in messages.task_guidance
