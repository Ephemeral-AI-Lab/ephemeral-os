"""``task_center_runner.core`` — engine seams and config contracts.

Phase 2 of the restructure (.omc/plans/task_center_runner-restructure.md)
introduces the Protocol/dataclass surface that the unified ``run_pipeline``
will consume in Phase 4. None of these symbols are wired into the runtime
yet; the existing ``run_scenario`` / ``run_sweevo_real_agent`` paths remain
the in-use entrypoints until Phase 4 lands.
"""

from __future__ import annotations

from task_center_runner.core.config import RunConfig, RunContext
from task_center_runner.core.lifecycle import LifecycleHooks, NoopLifecycle
from task_center_runner.core.report import PipelineReport
from task_center_runner.core.sandbox import (
    AttachExisting,
    SandboxLease,
    SandboxProvisioner,
)

__all__ = [
    "AttachExisting",
    "LifecycleHooks",
    "NoopLifecycle",
    "PipelineReport",
    "RunConfig",
    "RunContext",
    "SandboxLease",
    "SandboxProvisioner",
]
