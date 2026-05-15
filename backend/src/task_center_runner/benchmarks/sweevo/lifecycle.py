"""``SweevoLifecycle`` — evaluates F2P/P2P after the run drains.

In Phase 4f the legacy ``run_sweevo_real_agent`` becomes a thin shim over
``run_pipeline``: instead of in-lining the F2P/P2P evaluation as
``run_sweevo_real_agent`` does today (lines ~195-202), the engine fires
``LifecycleHooks.after_run`` and this implementation calls
:func:`benchmarks.sweevo.evaluation.evaluate_sweevo_result` and writes the
``sweevo_result.json`` artifact.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

from benchmarks.sweevo.evaluation import evaluate_sweevo_result
from benchmarks.sweevo.models import SWEEvoInstance, SWEEvoResult
from task_center_runner.audit.io import atomic_write_json

if TYPE_CHECKING:
    from task_center_runner.audit.events import Event
    from task_center_runner.core.config import RunContext
    from task_center_runner.core.report import PipelineReport


class SweevoLifecycle:
    """``LifecycleHooks`` implementation for SWE-EVO benchmark runs."""

    def __init__(self, instance: SWEEvoInstance, *, repo_dir: str) -> None:
        self._instance = instance
        self._repo_dir = repo_dir
        self._aborted_reason: str | None = None

    async def before_run(self, ctx: "RunContext") -> None:
        return None

    def on_event(self, event: "Event") -> None:
        return None

    async def on_aborted(self, ctx: "RunContext", reason: str) -> None:
        self._aborted_reason = reason

    async def after_run(self, ctx: "RunContext", report: "PipelineReport") -> None:
        completed_cleanly = (
            report.task_center_status == "done" and not report.aborted_by_timeout
        )
        result = SWEEvoResult(
            plan_id=report.task_center_run_id,
            instance_id=self._instance.instance_id,
            status="completed" if completed_cleanly else "failed",
            duration_s=report.duration_s,
            task_count=report.task_count,
            tasks_completed=report.tasks_completed,
            tasks_failed=report.tasks_failed,
        )
        if completed_cleanly:
            result = await evaluate_sweevo_result(
                self._instance, result, report.sandbox_id, self._repo_dir
            )
        else:
            result.error = (
                "timeout"
                if report.aborted_by_timeout
                else (report.task_center_status or "unknown")
            )

        atomic_write_json(
            report.run_dir / "sweevo_result.json", dataclasses.asdict(result)
        )
        report.lifecycle_extras["sweevo_result"] = result


__all__ = ["SweevoLifecycle"]
