"""Sandbox subsystem scenarios — OCC, overlay, layerstack, LSP, daemon.

Drive the sandbox subsystem through tool calls; assert on
``EventType.SANDBOX_*`` events emitted from tool completions and on file
content read back through the sandbox toolkit.

Implemented (reference scenarios):
- :class:`AutoSquashCommitResume`
- :class:`ComplexProjectBuild`
- :class:`ComplexProjectBuildShellEditLsp`
- :class:`ComplexProjectBuildShellEditLspSmoke`
- :class:`ComplexProjectBuildSmoke`
- :class:`OccConcurrentConflicts`
"""

from __future__ import annotations

from live_e2e.scenarios.sandbox.auto_squash_commit_resume import (
    AutoSquashCommitResume,
)
from live_e2e.scenarios.sandbox.complex_project_build import (
    ComplexProjectBuild,
    ComplexProjectBuildSmoke,
)
from live_e2e.scenarios.sandbox.complex_project_build_shell_edit_lsp import (
    ComplexProjectBuildShellEditLsp,
    ComplexProjectBuildShellEditLspSmoke,
)
from live_e2e.scenarios.sandbox.occ_concurrent_conflicts import (
    OccConcurrentConflicts,
)

__all__ = [
    "AutoSquashCommitResume",
    "ComplexProjectBuild",
    "ComplexProjectBuildShellEditLsp",
    "ComplexProjectBuildShellEditLspSmoke",
    "ComplexProjectBuildSmoke",
    "OccConcurrentConflicts",
]
