"""Attempt budget exhausted — every attempt fails, workflow closes failed.

The default ``TaskCenterLifecycleConfig.default_attempt_budget`` is ``2``
(``backend/src/task_center/config.py:16``). This scenario plans a single
generator task that **always** calls ``submit_generator_outcome(status="failed", ...)``, so each
attempt closes ``status=failed``, ``fail_reason="task_failed"``. After
attempt 2 fails, ``IterationAttemptCoordinator.has_budget_remaining`` is False — iteration
closes failed, and the workflow lifecycle closes the workflow failed.

Asserts: 1 workflow (status=failed), 1 iteration (status=failed), exactly 2
attempts each with ``fail_reason=task_failed``,
``EXECUTOR_FAILURE`` appears twice in the event sequence, and no reducer task
ever reaches ``done`` (the reducer's only generator never completes, so the
reducer stays pending).

This is the canonical "max-retry" coverage; reuse the pattern when adding
scenarios that exercise budget-exhaustion under other task-failure retry paths.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from tools.submission.planner import submit_planner_outcome
from tools.submission.reducer import submit_reducer_outcome

from task_center_runner.scenarios.base import ScenarioBase, ScenarioContext, ToolCallSpec


def _always_fail_plan() -> dict[str, Any]:
    return {
        "tasks": [
            {"id": "always_fail", "agent_name": "executor", "needs": []},
        ],
        "task_specs": {
            "always_fail": "Intentionally fail this generator task.",
        },
        "reducers": [
            {
                "id": "reduce",
                "needs": ["always_fail"],
                "prompt": "Confirm the generator task completed (never reached).",
            }
        ],
    }


class AttemptBudgetExhausted(ScenarioBase):
    """Every attempt fails — budget exhaustion closes the workflow failed."""

    name = "pipeline.attempt_budget_exhausted"

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(submit_planner_outcome, _always_fail_plan())

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[str]:  # noqa: ARG002
        return ("fail:Intentional generator failure to exhaust the attempt budget.",)

    def reducer_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        # Should never be invoked — the generator never reaches DONE, so the
        # reducer's need is never satisfied. Implementation exists only to
        # satisfy the protocol.
        return ToolCallSpec(
            submit_reducer_outcome,
            {
                "status": "failed",
                "outcome": "Unexpected reducer invocation; no generator ever DONE.",
            },
        )


__all__ = ["AttemptBudgetExhausted"]
