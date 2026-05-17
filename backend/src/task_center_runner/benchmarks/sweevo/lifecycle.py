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
import json
from datetime import UTC, datetime
from pathlib import Path
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

    def __init__(
        self,
        instance: SWEEvoInstance,
        *,
        repo_dir: str,
        aggregate_jsonl_path: Path | None = None,
    ) -> None:
        self._instance = instance
        self._repo_dir = repo_dir
        self._aggregate_jsonl_path = aggregate_jsonl_path
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

        if self._aggregate_jsonl_path is not None:
            self._append_aggregate_line(result, report)

    def _append_aggregate_line(
        self, result: SWEEvoResult, report: "PipelineReport"
    ) -> None:
        """Append one JSON line to the aggregate JSONL.

        Atomicity: single ``write()`` on a binary-append handle relies on
        POSIX O_APPEND for sub-page atomicity. SWE-EVO lines are < 1 KB.
        """
        assert self._aggregate_jsonl_path is not None
        path = self._aggregate_jsonl_path
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "instance_id": result.instance_id,
            "run_id": report.task_center_run_id,
            "resolved": result.resolved,
            "fix_rate": result.fix_rate,
            "fail_to_pass_passed": result.fail_to_pass_passed,
            "fail_to_pass_total": result.fail_to_pass_total,
            "pass_to_pass_broken": result.pass_to_pass_broken,
            "pass_to_pass_total": result.pass_to_pass_total,
            "duration_s": result.duration_s,
            "task_count": result.task_count,
            "tasks_completed": result.tasks_completed,
            "tasks_failed": result.tasks_failed,
            "status": result.status,
            "error": result.error,
            "sandbox_id": report.sandbox_id,
            "timestamp_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        line = json.dumps(payload, separators=(",", ":")).encode() + b"\n"
        with open(path, "ab") as handle:
            handle.write(line)


__all__ = ["SweevoLifecycle"]
