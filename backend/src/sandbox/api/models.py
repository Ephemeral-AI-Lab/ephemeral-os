"""Request/result models and shared data types for the sandbox API.

This module is the contract surface. It must not import from
``sandbox.daytona`` or ``sandbox.code_intelligence`` so the API package
can be re-implemented for any sandbox provider without dragging engine
internals along. CI engine types and provider primitives live elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol


# -- Shared identity --------------------------------------------------------

@dataclass(frozen=True, kw_only=True)
class RequestActor:
    """Caller identity threaded onto every audit-aware request.

    ``agent_id`` is the ledger attribution label and is the only required
    field; the others are populated when the runtime knows them. Keeping
    the optional fields defaulted lets call sites that have only an agent
    name still construct a valid actor.
    """

    agent_id: str
    run_id: str = ""
    agent_run_id: str = ""
    task_id: str = ""


# -- Transport-level primitives --------------------------------------------

@dataclass(frozen=True, kw_only=True)
class RawExecResult:
    """Result of a one-shot ``SandboxTransport.exec`` call."""

    exit_code: int
    stdout: str
    stderr: str = ""


@dataclass(frozen=True, kw_only=True)
class CheckedWriteSpec:
    """One file slot in a transport-level checked apply.

    ``content`` semantics:
      - ``bytes``: write or overwrite ``path`` with this payload.
      - ``None``: delete ``path``. The apply still verifies the
        expected hash before unlinking, so a delete of a file that was
        modified concurrently fails with a ``base_mismatch`` reason.

    ``expected_sha`` semantics:
      - ``str``: the file's prior content hash that the caller observed.
      - ``None``: assert the file does not exist (create-only).
    """

    path: str
    content: bytes | None
    expected_sha: str | None


@dataclass(frozen=True, kw_only=True)
class CheckedWriteResult:
    """Outcome of ``SandboxTransport.apply_diff_batch_checked``."""

    success: bool
    written_paths: tuple[str, ...] = ()
    conflict_paths: tuple[str, ...] = ()
    conflict_reason: str | None = None


@dataclass(frozen=True, kw_only=True)
class SearchMatch:
    """One result from ``SandboxTransport.search``."""

    path: str
    line: int
    column: int = 0
    preview: str = ""


@dataclass(frozen=True, kw_only=True)
class ProcessStatus:
    """Snapshot of a ``ProcessHandle`` lifecycle state."""

    running: bool
    exit_code: int | None = None


class ProcessHandle(Protocol):
    """Bidirectional handle to a long-running sandbox process.

    Serves three call sites today and any future sidecar daemon: the LSP
    transport (stdin/stdout streaming), the background shell tool
    (status/wait/kill), and code-intelligence RPC clients.
    """

    process_id: str

    async def write_stdin(self, data: bytes) -> None: ...
    async def read_stdout(self, n: int = -1) -> bytes: ...
    async def read_stderr(self, n: int = -1) -> bytes: ...
    async def status(self) -> ProcessStatus: ...
    async def kill(self) -> None: ...
    async def wait(self) -> int: ...


# -- SandboxApi: file I/O ---------------------------------------------------

@dataclass(frozen=True, kw_only=True)
class ReadFileRequest:
    path: str
    actor: RequestActor


@dataclass(frozen=True, kw_only=True)
class ReadFileResult:
    content: str
    exists: bool = True
    encoding: str = "utf-8"


@dataclass(frozen=True, kw_only=True)
class WriteFileRequest:
    path: str
    content: str
    actor: RequestActor
    description: str = ""
    overwrite: bool = True


@dataclass(frozen=True, kw_only=True)
class WriteFileResult:
    success: bool
    changed_paths: tuple[str, ...] = ()
    conflict_reason: str | None = None


@dataclass(frozen=True, kw_only=True)
class SearchReplaceEdit:
    """One exact-match replacement applied as part of an ``EditFileRequest``."""

    old_text: str
    new_text: str


@dataclass(frozen=True, kw_only=True)
class EditFileRequest:
    path: str
    edits: tuple[SearchReplaceEdit, ...]
    actor: RequestActor
    description: str = ""


@dataclass(frozen=True, kw_only=True)
class EditFileResult:
    success: bool
    changed_paths: tuple[str, ...] = ()
    applied_edits: int = 0
    conflict_reason: str | None = None


@dataclass(frozen=True, kw_only=True)
class RemoveFileRequest:
    path: str
    actor: RequestActor
    is_folder: bool = False
    description: str = ""


@dataclass(frozen=True, kw_only=True)
class RemoveFileResult:
    success: bool
    changed_paths: tuple[str, ...] = ()
    conflict_reason: str | None = None


@dataclass(frozen=True, kw_only=True)
class MoveFileRequest:
    src_path: str
    dst_path: str
    actor: RequestActor
    is_folder: bool = False
    overwrite: bool = False
    description: str = ""


@dataclass(frozen=True, kw_only=True)
class MoveFileResult:
    success: bool
    changed_paths: tuple[str, ...] = ()
    conflict_reason: str | None = None


# -- SandboxApi: search -----------------------------------------------------

@dataclass(frozen=True, kw_only=True)
class GrepMatch:
    file_path: str
    line: int
    text: str = ""


@dataclass(frozen=True, kw_only=True)
class GrepRequest:
    pattern: str
    actor: RequestActor
    path: str = "."
    timeout: int | None = 60


@dataclass(frozen=True, kw_only=True)
class GrepResult:
    matches: tuple[GrepMatch, ...] = ()
    total_matches: int | None = None
    truncated: bool = False


@dataclass(frozen=True, kw_only=True)
class GlobRequest:
    pattern: str
    actor: RequestActor
    path: str = "."
    timeout: int | None = 30


@dataclass(frozen=True, kw_only=True)
class GlobResult:
    files: tuple[str, ...] = ()


# -- SandboxApi: shell ------------------------------------------------------

@dataclass(frozen=True, kw_only=True)
class ShellRequest:
    command: str
    actor: RequestActor
    cwd: str | None = None
    timeout: int | None = None
    stdin: str | None = None
    description: str = ""
    attribute_changes: bool = True


@dataclass(frozen=True, kw_only=True)
class ShellResult:
    exit_code: int
    stdout: str
    stderr: str = ""
    changed_paths: tuple[str, ...] = ()
    ambient_changed_paths: tuple[str, ...] = ()
    audit_success: bool = True
    audit_conflict_reason: str | None = None
    git_commit_status: str | None = None
    warnings: tuple[str, ...] = ()


# -- CodeIntelligenceApi: status -------------------------------------------

@dataclass(frozen=True, kw_only=True)
class WorkspaceStatus:
    """Read-only snapshot of code intelligence readiness for a sandbox."""

    sandbox_id: str
    workspace_root: str
    initialized: bool
    symbol_index: dict[str, Any] = field(default_factory=dict)
    arbiter: dict[str, Any] = field(default_factory=dict)
    edit_buffer: dict[str, Any] = field(default_factory=dict)
    lsp: dict[str, Any] = field(default_factory=dict)
    edit_hotspots: dict[str, Any] | None = None


# -- CodeIntelligenceApi: symbols ------------------------------------------

@dataclass(frozen=True, kw_only=True)
class SymbolDefinition:
    name: str
    kind: str             # serialized SymbolKind: function|class|method|variable|...
    file_path: str
    line: int
    character: int = 0
    signature: str = ""
    container: str = ""


@dataclass(frozen=True, kw_only=True)
class SymbolReference:
    file_path: str
    line: int
    character: int = 0
    text: str = ""


@dataclass(frozen=True, kw_only=True)
class SymbolQueryRequest:
    """Query for a symbol by name or by file path.

    The implementation chooses how to interpret ``query``: a short name
    triggers a symbol search; a file path returns all symbols in that
    file. Tools rely on the file-path bootstrap, so the API surface
    accepts both modes through a single request type.
    """

    query: str
    actor: RequestActor
    kind: str = ""                 # filter by SymbolKind value, "" = any
    include_references: bool = False


SymbolQueryConfidence = Literal[
    "full",          # references resolved via LSP
    "file_symbols",  # bootstrapped from file-path query
    "unavailable",   # references requested but LSP could not serve
    "none",          # no matches
    "",              # references not requested
]


@dataclass(frozen=True, kw_only=True)
class SymbolQueryResult:
    definitions: tuple[SymbolDefinition, ...] = ()
    references: tuple[SymbolReference, ...] = ()
    total_references: int = 0
    confidence: SymbolQueryConfidence = ""
    matched_file: str | None = None     # set when query was a file path
    reference_status: str | None = None
    reference_unavailable_reason: str | None = None


@dataclass(frozen=True, kw_only=True)
class ReferencesRequest:
    file_path: str
    symbol: str
    actor: RequestActor
    line: int = 0
    character: int = 0


@dataclass(frozen=True, kw_only=True)
class ReferencesResult:
    references: tuple[SymbolReference, ...] = ()


# -- CodeIntelligenceApi: diagnostics --------------------------------------

DiagnosticSeverity = Literal["error", "warning", "information", "hint"]


@dataclass(frozen=True, kw_only=True)
class Diagnostic:
    line: int
    character: int = 0
    severity: DiagnosticSeverity = "error"
    message: str = ""
    source: str = ""
    code: str = ""


@dataclass(frozen=True, kw_only=True)
class DiagnosticsRequest:
    file_path: str
    actor: RequestActor


@dataclass(frozen=True, kw_only=True)
class DiagnosticsResult:
    diagnostics: tuple[Diagnostic, ...] = ()
    clean: bool = True


# -- CodeIntelligenceApi: workspace structure ------------------------------

WorkspaceStructureSource = Literal["index", "local", "remote", "none"]


@dataclass(frozen=True, kw_only=True)
class WorkspaceStructureRequest:
    actor: RequestActor
    path: str = ""
    max_depth: int = 3


@dataclass(frozen=True, kw_only=True)
class WorkspaceStructureResult:
    paths: tuple[str, ...] = ()
    source: WorkspaceStructureSource = "none"
    workspace_root: str = ""


__all__ = [
    "CheckedWriteResult",
    "CheckedWriteSpec",
    "Diagnostic",
    "DiagnosticSeverity",
    "DiagnosticsRequest",
    "DiagnosticsResult",
    "EditFileRequest",
    "EditFileResult",
    "GlobRequest",
    "GlobResult",
    "GrepMatch",
    "GrepRequest",
    "GrepResult",
    "MoveFileRequest",
    "MoveFileResult",
    "ProcessHandle",
    "ProcessStatus",
    "RawExecResult",
    "ReadFileRequest",
    "ReadFileResult",
    "ReferencesRequest",
    "ReferencesResult",
    "RemoveFileRequest",
    "RemoveFileResult",
    "RequestActor",
    "SearchMatch",
    "SearchReplaceEdit",
    "ShellRequest",
    "ShellResult",
    "SymbolDefinition",
    "SymbolQueryConfidence",
    "SymbolQueryRequest",
    "SymbolQueryResult",
    "SymbolReference",
    "WorkspaceStatus",
    "WorkspaceStructureRequest",
    "WorkspaceStructureResult",
    "WorkspaceStructureSource",
    "WriteFileRequest",
    "WriteFileResult",
]
