"""Provider-neutral sandbox API contract.

Three Protocols, one shared model module, and an error hierarchy. See
``docs/architecture/sandbox-api-adapter-migration-plan.md`` for the full
layering and dependency rules.
"""

from __future__ import annotations

from sandbox.api.audited_sandbox_api import AuditedSandboxApi
from sandbox.api.code_intelligence_api import CodeIntelligenceApi
from sandbox.api.code_intelligence_impl import SvcCodeIntelligence
from sandbox.api.errors import (
    SandboxApiError,
    SandboxConflictError,
    SandboxNotFoundError,
    SandboxTimeoutError,
    SandboxTransportError,
)
from sandbox.api.models import (
    CheckedWriteResult,
    CheckedWriteSpec,
    EditFileRequest,
    EditFileResult,
    RawExecResult,
    ReadFileRequest,
    ReadFileResult,
    RequestActor,
    SearchReplaceEdit,
    ShellRequest,
    ShellResult,
    SymbolDefinition,
    SymbolQueryConfidence,
    SymbolQueryRequest,
    SymbolQueryResult,
    WorkspaceStatus,
    WorkspaceStructureRequest,
    WorkspaceStructureResult,
    WorkspaceStructureSource,
    WriteFileRequest,
    WriteFileResult,
)
from sandbox.api.sandbox_api import SandboxApi
from sandbox.api.transport import SandboxTransport

__all__ = [
    "AuditedSandboxApi",
    "CheckedWriteResult",
    "CheckedWriteSpec",
    "CodeIntelligenceApi",
    "EditFileRequest",
    "EditFileResult",
    "RawExecResult",
    "ReadFileRequest",
    "ReadFileResult",
    "RequestActor",
    "SandboxApi",
    "SandboxApiError",
    "SandboxConflictError",
    "SandboxNotFoundError",
    "SandboxTimeoutError",
    "SandboxTransport",
    "SandboxTransportError",
    "SearchReplaceEdit",
    "ShellRequest",
    "ShellResult",
    "SvcCodeIntelligence",
    "SymbolDefinition",
    "SymbolQueryConfidence",
    "SymbolQueryRequest",
    "SymbolQueryResult",
    "WorkspaceStatus",
    "WorkspaceStructureRequest",
    "WorkspaceStructureResult",
    "WorkspaceStructureSource",
    "WriteFileRequest",
    "WriteFileResult",
]
