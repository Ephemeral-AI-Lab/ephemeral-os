"""Workflow outcome records and aggregate projections.

Task outcomes are bounded to a single persisted task. Planner submissions create
real generator/reducer task rows, so attempt, iteration, and workflow outcomes
contain only generator/reducer execution evidence.

Writers and readers in this module use only the flat
``{status, role, task_id, ...}`` record shape.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, TypeAlias

from workflow._core.state import Attempt, AttemptStatus

if TYPE_CHECKING:  # pragma: no cover - typing-only
    from workflow._core.persistence import IterationStoreProtocol, TaskStoreProtocol
    from workflow._core.state import Workflow

TaskOutcomeStatus: TypeAlias = Literal["success", "failed"]
ExecutionRole: TypeAlias = Literal["generator", "reducer"]

_NO_OUTCOME = "(no outcome recorded)"

@dataclass(frozen=True, slots=True)
class ExecutionTaskOutcome:
    status: TaskOutcomeStatus
    role: ExecutionRole
    task_id: str
    outcome: str


TaskOutcome: TypeAlias = ExecutionTaskOutcome


def present_status(raw_status: str) -> TaskOutcomeStatus:
    return "success" if raw_status == "done" else "failed"


def task_outcomes_from_row(task_id: str, task: dict[str, Any] | None) -> tuple[ExecutionTaskOutcome, ...]:
    """Parse all stored outcomes on one task row.

    Missing rows and tasks with no terminal outcome return an empty tuple. This
    reflects the new model: startup failures and handoff starts are not task
    outcomes until a terminal writes an explicit flattened result.
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
        parsed.extend(
            _outcomes_from_record(
                normalized,
                fallback_task_id=task_id,
                fallback_role=_execution_role(task.get("role")),
            )
        )
    return tuple(parsed)


def execution_outcomes_from_row(
    task_id: str, task: dict[str, Any] | None
) -> tuple[ExecutionTaskOutcome, ...]:
    return task_outcomes_from_row(task_id, task)


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


def to_record(outcome: ExecutionTaskOutcome) -> dict[str, Any]:
    """Serialize an execution outcome to a JSON-safe dict."""
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
                record,
                fallback_task_id=str(record.get("task_id") or ""),
                fallback_role=None,
            )
        )
    return tuple(parsed)


def records_json(outcomes: tuple[ExecutionTaskOutcome, ...]) -> str:
    return json.dumps([to_record(outcome) for outcome in outcomes])


# ---- internals ------------------------------------------------------------


def _normalize_status(value: Any) -> TaskOutcomeStatus:
    raw = str(value or "").strip()
    if raw == "success":
        return "success"
    return "failed"


def _execution_role(value: Any) -> ExecutionRole | None:
    if value in ("generator", "reducer"):
        return value
    return None


def _outcomes_from_record(
    record: dict[str, Any],
    *,
    fallback_task_id: str,
    fallback_role: ExecutionRole | None,
) -> tuple[ExecutionTaskOutcome, ...]:
    role = _execution_role(record.get("role")) or fallback_role
    task_id = str(record.get("task_id") or fallback_task_id or "")
    if role is not None:
        return (
            ExecutionTaskOutcome(
                status=_normalize_status(record.get("status")),
                role=role,
                task_id=task_id,
                outcome=str(record.get("outcome") or _NO_OUTCOME),
            ),
        )

    return (
        ExecutionTaskOutcome(
            status=_normalize_status(record.get("status")),
            role="generator",
            task_id=task_id,
            outcome=str(record.get("outcome") or _NO_OUTCOME),
        ),
    )

__all__ = [
    "ExecutionRole",
    "ExecutionTaskOutcome",
    "TaskOutcome",
    "TaskOutcomeStatus",
    "attempt_execution_outcomes",
    "execution_outcome_for_submission",
    "execution_outcomes_from_row",
    "parse_outcomes_record",
    "present_status",
    "project_attempt_outcomes",
    "project_iteration_outcomes",
    "records_json",
    "task_outcomes_from_row",
    "to_record",
    "workflow_outcomes",
]
