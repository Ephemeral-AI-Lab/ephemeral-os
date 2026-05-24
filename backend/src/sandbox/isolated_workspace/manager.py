"""Compatibility facade for the isolated workspace pipeline."""

from sandbox.isolated_workspace._runtime import _LinuxRuntime
from sandbox.isolated_workspace._types import (
    AuditSink,
    IsolatedWorkspaceError,
    IsolatedWorkspaceHandle,
    LayerSnapshotLike,
    LayerStackPort,
    _ManagerConfig,
    _PHASE_TIMER_OVERHEAD_BUDGET_MS,
    _PhaseTimer,
)
from sandbox.isolated_workspace.pipeline import (
    IsolatedPipeline,
    IsolatedWorkspaceManager,
    get_active_pipeline,
    require_arg,
    require_manager,
    require_pipeline,
    set_manager,
    set_pipeline,
)

__all__ = [
    "AuditSink",
    "IsolatedPipeline",
    "IsolatedWorkspaceError",
    "IsolatedWorkspaceHandle",
    "IsolatedWorkspaceManager",
    "LayerSnapshotLike",
    "LayerStackPort",
    "_LinuxRuntime",
    "_ManagerConfig",
    "_PHASE_TIMER_OVERHEAD_BUDGET_MS",
    "_PhaseTimer",
    "get_active_pipeline",
    "require_arg",
    "require_manager",
    "require_pipeline",
    "set_manager",
    "set_pipeline",
]
