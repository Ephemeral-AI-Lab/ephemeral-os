"""``task_center_runner.core`` — engine seams and config contracts.

Exposes the Protocol/dataclass surface consumed by the unified
:func:`run_pipeline`: :class:`RunConfig`, :class:`RunContext`,
:class:`PipelineReport`, :class:`SandboxProvisioner` /
:class:`SandboxLease` / :class:`AttachExisting`, and the
:class:`LifecycleHooks` / :class:`NoopLifecycle` pair.
"""

from __future__ import annotations

from task_center_runner.core.config import RunConfig, RunContext
from task_center_runner.core.engine import run_pipeline
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
    "run_pipeline",
]
