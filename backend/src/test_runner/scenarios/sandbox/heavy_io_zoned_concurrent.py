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

from tools.submission.planner import submit_planner_outcome
from tools.submission.reducer import submit_reducer_outcome

from test_runner.scenarios.base import ScenarioBase, ScenarioContext, ToolCallSpec


WORKER_COUNT = 5


def _plan() -> dict[str, Any]:
    worker_ids = [f"heavy_io_zoned_worker_{index:02d}" for index in range(WORKER_COUNT)]
    tasks = [
        {"id": "heavy_io_zoned_seed", "agent_name": "executor", "needs": []},
        *(
            {"id": worker_id, "agent_name": "executor", "needs": ["heavy_io_zoned_seed"]}
            for worker_id in worker_ids
        ),
        {
            "id": "heavy_io_zoned_reconcile",
            "agent_name": "executor",
            "needs": worker_ids,
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
        "tasks": tasks,
        "task_specs": task_specs,
        "reducers": [
            {
                "id": "reduce",
                "needs": [
                    "heavy_io_zoned_seed",
                    *worker_ids,
                    "heavy_io_zoned_reconcile",
                ],
                "prompt": (
                    "Confirm all five workers wrote per-worker fragments across "
                    "the three placement zones, each zone write was observable "
                    "after the lease released and the OCC merge published, "
                    "changed_paths reflected zone semantics, and the final "
                    "summary uses test_runner.heavy_io_zoned.v1."
                ),
            }
        ],
    }


class HeavyIoZonedConcurrent(ScenarioBase):
    """Long-running zoned-IO scenario for layerstack lease + OCC merge."""

    name = "sandbox.heavy_io_zoned_concurrent"

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(submit_planner_outcome, _plan())

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[str]:
        instruction = ctx.instruction or ctx.prompt or ""
        if "ACTION heavy_io_zoned_seed" in instruction:
            return ("heavy_io_zoned_seed",)
        if "ACTION heavy_io_zoned_reconcile" in instruction:
            return ("heavy_io_zoned_reconcile",)
        worker_index = _worker_index(instruction)
        if worker_index is not None:
            return (f"heavy_io_zoned_worker:{worker_index}",)
        return ()

    def reducer_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(
            submit_reducer_outcome,
            {
                "status": "success",
                "outcome": (
                    "Heavy-IO zoned lease/merge scenario completed with "
                    "per-worker fragments across gitincluded, gitignored, "
                    "and outside-workspace zones."
                ),
            },
        )


def _worker_index(instruction: str) -> int | None:
    marker = "ACTION heavy_io_zoned_worker"
    if marker not in instruction:
        return None
    for token in instruction.split():
        if not token.startswith("index="):
            continue
        raw = token.split("=", 1)[1].rstrip(".,;")
        try:
            return int(raw)
        except ValueError:
            return None
    return None


__all__ = ["HeavyIoZonedConcurrent", "WORKER_COUNT"]
