"""Backend Protocol and concrete implementations for :class:`CodeIntelligenceService`.

This module introduces the seam between the public service facade and the
concrete code-intelligence implementation. With the seam in place the
remaining phases of the in-sandbox-daemon migration can swap the backend
without touching the public facade or any caller.

Three artifacts live here:

* :class:`CiBackend` — typing.Protocol that every backend implements.
* :class:`InProcessCiBackend` — wraps today's in-process logic. This is the
  default backend selected when ``EOS_CI_IN_SANDBOX`` is unset.
* :class:`RpcCiBackend` — placeholder stub for the in-sandbox daemon. Every
  method raises :class:`NotImplementedError` until Phase 1+ ships the daemon.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol

from sandbox.api.transport import SandboxTransport
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
from sandbox.code_intelligence.indexing.symbol_index import SymbolIndex
from sandbox.code_intelligence.language_server.client import LspClient
from sandbox.code_intelligence.mutations.arbiter import Arbiter
from sandbox.code_intelligence.mutations.content_manager import ContentManager
from sandbox.code_intelligence.mutations.mutation_service import MutationService
from sandbox.code_intelligence.mutations.patcher import Patcher
from sandbox.code_intelligence.mutations.time_machine import TimeMachine
from sandbox.code_intelligence.mutations.write_coordinator import WriteCoordinator
from sandbox.code_intelligence.overlay.command_executor import AuditedCommandExecutor
from sandbox.code_intelligence.telemetry import build_status, build_telemetry

__all__ = ["CiBackend", "InProcessCiBackend", "RpcCiBackend"]

logger = logging.getLogger(__name__)


class CiBackend(Protocol):
    """Shape that every code-intelligence backend implements."""

    sandbox_id: str
    workspace_root: str
    is_initialized: bool

    def ensure_initialized(self, wait: bool = True) -> bool: ...
    def warmup(self) -> None: ...
    def rebind_sandbox(self, sandbox: Any) -> None: ...
    async def cmd(self, sandbox: Any, command: str, **kwargs: Any) -> Any: ...
    def find_definitions(
        self,
        file_path: str,
        symbol: str,
        line: int = 0,
        character: int = 0,
    ) -> list[SymbolInfo]: ...
    def find_references(
        self,
        file_path: str,
        symbol: str,
        line: int = 0,
        character: int = 0,
    ) -> list[ReferenceInfo]: ...
    def hover(self, file_path: str, line: int, character: int) -> HoverResult | None: ...
    def diagnostics(self, file_path: str) -> list[Diagnostic]: ...
    def query_symbols(self, query: str) -> list[SymbolInfo]: ...
    def apply_edit(self, request: EditRequest) -> EditResult: ...
    def commit_operation_against_base(
        self,
        changes: Sequence[OperationChange],
        *,
        agent_id: str = "",
        edit_type: str,
        description: str = "",
    ) -> OperationResult: ...
    def commit_specs_many(
        self,
        requests: Sequence[dict[str, Any]],
    ) -> list[OperationResult]: ...
    def list_folder_files(self, folder: str) -> list[str]: ...
    def write_file(
        self,
        specs: Sequence[WriteSpec] | WriteSpec,
        *,
        agent_id: str = "",
        description: str = "",
    ) -> OperationResult: ...
    def edit_file(
        self,
        specs: Sequence[EditSpec] | EditSpec,
        *,
        agent_id: str = "",
        description: str = "",
    ) -> OperationResult: ...
    def delete_file(
        self,
        paths: Sequence[str | DeleteSpec],
        *,
        agent_id: str = "",
        description: str = "",
    ) -> OperationResult: ...
    def move_file(
        self,
        specs: Sequence[MoveSpec],
        *,
        agent_id: str = "",
        description: str = "",
    ) -> OperationResult: ...
    def undo_last_edit(self, file_path: str) -> EditResult: ...
    def status(self) -> dict[str, Any]: ...
    def get_telemetry(self) -> CITelemetry: ...
    def dispose(self) -> None: ...


class InProcessCiBackend:
    """In-process backend wrapping today's :class:`CodeIntelligenceService` logic.

    The constructor and method bodies are a verbatim re-home of the previous
    facade implementation. No behavior change.
    """

    def __init__(
        self,
        sandbox_id: str,
        workspace_root: str = "/workspace",
        sandbox: Any = None,
        *,
        transport: SandboxTransport | None = None,
    ) -> None:
        self.sandbox_id = sandbox_id
        self.workspace_root = workspace_root
        self._sandbox = sandbox
        self._transport = transport
        self._initialized = False
        self._lsp_bootstrap_attempted = False
        self._init_lock = threading.Lock()

        self.symbol_index = SymbolIndex(
            workspace_root=workspace_root,
            sandbox=sandbox,
            transport=transport,
            sandbox_id=sandbox_id if transport is not None else "",
        )
        self.arbiter = Arbiter(workspace_root=workspace_root)
        self.time_machine = TimeMachine()
        self.patcher = Patcher()
        self.lsp_client = LspClient(
            workspace_root=workspace_root,
            sandbox=sandbox,
            transport=transport,
            sandbox_id=sandbox_id if transport is not None else "",
        )

        self._content = ContentManager(
            workspace_root,
            sandbox=sandbox,
            transport=transport,
            sandbox_id=sandbox_id if transport is not None else "",
        )
        self._write_coordinator = WriteCoordinator(
            arbiter=self.arbiter,
            time_machine=self.time_machine,
            symbol_index=self.symbol_index,
            lsp_client=self.lsp_client,
            content=self._content,
        )
        self._mutations = MutationService(
            content=self._content,
            write_coordinator=self._write_coordinator,
            patcher=self.patcher,
        )
        self._command_executor = AuditedCommandExecutor(
            sandbox_id=sandbox_id,
            workspace_root=workspace_root,
            write_coordinator=self._write_coordinator,
            rebind_sandbox=self.rebind_sandbox,
            transport=transport,
        )

    def ensure_initialized(self, wait: bool = True) -> bool:
        with self._init_lock:
            if self._initialized:
                return True

        ready = self.symbol_index.ensure_built(wait=wait)
        lsp_ready = self.lsp_client.ensure_ready(languages=("python",))
        if (
            self._sandbox is not None
            and not lsp_ready.get("python")
            and not self._lsp_bootstrap_attempted
        ):
            self._lsp_bootstrap_attempted = True
            self.lsp_client.ensure_ready(install_missing=True, languages=("python",))

        with self._init_lock:
            self._initialized = ready or self.symbol_index.is_built
        return self.is_initialized

    @property
    def is_initialized(self) -> bool:
        with self._init_lock:
            if self._initialized:
                return True
        if self.symbol_index.is_built:
            with self._init_lock:
                self._initialized = True
            return True
        return False

    def warmup(self) -> None:
        if self.is_initialized:
            return
        workspace_root = str(self.workspace_root or "")
        is_remote_only = bool(
            self._sandbox is not None
            and workspace_root
            and not Path(workspace_root).is_dir()
        )
        if is_remote_only:
            si = self.symbol_index
            if si is not None and not si.is_built:
                try:
                    si.ensure_built(wait=True, timeout=60.0)
                except Exception:
                    logger.debug("warmup remote symbol index failed", exc_info=True)
            return
        try:
            self.ensure_initialized(wait=True)
        except Exception:
            logger.debug("warmup full init failed", exc_info=True)

    def rebind_sandbox(self, sandbox: Any) -> None:
        if sandbox is None:
            return
        self._sandbox = sandbox
        self.symbol_index.bind_sandbox(sandbox)
        old_sandbox = getattr(self.lsp_client, "_sandbox", None)
        self.lsp_client._sandbox = sandbox
        if old_sandbox is not sandbox:
            self.lsp_client.reset_backend_availability()
        self._content.bind_sandbox(sandbox)

    async def cmd(self, sandbox: Any, command: str, **kwargs: Any) -> Any:
        return await self._command_executor.cmd(sandbox, command, **kwargs)

    def find_definitions(
        self,
        file_path: str,
        symbol: str,
        line: int = 0,
        character: int = 0,
    ) -> list[SymbolInfo]:
        if self._is_python(file_path) and line >= 1:
            try:
                results = self.lsp_client.goto_definition(file_path, line, character)
            except Exception as exc:
                logger.warning("LSP definition lookup failed, falling back: %s", exc)
            else:
                if results:
                    return results
        return self.symbol_index.find(symbol)

    def find_references(
        self,
        file_path: str,
        symbol: str,
        line: int = 0,
        character: int = 0,
    ) -> list[ReferenceInfo]:
        del symbol
        if not self._is_python(file_path) or line < 1:
            return []
        try:
            return self.lsp_client.find_references(file_path, line, character)
        except Exception as exc:
            logger.warning("LSP reference lookup failed: %s", exc)
            return []

    def hover(self, file_path: str, line: int, character: int) -> HoverResult | None:
        if self._is_python(file_path) and line >= 1:
            try:
                result = self.lsp_client.hover(file_path, line, character)
            except Exception as exc:
                logger.warning("LSP hover lookup failed, falling back: %s", exc)
            else:
                if result is not None:
                    return result
        for symbol in self.symbol_index.file_symbols(file_path):
            if symbol.line == line:
                return HoverResult(content=symbol.signature or symbol.name, symbol=symbol)
        return None

    def diagnostics(self, file_path: str) -> list[Diagnostic]:
        if not self._is_python(file_path):
            return []
        try:
            return self.lsp_client.diagnostics(file_path)
        except Exception as exc:
            raise RuntimeError(
                f"Diagnostic backend lsp failed and no fallback diagnostic backend succeeded: {exc}"
            ) from exc

    def query_symbols(self, query: str) -> list[SymbolInfo]:
        return self.symbol_index.find(query)

    def apply_edit(self, request: EditRequest) -> EditResult:
        return self._mutations.apply_edit(request)

    def commit_operation_against_base(
        self,
        changes: Sequence[OperationChange],
        *,
        agent_id: str = "",
        edit_type: str,
        description: str = "",
    ) -> OperationResult:
        return self._mutations.commit_operation_against_base(
            changes,
            agent_id=agent_id,
            edit_type=edit_type,
            description=description,
        )

    def commit_specs_many(
        self,
        requests: Sequence[dict[str, Any]],
    ) -> list[OperationResult]:
        return self._mutations.commit_specs_many(requests)

    def list_folder_files(self, folder: str) -> list[str]:
        return self._content.list_folder_files(folder)

    def write_file(
        self,
        specs: Sequence[WriteSpec] | WriteSpec,
        *,
        agent_id: str = "",
        description: str = "",
    ) -> OperationResult:
        return self._mutations.write_file(specs, agent_id=agent_id, description=description)

    def edit_file(
        self,
        specs: Sequence[EditSpec] | EditSpec,
        *,
        agent_id: str = "",
        description: str = "",
    ) -> OperationResult:
        return self._mutations.edit_file(specs, agent_id=agent_id, description=description)

    def delete_file(
        self,
        paths: Sequence[str | DeleteSpec],
        *,
        agent_id: str = "",
        description: str = "",
    ) -> OperationResult:
        return self._mutations.delete_file(paths, agent_id=agent_id, description=description)

    def move_file(
        self,
        specs: Sequence[MoveSpec],
        *,
        agent_id: str = "",
        description: str = "",
    ) -> OperationResult:
        return self._mutations.move_file(specs, agent_id=agent_id, description=description)

    def undo_last_edit(self, file_path: str) -> EditResult:
        return self._mutations.undo_last_edit(file_path)

    def status(self) -> dict[str, Any]:
        return build_status(
            sandbox_id=self.sandbox_id,
            workspace_root=self.workspace_root,
            initialized=self.is_initialized,
            symbol_index=self.symbol_index,
            arbiter=self.arbiter,
            lsp_client=self.lsp_client,
        )

    def get_telemetry(self) -> CITelemetry:
        return build_telemetry(
            symbol_index=self.symbol_index,
            arbiter=self.arbiter,
            lsp_client=self.lsp_client,
        )

    @staticmethod
    def _is_python(file_path: str) -> bool:
        return Path(file_path).suffix.lower() == ".py"

    def dispose(self) -> None:
        self.arbiter.cleanup_locks()
        self.time_machine.clear()
        try:
            self.lsp_client.close()
        except Exception:  # pragma: no cover - defensive
            logger.debug("lsp_client.close() failed during dispose", exc_info=True)
        logger.info("CodeIntelligenceService disposed for sandbox %s", self.sandbox_id)


_RPC_NOT_READY = "RpcCiBackend lands in Phase 1+"


class RpcCiBackend:
    """Placeholder daemon-bound backend.

    Selected when ``EOS_CI_IN_SANDBOX=1`` and a ``transport`` + ``sandbox_id``
    are available. Every method raises :class:`NotImplementedError` until
    Phase 1+ implements the actual daemon RPC plumbing.
    """

    is_initialized: bool = False

    def __init__(
        self,
        sandbox_id: str,
        workspace_root: str = "/workspace",
        *,
        transport: SandboxTransport,
    ) -> None:
        self.sandbox_id = sandbox_id
        self.workspace_root = workspace_root
        self._transport = transport
        self.is_initialized = False

    def ensure_initialized(self, wait: bool = True) -> bool:
        raise NotImplementedError(_RPC_NOT_READY)

    def warmup(self) -> None:
        raise NotImplementedError(_RPC_NOT_READY)

    def rebind_sandbox(self, sandbox: Any) -> None:
        raise NotImplementedError(_RPC_NOT_READY)

    async def cmd(self, sandbox: Any, command: str, **kwargs: Any) -> Any:
        raise NotImplementedError(_RPC_NOT_READY)

    def find_definitions(
        self,
        file_path: str,
        symbol: str,
        line: int = 0,
        character: int = 0,
    ) -> list[SymbolInfo]:
        raise NotImplementedError(_RPC_NOT_READY)

    def find_references(
        self,
        file_path: str,
        symbol: str,
        line: int = 0,
        character: int = 0,
    ) -> list[ReferenceInfo]:
        raise NotImplementedError(_RPC_NOT_READY)

    def hover(self, file_path: str, line: int, character: int) -> HoverResult | None:
        raise NotImplementedError(_RPC_NOT_READY)

    def diagnostics(self, file_path: str) -> list[Diagnostic]:
        raise NotImplementedError(_RPC_NOT_READY)

    def query_symbols(self, query: str) -> list[SymbolInfo]:
        raise NotImplementedError(_RPC_NOT_READY)

    def apply_edit(self, request: EditRequest) -> EditResult:
        raise NotImplementedError(_RPC_NOT_READY)

    def commit_operation_against_base(
        self,
        changes: Sequence[OperationChange],
        *,
        agent_id: str = "",
        edit_type: str,
        description: str = "",
    ) -> OperationResult:
        raise NotImplementedError(_RPC_NOT_READY)

    def commit_specs_many(
        self,
        requests: Sequence[dict[str, Any]],
    ) -> list[OperationResult]:
        raise NotImplementedError(_RPC_NOT_READY)

    def list_folder_files(self, folder: str) -> list[str]:
        raise NotImplementedError(_RPC_NOT_READY)

    def write_file(
        self,
        specs: Sequence[WriteSpec] | WriteSpec,
        *,
        agent_id: str = "",
        description: str = "",
    ) -> OperationResult:
        raise NotImplementedError(_RPC_NOT_READY)

    def edit_file(
        self,
        specs: Sequence[EditSpec] | EditSpec,
        *,
        agent_id: str = "",
        description: str = "",
    ) -> OperationResult:
        raise NotImplementedError(_RPC_NOT_READY)

    def delete_file(
        self,
        paths: Sequence[str | DeleteSpec],
        *,
        agent_id: str = "",
        description: str = "",
    ) -> OperationResult:
        raise NotImplementedError(_RPC_NOT_READY)

    def move_file(
        self,
        specs: Sequence[MoveSpec],
        *,
        agent_id: str = "",
        description: str = "",
    ) -> OperationResult:
        raise NotImplementedError(_RPC_NOT_READY)

    def undo_last_edit(self, file_path: str) -> EditResult:
        raise NotImplementedError(_RPC_NOT_READY)

    def status(self) -> dict[str, Any]:
        raise NotImplementedError(_RPC_NOT_READY)

    def get_telemetry(self) -> CITelemetry:
        raise NotImplementedError(_RPC_NOT_READY)

    def dispose(self) -> None:
        raise NotImplementedError(_RPC_NOT_READY)
