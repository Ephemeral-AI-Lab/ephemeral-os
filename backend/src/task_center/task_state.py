"""TaskCenter attempt task roles, statuses, and submission DTOs."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal


class TaskCenterTaskRole(StrEnum):
    PLANNER = "planner"
    GENERATOR = "generator"
    EVALUATOR = "evaluator"
    ENTRY_EXECUTOR = "entry_executor"


class SpawnReason(StrEnum):
    """Why a task row was created. Replaces free-form spawn_reason strings."""

    ATTEMPT_PLANNER = "attempt_planner"
    ATTEMPT_GENERATOR = "attempt_generator"
    ATTEMPT_EVALUATOR = "attempt_evaluator"
    ENTRY_EXECUTOR = "entry_executor"


class TaskCenterTaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_MISSION = "waiting_mission"
    DONE = "done"
    FAILED = "failed"
    BLOCKED = "blocked"


TERMINAL_GENERATOR_STATUSES: frozenset[TaskCenterTaskStatus] = frozenset(
    {
        TaskCenterTaskStatus.DONE,
        TaskCenterTaskStatus.FAILED,
        TaskCenterTaskStatus.BLOCKED,
    }
)


@dataclass(frozen=True, slots=True)
class PlannedGeneratorTask:
    """One normalized generator DAG node."""

    local_id: str
    agent_name: str
    deps: tuple[str, ...]
    task_spec: str


@dataclass(frozen=True, slots=True)
class PlannerSubmission:
    """Validated planner submission from a full or partial plan tool."""

    attempt_id: str
    planner_task_id: str
    kind: Literal["full", "partial"]
    task_specification: str
    evaluation_criteria: tuple[str, ...]
    tasks: tuple[PlannedGeneratorTask, ...]
    continuation_goal: str | None
    summary: str


@dataclass(frozen=True, slots=True)
class PlannerFailureSubmission:
    """Runtime-synthesized planner failure."""

    attempt_id: str
    planner_task_id: str
    fail_reason: Literal["run_exhausted"]
    summary: str


@dataclass(frozen=True, slots=True)
class GeneratorSubmission:
    """Validated terminal outcome for one generator task.

    ``payload`` retains ``dict[str, Any]`` shape for backward compatibility
    with persisted task-summary rows and the submission-tool surface.
    Callers building payloads from typed sources should use one of the
    :class:`GeneratorPayload` schemas below and call ``.to_dict()`` so the
    expected keys and field types are visible at the type level.
    """

    attempt_id: str
    task_id: str
    outcome: Literal["success", "failure"]
    summary: str
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class EvaluatorSubmission:
    """Validated terminal outcome for one evaluator task.

    See :class:`GeneratorSubmission` regarding ``payload`` typing. Build
    typed payloads via :class:`EvaluatorPayload` schemas below.
    """

    attempt_id: str
    task_id: str
    outcome: Literal["success", "failure"]
    summary: str
    payload: dict[str, Any]


# ---- Typed submission payload schemas --------------------------------------
#
# These dataclasses document the expected ``payload`` shape for each
# submission outcome. Tools and consumers can opt into typed construction
# and serialization via ``.to_dict()`` while the persistence and tool-call
# protocols continue to accept ``dict[str, Any]``. Adding a new payload
# shape is a new class here plus a new ``to_dict`` mapping — no scattered
# string-key additions.


@dataclass(frozen=True, slots=True)
class ExecutorSuccessPayload:
    """Payload for a generator task completed via ``submit_execution_success``."""

    artifacts: tuple[str, ...] = ()
    generator_role: Literal["executor"] = "executor"

    def to_dict(self) -> dict[str, Any]:
        return {
            "generator_role": self.generator_role,
            "artifacts": list(self.artifacts),
        }


@dataclass(frozen=True, slots=True)
class ExecutorFailurePayload:
    """Payload for ``submit_execution_failure``."""

    reason: str
    details: tuple[str, ...] = ()
    generator_role: Literal["executor"] = "executor"

    def to_dict(self) -> dict[str, Any]:
        return {
            "generator_role": self.generator_role,
            "reason": self.reason,
            "details": list(self.details),
        }


@dataclass(frozen=True, slots=True)
class VerifierSuccessPayload:
    """Payload for ``submit_verification_success``."""

    checks: tuple[str, ...] = ()
    generator_role: Literal["verifier"] = "verifier"

    def to_dict(self) -> dict[str, Any]:
        return {
            "generator_role": self.generator_role,
            "checks": list(self.checks),
        }


@dataclass(frozen=True, slots=True)
class VerifierFailurePayload:
    """Payload for ``submit_verification_failure``."""

    reason: str
    details: tuple[str, ...] = ()
    generator_role: Literal["verifier"] = "verifier"

    def to_dict(self) -> dict[str, Any]:
        return {
            "generator_role": self.generator_role,
            "reason": self.reason,
            "details": list(self.details),
        }


@dataclass(frozen=True, slots=True)
class RunExhaustedPayload:
    """Payload synthesized by the launcher when an agent run ends without
    a terminal submission."""

    fail_reason: Literal["run_exhausted"] = "run_exhausted"

    def to_dict(self) -> dict[str, Any]:
        return {"fail_reason": self.fail_reason}


@dataclass(frozen=True, slots=True)
class EvaluationSuccessPayload:
    """Payload for ``submit_evaluation_success``."""

    passed_criteria: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"passed_criteria": list(self.passed_criteria)}


@dataclass(frozen=True, slots=True)
class EvaluationFailurePayload:
    """Payload for ``submit_evaluation_failure``."""

    failed_criteria: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"failed_criteria": list(self.failed_criteria)}


# Discriminated unions for callers that want type-level dispatch.
GeneratorPayload = (
    ExecutorSuccessPayload
    | ExecutorFailurePayload
    | VerifierSuccessPayload
    | VerifierFailurePayload
    | RunExhaustedPayload
)
EvaluatorPayload = (
    EvaluationSuccessPayload
    | EvaluationFailurePayload
    | RunExhaustedPayload
)
