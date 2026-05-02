"""Persistent LSP child process owned by the daemon (Phase 3.6 Stage B).

The Stage A qualification spike picked **basedpyright** as the LSP backend on
the ``dask__dask_2023.3.2_2023.4.0`` sandbox image; see
``docs/architecture/code-intelligence-in-sandbox-daemon/lsp-qualification-spike-result.md``
for the evidence and the launch-command rationale (the dedicated
``basedpyright-langserver`` binary, NOT ``python3 -m basedpyright.langserver``).

Single backend, hardcoded — there is no runtime selector and no fallback.
A child crash is bounded to one respawn; a second crash escalates
:class:`LspChildUnavailable` so the operator sees the failure instead of
silently degraded results.

This module ships the :class:`LspBackendChild` lifecycle (``start`` /
``find_definitions`` / ``find_references`` / ``hover`` / ``diagnostics`` /
``did_change`` / ``shutdown``) over the :mod:`jsonrpc` framing primitives.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import os
from typing import Any, Literal

from sandbox.code_intelligence.core.types import (
    Diagnostic,
    DiagnosticSeverity,
    HoverResult,
    ReferenceInfo,
    SymbolInfo,
    SymbolKind,
)
from sandbox.code_intelligence.language_server.jsonrpc import (
    encode_notification,
    encode_request,
    read_frame,
)

__all__ = [
    "LSP_BACKEND_CHOSEN",
    "LspBackendChild",
    "LspChildCrashed",
    "LspChildUnavailable",
]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Hardcoded from Stage A qualification (P36-A) — see
# lsp-qualification-spike-result.md for evidence.
# ---------------------------------------------------------------------------

LSP_BACKEND_CHOSEN: Literal["basedpyright", "pyright"] = "basedpyright"

_LAUNCH_CMD: dict[str, list[str]] = {
    # The dedicated entry-point binary side-steps `python3 -m`'s
    # cwd-on-sys.path issue (which makes /testbed/dask/typing.py shadow the
    # stdlib ``typing`` module). MUST be the bin script, NOT ``python3 -m``.
    "basedpyright": ["basedpyright-langserver", "--stdio"],
    "pyright": ["pyright-langserver", "--stdio"],
}

_DEFAULT_INIT_TIMEOUT_S = 30.0
_DEFAULT_REQUEST_TIMEOUT_S = 30.0
_SHUTDOWN_TIMEOUT_S = 2.0


class LspChildUnavailable(Exception):
    """The chosen LSP backend isn't installed/runnable on this sandbox.

    Raised on initial spawn (binary missing, handshake fails) AND on the
    second consecutive crash. Surfaces loud — the daemon never silently
    swaps to a different backend.
    """


class LspChildCrashed(Exception):
    """The child process died mid-request. The first occurrence in a session
    triggers one bounded restart; a second escalates to
    :class:`LspChildUnavailable`."""


class LspBackendChild:
    """Persistent LSP child process — one per daemon, owned by ``LspClient``.

    Lifecycle:

    * ``start()`` — spawn the chosen backend, exchange initialize/initialized.
    * ``find_definitions / find_references / hover / diagnostics`` — JSON-RPC
      request / await response by id / parse to typed dataclass.
    * ``did_change(...)`` — notification on commit (HARD INVARIANT 5).
    * ``shutdown()`` — graceful: shutdown request → exit notification → close
      stdin → wait → terminate on timeout.
    * On crash, ``find_definitions`` etc. raise :class:`LspChildCrashed`;
      the caller decides whether to ``start()`` again.
    """

    def __init__(self, workspace_root: str) -> None:
        self.workspace_root = workspace_root
        self._proc: asyncio.subprocess.Process | None = None
        self._next_id = itertools.count(1)
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._write_lock = asyncio.Lock()
        self._closed = False
        self._stderr_tail: list[str] = []

    # ----------------------------------------------------------------- start

    async def start(
        self,
        *,
        init_timeout_s: float = _DEFAULT_INIT_TIMEOUT_S,
    ) -> None:
        """Spawn the chosen backend + initialize handshake.

        Raises :class:`LspChildUnavailable` if the binary isn't on PATH or
        the handshake fails / times out.
        """
        cmd = _LAUNCH_CMD[LSP_BACKEND_CHOSEN]
        try:
            # cwd MUST be neutral. ``python3 -m`` adds cwd to sys.path; even
            # for the dedicated binary, a workspace-relative cwd risks
            # surprising imports.
            self._proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd="/tmp",
            )
        except FileNotFoundError as exc:
            raise LspChildUnavailable(
                f"chosen LSP backend {LSP_BACKEND_CHOSEN!r} not found on PATH "
                f"({cmd[0]!r}): {exc}. Install it on the sandbox image; see "
                "lsp-qualification-spike-result.md for the install command."
            ) from exc

        assert self._proc.stdout is not None  # noqa: S101 - narrow for type-checker
        assert self._proc.stderr is not None  # noqa: S101
        self._reader_task = asyncio.create_task(
            self._read_loop(self._proc.stdout),
            name="lsp-child-reader",
        )
        self._stderr_task = asyncio.create_task(
            self._drain_stderr(self._proc.stderr),
            name="lsp-child-stderr",
        )

        try:
            await asyncio.wait_for(
                self._send_request(
                    "initialize",
                    {
                        "processId": os.getpid(),
                        "rootUri": f"file://{self.workspace_root}",
                        "rootPath": self.workspace_root,
                        "capabilities": {
                            "textDocument": {
                                "definition": {"dynamicRegistration": True},
                                "references": {"dynamicRegistration": True},
                                "hover": {"dynamicRegistration": True},
                                "publishDiagnostics": {"relatedInformation": True},
                            },
                            "workspace": {"workspaceFolders": True},
                        },
                        "initializationOptions": {},
                        "workspaceFolders": [
                            {
                                "uri": f"file://{self.workspace_root}",
                                "name": "workspace",
                            }
                        ],
                    },
                ),
                timeout=init_timeout_s,
            )
            await self._send_notification("initialized", {})
        except (TimeoutError, LspChildCrashed) as exc:
            await self._terminate_proc()
            raise LspChildUnavailable(
                f"chosen LSP backend {LSP_BACKEND_CHOSEN!r} failed initialize "
                f"handshake: {exc}; stderr_tail={self.stderr_tail()[-500:]!r}"
            ) from exc

    # --------------------------------------------------------------- queries

    async def find_definitions(
        self, file_path: str, line: int, character: int
    ) -> list[SymbolInfo]:
        """LSP ``textDocument/definition``."""
        await self._ensure_did_open(file_path)
        result = await self._send_request(
            "textDocument/definition",
            {
                "textDocument": {"uri": _file_uri(file_path)},
                "position": {"line": line, "character": character},
            },
        )
        return [_parse_location_to_symbol(loc) for loc in _normalize_locations(result)]

    async def find_references(
        self, file_path: str, line: int, character: int
    ) -> list[ReferenceInfo]:
        """LSP ``textDocument/references``."""
        await self._ensure_did_open(file_path)
        result = await self._send_request(
            "textDocument/references",
            {
                "textDocument": {"uri": _file_uri(file_path)},
                "position": {"line": line, "character": character},
                "context": {"includeDeclaration": True},
            },
        )
        return [_parse_location_to_reference(loc) for loc in _normalize_locations(result)]

    async def hover(
        self, file_path: str, line: int, character: int
    ) -> HoverResult | None:
        """LSP ``textDocument/hover``."""
        await self._ensure_did_open(file_path)
        result = await self._send_request(
            "textDocument/hover",
            {
                "textDocument": {"uri": _file_uri(file_path)},
                "position": {"line": line, "character": character},
            },
        )
        return _parse_hover(result)

    async def diagnostics(self, file_path: str) -> list[Diagnostic]:
        """LSP ``textDocument/diagnostic`` (pull model).

        Falls back to an empty list when the server doesn't support pull
        diagnostics — push diagnostics arrive via ``textDocument/publishDiagnostics``
        and would need a different routing path.
        """
        await self._ensure_did_open(file_path)
        try:
            result = await self._send_request(
                "textDocument/diagnostic",
                {"textDocument": {"uri": _file_uri(file_path)}},
            )
        except LspChildCrashed:
            raise
        except Exception:
            logger.debug(
                "textDocument/diagnostic not supported or returned error",
                exc_info=True,
            )
            return []
        items = []
        if isinstance(result, dict):
            items = result.get("items") or []
        elif isinstance(result, list):
            items = result
        return [_parse_diagnostic(item, file_path) for item in items]

    async def did_change(self, file_path: str, content: str) -> None:
        """LSP ``textDocument/didChange`` — notify on commit (HARD INVARIANT 5)."""
        await self._send_notification(
            "textDocument/didChange",
            {
                "textDocument": {
                    "uri": _file_uri(file_path),
                    "version": next(self._next_id),
                },
                "contentChanges": [{"text": content}],
            },
        )

    # -------------------------------------------------------------- shutdown

    async def shutdown(self, *, timeout_s: float = _SHUTDOWN_TIMEOUT_S) -> None:
        """Graceful shutdown: shutdown → exit → close stdin → wait → terminate."""
        if self._closed or self._proc is None:
            return
        self._closed = True
        try:
            await asyncio.wait_for(
                self._send_request("shutdown", None),
                timeout=timeout_s,
            )
        except (TimeoutError, LspChildCrashed, Exception):
            pass
        try:
            await self._send_notification("exit", {})
        except Exception:
            pass
        await self._terminate_proc(grace_s=timeout_s)

    def stderr_tail(self) -> str:
        return "".join(self._stderr_tail)

    # --------------------------------------------------------------- private

    async def _ensure_did_open(self, file_path: str) -> None:
        """Send ``textDocument/didOpen`` for *file_path* if not already open.

        basedpyright resolves position-based queries against its in-memory
        document model — sending didOpen before each request is cheap and
        prevents stale state after a daemon-side commit.
        """
        try:
            with open(file_path, encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            return
        await self._send_notification(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": _file_uri(file_path),
                    "languageId": "python",
                    "version": next(self._next_id),
                    "text": text,
                }
            },
        )

    async def _send_request(
        self, method: str, params: Any
    ) -> Any:
        """Send a request and await the matching response by id."""
        if self._proc is None or self._proc.stdin is None:
            raise LspChildCrashed(f"child not running for op {method}")
        req_id = next(self._next_id)
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[req_id] = future
        try:
            async with self._write_lock:
                self._proc.stdin.write(encode_request(req_id, method, params))
                await self._proc.stdin.drain()
        except (ConnectionResetError, BrokenPipeError, OSError) as exc:
            self._pending.pop(req_id, None)
            raise LspChildCrashed(
                f"write failed for op {method}: {exc!r}; stderr_tail="
                f"{self.stderr_tail()[-500:]!r}"
            ) from exc
        try:
            return await asyncio.wait_for(future, timeout=_DEFAULT_REQUEST_TIMEOUT_S)
        except TimeoutError:
            self._pending.pop(req_id, None)
            raise

    async def _send_notification(self, method: str, params: Any) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise LspChildCrashed(f"child not running for notification {method}")
        try:
            async with self._write_lock:
                self._proc.stdin.write(encode_notification(method, params))
                await self._proc.stdin.drain()
        except (ConnectionResetError, BrokenPipeError, OSError) as exc:
            raise LspChildCrashed(
                f"write failed for notification {method}: {exc!r}; stderr_tail="
                f"{self.stderr_tail()[-500:]!r}"
            ) from exc

    async def _read_loop(self, reader: asyncio.StreamReader) -> None:
        """Demultiplex inbound frames onto pending request futures."""
        try:
            while True:
                msg = await read_frame(reader)
                msg_id = msg.get("id")
                if msg_id is None:
                    # Server-initiated notification (e.g. publishDiagnostics) —
                    # ignored at this layer; daemon-side handlers can route
                    # them to the cache invalidation path in a future phase.
                    continue
                future = self._pending.pop(msg_id, None)
                if future is None or future.done():
                    continue
                if "error" in msg:
                    future.set_exception(
                        RuntimeError(f"LSP error: {msg['error']}")
                    )
                else:
                    future.set_result(msg.get("result"))
        except (asyncio.IncompleteReadError, asyncio.CancelledError):
            pass
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("LSP read loop terminated: %r", exc, exc_info=True)
        finally:
            # Fail every outstanding request — the child is dead.
            tail = self.stderr_tail()[-500:]
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(
                        LspChildCrashed(
                            f"child stdout closed; stderr_tail={tail!r}"
                        )
                    )
            self._pending.clear()

    async def _drain_stderr(self, reader: asyncio.StreamReader) -> None:
        """Buffer the child's stderr in a small ring so we can surface it on errors."""
        try:
            while True:
                line = await reader.readline()
                if not line:
                    return
                self._stderr_tail.append(line.decode("utf-8", "replace"))
                if len(self._stderr_tail) > 200:
                    self._stderr_tail.pop(0)
        except (asyncio.CancelledError, Exception):
            return

    async def _terminate_proc(self, *, grace_s: float = 2.0) -> None:
        """Best-effort terminate the child + clean up reader tasks."""
        if self._proc is None:
            return
        try:
            if self._proc.stdin is not None and not self._proc.stdin.is_closing():
                self._proc.stdin.close()
        except Exception:
            pass
        try:
            await asyncio.wait_for(self._proc.wait(), timeout=grace_s)
        except TimeoutError:
            try:
                self._proc.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=1.0)
            except TimeoutError:
                try:
                    self._proc.kill()
                except ProcessLookupError:
                    pass
        for task in (self._reader_task, self._stderr_task):
            if task is not None and not task.done():
                task.cancel()


# ---------------------------------------------------------------------------
# LSP → CI-types parsing helpers
# ---------------------------------------------------------------------------


def _file_uri(file_path: str) -> str:
    if file_path.startswith("file://"):
        return file_path
    if not file_path.startswith("/"):
        file_path = os.path.abspath(file_path)
    return f"file://{file_path}"


def _uri_to_path(uri: str) -> str:
    if uri.startswith("file://"):
        return uri[len("file://"):]
    return uri


def _normalize_locations(result: Any) -> list[dict[str, Any]]:
    """LSP definition / references can return Location | Location[] | LocationLink[]."""
    if result is None:
        return []
    if isinstance(result, dict):
        return [result]
    if isinstance(result, list):
        return [r for r in result if isinstance(r, dict)]
    return []


def _parse_location_to_symbol(loc: dict[str, Any]) -> SymbolInfo:
    uri = loc.get("uri") or loc.get("targetUri") or ""
    rng = loc.get("range") or loc.get("targetRange") or {}
    start = rng.get("start") or {}
    end = rng.get("end") or {}
    return SymbolInfo(
        name="",  # LSP definition doesn't return the symbol name; caller can fill in.
        kind=SymbolKind.UNKNOWN,
        file_path=_uri_to_path(uri),
        line=int(start.get("line", 0)),
        end_line=int(end.get("line")) if end.get("line") is not None else None,
        character=int(start.get("character", 0)),
    )


def _parse_location_to_reference(loc: dict[str, Any]) -> ReferenceInfo:
    uri = loc.get("uri") or ""
    rng = loc.get("range") or {}
    start = rng.get("start") or {}
    return ReferenceInfo(
        file_path=_uri_to_path(uri),
        line=int(start.get("line", 0)),
        character=int(start.get("character", 0)),
    )


def _parse_hover(result: Any) -> HoverResult | None:
    if not result:
        return None
    contents = result.get("contents") if isinstance(result, dict) else None
    if contents is None:
        return None
    text = ""
    language = ""
    if isinstance(contents, str):
        text = contents
    elif isinstance(contents, dict):
        # MarkupContent | MarkedString form.
        text = str(contents.get("value") or "")
        language = str(contents.get("language") or "")
    elif isinstance(contents, list):
        parts: list[str] = []
        for entry in contents:
            if isinstance(entry, str):
                parts.append(entry)
            elif isinstance(entry, dict):
                parts.append(str(entry.get("value") or ""))
        text = "\n".join(parts)
    return HoverResult(content=text, language=language)


_DIAG_SEVERITY = {
    1: DiagnosticSeverity.ERROR,
    2: DiagnosticSeverity.WARNING,
    3: DiagnosticSeverity.INFORMATION,
    4: DiagnosticSeverity.HINT,
}


def _parse_diagnostic(item: dict[str, Any], file_path: str) -> Diagnostic:
    rng = item.get("range") or {}
    start = rng.get("start") or {}
    end = rng.get("end") or {}
    severity_raw = item.get("severity")
    severity = _DIAG_SEVERITY.get(int(severity_raw), DiagnosticSeverity.ERROR) \
        if isinstance(severity_raw, (int, float)) \
        else DiagnosticSeverity.ERROR
    code_raw = item.get("code")
    code = str(code_raw) if code_raw is not None else ""
    return Diagnostic(
        file_path=file_path,
        line=int(start.get("line", 0)),
        character=int(start.get("character", 0)),
        end_line=int(end.get("line")) if end.get("line") is not None else None,
        end_character=int(end.get("character")) if end.get("character") is not None else None,
        severity=severity,
        message=str(item.get("message") or ""),
        source=str(item.get("source") or LSP_BACKEND_CHOSEN),
        code=code,
    )
