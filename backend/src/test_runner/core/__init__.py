"""``test_runner.core`` — engine seams and config contracts.

Exposes the Protocol/dataclass surface consumed by the unified
:func:`run_pipeline`: :class:`RunConfig`, :class:`RunContext`,
:class:`PipelineReport`, :class:`SandboxProvisioner` /
:class:`SandboxLease` / :class:`AttachExisting`, and the
:class:`LifecycleHooks` / :class:`NoopLifecycle` pair.
"""

from __future__ import annotations

from test_runner.core.config import RunConfig, RunContext
from test_runner.core.engine import run_pipeline
from test_runner.core.lifecycle import LifecycleHooks, NoopLifecycle
from test_runner.core.report import PipelineReport
from test_runner.core.sandbox import (
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
