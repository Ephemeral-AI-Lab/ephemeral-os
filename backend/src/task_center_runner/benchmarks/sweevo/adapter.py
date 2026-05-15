"""``SWEEvoBenchmark`` — :class:`BenchmarkAdapter` for SWE-EVO instances.

The adapter implements the three Protocol methods documented in
``task_center_runner.benchmarks.base``:

- ``build_prompt`` — re-exports ``benchmarks.sweevo.prompt.build_sweevo_user_prompt``
- ``provisioner_for`` — returns a :class:`SweevoProvisioner` for the instance
- ``evaluate`` — invokes :func:`benchmarks.sweevo.evaluation.evaluate_sweevo_result`
  and returns its asdict so the lifecycle can persist it
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from benchmarks.sweevo.evaluation import evaluate_sweevo_result
from benchmarks.sweevo.models import SWEEvoInstance, SWEEvoResult
from task_center_runner.benchmarks.base import BenchmarkAdapter
from task_center_runner.benchmarks.sweevo.prompt import build_sweevo_user_prompt
from task_center_runner.benchmarks.sweevo.provisioner import SweevoProvisioner
from task_center_runner.core.sandbox import SandboxProvisioner


class SWEEvoBenchmark(BenchmarkAdapter):
    """SWE-EVO :class:`BenchmarkAdapter` implementation.

    The adapter does not own sandbox creation — it expects a pre-created
    Daytona sandbox id to be supplied alongside each instance. Sandbox
    creation lives in ``benchmarks.sweevo.__main__`` until a follow-up
    milestone hoists it into the adapter.
    """

    def __init__(self, *, repo_dir: str, sandbox_id_for: Mapping[str, str]) -> None:
        self._repo_dir = repo_dir
        self._sandbox_id_for = dict(sandbox_id_for)

    def build_prompt(self, instance: SWEEvoInstance, *, repo_dir: str) -> str:
        return build_sweevo_user_prompt(instance, repo_dir=repo_dir)

    def provisioner_for(self, instance: SWEEvoInstance) -> SandboxProvisioner:
        try:
            sandbox_id = self._sandbox_id_for[instance.instance_id]
        except KeyError as exc:
            raise KeyError(
                f"SWEEvoBenchmark has no sandbox id registered for instance "
                f"{instance.instance_id!r}; register one via the sandbox_id_for "
                f"mapping or via a follow-up milestone that hoists sandbox "
                f"creation into the adapter."
            ) from exc
        return SweevoProvisioner(instance, sandbox_id, repo_dir=self._repo_dir)

    async def evaluate(
        self,
        instance: SWEEvoInstance,
        *,
        sandbox_id: str,
        run_dir: Path,
        task_center_status: str | None,
        duration_s: float,
        task_count: int,
        tasks_completed: int,
        tasks_failed: int,
    ) -> Mapping[str, Any]:
        completed_cleanly = task_center_status == "done"
        result = SWEEvoResult(
            plan_id=run_dir.name,
            instance_id=instance.instance_id,
            status="completed" if completed_cleanly else "failed",
            duration_s=duration_s,
            task_count=task_count,
            tasks_completed=tasks_completed,
            tasks_failed=tasks_failed,
        )
        if completed_cleanly:
            result = await evaluate_sweevo_result(
                instance, result, sandbox_id, self._repo_dir
            )
        else:
            result.error = task_center_status or "unknown"
        return dataclasses.asdict(result)


__all__ = ["SWEEvoBenchmark"]
