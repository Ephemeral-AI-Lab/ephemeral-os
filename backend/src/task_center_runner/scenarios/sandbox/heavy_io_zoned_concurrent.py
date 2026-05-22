"""Heavy-IO zoned-lease scenario.

Five concurrent workers each run long-running shell commands (~30-50s) that
write ~100 MB total across three placement zones to characterize layerstack
lease and OCC merge behavior:

  - **gitincluded**: ``$WORKSPACE/perf_load_tracked/worker_NN`` (tracked-shape;
    not matched by the SWE-EVO repo ``.gitignore``).
  - **gitignored**: ``$WORKSPACE/build/perf_load_worker_NN`` (``build/`` is
    ignored by the SWE-EVO repo).
  - **outside**: ``/tmp/heavy_io_zoned/worker_NN`` (outside any workspace
    binding; not captured by workspace OCC).

The scenario validates that workspace_tree_bytes stays at 0 (O(1) overlay
disk) while leases are held for tens of seconds under five-way concurrency,
and that the OCC-captured ``changed_paths`` set reflects the zone semantics.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from tools.submission.evaluator import submit_evaluation_success
from tools.submission.planner import submit_plan_closes_goal

from task_center_runner.audit.events import EventType
from task_center_runner.scenarios.base import ScenarioBase, ScenarioContext, ToolCallSpec


WORKER_COUNT = 5


def _plan() -> dict[str, Any]:
    worker_ids = [f"heavy_io_zoned_worker_{index:02d}" for index in range(WORKER_COUNT)]
    tasks = [
        {"id": "heavy_io_zoned_seed", "agent_name": "executor", "deps": []},
        *(
            {"id": worker_id, "agent_name": "executor", "deps": ["heavy_io_zoned_seed"]}
            for worker_id in worker_ids
        ),
        {
            "id": "heavy_io_zoned_reconcile",
            "agent_name": "executor",
            "deps": worker_ids,
        },
    ]
    task_specs: dict[str, str] = {
        "heavy_io_zoned_seed": (
            "ACTION heavy_io_zoned_seed. Provision the shared root directories "
            "for the heavy-IO zoned lease/merge scenario across gitincluded, "
            "gitignored, and outside-workspace zones."
        ),
        "heavy_io_zoned_reconcile": (
            "ACTION heavy_io_zoned_reconcile. Aggregate per-worker fragments "
            "and verify the zoned lease/merge contract."
        ),
    }
    for index, worker_id in enumerate(worker_ids):
        task_specs[worker_id] = (
            f"ACTION heavy_io_zoned_worker index={index}. Run long-running "
            "shell write workloads across three placement zones (gitincluded "
            "vs gitignored vs outside-workspace) and record per-zone results."
        )
    return {
        "plan_spec": (
            "Seed shared directories, fan out five concurrent workers that "
            "drive long-running shell writes to three placement zones "
            "(gitincluded, gitignored, outside-workspace), then reconcile "
            "per-zone results."
        ),
        "evaluation_criteria": [
            "All five workers complete and write per-worker fragments.",
            "Each worker exercises three zones (gitincluded, gitignored, "
            "outside-workspace) with long-running shell commands.",
            "Each zone write is observable via a follow-up read after the "
            "lease is released and the OCC merge has published.",
            "Workspace OCC changed_paths reflect zone semantics: workspace "
            "zones report writes; outside-workspace shells report none.",
            "The final summary uses task_center_runner.heavy_io_zoned.v1.",
        ],
        "tasks": tasks,
        "task_specs": task_specs,
    }


class HeavyIoZonedConcurrent(ScenarioBase):
    """Long-running zoned-IO scenario for layerstack lease + OCC merge."""

    name = "sandbox.heavy_io_zoned_concurrent"
    expected_event_sequence: tuple[EventType, ...] = (
        EventType.PLANNER_INVOKED,
        EventType.PLANNER_COMPLETES_GOAL_PLAN,
        EventType.EXECUTOR_INVOKED,
        EventType.EXECUTOR_SUCCESS,
        EventType.EVALUATOR_INVOKED,
        EventType.EVALUATOR_SUCCESS,
    )

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(submit_plan_closes_goal, _plan())

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[str]:
        context_message = ctx.context_message or ctx.prompt or ""
        if "ACTION heavy_io_zoned_seed" in context_message:
            return ("heavy_io_zoned_seed",)
        if "ACTION heavy_io_zoned_reconcile" in context_message:
            return ("heavy_io_zoned_reconcile",)
        worker_index = _worker_index(context_message)
        if worker_index is not None:
            return (f"heavy_io_zoned_worker:{worker_index}",)
        return ()

    def evaluator_response(self, ctx: ScenarioContext) -> ToolCallSpec:
        return ToolCallSpec(
            submit_evaluation_success,
            {
                "summary": (
                    "Heavy-IO zoned lease/merge scenario completed with "
                    "per-worker fragments across gitincluded, gitignored, "
                    "and outside-workspace zones."
                ),
                "passed_criteria": list(ctx.attempt.evaluation_criteria),
            },
        )


def _worker_index(context_message: str) -> int | None:
    marker = "ACTION heavy_io_zoned_worker"
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


__all__ = ["HeavyIoZonedConcurrent", "WORKER_COUNT"]
