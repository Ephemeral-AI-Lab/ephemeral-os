"""High-concurrency layer-stack, overlay, and OCC pressure scenario.

This scenario fans out executor tasks after a seed task so the TaskCenter
dispatcher launches bounded public-tool traffic against the same sandbox. Each
worker performs independent write/edit/read work, and the first few workers
also race a shared OCC edit that must produce at least one conflict. The final
reconciliation task reads per-worker fragments and writes a summary artifact
for capacity/performance assertions.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from tools.submission.planner import submit_plan_closes_goal
from tools.submission.reducer import submit_reduction_success

from task_center_runner.scenarios.base import ScenarioBase, ScenarioContext, ToolCallSpec


WORKER_COUNT = 20
MAX_CONCURRENT_WORKERS = 20


def _plan() -> dict[str, Any]:
    worker_ids = [f"concurrent_worker_{index:02d}" for index in range(WORKER_COUNT)]
    tasks = [
        {"id": "concurrency_seed", "agent_name": "executor", "needs": []},
        *(
            {
                "id": worker_id,
                "agent_name": "executor",
                "needs": _worker_deps(index, worker_ids),
            }
            for index, worker_id in enumerate(worker_ids)
        ),
        {
            "id": "concurrency_reconcile",
            "agent_name": "executor",
            "needs": worker_ids,
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
            "write/edit/read workload against the sandbox; workers 0..3 also "
            "race the shared OCC conflict target."
        )
    return {
        "tasks": tasks,
        "task_specs": task_specs,
        "reducers": [
            {
                "id": "reduce",
                "needs": [
                    "concurrency_seed",
                    *worker_ids,
                    "concurrency_reconcile",
                ],
                "prompt": (
                    "Confirm all 20 workers completed and wrote fragments, the "
                    "workload never fanned out beyond 20 active sandbox tool "
                    "calls, setup and reconciliation stayed bounded, the shared "
                    "OCC target recorded at least one success and one conflict, "
                    "and the final summary uses "
                    "task_center_runner.high_concurrency.v1."
                ),
            }
        ],
    }


def _worker_deps(index: int, worker_ids: Sequence[str]) -> list[str]:
    if index < MAX_CONCURRENT_WORKERS:
        return ["concurrency_seed"]
    return [worker_ids[index - MAX_CONCURRENT_WORKERS]]


class HighConcurrencyLayerstackOverlayOcc(ScenarioBase):
    """Capacity case for concurrent layer-stack, overlay, and OCC pressure."""

    name = "sandbox.high_concurrency_layerstack_overlay_occ"

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

    def reducer_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(
            submit_reduction_success,
            {
                "outcome": (
                    "High-concurrency sandbox pressure run completed with "
                    "worker fragments, overlay capture, and OCC conflict "
                    "evidence."
                ),
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


__all__ = [
    "HighConcurrencyLayerstackOverlayOcc",
    "MAX_CONCURRENT_WORKERS",
    "WORKER_COUNT",
]
