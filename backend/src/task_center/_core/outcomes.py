"""Recursive ``Outcome`` algebra — the single result unit for the harness.

This module is the source of truth for the *data* behind a
``<task id="<local_id>" status="<success|failure|pending>">`` element: the
projection off a task row's persisted ``outcomes`` list, the internal-enum →
presentation-status mapping, the local-id derivation, the per-task outcome
record, the failure line, and the JSON round-trip used by the denormalized
iteration ``outcomes`` and the handoff roll-up.

``Outcome`` is recursive: a handoff generator emits one ``Outcome`` whose
``children`` are the child workflow's outcomes. It deliberately holds **no XML
and no ``ContextEngineError``** — rendering and hostile-body sanitization live
in the ``context_engine`` layer (``recipes/_task_xml.py``), which depends on
this module, never the reverse.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from task_center._core.state import Attempt, AttemptFailReason
from task_center._core.task_state import TERMINAL_GENERATOR_STATUSES

if TYPE_CHECKING:  # pragma: no cover - typing-only
    from task_center._core.persistence import IterationStoreProtocol, TaskStoreProtocol
    from task_center._core.state import Workflow

_NO_OUTCOME = "(no outcome recorded)"
_EMPTY = "(empty)"
_NO_DETAIL = "(no detail recorded)"
EMPTY_OUTCOME_PLACEHOLDERS: frozenset[str] = frozenset({_EMPTY, _NO_OUTCOME})

_GEN_SEP = ":gen:"
_RED_SEP = ":red:"
_RUN_EXHAUSTED = "run_exhausted"

# Internal enum value → presentation status. Unknown values (``running``,
# ``waiting_workflow``, ``"missing task row"``) fall through unchanged so callers
# stay presence-defensive.
_PRESENTATION: dict[str, str] = {
    "done": "success",
    "failed": "failure",
    "blocked": "failure",
    "pending": "pending",
}
_TERMINAL_RAW: frozenset[str] = frozenset(s.value for s in TERMINAL_GENERATOR_STATUSES)
_MISSING_TASK_ROW_STATUS = "missing task row"
_FAILED_RAW = frozenset({"failed", "blocked"})


@dataclass(frozen=True, slots=True)
class Outcome:
    """One generator/reducer/task outcome, the data behind a ``<task>`` element.

    ``outcome`` is the agent's terminal result text; ``status`` is the
    presentation status; ``raw_status`` is the internal task status (``None``
    when rebuilt from a serialized record, where the raw value is no longer
    needed). ``children`` carries a handoff roll-up (one or more levels of
    nested ``<task>``); ``failure`` is the ``<failure>`` line for a failed task
    or handoff.
    """

    local_id: str
    status: str
    outcome: str | None
    children: tuple["Outcome", ...] = ()
    failure: str | None = None
    raw_status: str | None = None

    @property
    def is_terminal(self) -> bool:
        return self.raw_status in _TERMINAL_RAW


def present_status(raw_status: str) -> str:
    """Map an internal task status to the presentation vocabulary.

    ``done→success``, ``failed|blocked→failure``, ``pending→pending``. Any
    other value (``running``/``waiting_workflow``/``missing task row``) passes
    through unchanged.
    """
    return _PRESENTATION.get(raw_status, raw_status)


def local_id_of(task_id: str) -> str:
    """Derive the planner-assigned local id from a generator/reducer task id.

    Task ids are ``"<attempt_id>:gen:<local_id>"`` / ``":red:<local_id>"``.
    Short fixture ids without a separator pass through unchanged.
    """
    for sep in (_GEN_SEP, _RED_SEP):
        if sep in task_id:
            return task_id.split(sep, 1)[1]
    return task_id


def task_outcome_from_row(task_id: str, task: dict[str, Any] | None) -> Outcome:
    """Build an :class:`Outcome` from a (possibly missing) task row.

    The presentation status is derived from the live task status; ``outcome`` /
    ``children`` / ``failure`` come from the latest persisted ``outcomes``
    record (the agent's terminal result, written by the submit path).
    """
    local_id = local_id_of(task_id)
    if task is None:
        return Outcome(
            local_id=local_id, status=_MISSING_TASK_ROW_STATUS, outcome=None, raw_status=None
        )
    raw_status = str(task.get("status") or "unknown")
    latest = _latest_outcome_record(task.get("outcomes"))
    outcome_text: str | None = None
    children: tuple[Outcome, ...] = ()
    failure: str | None = None
    if latest is not None:
        rebuilt = from_record(latest)
        outcome_text, children, failure = rebuilt.outcome, rebuilt.children, rebuilt.failure
    return Outcome(
        local_id=local_id,
        status=present_status(raw_status),
        outcome=outcome_text,
        children=children,
        failure=failure,
        raw_status=raw_status,
    )


def generator_outcomes(
    attempt: Attempt, *, task_store: TaskStoreProtocol | None
) -> list[Outcome]:
    """Return one :class:`Outcome` per generator task, in DAG order."""
    if task_store is None or not attempt.generator_task_ids:
        return []
    return [
        task_outcome_from_row(task_id, task_store.get_task(task_id))
        for task_id in attempt.generator_task_ids
    ]


def reducer_outcomes(
    attempt: Attempt, *, task_store: TaskStoreProtocol | None
) -> list[Outcome]:
    """Return one :class:`Outcome` per reducer task, in DAG order.

    ``attempt.outcomes`` is the union of its reducers' outcomes; this is the
    canonical projection a passing iteration denormalizes.
    """
    if task_store is None or not attempt.reducer_task_ids:
        return []
    return [
        task_outcome_from_row(task_id, task_store.get_task(task_id))
        for task_id in attempt.reducer_task_ids
    ]


def attempt_failure_line(attempt: Attempt, task_store: TaskStoreProtocol | None) -> str:
    """Render the ``<failure>`` body for *attempt* from its ``fail_reason``.

    ``STARTUP_FAILED`` → ``agent_launch_failed``. ``TASK_FAILED`` →
    one ``<role> <local_id>: <outcome>`` line per failed/blocked plan task (any
    role). Appends ``(terminated)`` when the failing task's latest terminal
    result was ``run_exhausted``. Presence-defensive: ``(no detail recorded)``
    when nothing is available.
    """
    reason = attempt.fail_reason
    if reason == AttemptFailReason.STARTUP_FAILED:
        return "agent_launch_failed"
    if reason == AttemptFailReason.TASK_FAILED:
        return _failed_task_lines(attempt, task_store)
    return _NO_DETAIL


def failed_task_outcomes(
    attempt: Attempt, task_store: TaskStoreProtocol | None
) -> list[Outcome]:
    """Failed/blocked plan-task outcomes (any role) for a failed attempt.

    Used by the failure-aware iteration ``outcomes`` (the projection a failed
    iteration denormalizes) and the failed-handoff roll-up.
    """
    out: list[Outcome] = []
    for outcome in generator_outcomes(attempt, task_store=task_store) + reducer_outcomes(
        attempt, task_store=task_store
    ):
        if outcome.raw_status in _FAILED_RAW:
            out.append(outcome)
    return out


# ---- JSON round-trip ------------------------------------------------------


def to_record(outcome: Outcome) -> dict[str, Any]:
    """Serialize an :class:`Outcome` to a JSON-safe dict (drops raw_status)."""
    record: dict[str, Any] = {
        "local_id": outcome.local_id,
        "status": outcome.status,
        "outcome": outcome.outcome,
    }
    if outcome.children:
        record["children"] = [to_record(child) for child in outcome.children]
    if outcome.failure is not None:
        record["failure"] = outcome.failure
    return record


def from_record(record: dict[str, Any]) -> Outcome:
    """Rebuild an :class:`Outcome` from a serialized dict.

    Reads ``outcome`` then the legacy ``summary`` key (pre-redesign rows).
    """
    outcome_text = record.get("outcome")
    if outcome_text is None:
        outcome_text = record.get("summary")
    failure = record.get("failure")
    return Outcome(
        local_id=str(record.get("local_id") or ""),
        status=str(record.get("status") or "pending"),
        outcome=None if outcome_text is None else str(outcome_text),
        children=tuple(
            from_record(child)
            for child in record.get("children") or ()
            if isinstance(child, dict)
        ),
        failure=None if failure is None else str(failure),
    )


def parse_outcomes_record(value: Any) -> list[Outcome]:
    """Parse a denormalized iteration ``outcomes`` field into outcomes.

    The field is a ``json.dumps`` list-of-records string. Degrades gracefully
    for legacy (pre-migration) rows whose value is free text rather than a JSON
    list: such a row renders as a single ``<task>`` carrying the legacy text.
    A list value (already-parsed) is accepted directly.
    """
    if not value:
        return []
    if isinstance(value, list):
        return [from_record(item) for item in value if isinstance(item, dict)]
    try:
        data = json.loads(value)
    except (ValueError, TypeError):
        data = None
    if not isinstance(data, list):
        return [Outcome(local_id="summary", status="success", outcome=str(value))]
    return [from_record(item) for item in data if isinstance(item, dict)]


def workflow_outcomes(
    workflow: Workflow, *, iteration_store: IterationStoreProtocol
) -> list[Outcome]:
    """Derived ``workflow.outcomes`` = the last iteration's outcomes.

    Not stored; computed from the latest iteration's denormalized ``outcomes``.
    A failure-aware iteration carries its last failed attempt's failed-task
    outcomes, so this surfaces the right result for both passing and failing
    workflows. Empty when the workflow has no closed iteration yet.
    """
    iterations = iteration_store.list_for_workflow(workflow.id)
    if not iterations:
        return []
    last = max(iterations, key=lambda it: it.sequence_no)
    return parse_outcomes_record(last.outcomes)


# ---- internals ------------------------------------------------------------


def _latest_outcome_record(records: Any) -> dict[str, Any] | None:
    if not records:
        return None
    latest = records[-1]
    return latest if isinstance(latest, dict) else None


def _outcome_text(task: dict[str, Any]) -> str:
    latest = _latest_outcome_record(task.get("outcomes"))
    if latest is None:
        return _NO_OUTCOME
    text = latest.get("outcome")
    if text is None:
        text = latest.get("summary")
    return str(text) if text is not None else _EMPTY


def _is_terminated(task: dict[str, Any]) -> bool:
    result = task.get("terminal_tool_result")
    return isinstance(result, dict) and result.get("fail_reason") == _RUN_EXHAUSTED


def _failed_task_lines(attempt: Attempt, task_store: TaskStoreProtocol | None) -> str:
    if task_store is None:
        return _NO_DETAIL
    lines: list[str] = []
    for role, task_ids in (
        ("generator", attempt.generator_task_ids),
        ("reducer", attempt.reducer_task_ids),
    ):
        for task_id in task_ids:
            task = task_store.get_task(task_id)
            if task is None or str(task.get("status") or "") not in ("failed", "blocked"):
                continue
            suffix = " (terminated)" if _is_terminated(task) else ""
            lines.append(f"{role} {local_id_of(task_id)}: {_outcome_text(task)}{suffix}")
    return "\n".join(lines) if lines else _NO_DETAIL


__all__ = [
    "EMPTY_OUTCOME_PLACEHOLDERS",
    "Outcome",
    "attempt_failure_line",
    "failed_task_outcomes",
    "from_record",
    "generator_outcomes",
    "local_id_of",
    "parse_outcomes_record",
    "present_status",
    "reducer_outcomes",
    "task_outcome_from_row",
    "to_record",
    "workflow_outcomes",
]
