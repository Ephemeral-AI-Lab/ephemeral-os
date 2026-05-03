"""Backend Protocol and concrete implementations for :class:`CodeIntelligenceService`.

This module introduces the seam between the public service facade and the
concrete code-intelligence implementation. With the seam in place the
remaining phases of the in-sandbox-daemon migration can swap the backend
without touching the public facade or any caller.

Three artifacts live here:

* :class:`CiBackend` — typing.Protocol that every backend implements.
* :class:`InProcessCiBackend` — wraps local in-process logic for sandboxless
  flows.
* :class:`DaemonCiBackend` — the default transport-backed path. Phase 3.5
  collapsed the Phase 1 pickle-snapshot bootstrap and Phase 3 daemon dispatch
  into a single ``ensure_daemon → index_ready → query`` pipeline; the
  orchestrator no longer holds business state.
"""

from __future__ import annotations

import asyncio
import base64
import dataclasses
import logging
import textwrap
import threading
import time
import uuid
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Protocol

from sandbox.api.transport import SandboxTransport
from sandbox.client.async_bridge import run_sync
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
from sandbox.code_intelligence.in_sandbox.ci_protocol import (
    CI_PROTOCOL_VERSION,
    encode_frame,
    parse_response,
    read_frame,
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

__all__ = [
    "CiBackend",
    "CiDaemonCommandError",
    "DaemonCiBackend",
    "InProcessCiBackend",
]

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
        edit_history: Any | None = None,
        symbol_index_persistence: Any | None = None,
        daemon_local: bool = False,
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
            persistence=symbol_index_persistence,
        )
        self.arbiter = Arbiter(
            workspace_root=workspace_root,
            edit_history=edit_history,
        )
        self.time_machine = TimeMachine()
        self.patcher = Patcher()
        self.lsp_client = LspClient(
            workspace_root=workspace_root,
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
            daemon_local=daemon_local,
        )

    def ensure_initialized(self, wait: bool = True) -> bool:
        with self._init_lock:
            if self._initialized:
                return True

        ready = self.symbol_index.ensure_built(wait=wait)
        lsp_ready = self.lsp_client.ensure_ready(languages=("python",))
        if (
            self._transport is not None
            and self.sandbox_id
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
        try:
            self.ensure_initialized(wait=True)
        except Exception:
            logger.debug("warmup full init failed", exc_info=True)

    def rebind_sandbox(self, sandbox: Any) -> None:
        if sandbox is None:
            return
        self._sandbox = sandbox
        self.symbol_index.bind_sandbox(sandbox)
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

class CiDaemonCommandError(Exception):
    """Raised when the daemon returns an ``ok=False`` command envelope."""

    def __init__(
        self,
        kind: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(f"{kind}: {message}")
        self.kind = kind
        self.message = message
        self.details = details or {}


class DaemonCiBackend:
    """Daemon-bound backend.

    Phase 1 shipped ``ensure_initialized`` + ``query_symbols`` against the
    orchestrator-side cache. Phase 2 added daemon lifecycle cleanup. Phase 3
    routed every code-intelligence verb through the daemon's framed-msgpack
    command dispatch. Phase 3.5 retired the orchestrator-side pickle snapshot
    fallback now that the daemon serves queries directly from the SQLite
    ``IndexStore``: the orchestrator no longer pulls ``index.snapshot`` over
    the wire and no longer caches symbols. ``ensure_initialized`` simply
    launches the daemon and polls ``index_ready``.

    ``cmd`` routes through the same daemon dispatch path as query and mutation
    verbs so callers do not need a separate shell path.
    """

    is_initialized: bool = False
    _INDEX_READY_TIMEOUT_S: float = 60.0
    _INDEX_READY_POLL_S: float = 0.5

    def __init__(
        self,
        sandbox_id: str,
        workspace_root: str = "/workspace",
        *,
        transport: SandboxTransport,
    ) -> None:
        from sandbox.code_intelligence.daemon.launcher import DaemonLauncher

        self.sandbox_id = sandbox_id
        self.workspace_root = workspace_root
        self._transport = transport
        self._launcher = DaemonLauncher(transport, sandbox_id, workspace_root)
        self.is_initialized = False
        self._init_lock = threading.Lock()

    def ensure_initialized(self, wait: bool = True) -> bool:
        del wait  # The Phase 1 path always runs to completion.
        with self._init_lock:
            if self.is_initialized:
                return True
        run_sync(self._ensure_initialized_async())
        with self._init_lock:
            return self.is_initialized

    async def _ensure_initialized_async(self) -> None:
        """Launch the daemon and wait for the SymbolIndex background build.

        Phase 3.5 retirement: no longer downloads ``index.snapshot`` and no
        longer hydrates an orchestrator-side ``_symbol_cache``. The daemon
        owns the canonical SQLite ``IndexStore`` and serves queries directly
        from it.
        """
        await self._launcher.ensure_daemon()

        deadline = (
            asyncio.get_event_loop().time() + self._INDEX_READY_TIMEOUT_S
        )
        while True:
            try:
                resp = await self._call_daemon_command("index_ready", {})
            except Exception as exc:  # pragma: no cover - exposed via tests
                logger.debug(
                    "index_ready call failed during ensure_initialized: %s", exc
                )
                resp = None
            if isinstance(resp, dict) and resp.get("ready"):
                break
            if asyncio.get_event_loop().time() >= deadline:
                # Daemon is up but index isn't built yet — surface as initialised
                # anyway so callers can attempt queries (which will return [] until
                # the build finishes). Future polling can re-check.
                break
            await asyncio.sleep(self._INDEX_READY_POLL_S)
        with self._init_lock:
            self.is_initialized = True

    # ------------------------------------------------------------------ helpers

    def _call_sync(self, op: str, args: dict[str, Any] | None = None) -> Any:
        """Send one daemon command synchronously (bridges asyncio internally)."""
        return run_sync(self._call_daemon_command(op, args or {}))

    async def _call_async(
        self,
        op: str,
        args: dict[str, Any] | None = None,
        *,
        timeout: float = 30.0,
    ) -> Any:
        return await self._call_daemon_command(op, args or {}, timeout=timeout)

    async def _call_daemon_command(
        self,
        op: str,
        args: dict[str, Any] | None = None,
        *,
        timeout: float = 30.0,
    ) -> Any:
        """Send one framed command to the in-sandbox daemon."""
        from sandbox.code_intelligence.daemon.launcher import (
            CiDaemonUnavailable,
        )
        started = time.perf_counter()
        try:
            result = await self._call_daemon_once(
                self._launcher,
                op,
                args or {},
                timeout=timeout,
            )
            logger.debug(
                "ci daemon command done: op=%s elapsed=%.3fs retry=false",
                op,
                time.perf_counter() - started,
            )
            return result
        except (ConnectionRefusedError, BrokenPipeError, FileNotFoundError, OSError):
            retry_started = time.perf_counter()
            await self._launcher.ensure_daemon()
            logger.debug(
                "ci daemon command retry after ensure_daemon: "
                "op=%s ensure_elapsed=%.3fs",
                op,
                time.perf_counter() - retry_started,
            )
            try:
                result = await self._call_daemon_once(
                    self._launcher,
                    op,
                    args or {},
                    timeout=timeout,
                )
                logger.debug(
                    "ci daemon command done: op=%s elapsed=%.3fs retry=true",
                    op,
                    time.perf_counter() - started,
                )
                return result
            except (
                ConnectionRefusedError,
                BrokenPipeError,
                FileNotFoundError,
                OSError,
            ) as exc:
                raise CiDaemonUnavailable(
                    f"daemon unreachable after respawn: {exc}"
                ) from exc

    async def _call_daemon_once(
        self,
        launcher: Any,
        op: str,
        args: dict[str, Any],
        *,
        timeout: float,
    ) -> Any:
        request_id = uuid.uuid4().hex
        frame = encode_frame(
            {"v": CI_PROTOCOL_VERSION, "id": request_id, "op": op, "args": args}
        )
        socket_started = time.perf_counter()
        socket_path = await launcher.socket_path()
        socket_elapsed = time.perf_counter() - socket_started
        send_started = time.perf_counter()
        response_frame = await self._send_frame_via_process_exec(
            socket_path,
            frame,
            timeout=timeout,
        )
        send_elapsed = time.perf_counter() - send_started
        parse_started = time.perf_counter()
        reader = asyncio.StreamReader()
        reader.feed_data(response_frame)
        reader.feed_eof()
        response = parse_response(await read_frame(reader))
        parse_elapsed = time.perf_counter() - parse_started
        logger.debug(
            "ci daemon command_once: op=%s request_id=%s socket_path_elapsed=%.3fs "
            "send_frame_elapsed=%.3fs parse_elapsed=%.3fs "
            "request_bytes=%d response_bytes=%d",
            op,
            request_id,
            socket_elapsed,
            send_elapsed,
            parse_elapsed,
            len(frame),
            len(response_frame),
        )
        if response.id != request_id:
            raise RuntimeError(
                f"daemon response id mismatch: expected {request_id}, got {response.id}"
            )
        if not response.ok:
            error = response.error or {}
            raise CiDaemonCommandError(
                kind=str(error.get("kind") or "InternalError"),
                message=str(error.get("message") or ""),
                details=error.get("details")
                if isinstance(error.get("details"), dict)
                else {},
            )
        return response.result

    async def _send_frame_via_process_exec(
        self,
        socket_path: str,
        frame: bytes,
        *,
        timeout: float,
    ) -> bytes:
        """Send ``frame`` through a sandbox-local Python Unix-socket bridge."""
        encoded = base64.b64encode(frame).decode("ascii")
        script = textwrap.dedent(
            f"""
            import base64
            import socket
            import sys

            frame = base64.b64decode({encoded!r})
            sock = socket.socket(socket.AF_UNIX)
            sock.settimeout({float(timeout)!r})
            sock.connect({socket_path!r})
            sock.sendall(frame)
            sock.shutdown(socket.SHUT_WR)
            chunks = []
            while True:
                data = sock.recv(65536)
                if not data:
                    break
                chunks.append(data)
            sock.close()
            sys.stdout.write(base64.b64encode(b"".join(chunks)).decode("ascii"))
            """
        ).strip()
        command = f"python3 - <<'PY'\n{script}\nPY"
        result = await self._transport.exec(
            self.sandbox_id,
            command,
            timeout=max(1, int(timeout) + 5),
        )
        stdout = (getattr(result, "stdout", "") or "").strip()
        if getattr(result, "exit_code", 1) != 0:
            raise ConnectionRefusedError(stdout)
        try:
            return base64.b64decode(stdout)
        except Exception as exc:
            raise ConnectionRefusedError(
                f"daemon bridge produced invalid base64: {stdout!r}"
            ) from exc

    def warmup(self) -> None:
        # Warmup is just "make sure ensure_initialized has run"; daemon
        # request handlers initialize their own long-lived children lazily.
        self.ensure_initialized(wait=True)

    def rebind_sandbox(self, sandbox: Any) -> None:
        # Daemon's CodeIntelligenceService is constructed with sandbox=None
        # and never needs an external sandbox handle — local-FS branches do
        # the work. Rebinding is a no-op on the daemon side.
        del sandbox
        return None

    async def cmd(self, sandbox: Any, command: str, **kwargs: Any) -> Any:
        del sandbox
        on_progress_line = kwargs.pop("on_progress_line", None)
        timeout = kwargs.get("timeout")
        command_timeout = float(timeout if timeout is not None else 600) + 30.0
        payload = {"command": command, **kwargs}
        command_started = time.perf_counter()
        raw = await self._call_async("svc_cmd", payload, timeout=command_timeout)
        command_elapsed = round(time.perf_counter() - command_started, 6)
        result = SimpleNamespace(**(raw or {}))
        result.daemon_call_timings = {"total": command_elapsed}
        if on_progress_line is not None:
            progress_text = str(getattr(result, "result", "") or "")
            if progress_text:
                on_progress_line(progress_text)
        return result

    def find_definitions(
        self,
        file_path: str,
        symbol: str,
        line: int = 0,
        character: int = 0,
    ) -> list[SymbolInfo]:
        rows = self._call_sync(
            "find_definitions",
            {
                "file_path": file_path,
                "symbol": symbol,
                "line": line,
                "character": character,
            },
        )
        return [_symbol_info_from_dict(r) for r in (rows or [])]

    def find_references(
        self,
        file_path: str,
        symbol: str,
        line: int = 0,
        character: int = 0,
    ) -> list[ReferenceInfo]:
        rows = self._call_sync(
            "find_references",
            {
                "file_path": file_path,
                "symbol": symbol,
                "line": line,
                "character": character,
            },
        )
        return [_reference_info_from_dict(r) for r in (rows or [])]

    def hover(self, file_path: str, line: int, character: int) -> HoverResult | None:
        result = self._call_sync(
            "hover",
            {"file_path": file_path, "line": line, "character": character},
        )
        if not result:
            return None
        return _hover_result_from_dict(result)

    def diagnostics(self, file_path: str) -> list[Diagnostic]:
        rows = self._call_sync("diagnostics", {"file_path": file_path})
        return [_diagnostic_from_dict(r) for r in (rows or [])]

    def query_symbols(self, query: str) -> list[SymbolInfo]:
        # Phase 3.5: queries route through the daemon. The Phase 1
        # orchestrator-side pickle cache fallback was retired once the daemon
        # became the canonical owner of the SQLite IndexStore.
        rows = self._call_sync("query_symbols", {"query": query})
        return [_symbol_info_from_dict(r) for r in (rows or [])]

    def apply_edit(self, request: EditRequest) -> EditResult:
        result = self._call_sync(
            "apply_edit",
            {"request": _edit_request_to_dict(request)},
        )
        return _edit_result_from_dict(result)

    def commit_operation_against_base(
        self,
        changes: Sequence[OperationChange],
        *,
        agent_id: str = "",
        edit_type: str,
        description: str = "",
    ) -> OperationResult:
        result = self._call_sync(
            "commit_operation_against_base",
            {
                "changes": [_operation_change_to_dict(c) for c in changes],
                "agent_id": agent_id,
                "edit_type": edit_type,
                "description": description,
            },
        )
        return _operation_result_from_dict(result)

    def commit_specs_many(
        self,
        requests: Sequence[dict[str, Any]],
    ) -> list[OperationResult]:
        rows = self._call_sync(
            "commit_specs_many", {"requests": list(requests)}
        )
        return [_operation_result_from_dict(r) for r in (rows or [])]

    def list_folder_files(self, folder: str) -> list[str]:
        rows = self._call_sync("list_folder_files", {"folder": folder})
        return list(rows or [])

    def write_file(
        self,
        specs: Sequence[WriteSpec] | WriteSpec,
        *,
        agent_id: str = "",
        description: str = "",
    ) -> OperationResult:
        normalized = _normalize_write_specs(specs)
        result = self._call_sync(
            "write_file",
            {
                "specs": [_writespec_to_dict(s) for s in normalized],
                "agent_id": agent_id,
                "description": description,
            },
        )
        return _operation_result_from_dict(result)

    def edit_file(
        self,
        specs: Sequence[EditSpec] | EditSpec,
        *,
        agent_id: str = "",
        description: str = "",
    ) -> OperationResult:
        normalized = _normalize_edit_specs(specs)
        result = self._call_sync(
            "edit_file",
            {
                "specs": [_editspec_to_dict(s) for s in normalized],
                "agent_id": agent_id,
                "description": description,
            },
        )
        return _operation_result_from_dict(result)

    def delete_file(
        self,
        paths: Sequence[str | DeleteSpec],
        *,
        agent_id: str = "",
        description: str = "",
    ) -> OperationResult:
        encoded: list[Any] = []
        for entry in paths:
            if isinstance(entry, str):
                encoded.append(entry)
            else:
                encoded.append(_deletespec_to_dict(entry))
        result = self._call_sync(
            "delete_file",
            {
                "paths": encoded,
                "agent_id": agent_id,
                "description": description,
            },
        )
        return _operation_result_from_dict(result)

    def move_file(
        self,
        specs: Sequence[MoveSpec],
        *,
        agent_id: str = "",
        description: str = "",
    ) -> OperationResult:
        result = self._call_sync(
            "move_file",
            {
                "specs": [_movespec_to_dict(s) for s in specs],
                "agent_id": agent_id,
                "description": description,
            },
        )
        return _operation_result_from_dict(result)

    def undo_last_edit(self, file_path: str) -> EditResult:
        result = self._call_sync("undo_last_edit", {"file_path": file_path})
        return _edit_result_from_dict(result)

    def status(self) -> dict[str, Any]:
        result = self._call_sync("status")
        return dict(result or {})

    def get_telemetry(self) -> CITelemetry:
        result = self._call_sync("get_telemetry")
        return _telemetry_from_dict(result or {})

    def dispose(self) -> None:
        try:
            run_sync(self._launcher.shutdown())
        except Exception:
            logger.debug(
                "CI daemon shutdown skipped for sandbox %s",
                self.sandbox_id,
                exc_info=True,
            )


# ---------------------------------------------------------------------------
# Serialization helpers (orchestrator side)
# ---------------------------------------------------------------------------


def _normalize_write_specs(
    specs: Sequence[WriteSpec] | WriteSpec,
) -> list[WriteSpec]:
    return [specs] if isinstance(specs, WriteSpec) else list(specs)


def _normalize_edit_specs(
    specs: Sequence[EditSpec] | EditSpec,
) -> list[EditSpec]:
    return [specs] if isinstance(specs, EditSpec) else list(specs)


def _writespec_to_dict(spec: WriteSpec) -> dict[str, Any]:
    return {
        "file_path": spec.file_path,
        "content": spec.content,
        "overwrite": spec.overwrite,
    }


def _editspec_to_dict(spec: EditSpec) -> dict[str, Any]:
    return {
        "file_path": spec.file_path,
        "edits": list(spec.edits),
    }


def _movespec_to_dict(spec: MoveSpec) -> dict[str, Any]:
    return {
        "src_path": spec.src_path,
        "dst_path": spec.dst_path,
        "overwrite": spec.overwrite,
        "is_folder": spec.is_folder,
    }


def _deletespec_to_dict(spec: DeleteSpec) -> dict[str, Any]:
    return {"path": spec.path, "is_folder": spec.is_folder}


def _operation_change_to_dict(change: OperationChange) -> dict[str, Any]:
    return {
        "file_path": change.file_path,
        "base_content": change.base_content,
        "base_hash": change.base_hash,
        "final_content": change.final_content,
        "base_existed": change.base_existed,
        "strict_base": change.strict_base,
    }


def _edit_request_to_dict(request: EditRequest) -> dict[str, Any]:
    return {
        "file_path": request.file_path,
        "old_text": request.old_text,
        "new_text": request.new_text,
        "agent_id": request.agent_id,
        "description": request.description,
    }


def _symbol_info_from_dict(d: dict[str, Any]) -> SymbolInfo:
    from sandbox.code_intelligence.core.types import SymbolKind

    kind_raw = d.get("kind")
    if isinstance(kind_raw, SymbolKind):
        kind = kind_raw
    elif isinstance(kind_raw, str):
        try:
            kind = SymbolKind(kind_raw)
        except ValueError:
            kind = SymbolKind.OTHER if hasattr(SymbolKind, "OTHER") else SymbolKind.CLASS
    else:
        kind = SymbolKind.CLASS
    return SymbolInfo(
        name=str(d.get("name", "")),
        kind=kind,
        file_path=str(d.get("file_path", "")),
        line=int(d.get("line", 0)),
        end_line=d.get("end_line"),
        character=int(d.get("character", 0)),
        signature=str(d.get("signature", "")),
        docstring=str(d.get("docstring", "")),
        container=str(d.get("container", "")),
    )


def _reference_info_from_dict(d: dict[str, Any]) -> ReferenceInfo:
    return ReferenceInfo(
        file_path=str(d.get("file_path", "")),
        line=int(d.get("line", 0)),
        character=int(d.get("character", 0)),
        text=str(d.get("text", "")),
    )


def _hover_result_from_dict(d: dict[str, Any]) -> HoverResult:
    sym_dict = d.get("symbol")
    symbol = _symbol_info_from_dict(sym_dict) if sym_dict else None
    return HoverResult(
        content=str(d.get("content", "")),
        language=str(d.get("language", "")),
        symbol=symbol,
    )


def _diagnostic_from_dict(d: dict[str, Any]) -> Diagnostic:
    from sandbox.code_intelligence.core.types import DiagnosticSeverity

    severity_raw = d.get("severity")
    if isinstance(severity_raw, DiagnosticSeverity):
        severity = severity_raw
    elif isinstance(severity_raw, str):
        try:
            severity = DiagnosticSeverity(severity_raw)
        except ValueError:
            severity = DiagnosticSeverity.ERROR
    else:
        severity = DiagnosticSeverity.ERROR
    return Diagnostic(
        file_path=str(d.get("file_path", "")),
        line=int(d.get("line", 0)),
        character=int(d.get("character", 0)),
        end_line=d.get("end_line"),
        end_character=d.get("end_character"),
        severity=severity,
        message=str(d.get("message", "")),
        source=str(d.get("source", "")),
        code=str(d.get("code", "")),
    )


def _edit_result_from_dict(d: dict[str, Any]) -> EditResult:
    return EditResult(
        success=bool(d.get("success", False)),
        file_path=str(d.get("file_path", "")),
        message=str(d.get("message", "")),
        conflict=bool(d.get("conflict", False)),
        conflict_reason=str(d.get("conflict_reason", "")),
        snapshot_id=str(d.get("snapshot_id", "")),
        timings=dict(d.get("timings") or {}),
    )


def _operation_result_from_dict(d: dict[str, Any]) -> OperationResult:
    files = tuple(_edit_result_from_dict(f) for f in (d.get("files") or ()))
    status = d.get("status", "failed")
    return OperationResult(
        success=bool(d.get("success", False)),
        status=status,  # type: ignore[arg-type]
        files=files,
        conflict_file=d.get("conflict_file"),
        conflict_reason=str(d.get("conflict_reason", "")),
        timings=dict(d.get("timings") or {}),
    )


def _telemetry_from_dict(d: dict[str, Any]) -> CITelemetry:
    """Reconstruct a :class:`CITelemetry` from its asdict() shape.

    The telemetry struct is mostly nested dicts; we only need the round-trip
    to preserve the data, not enforce strict typing per nested counter.
    """
    if isinstance(d, CITelemetry):
        return d
    init = {
        f.name: d.get(f.name)
        for f in dataclasses.fields(CITelemetry)
        if f.name in d
    }
    try:
        return CITelemetry(**init)  # type: ignore[arg-type]
    except TypeError:
        # Fall back to a permissive construction on schema drift.
        return CITelemetry()
