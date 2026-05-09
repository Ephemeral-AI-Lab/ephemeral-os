"""Owns the Pyright language server subprocess and exposes typed query helpers."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from typing import Any

from plugins.catalog.lsp.runtime.lsp_jsonrpc import (
    JsonRpcError,
    LspJsonRpcClient,
)
from plugins.catalog.lsp.runtime.paths import PathMapper

__all__ = [
    "PyrightSession",
    "PyrightSpawnError",
]


logger = logging.getLogger(__name__)

_CONDA_HOOK = "/opt/miniconda3/etc/profile.d/conda.sh"
_DEFAULT_INIT_TIMEOUT_S = 30.0
_DEFAULT_REQUEST_TIMEOUT_S = 30.0


class PyrightSpawnError(RuntimeError):
    """Raised when the Pyright language-server subprocess fails to start."""


class PyrightSession:
    """Long-lived Pyright session keyed by ``(layer_stack_root, manifest_key)``."""

    def __init__(
        self,
        *,
        manifest_key: str,
        lowerdir: str,
        workspace_root: str,
        projection_handle: Any,
    ) -> None:
        self.manifest_key = manifest_key
        self.lowerdir = lowerdir
        self.workspace_root = workspace_root
        self._projection_handle = projection_handle
        self._proc: asyncio.subprocess.Process | None = None
        self._client: LspJsonRpcClient | None = None
        self._opened: set[str] = set()
        self._diagnostics: dict[str, list[dict[str, Any]]] = {}
        self._lock = asyncio.Lock()
        self._started = False
        self._document_versions: dict[str, int] = {}
        self._mapper = PathMapper(lowerdir=lowerdir, workspace_root=workspace_root)

    async def start(self) -> None:
        if self._started:
            return
        async with self._lock:
            if self._started:
                return
            await self._spawn()
            await self._initialize()
            self._started = True

    async def hover(self, args: dict[str, Any]) -> dict[str, Any]:
        await self.start()
        uri = await self._open_document(args["file_path"])
        params = {
            "textDocument": {"uri": uri},
            "position": {
                "line": int(args["line"]),
                "character": int(args["character"]),
            },
        }
        result = await self._send_request("textDocument/hover", params)
        return {"hover": result}

    async def find_definitions(self, args: dict[str, Any]) -> dict[str, Any]:
        return {
            "definitions": await self._point_query(
                "textDocument/definition", args
            )
        }

    async def find_references(self, args: dict[str, Any]) -> dict[str, Any]:
        await self.start()
        uri = await self._open_document(args["file_path"])
        params = {
            "textDocument": {"uri": uri},
            "position": {
                "line": int(args["line"]),
                "character": int(args["character"]),
            },
            "context": {
                "includeDeclaration": bool(args.get("include_declaration", True))
            },
        }
        raw = await self._send_request("textDocument/references", params)
        return {"references": self._normalize_locations(raw)}

    async def diagnostics(self, args: dict[str, Any]) -> dict[str, Any]:
        await self.start()
        uri = await self._open_document(args["file_path"])
        # Wait up to ~5s for publishDiagnostics to arrive; Pyright emits
        # it shortly after didOpen / didChange but the first analysis can
        # take longer when the server just loaded a workspace.
        for _ in range(100):
            entries = self._diagnostics.get(uri)
            if entries is not None:
                return {"diagnostics": entries}
            await asyncio.sleep(0.05)
        return {"diagnostics": self._diagnostics.get(uri, [])}

    async def query_symbols(self, args: dict[str, Any]) -> dict[str, Any]:
        await self.start()
        query = str(args.get("query", "")).strip()
        if "file_path" in args and args["file_path"]:
            uri = await self._open_document(str(args["file_path"]))
            params = {"textDocument": {"uri": uri}}
            raw = await self._send_request(
                "textDocument/documentSymbol", params
            )
        else:
            params = {"query": query}
            raw = await self._send_request("workspace/symbol", params)
        if isinstance(raw, list):
            symbols = raw
        else:
            symbols = []
        if query and isinstance(symbols, list):
            symbols = [
                s
                for s in symbols
                if isinstance(s, dict)
                and query.lower() in str(s.get("name", "")).lower()
            ]
        return {"symbols": symbols}

    async def evict(self) -> None:
        client = self._client
        proc = self._proc
        self._client = None
        self._proc = None
        self._started = False
        if client is not None:
            try:
                await client.close()
            except Exception:
                logger.debug("pyright client close error", exc_info=True)
        if proc is not None:
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except Exception:
                logger.debug("pyright proc terminate error", exc_info=True)
        try:
            self._projection_handle.release()
        except Exception:
            logger.debug("projection lease release error", exc_info=True)

    async def _point_query(
        self, method: str, args: dict[str, Any]
    ) -> list[dict[str, Any]]:
        await self.start()
        uri = await self._open_document(args["file_path"])
        params = {
            "textDocument": {"uri": uri},
            "position": {
                "line": int(args["line"]),
                "character": int(args["character"]),
            },
        }
        raw = await self._send_request(method, params)
        return self._normalize_locations(raw)

    def _normalize_locations(self, raw: Any) -> list[dict[str, Any]]:
        if not isinstance(raw, list):
            if isinstance(raw, dict):
                raw = [raw]
            else:
                return []
        out: list[dict[str, Any]] = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            uri = entry.get("uri") or entry.get("targetUri") or ""
            try:
                file_path = self._mapper.from_snapshot_uri(str(uri))
            except Exception:
                file_path = str(uri)
            range_obj = entry.get("range") or entry.get("targetRange")
            out.append({"file_path": file_path, "range": range_obj})
        return out

    async def _open_document(self, file_path: str) -> str:
        uri = self._mapper.to_snapshot_uri(file_path)
        full = self._mapper.to_full_path(file_path)
        try:
            with open(full, encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            text = ""
        notify = self._client
        if notify is None:
            return uri
        if uri in self._opened:
            # Already open: notify the new content via didChange so the
            # server picks up edits between calls. version is auto-bumped.
            self._diagnostics.pop(uri, None)
            await notify.notify(
                "textDocument/didChange",
                {
                    "textDocument": {
                        "uri": uri,
                        "version": self._next_document_version(uri),
                    },
                    "contentChanges": [{"text": text}],
                },
            )
            return uri
        await notify.notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": uri,
                    "languageId": "python",
                    "version": self._next_document_version(uri),
                    "text": text,
                }
            },
        )
        self._opened.add(uri)
        return uri

    async def _send_request(self, method: str, params: dict[str, Any]) -> Any:
        client = self._client
        if client is None:
            raise RuntimeError("pyright client is not started")
        try:
            return await client.request(method, params)
        except JsonRpcError as exc:
            return {"error": {"code": exc.code, "message": exc.message}}

    async def _spawn(self) -> None:
        argv = self._build_argv()
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env=os.environ.copy(),
            )
        except FileNotFoundError as exc:
            raise PyrightSpawnError(
                f"failed to spawn pyright-langserver: {exc}"
            ) from exc
        if proc.stdin is None or proc.stdout is None:
            raise PyrightSpawnError(
                "pyright-langserver subprocess streams are unavailable"
            )
        self._proc = proc
        self._client = LspJsonRpcClient(
            proc.stdin,
            proc.stdout,
            request_timeout_s=_DEFAULT_REQUEST_TIMEOUT_S,
            server_request_handler=self._handle_server_request,
        )
        self._client.add_notification_handler(self._on_notification)
        self._client.start()

    def _build_argv(self) -> list[str]:
        if os.path.exists(_CONDA_HOOK):
            return [
                "bash",
                "-lc",
                (
                    f". {_CONDA_HOOK} && conda activate testbed "
                    "&& export PATH=/tmp/eos-node22/bin:$PATH "
                    "&& exec pyright-langserver --stdio"
                ),
            ]
        binary = shutil.which("pyright-langserver")
        if binary:
            return [binary, "--stdio"]
        return [
            "bash",
            "-lc",
            "export PATH=/tmp/eos-node22/bin:$PATH && exec pyright-langserver --stdio",
        ]

    async def _initialize(self) -> None:
        client = self._client
        if client is None:
            return
        await asyncio.wait_for(
            client.request(
                "initialize",
                {
                    "processId": os.getpid(),
                    "rootUri": f"file://{self.lowerdir}",
                    "workspaceFolders": [
                        {"uri": f"file://{self.lowerdir}", "name": "layerstack"}
                    ],
                    "capabilities": {
                        "workspace": {"workspaceFolders": True},
                        "textDocument": {
                            "definition": {"linkSupport": True},
                            "hover": {"contentFormat": ["markdown", "plaintext"]},
                        },
                    },
                    "initializationOptions": {},
                },
            ),
            timeout=_DEFAULT_INIT_TIMEOUT_S,
        )
        await client.notify("initialized", {})

    async def _on_notification(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        if method == "textDocument/publishDiagnostics":
            params = message.get("params") or {}
            uri = str(params.get("uri", ""))
            diags = params.get("diagnostics") or []
            if isinstance(diags, list):
                self._diagnostics[uri] = list(diags)

    def _handle_server_request(self, message: dict[str, Any]) -> Any:
        method = message.get("method")
        params = message.get("params") or {}
        if method == "workspace/configuration":
            items = params.get("items") if isinstance(params, dict) else []
            return [{} for _ in items] if isinstance(items, list) else []
        if method == "workspace/workspaceFolders":
            return [{"uri": f"file://{self.lowerdir}", "name": "layerstack"}]
        return None

    def _next_document_version(self, uri: str) -> int:
        version = self._document_versions.get(uri, 0) + 1
        self._document_versions[uri] = version
        return version
