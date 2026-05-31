"""TaskCenter outcome records and aggregate projections.

Task outcomes are bounded to a single TaskCenter task. Planner outcomes are
UI/rendering metadata for planner tasks; attempt, iteration, and workflow
outcomes are execution evidence and therefore contain only generator/reducer
outcomes.

Writers and readers in this module use only the flat
``{status, role, task_id, ...}`` record shape.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, TypeAlias

from task_center._core.state import Attempt, AttemptStatus

if TYPE_CHECKING:  # pragma: no cover - typing-only
    from task_center._core.persistence import IterationStoreProtocol, TaskStoreProtocol
    from task_center._core.state import Workflow
    from task_center.submissions import PlannerSubmission

TaskOutcomeStatus: TypeAlias = Literal["success", "failed"]
ExecutionRole: TypeAlias = Literal["generator", "reducer"]

_NO_OUTCOME = "(no outcome recorded)"

_GEN_SEP = ":gen:"
_RED_SEP = ":red:"


@dataclass(frozen=True, slots=True)
class PlannedTaskRef:
    task_id: str
    role: ExecutionRole
    assigned_task: str
    needs: tuple[str, ...]
    agent_name: str | None = None


@dataclass(frozen=True, slots=True)
class PlannerTaskOutcome:
    status: TaskOutcomeStatus
    role: Literal["planner"]
    task_id: str
    planned_tasks: tuple[PlannedTaskRef, ...]
    deferred_goal_for_next_iteration: str | None = None


@dataclass(frozen=True, slots=True)
class ExecutionTaskOutcome:
    status: TaskOutcomeStatus
    role: ExecutionRole
    task_id: str
    outcome: str


TaskOutcome: TypeAlias = PlannerTaskOutcome | ExecutionTaskOutcome


def role_from_task_id(task_id: str) -> ExecutionRole | None:
    if _GEN_SEP in task_id:
        return "generator"
    if _RED_SEP in task_id:
        return "reducer"
    return None


def present_status(raw_status: str) -> TaskOutcomeStatus:
    return "success" if raw_status == "done" else "failed"


def task_outcomes_from_row(task_id: str, task: dict[str, Any] | None) -> tuple[TaskOutcome, ...]:
    """Parse all stored outcomes on one task row.

    Missing rows and tasks with no terminal outcome return an empty tuple. This
    reflects the new model: startup failures and handoff starts are not task
    outcomes until TaskCenter writes an explicit terminal/flattened result.
    """
    if task is None:
        return ()
    parsed: list[TaskOutcome] = []
    for record in task.get("outcomes") or ():
        if not isinstance(record, dict):
            continue
        normalized = dict(record)
        normalized.setdefault("task_id", task_id)
        normalized.setdefault("status", present_status(str(task.get("status") or "failed")))
        if "role" not in normalized:
            role = role_from_task_id(task_id)
            if role is not None:
                normalized["role"] = role
        parsed.extend(_outcomes_from_record(normalized, fallback_task_id=task_id))
    return tuple(parsed)


def execution_outcomes_from_row(
    task_id: str, task: dict[str, Any] | None
) -> tuple[ExecutionTaskOutcome, ...]:
    return tuple(
        outcome
        for outcome in task_outcomes_from_row(task_id, task)
        if isinstance(outcome, ExecutionTaskOutcome)
    )


def planner_outcome_from_submission(submission: PlannerSubmission) -> PlannerTaskOutcome:
    """Build the planner task outcome from a normalized planner submission."""
    id_map = _planned_id_map(submission)
    planned: list[PlannedTaskRef] = []
    for task in submission.generators:
        planned.append(
            PlannedTaskRef(
                task_id=id_map[task.local_id],
                role="generator",
                assigned_task=task.task_spec,
                needs=tuple(id_map[dep] for dep in task.needs),
                agent_name=task.agent_name,
            )
        )
    for reducer in submission.reducers:
        planned.append(
            PlannedTaskRef(
                task_id=id_map[reducer.local_id],
                role="reducer",
                assigned_task=reducer.prompt,
                needs=tuple(id_map[dep] for dep in reducer.needs),
                agent_name=None,
            )
        )
    return PlannerTaskOutcome(
        status="success",
        role="planner",
        task_id=submission.planner_task_id,
        planned_tasks=tuple(planned),
        deferred_goal_for_next_iteration=submission.deferred_goal_for_next_iteration,
    )


def execution_outcome_for_submission(
    *, task_id: str, role: ExecutionRole, status: TaskOutcomeStatus, outcome: str
) -> ExecutionTaskOutcome:
    return ExecutionTaskOutcome(status=status, role=role, task_id=task_id, outcome=outcome)


def project_attempt_outcomes(
    attempt: Attempt, task_store: TaskStoreProtocol | None
) -> tuple[ExecutionTaskOutcome, ...]:
    """Project generator/reducer execution outcomes for one attempt."""
    if task_store is None:
        return attempt.outcomes
    out: list[ExecutionTaskOutcome] = []
    for task_id in (*attempt.generator_task_ids, *attempt.reducer_task_ids):
        out.extend(execution_outcomes_from_row(task_id, task_store.get_task(task_id)))
    return tuple(out)


def attempt_execution_outcomes(
    attempt: Attempt, task_store: TaskStoreProtocol | None
) -> tuple[ExecutionTaskOutcome, ...]:
    """Return persisted attempt outcomes, or recompute from task rows when not yet persisted."""
    if attempt.outcomes:
        return attempt.outcomes
    return project_attempt_outcomes(attempt, task_store)


def project_iteration_outcomes(
    attempts: list[Attempt] | tuple[Attempt, ...],
    task_store: TaskStoreProtocol | None,
) -> tuple[ExecutionTaskOutcome, ...]:
    """Execution evidence for the iteration's closing attempt only.

    On a passing close, the closing attempt's successful reducer outcomes; on a
    failed close, that attempt's failed generator/reducer tasks. Reducer
    successes from earlier failed attempts are internal attempt history, not
    iteration evidence, and are never surfaced.
    """
    if not attempts:
        return ()
    final_attempt = attempts[-1]
    final_outcomes = attempt_execution_outcomes(final_attempt, task_store)
    if final_attempt.status == AttemptStatus.PASSED:
        return tuple(
            outcome
            for outcome in final_outcomes
            if outcome.role == "reducer" and outcome.status == "success"
        )
    return tuple(
        outcome
        for outcome in final_outcomes
        if outcome.role in ("generator", "reducer") and outcome.status == "failed"
    )


def workflow_outcomes(
    workflow: Workflow, *, iteration_store: IterationStoreProtocol
) -> tuple[ExecutionTaskOutcome, ...]:
    """Derived ``workflow.outcomes`` = latest iteration projection."""
    iterations = iteration_store.list_for_workflow(workflow.id)
    if not iterations:
        return ()
    latest = max(iterations, key=lambda it: it.sequence_no)
    return parse_outcomes_record(latest.outcomes)


# ---- JSON round-trip ------------------------------------------------------


def to_record(outcome: TaskOutcome) -> dict[str, Any]:
    """Serialize a planner or execution outcome to a JSON-safe dict."""
    if isinstance(outcome, PlannerTaskOutcome):
        record: dict[str, Any] = {
            "status": outcome.status,
            "role": outcome.role,
            "task_id": outcome.task_id,
            "planned_tasks": [
                {
                    "task_id": task.task_id,
                    "role": task.role,
                    "assigned_task": task.assigned_task,
                    "needs": list(task.needs),
                    "agent_name": task.agent_name,
                }
                for task in outcome.planned_tasks
            ],
        }
        if outcome.deferred_goal_for_next_iteration is not None:
            record["deferred_goal_for_next_iteration"] = (
                outcome.deferred_goal_for_next_iteration
            )
        return record
    if isinstance(outcome, ExecutionTaskOutcome):
        return {
            "status": outcome.status,
            "role": outcome.role,
            "task_id": outcome.task_id,
            "outcome": outcome.outcome,
        }
    raise TypeError(f"Unsupported outcome type: {type(outcome).__name__}")


def parse_outcomes_record(value: Any) -> tuple[ExecutionTaskOutcome, ...]:
    """Parse an iteration/workflow outcomes field into execution outcomes."""
    if not value:
        return ()
    records = value
    if isinstance(value, str):
        records = json.loads(value)
    if not isinstance(records, list):
        return ()
    parsed: list[ExecutionTaskOutcome] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        parsed.extend(
            outcome
            for outcome in _outcomes_from_record(
                record, fallback_task_id=str(record.get("task_id") or "")
            )
            if isinstance(outcome, ExecutionTaskOutcome)
        )
    return tuple(parsed)


def records_json(outcomes: tuple[ExecutionTaskOutcome, ...]) -> str:
    return json.dumps([to_record(outcome) for outcome in outcomes])


# ---- internals ------------------------------------------------------------


def _planned_id_map(submission: PlannerSubmission) -> dict[str, str]:
    from task_center._core.primitives import generator_task_id, reducer_task_id

    ids = {
        task.local_id: generator_task_id(submission.attempt_id, task.local_id)
        for task in submission.generators
    }
    ids.update(
        {
            reducer.local_id: reducer_task_id(submission.attempt_id, reducer.local_id)
            for reducer in submission.reducers
        }
    )
    return ids


def _normalize_status(value: Any) -> TaskOutcomeStatus:
    raw = str(value or "").strip()
    if raw == "success":
        return "success"
    return "failed"


def _outcomes_from_record(record: dict[str, Any], *, fallback_task_id: str) -> tuple[TaskOutcome, ...]:
    role = record.get("role")
    task_id = str(record.get("task_id") or fallback_task_id or "")
    if role == "planner":
        planned_tasks = tuple(
            _planned_ref_from_record(item)
            for item in record.get("planned_tasks") or ()
            if isinstance(item, dict)
        )
        return (
            PlannerTaskOutcome(
                status=_normalize_status(record.get("status")),
                role="planner",
                task_id=task_id,
                planned_tasks=planned_tasks,
                deferred_goal_for_next_iteration=record.get(
                    "deferred_goal_for_next_iteration"
                ),
            ),
        )
    if role in ("generator", "reducer"):
        return (
            ExecutionTaskOutcome(
                status=_normalize_status(record.get("status")),
                role=role,
                task_id=task_id,
                outcome=str(record.get("outcome") or _NO_OUTCOME),
            ),
        )

    inferred = role_from_task_id(task_id) or "generator"
    return (
        ExecutionTaskOutcome(
            status=_normalize_status(record.get("status")),
            role=inferred,
            task_id=task_id,
            outcome=str(record.get("outcome") or _NO_OUTCOME),
        ),
    )


def _planned_ref_from_record(record: dict[str, Any]) -> PlannedTaskRef:
    role = "reducer" if record.get("role") == "reducer" else "generator"
    return PlannedTaskRef(
        task_id=str(record.get("task_id") or ""),
        role=role,
        assigned_task=str(record.get("assigned_task") or ""),
        needs=tuple(str(dep) for dep in record.get("needs") or ()),
        agent_name=(
            str(record["agent_name"])
            if record.get("agent_name") is not None
            else None
        ),
    )


__all__ = [
    "ExecutionRole",
    "ExecutionTaskOutcome",
    "PlannedTaskRef",
    "PlannerTaskOutcome",
    "TaskOutcome",
    "TaskOutcomeStatus",
    "attempt_execution_outcomes",
    "execution_outcome_for_submission",
    "execution_outcomes_from_row",
    "parse_outcomes_record",
    "present_status",
    "planner_outcome_from_submission",
    "project_attempt_outcomes",
    "project_iteration_outcomes",
    "records_json",
    "role_from_task_id",
    "task_outcomes_from_row",
    "to_record",
    "workflow_outcomes",
]
