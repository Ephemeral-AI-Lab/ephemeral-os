"""Tool-facing semantic queries for code intelligence.

Backed in phase 1 by the existing backend-hosted ``CodeIntelligenceService``
through a thin async wrapper. In phase 2 the same Protocol is served by an
in-sandbox sidecar daemon — tools and the wider runtime do not change.
"""

from __future__ import annotations

from typing import Protocol

from sandbox.api.models import (
    DiagnosticsRequest,
    DiagnosticsResult,
    ReferencesRequest,
    ReferencesResult,
    SymbolQueryRequest,
    SymbolQueryResult,
    WorkspaceStatus,
    WorkspaceStructureRequest,
    WorkspaceStructureResult,
)


class CodeIntelligenceApi(Protocol):
    """Read-only semantic queries over a sandbox workspace.

    No raw attribute access is exposed; the legacy ``svc.symbol_index``
    pattern is replaced by ``workspace_structure(...)``. Tools never
    reach into engine internals.
    """

    name: str

    async def status(self, sandbox_id: str) -> WorkspaceStatus: ...

    async def query_symbols(
        self, sandbox_id: str, request: SymbolQueryRequest,
    ) -> SymbolQueryResult: ...

    async def find_references(
        self, sandbox_id: str, request: ReferencesRequest,
    ) -> ReferencesResult: ...

    async def diagnostics(
        self, sandbox_id: str, request: DiagnosticsRequest,
    ) -> DiagnosticsResult: ...

    async def workspace_structure(
        self, sandbox_id: str, request: WorkspaceStructureRequest,
    ) -> WorkspaceStructureResult: ...


__all__ = ["CodeIntelligenceApi"]
