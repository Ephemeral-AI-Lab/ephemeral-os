"""TaskCenter context engine.

The engine builds one explicit :class:`AgentContext` for the launch role. It
does not own lifecycle policy, terminal routing, token budgeting, or a generic
recipe registry.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from task_center._core.outcomes import (
    ExecutionTaskOutcome,
    attempt_execution_outcomes,
    latest_execution_outcome,
    parse_outcomes_record,
    role_from_task_id,
)
from task_center._core.state import Attempt
from task_center.context_engine.context import AgentContext, ContextSection
from task_center.context_engine.exceptions import (
    AgentDefinitionValidationError,
    ContextEngineError,
    MissingContextRecipeError,
    RecipeScopeError,
)
from task_center.context_engine.scope import ContextScope
from task_center.context_engine.xml import render_task_outcome

if TYPE_CHECKING:  # pragma: no cover - typing-only
    from task_center._core.persistence import (
        AttemptStoreProtocol,
        IterationStoreProtocol,
        TaskStoreProtocol,
        WorkflowStoreProtocol,
    )

__all__ = [
    "AgentDefinitionValidationError",
    "AgentContext",
    "ContextEngine",
    "ContextEngineDeps",
    "ContextEngineError",
    "ContextSection",
    "MissingContextRecipeError",
    "RecipeScopeError",
    "build_agent_context",
    "build_generator_context",
    "build_planner_context",
    "build_reducer_context",
]


@dataclass(frozen=True, slots=True)
class ContextEngineDeps:
    """Frozen bundle of stores context builders read from."""

    workflow_store: WorkflowStoreProtocol
    iteration_store: IterationStoreProtocol
    attempt_store: AttemptStoreProtocol
    task_store: TaskStoreProtocol


@dataclass(frozen=True, slots=True)
class ContextEngine:
    """Build role-scoped context for one launch."""

    deps: ContextEngineDeps

    def build(self, recipe_id: str, scope: ContextScope) -> AgentContext:  # noqa: ARG002
        return build_agent_context(scope, self.deps)


def build_agent_context(scope: ContextScope, deps: ContextEngineDeps) -> AgentContext:
    match scope.role:
        case "planner":
            return build_planner_context(scope, deps)
        case "generator":
            return build_generator_context(scope, deps)
        case "reducer":
            return build_reducer_context(scope, deps)


def build_planner_context(scope: ContextScope, deps: ContextEngineDeps) -> AgentContext:
    workflow_id = scope.require_field("workflow_id")
    iteration_id = scope.require_field("iteration_id")
    attempt_id = scope.require_field("attempt_id")

    workflow = deps.workflow_store.get(workflow_id)
    if workflow is None:
        raise ContextEngineError(f"Workflow {workflow_id!r} not found")
    iteration = deps.iteration_store.get(iteration_id)
    if iteration is None:
        raise ContextEngineError(f"Iteration {iteration_id!r} not found")
    current_attempt = deps.attempt_store.get(attempt_id)
    if current_attempt is None:
        raise ContextEngineError(f"Attempt {attempt_id!r} not found")

    workflow_children = [
        ContextSection(tag="goal", text=workflow.workflow_goal),
    ]
    prior_iterations = _prior_iteration_sections(
        current_sequence=iteration.sequence_no,
        iterations=deps.iteration_store.list_for_workflow(workflow.id),
    )
    if prior_iterations:
        workflow_children.append(
            ContextSection(tag="prior_iterations", children=prior_iterations)
        )

    current_children = [ContextSection(tag="goal", text=iteration.iteration_goal)]
    previous_attempts = _previous_attempt_sections(
        current_attempt=current_attempt,
        attempts=deps.attempt_store.list_for_iteration(iteration.id),
        deps=deps,
    )
    if previous_attempts:
        current_children.append(
            ContextSection(tag="previous_attempts", children=previous_attempts)
        )
    workflow_children.append(
        ContextSection(
            tag="current_iteration",
            attrs={"sequence": str(iteration.sequence_no)},
            children=tuple(current_children),
        )
    )

    return AgentContext(
        role="planner",
        sections=(ContextSection(tag="workflow", children=tuple(workflow_children)),),
        directive="Plan generator and reducer tasks for <current_iteration><goal>.",
        context_limits=(
            "Prior iterations omit internal attempt history.",
            "Planner outcomes are omitted from iteration and workflow history.",
        ),
        target_id=attempt_id,
        workflow_id=workflow_id,
        iteration_id=iteration_id,
        attempt_id=attempt_id,
    )


def build_generator_context(scope: ContextScope, deps: ContextEngineDeps) -> AgentContext:
    return _build_execution_context(scope, deps, role="generator")


def build_reducer_context(scope: ContextScope, deps: ContextEngineDeps) -> AgentContext:
    return _build_execution_context(scope, deps, role="reducer")


def _build_execution_context(
    scope: ContextScope,
    deps: ContextEngineDeps,
    *,
    role: str,
) -> AgentContext:
    workflow_id = scope.require_field("workflow_id")
    attempt_id = scope.require_field("attempt_id")
    task_id = scope.require_field("task_id")
    attempt = deps.attempt_store.get(attempt_id)
    if attempt is None:
        raise ContextEngineError(f"Attempt {attempt_id!r} not found")
    task = deps.task_store.get_task(task_id)
    if task is None:
        raise ContextEngineError(f"TaskCenterTask {task_id!r} not found")

    sections: list[ContextSection] = []
    dependency_sections = _dependency_sections(
        needs=tuple(str(dep) for dep in task.get("needs") or ()),
        deps=deps,
    )
    if dependency_sections:
        sections.append(
            ContextSection(tag="dependencies", children=dependency_sections)
        )
    sections.append(
        ContextSection(
            tag="assigned_task",
            attrs={"task_id": task_id},
            text=str(task.get("context_message") or ""),
        )
    )
    return AgentContext(
        role="reducer" if role == "reducer" else "generator",
        sections=tuple(sections),
        directive="Complete <assigned_task> using <dependencies>.",
        target_id=task_id,
        workflow_id=workflow_id,
        iteration_id=scope.iteration_id or attempt.iteration_id,
        attempt_id=attempt_id,
        task_id=task_id,
    )


def _prior_iteration_sections(
    *,
    current_sequence: int,
    iterations: list,
) -> tuple[ContextSection, ...]:
    sections: list[ContextSection] = []
    for iteration in sorted(iterations, key=lambda item: item.sequence_no):
        if iteration.sequence_no >= current_sequence:
            continue
        outcomes = parse_outcomes_record(iteration.outcomes)
        if not outcomes:
            continue
        sections.append(
            ContextSection(
                tag="iteration",
                attrs={"sequence": str(iteration.sequence_no)},
                children=tuple(render_task_outcome(outcome) for outcome in outcomes),
            )
        )
    return tuple(sections)


def _previous_attempt_sections(
    *,
    current_attempt: Attempt,
    attempts: list[Attempt],
    deps: ContextEngineDeps,
) -> tuple[ContextSection, ...]:
    sections: list[ContextSection] = []
    for attempt in sorted(attempts, key=lambda item: item.attempt_sequence_no):
        if attempt.attempt_sequence_no >= current_attempt.attempt_sequence_no:
            continue
        outcomes = attempt_execution_outcomes(attempt, deps.task_store)
        if not outcomes:
            continue
        sections.append(
            ContextSection(
                tag="attempt",
                attrs={
                    "sequence": str(attempt.attempt_sequence_no),
                    "status": attempt.status.value,
                },
                children=tuple(render_task_outcome(outcome) for outcome in outcomes),
            )
        )
    return tuple(sections)


def _dependency_sections(
    *, needs: tuple[str, ...], deps: ContextEngineDeps
) -> tuple[ContextSection, ...]:
    sections: list[ContextSection] = []
    for task_id in needs:
        task = deps.task_store.get_task(task_id)
        if task is None:
            raise ContextEngineError(
                f"Dependency task {task_id!r} is missing; context cannot be assembled."
            )
        outcome = latest_execution_outcome(task_id, task)
        if outcome is None:
            if task.get("status") != "done":
                raise ContextEngineError(
                    f"Dependency task {task_id!r} has no execution outcome."
                )
            outcome = ExecutionTaskOutcome(
                status="success",
                role=role_from_task_id(task_id) or "generator",
                task_id=task_id,
                outcome="(no outcome recorded)",
            )
        sections.append(
            ContextSection(
                tag="dependency",
                attrs={"task_id": outcome.task_id},
                children=(render_task_outcome(outcome),),
            )
        )
    return tuple(sections)
