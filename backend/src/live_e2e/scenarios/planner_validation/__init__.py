"""Planner submission rejection scenarios.

Each scenario emits an invalid ``submit_full_plan`` / ``submit_partial_plan``
and asserts the attempt closes with ``fail_reason="planner_failed"``, no
generator/evaluator ran, and the right ``TaskCenterInvariantViolation`` was
surfaced.

Implemented (reference scenarios):
- :class:`PlannerDuplicateLocalId`
"""

from __future__ import annotations

from live_e2e.scenarios.planner_validation.duplicate_local_id import (
    PlannerDuplicateLocalId,
)

__all__ = ["PlannerDuplicateLocalId"]
