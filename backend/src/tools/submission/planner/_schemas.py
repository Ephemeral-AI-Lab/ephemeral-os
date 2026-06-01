"""Planner submission schemas and validation helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from agents import AgentRole, get_definition
from task import TaskStatus
from workflow import (
    PlannerSubmission,
    TaskCenterInvariantViolation,
    ordered_plan_tasks,
)
from workflow._core.primitives import generator_task_id, reducer_task_id
from tools.submission.context import AttemptSubmissionContext


# `submission_kind` payload string constants.
SUBMISSION_KIND_PLANNER_DEFERS = "planner_defers"
SUBMISSION_KIND_PLANNER_COMPLETES = "planner_completes"

_REDUCER_AGENT_NAME = "reducer"


@dataclass(frozen=True, slots=True)
class _GeneratorDraft:
    local_id: str
    agent_name: str
    needs: tuple[str, ...]
    instruction: str


@dataclass(frozen=True, slots=True)
class _ReducerDraft:
    local_id: str
    needs: tuple[str, ...]
    instruction: str


class PlanTaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1)
    agent_name: str = Field(..., min_length=1)
    needs: list[str] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        return validate_nonblank(value, "id")

    @field_validator("agent_name")
    @classmethod
    def _validate_agent_name(cls, value: str) -> str:
        return validate_nonblank(value, "agent_name")

    @field_validator("needs")
    @classmethod
    def _validate_needs(cls, value: list[str]) -> list[str]:
        for dep in value:
            validate_nonblank(dep, "needs")
        return value


class ReducerInput(BaseModel):
    """One reducer plan task. ``prompt`` required + nonblank."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1)
    needs: list[str] = Field(default_factory=list)
    prompt: str = Field(..., min_length=1)

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        return validate_nonblank(value, "id")

    @field_validator("needs")
    @classmethod
    def _validate_needs(cls, value: list[str]) -> list[str]:
        for dep in value:
            validate_nonblank(dep, "needs")
        return value

    @field_validator("prompt")
    @classmethod
    def _validate_prompt(cls, value: str) -> str:
        return validate_nonblank(value, "prompt")


class SharedPlannerSubmissionInput(BaseModel):
    """Planner submission boundary schema.

    A plan is a DAG of generator + reducer tasks. ``tasks`` + ``task_specs``
    define the generators; ``reducers`` (>=1) define iteration outcomes.
    Framing lives in each task spec and each reducer prompt.
    """

    model_config = ConfigDict(extra="forbid")

    tasks: list[PlanTaskInput] = Field(..., min_length=1)
    task_specs: dict[str, str] = Field(..., min_length=1)
    reducers: list[ReducerInput] = Field(..., min_length=1)

    @field_validator("task_specs")
    @classmethod
    def _validate_task_specs(cls, value: dict[str, str]) -> dict[str, str]:
        for key, spec in value.items():
            validate_nonblank(key, "task_specs key")
            validate_nonblank(spec, f"task spec for {key!r}")
        return value


def validate_nonblank(value: str, field_name: str) -> str:
    if not value or value.isspace():
        raise ValueError(f"{field_name} must be nonblank")
    return value


def planner_kind_from_deferred_goal(
    deferred_goal_for_next_iteration: str | None,
) -> tuple[Literal["completes", "defers"], str | None]:
    if deferred_goal_for_next_iteration is None:
        return "completes", None
    validate_nonblank(
        deferred_goal_for_next_iteration,
        "deferred_goal_for_next_iteration",
    )
    return "defers", deferred_goal_for_next_iteration


def _is_generator_capable_agent(agent_name: str) -> bool:
    """Gate for ``agent_name`` values a planner may submit as a generator task.

    Only ``generator``-role profiles (executor) are generator-capable; planner,
    reducer, helper, and subagent roles are never planner-submittable.
    """
    definition = get_definition(agent_name)
    if definition is None:
        return False
    return definition.role == AgentRole.GENERATOR


def build_planner_submission(
    *,
    submission_context: AttemptSubmissionContext,
    kind: Literal["completes", "defers"],
    tasks: list[PlanTaskInput],
    task_specs: dict[str, str],
    reducers: list[ReducerInput],
    deferred_goal_for_next_iteration: str | None,
) -> tuple[PlannerSubmission | None, str | None]:
    task_id = submission_context.task_center_task_id
    if task_id != submission_context.attempt.planner_task_id:
        return None, "Current TaskCenter task is not this attempt's planner task."

    seen: set[str] = set()
    for task in tasks:
        if task.id in seen:
            return None, f"Plan contains duplicate task id {task.id!r}."
        seen.add(task.id)
        if not _is_generator_capable_agent(task.agent_name):
            return None, f"Unknown generator agent {task.agent_name!r}."

    task_ids = {task.id for task in tasks}
    spec_ids = set(task_specs)
    missing_specs = sorted(task_ids - spec_ids)
    if missing_specs:
        return None, f"Missing task_specs for {', '.join(missing_specs)}."
    extra_specs = sorted(spec_ids - task_ids)
    if extra_specs:
        return None, f"task_specs contains unknown ids {', '.join(extra_specs)}."

    for task_id_for_spec, spec in task_specs.items():
        if not spec or spec.isspace():
            return None, f"Task spec for {task_id_for_spec!r} is blank."

    generator_drafts = tuple(
        _GeneratorDraft(
            local_id=task.id,
            agent_name=task.agent_name,
            needs=tuple(task.needs),
            instruction=task_specs[task.id],
        )
        for task in tasks
    )
    reducer_drafts = tuple(
        _ReducerDraft(
            local_id=reducer.id,
            needs=tuple(reducer.needs),
            instruction=reducer.prompt,
        )
        for reducer in reducers
    )
    try:
        ordered_generators, ordered_reducers = ordered_plan_tasks(
            generator_drafts, reducer_drafts
        )
    except TaskCenterInvariantViolation as exc:
        message = str(exc)
        if "dependency cycle" in message:
            return None, "Plan contains a dependency cycle."
        return None, message

    attempt = submission_context.attempt
    id_map = {
        task.local_id: generator_task_id(attempt.id, task.local_id)
        for task in ordered_generators
    }
    id_map.update(
        {
            reducer.local_id: reducer_task_id(attempt.id, reducer.local_id)
            for reducer in ordered_reducers
        }
    )
    runtime = submission_context.runtime
    request_id = submission_context.workflow.request_id
    for task in ordered_generators:
        runtime.task_store.upsert_task(
            task_id=id_map[task.local_id],
            request_id=request_id,
            role=AgentRole.GENERATOR.value,
            agent_name=task.agent_name,
            instruction=task.instruction,
            status=TaskStatus.PENDING.value,
            outcomes=[],
            needs=[id_map[dep] for dep in task.needs],
            workflow_id=submission_context.workflow.id,
            iteration_id=submission_context.iteration.id,
            attempt_id=attempt.id,
        )
    for reducer in ordered_reducers:
        runtime.task_store.upsert_task(
            task_id=id_map[reducer.local_id],
            request_id=request_id,
            role=AgentRole.REDUCER.value,
            agent_name=_REDUCER_AGENT_NAME,
            instruction=reducer.instruction,
            status=TaskStatus.PENDING.value,
            outcomes=[],
            needs=[id_map[dep] for dep in reducer.needs],
            workflow_id=submission_context.workflow.id,
            iteration_id=submission_context.iteration.id,
            attempt_id=attempt.id,
        )

    return (
        PlannerSubmission(
            attempt_id=attempt.id,
            planner_task_id=task_id,
            kind=kind,
            generator_task_ids=tuple(id_map[task.local_id] for task in ordered_generators),
            reducer_task_ids=tuple(id_map[reducer.local_id] for reducer in ordered_reducers),
            deferred_goal_for_next_iteration=deferred_goal_for_next_iteration,
        ),
        None,
    )
