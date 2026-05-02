"""Per-sandbox :class:`CodeIntelligenceService` facade.

The facade delegates every public op to a :class:`CiBackend` selected at
construction time. With ``EOS_CI_IN_SANDBOX`` unset (or no transport
available) the default backend is :class:`InProcessCiBackend` — today's
in-process logic, bit-for-bit. With the flag on plus a transport and
sandbox id, :class:`RpcCiBackend` is selected for the in-sandbox indexing
path.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Sequence
from typing import Any

from sandbox.api.transport import SandboxTransport
from sandbox.code_intelligence.backend import (
    CiBackend,
    InProcessCiBackend,
    RpcCiBackend,
)
from sandbox.code_intelligence.core.types import (
    CITelemetry,
    DeleteSpec,
    Diagnostic,
    EditRequest,
    EditResult,
    EditSpec,
    HoverResult,
    MoveSpec,
    OperationChange,
    OperationResult,
    ReferenceInfo,
    SymbolInfo,
    WriteSpec,
)

__all__ = ["CodeIntelligenceService"]

logger = logging.getLogger(__name__)


def _select_backend(
    sandbox_id: str,
    workspace_root: str,
    sandbox: Any,
    *,
    transport: SandboxTransport | None,
    edit_history: Any | None = None,
    symbol_index_persistence: Any | None = None,
) -> CiBackend:
    """Pick a backend based on the EOS_CI_IN_SANDBOX flag, transport, and id.

    Phase 5 default flip: returns :class:`RpcCiBackend` whenever a transport
    AND a non-empty ``sandbox_id`` are present, UNLESS ``EOS_CI_IN_SANDBOX=0``
    is set (the explicit backout knob). The flag's other values (``"1"``,
    unset) all select the daemon path. Local sandboxless flows
    (no transport / empty sandbox_id) keep using :class:`InProcessCiBackend`.

    ``edit_history`` and ``symbol_index_persistence`` are only meaningful for
    the in-process backend (the daemon owns the canonical SQLite ledger and
    SQLite IndexStore when the RPC backend is in use).
    """
    flag = os.environ.get("EOS_CI_IN_SANDBOX")
    backout = flag == "0"
    use_daemon = (
        not backout
        and transport is not None
        and sandbox_id != ""
    )
    if use_daemon:
        assert transport is not None  # narrow for type-checker
        return RpcCiBackend(
            sandbox_id=sandbox_id,
            workspace_root=workspace_root,
            transport=transport,
        )
    return InProcessCiBackend(
        sandbox_id=sandbox_id,
        workspace_root=workspace_root,
        sandbox=sandbox,
        transport=transport,
        edit_history=edit_history,
        symbol_index_persistence=symbol_index_persistence,
    )


class CodeIntelligenceService:
    """Thin facade that forwards every public op to a selected :class:`CiBackend`."""

    def __init__(
        self,
        sandbox_id: str,
        workspace_root: str = "/workspace",
        sandbox: Any = None,
        *,
        transport: SandboxTransport | None = None,
        edit_history: Any | None = None,
        symbol_index_persistence: Any | None = None,
    ) -> None:
        self._impl: CiBackend = _select_backend(
            sandbox_id,
            workspace_root,
            sandbox,
            transport=transport,
            edit_history=edit_history,
            symbol_index_persistence=symbol_index_persistence,
        )

    # -- Identity / state forwarding -----------------------------------------

    @property
    def sandbox_id(self) -> str:
        return self._impl.sandbox_id

    @property
    def workspace_root(self) -> str:
        return self._impl.workspace_root

    @property
    def is_initialized(self) -> bool:
        return self._impl.is_initialized

    # -- Internal-component pass-through (load-bearing for callers) ----------
    # workspace.py, code_intelligence_api.py, and several tests read these
    # attributes directly. They forward to the in-process impl; the daemon
    # backend will surface equivalents in a future phase.

    @property
    def symbol_index(self) -> Any:
        return self._impl.symbol_index  # type: ignore[attr-defined]

    @symbol_index.setter
    def symbol_index(self, value: Any) -> None:
        self._impl.symbol_index = value  # type: ignore[attr-defined]

    @property
    def arbiter(self) -> Any:
        return self._impl.arbiter  # type: ignore[attr-defined]

    @property
    def time_machine(self) -> Any:
        return self._impl.time_machine  # type: ignore[attr-defined]

    @property
    def patcher(self) -> Any:
        return self._impl.patcher  # type: ignore[attr-defined]

    @property
    def lsp_client(self) -> Any:
        return self._impl.lsp_client  # type: ignore[attr-defined]

    @lsp_client.setter
    def lsp_client(self, value: Any) -> None:
        self._impl.lsp_client = value  # type: ignore[attr-defined]

    @property
    def _content(self) -> Any:
        return self._impl._content  # type: ignore[attr-defined]

    @property
    def _write_coordinator(self) -> Any:
        return self._impl._write_coordinator  # type: ignore[attr-defined]

    @property
    def _mutations(self) -> Any:
        return self._impl._mutations  # type: ignore[attr-defined]

    @property
    def _command_executor(self) -> Any:
        return self._impl._command_executor  # type: ignore[attr-defined]

    @property
    def _sandbox(self) -> Any:
        return getattr(self._impl, "_sandbox", None)

    @property
    def _transport(self) -> SandboxTransport | None:
        return getattr(self._impl, "_transport", None)

    # -- Public API forwarding -----------------------------------------------

    def ensure_initialized(self, wait: bool = True) -> bool:
        return self._impl.ensure_initialized(wait=wait)

    def warmup(self) -> None:
        self._impl.warmup()

    def rebind_sandbox(self, sandbox: Any) -> None:
        self._impl.rebind_sandbox(sandbox)

    async def cmd(self, sandbox: Any, command: str, **kwargs: Any) -> Any:
        return await self._impl.cmd(sandbox, command, **kwargs)

    def find_definitions(
        self,
        file_path: str,
        symbol: str,
        line: int = 0,
        character: int = 0,
    ) -> list[SymbolInfo]:
        return self._impl.find_definitions(file_path, symbol, line, character)

    def find_references(
        self,
        file_path: str,
        symbol: str,
        line: int = 0,
        character: int = 0,
    ) -> list[ReferenceInfo]:
        return self._impl.find_references(file_path, symbol, line, character)

    def hover(self, file_path: str, line: int, character: int) -> HoverResult | None:
        return self._impl.hover(file_path, line, character)

    def diagnostics(self, file_path: str) -> list[Diagnostic]:
        return self._impl.diagnostics(file_path)

    def query_symbols(self, query: str) -> list[SymbolInfo]:
        return self._impl.query_symbols(query)

    def apply_edit(self, request: EditRequest) -> EditResult:
        return self._impl.apply_edit(request)

    def commit_operation_against_base(
        self,
        changes: Sequence[OperationChange],
        *,
        agent_id: str = "",
        edit_type: str,
        description: str = "",
    ) -> OperationResult:
        return self._impl.commit_operation_against_base(
            changes,
            agent_id=agent_id,
            edit_type=edit_type,
            description=description,
        )

    def commit_specs_many(
        self,
        requests: Sequence[dict[str, Any]],
    ) -> list[OperationResult]:
        return self._impl.commit_specs_many(requests)

    def list_folder_files(self, folder: str) -> list[str]:
        return self._impl.list_folder_files(folder)

    def write_file(
        self,
        specs: Sequence[WriteSpec] | WriteSpec,
        *,
        agent_id: str = "",
        description: str = "",
    ) -> OperationResult:
        return self._impl.write_file(specs, agent_id=agent_id, description=description)

    def edit_file(
        self,
        specs: Sequence[EditSpec] | EditSpec,
        *,
        agent_id: str = "",
        description: str = "",
    ) -> OperationResult:
        return self._impl.edit_file(specs, agent_id=agent_id, description=description)

    def delete_file(
        self,
        paths: Sequence[str | DeleteSpec],
        *,
        agent_id: str = "",
        description: str = "",
    ) -> OperationResult:
        return self._impl.delete_file(paths, agent_id=agent_id, description=description)

    def move_file(
        self,
        specs: Sequence[MoveSpec],
        *,
        agent_id: str = "",
        description: str = "",
    ) -> OperationResult:
        return self._impl.move_file(specs, agent_id=agent_id, description=description)

    def undo_last_edit(self, file_path: str) -> EditResult:
        return self._impl.undo_last_edit(file_path)

    def status(self) -> dict[str, Any]:
        return self._impl.status()

    def get_telemetry(self) -> CITelemetry:
        return self._impl.get_telemetry()

    def dispose(self) -> None:
        self._impl.dispose()
