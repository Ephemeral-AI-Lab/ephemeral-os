"""``BenchmarkInstance`` + ``BenchmarkAdapter`` Protocols.

A benchmark adapter (e.g. SWE-EVO) supplies three things to ``run_pipeline``:

- ``build_prompt(instance, repo_dir)`` — the entry prompt for the run
- ``provisioner_for(instance)`` — the ``SandboxProvisioner`` for that instance
- ``evaluate(instance, ...)`` — invoked by the lifecycle ``after_run`` hook;
  the result is written to ``run_dir/<benchmark>_result.json`` by the adapter's
  ``LifecycleHooks`` implementation (e.g. ``SweevoLifecycle``)
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from task_center_runner.core.sandbox import SandboxProvisioner


class BenchmarkInstance(Protocol):
    """One scoring unit (e.g. a SWE-EVO repo+commit+test triple)."""

    @property
    def instance_id(self) -> str: ...


class BenchmarkAdapter(Protocol):
    """Per-benchmark glue between a data layer (e.g. benchmarks/sweevo/) and ``run_pipeline``."""

    def build_prompt(self, instance: BenchmarkInstance, *, repo_dir: str) -> str: ...

    def provisioner_for(self, instance: BenchmarkInstance) -> "SandboxProvisioner": ...

    async def evaluate(
        self,
        instance: BenchmarkInstance,
        *,
        sandbox_id: str,
        run_dir: Path,
        task_center_status: str | None,
        duration_s: float,
        task_count: int,
        tasks_completed: int,
        tasks_failed: int,
    ) -> Mapping[str, Any]: ...


__all__ = ["BenchmarkAdapter", "BenchmarkInstance"]
