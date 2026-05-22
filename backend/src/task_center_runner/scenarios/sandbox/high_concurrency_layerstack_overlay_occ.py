"""High-concurrency layer-stack, overlay, and OCC pressure scenario.

This scenario fans out many executor tasks after a seed task so the TaskCenter
dispatcher launches concurrent public-tool traffic against the same sandbox.
Each worker performs independent write/edit/read/shell work, and the first few
workers also race a shared OCC edit that must produce at least one conflict.
The final reconciliation task reads per-worker fragments and writes a summary
artifact for capacity/performance assertions.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from tools.submission.evaluator import submit_evaluation_success
from tools.submission.planner import submit_plan_closes_goal

from task_center_runner.audit.events import EventType
from task_center_runner.scenarios.base import ScenarioBase, ScenarioContext, ToolCallSpec


WORKER_COUNT = 20


def _plan() -> dict[str, Any]:
    worker_ids = [f"concurrent_worker_{index:02d}" for index in range(WORKER_COUNT)]
    tasks = [
        {"id": "concurrency_seed", "agent_name": "executor", "deps": []},
        *(
            {"id": worker_id, "agent_name": "executor", "deps": ["concurrency_seed"]}
            for worker_id in worker_ids
        ),
        {
            "id": "concurrency_reconcile",
            "agent_name": "executor",
            "deps": worker_ids,
        },
    ]
    task_specs = {
        "concurrency_seed": (
            "ACTION high_concurrency_seed. Initialize the shared OCC conflict "
            "target and control files for the high-concurrency sandbox run."
        ),
        "concurrency_reconcile": (
            "ACTION high_concurrency_reconcile. Read every per-worker fragment, "
            "assert the expected conflict/success mix, and write summary.json."
        ),
    }
    for index, worker_id in enumerate(worker_ids):
        task_specs[worker_id] = (
            f"ACTION high_concurrency_worker index={index}. Run an independent "
            "write/edit/read/shell workload against the sandbox; workers 0..3 "
            "also race the shared OCC conflict target."
        )
    return {
        "plan_spec": (
            "Seed one shared OCC target, launch 20 concurrent executor workers "
            "that pressure layer-stack commits and overlay capture, then "
            "reconcile all fragments into a capacity summary."
        ),
        "evaluation_criteria": [
            "All 20 concurrent workers complete and write fragments.",
            "Layer-stack commit depth crosses the auto-squash threshold during "
            "the concurrent workload.",
            "Overlay shell operations are captured for every worker.",
            "The shared OCC target records at least one success and one conflict.",
            "The final summary uses task_center_runner.high_concurrency.v1.",
        ],
        "tasks": tasks,
        "task_specs": task_specs,
    }


class HighConcurrencyLayerstackOverlayOcc(ScenarioBase):
    """Capacity case for concurrent layer-stack, overlay, and OCC pressure."""

    name = "sandbox.high_concurrency_layerstack_overlay_occ"
    expected_event_sequence: tuple[EventType, ...] = (
        EventType.PLANNER_INVOKED,
        EventType.PLANNER_COMPLETES_GOAL_PLAN,
        EventType.EXECUTOR_INVOKED,
        EventType.SANDBOX_CONFLICT_DETECTED,
        EventType.EXECUTOR_SUCCESS,
        EventType.EVALUATOR_INVOKED,
        EventType.EVALUATOR_SUCCESS,
    )

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(submit_plan_closes_goal, _plan())

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[str]:
        context_message = ctx.context_message or ctx.prompt or ""
        if "ACTION high_concurrency_seed" in context_message:
            return ("high_concurrency_seed",)
        if "ACTION high_concurrency_reconcile" in context_message:
            return ("high_concurrency_reconcile",)
        worker_index = _worker_index(context_message)
        if worker_index is not None:
            return (f"high_concurrency_worker:{worker_index}",)
        return ()

    def evaluator_response(self, ctx: ScenarioContext) -> ToolCallSpec:
        return ToolCallSpec(
            submit_evaluation_success,
            {
                "summary": (
                    "High-concurrency sandbox pressure run completed with "
                    "worker fragments, overlay capture, and OCC conflict "
                    "evidence."
                ),
                "passed_criteria": list(ctx.attempt.evaluation_criteria),
            },
        )


def _worker_index(context_message: str) -> int | None:
    marker = "ACTION high_concurrency_worker"
    if marker not in context_message:
        return None
    for token in context_message.split():
        if not token.startswith("index="):
            continue
        raw = token.split("=", 1)[1].rstrip(".,;")
        try:
            return int(raw)
        except ValueError:
            return None
    return None


__all__ = ["HighConcurrencyLayerstackOverlayOcc", "WORKER_COUNT"]
