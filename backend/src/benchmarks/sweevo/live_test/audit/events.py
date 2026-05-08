"""EventType enum + Event dataclass for the in-memory audit bus.

Per plan §8. Events live in-memory only — they drive the LifecycleObserver,
HookSet, and metrics aggregator. There is no persisted ``events.jsonl``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from benchmarks.sweevo.live_test.audit.node_id import NodeId


class EventType(StrEnum):
    """All audit event kinds. Plan §8."""

    # task center lifecycle
    RUN_STARTED = "run_started"
    RUN_COMPLETED = "run_completed"
    MISSION_STARTED = "mission_started"
    MISSION_COMPLETED = "mission_completed"
    MISSION_REQUESTED = "mission_requested"
    EPISODE_STARTED = "episode_started"
    EPISODE_COMPLETED = "episode_completed"
    EPISODE_CONTINUATION_CREATED = "episode_continuation_created"
    ATTEMPT_STARTED = "attempt_started"
    ATTEMPT_PASSED = "attempt_passed"
    ATTEMPT_FAILED = "attempt_failed"

    # agent invocations
    PLANNER_INVOKED = "planner_invoked"
    PLANNER_FULL_PLAN = "planner_full_plan"
    PLANNER_PARTIAL_PLAN = "planner_partial_plan"
    PLANNER_REPLAN = "planner_replan"
    EXECUTOR_INVOKED = "executor_invoked"
    EXECUTOR_SUCCESS = "executor_success"
    EXECUTOR_FAILURE = "executor_failure"
    EVALUATOR_INVOKED = "evaluator_invoked"
    EVALUATOR_SUCCESS = "evaluator_success"
    EVALUATOR_FAILURE = "evaluator_failure"
    ENTRY_EXECUTOR_INVOKED = "entry_executor_invoked"

    # tools
    TOOL_CALL_STARTED = "tool_call_started"
    TOOL_CALL_COMPLETED = "tool_call_completed"
    TOOL_CALL_ERROR = "tool_call_error"

    # sandbox-derived
    SANDBOX_WRITE_COMMITTED = "sandbox_write_committed"
    SANDBOX_EDIT_COMMITTED = "sandbox_edit_committed"
    SANDBOX_SHELL_COMMITTED = "sandbox_shell_committed"
    SANDBOX_BATCH_EDIT_APPLIED = "sandbox_batch_edit_applied"
    SANDBOX_CONFLICT_DETECTED = "sandbox_conflict_detected"
    SANDBOX_LAYER_GROWN = "sandbox_layer_grown"  # producer deferred (see squash_detection.md)
    SANDBOX_SQUASH_TRIGGERED = "sandbox_squash_triggered"  # producer deferred

    # hook synthetic
    HOOK_INJECTED_FAILURE = "hook_injected_failure"
    HOOK_ASSERTED = "hook_asserted"


@dataclass(frozen=True, slots=True)
class Event:
    """One audit event."""

    type: EventType
    node: NodeId
    payload: dict[str, Any] = field(default_factory=dict)
    correlation_id: str | None = None
    ts: datetime = field(default_factory=lambda: datetime.now(UTC))


__all__ = ["Event", "EventType"]
