"""Sandbox subsystem scenarios — OCC, overlay, layerstack, LSP, daemon.

Drive the sandbox subsystem through tool calls; assert on
``EventType.SANDBOX_*`` events emitted from tool completions and on file
content read back through the sandbox toolkit.

Implemented (reference scenarios):
- :class:`AutoSquashCommitResume`
- :class:`ComplexProjectBuild`
- :class:`ComplexProjectBuildGrepGlob`
- :class:`ComplexProjectBuildGrepGlobSmoke`
- :class:`ComplexProjectBuildShellEditLsp`
- :class:`ComplexProjectBuildShellEditLspSmoke`
- :class:`ComplexProjectBuildSmoke`
- :class:`HighConcurrencyLayerstackOverlayOcc`
- :class:`OccConcurrentConflicts`
"""

from __future__ import annotations

from task_center_runner.scenarios.sandbox.auto_squash_commit_resume import (
    AutoSquashCommitResume,
)
from task_center_runner.scenarios.sandbox.background_shell import (
    BackgroundShellCancel,
    BackgroundShellGolden,
    BackgroundShellInterleave,
)
from task_center_runner.scenarios.sandbox.complex_project_build import (
    ComplexProjectBuild,
    ComplexProjectBuildSmoke,
)
from task_center_runner.scenarios.sandbox.complex_project_build_grep_glob import (
    ComplexProjectBuildGrepGlob,
    ComplexProjectBuildGrepGlobSmoke,
)
from task_center_runner.scenarios.sandbox.complex_project_build_shell_edit_lsp import (
    ComplexProjectBuildShellEditLsp,
    ComplexProjectBuildShellEditLspSmoke,
)
from task_center_runner.scenarios.sandbox.heavy_io_zoned_concurrent import (
    HeavyIoZonedConcurrent,
)
from task_center_runner.scenarios.sandbox.high_concurrency_layerstack_overlay_occ import (
    HighConcurrencyLayerstackOverlayOcc,
)
from task_center_runner.scenarios.sandbox.occ_concurrent_conflicts import (
    OccConcurrentConflicts,
)

__all__ = [
    "AutoSquashCommitResume",
    "BackgroundShellCancel",
    "BackgroundShellGolden",
    "BackgroundShellInterleave",
    "ComplexProjectBuild",
    "ComplexProjectBuildGrepGlob",
    "ComplexProjectBuildGrepGlobSmoke",
    "ComplexProjectBuildShellEditLsp",
    "ComplexProjectBuildShellEditLspSmoke",
    "ComplexProjectBuildSmoke",
    "HeavyIoZonedConcurrent",
    "HighConcurrencyLayerstackOverlayOcc",
    "OccConcurrentConflicts",
]
