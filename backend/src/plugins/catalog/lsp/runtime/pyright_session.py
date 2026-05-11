"""Owns the Pyright language server subprocess and exposes typed query helpers."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import os
import shutil
from pathlib import Path
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
_DIAGNOSTICS_WAIT_S = 5.0
_DIAGNOSTICS_POLL_S = 0.05


class PyrightSpawnError(RuntimeError):
    """Raised when the Pyright language-server subprocess fails to start."""


class PyrightSession:
    """Long-lived Pyright session rooted at a stable layer-stack projection."""

    def __init__(
        self,
        *,
        manifest_key: str,
        lowerdir: str,
        workspace_root: str,
        projection_handle: Any,
        stable_root: str | None = None,
    ) -> None:
        self.manifest_key = manifest_key
        self.lowerdir = stable_root or lowerdir
        self.workspace_root = workspace_root
        self._stable_root = stable_root
        self._projection_handle = projection_handle
        self._proc: asyncio.subprocess.Process | None = None
        self._client: LspJsonRpcClient | None = None
        self._opened: set[str] = set()
        self._diagnostics: dict[str, list[dict[str, Any]]] = {}
        self._stale_diagnostics_after_change: dict[str, list[dict[str, Any]]] = {}
        self._lock = asyncio.Lock()
        self._started = False
        self._document_versions: dict[str, int] = {}
        self._document_hashes: dict[str, str] = {}
        if stable_root is not None:
            self._retarget_workspace_root(lowerdir)
        self._mapper = self._build_mapper()

    async def refresh_manifest(
        self,
        *,
        manifest_key: str,
        lowerdir: str,
        projection_handle: Any,
    ) -> None:
        """Retarget the stable workspace root to a new projection.

        The Pyright subprocess keeps the same ``rootUri``. We swap the stable
        root to the latest layer-stack snapshot, then notify Pyright about any
        already-open documents so semantic queries don't read stale content.
        If notification fails the caller must evict this session and start a
        clean one.
        """
        if manifest_key == self.manifest_key:
            projection_handle.release()
            return
        if self._stable_root is None:
            projection_handle.release()
            raise RuntimeError(
                "cannot refresh a Pyright session without a stable root"
            )

        async with self._lock:
            old_handle = self._projection_handle
            try:
                self._retarget_workspace_root(lowerdir)
            except Exception:
                projection_handle.release()
                raise

            self.manifest_key = manifest_key
            self._projection_handle = projection_handle
            self._mapper = self._build_mapper()
            if old_handle is not None:
                try:
                    old_handle.release()
                except Exception:
                    logger.debug("old projection lease release error", exc_info=True)
            await self._notify_workspace_refreshed()

    async def start(self) -> None:
        if self._started:
            return
        async with self._lock:
            if self._started:
                return
            try:
                await self._spawn()
                await self._initialize()
            except Exception:
                await self._cleanup_failed_start()
                raise
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
        await self.start()
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
        wait_for_diagnostics = bool(args.get("wait_for_diagnostics"))
        # Wait briefly for publishDiagnostics to arrive; Pyright emits
        # it shortly after didOpen / didChange but the first analysis can
        # take longer when the server just loaded a workspace.
        attempts = max(1, int(_DIAGNOSTICS_WAIT_S / _DIAGNOSTICS_POLL_S))
        for _ in range(attempts):
            entries = self._diagnostics.get(uri)
            if entries is not None:
                if wait_for_diagnostics and not entries:
                    await asyncio.sleep(_DIAGNOSTICS_POLL_S)
                    continue
                return {"diagnostics": entries}
            await asyncio.sleep(_DIAGNOSTICS_POLL_S)
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
        if self._stable_root is not None:
            with contextlib.suppress(OSError):
                Path(self._stable_root).unlink()

    async def _cleanup_failed_start(self) -> None:
        proc = self._proc
        self._client = None
        self._proc = None
        if proc is not None:
            with contextlib.suppress(Exception):
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=5.0)

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
        notify = self._client
        if notify is None:
            return uri
        if uri in self._opened:
            await self._sync_open_document(uri, file_path)
            return uri
        text = self._read_document_text(file_path)
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
        self._document_hashes[uri] = _text_hash(text)
        return uri

    async def _notify_workspace_refreshed(self) -> None:
        client = self._client
        if client is None or not self._started:
            return
        await client.notify(
            "workspace/didChangeWatchedFiles",
            {"changes": [{"uri": f"file://{self.lowerdir}", "type": 2}]},
        )
        for uri in tuple(self._opened):
            try:
                file_path = self._mapper.from_snapshot_uri(uri)
            except Exception:
                self._forget_document(uri)
                continue
            full_path = self._mapper.to_full_path(file_path)
            if not os.path.exists(full_path):
                await client.notify(
                    "textDocument/didClose",
                    {"textDocument": {"uri": uri}},
                )
                self._forget_document(uri)
                continue
            await self._sync_open_document(uri, file_path)

    async def _sync_open_document(self, uri: str, file_path: str) -> None:
        client = self._client
        if client is None:
            return
        text = self._read_document_text(file_path)
        text_hash = _text_hash(text)
        if self._document_hashes.get(uri) == text_hash:
            return
        self._invalidate_diagnostics(uri)
        await client.notify(
            "textDocument/didChange",
            {
                "textDocument": {
                    "uri": uri,
                    "version": self._next_document_version(uri),
                },
                "contentChanges": [{"text": text}],
            },
        )
        self._document_hashes[uri] = text_hash

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
                        "workspace": {
                            "workspaceFolders": True,
                            "didChangeWatchedFiles": {"dynamicRegistration": False},
                        },
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
                self._accept_diagnostics(uri, list(diags))

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

    def _build_mapper(self) -> PathMapper:
        return PathMapper(
            lowerdir=self.lowerdir,
            workspace_root=self.workspace_root,
        )

    def _read_document_text(self, file_path: str) -> str:
        try:
            with open(self._mapper.to_full_path(file_path), encoding="utf-8") as fh:
                return fh.read()
        except OSError:
            return ""

    def _invalidate_diagnostics(self, uri: str) -> None:
        previous = self._diagnostics.pop(uri, None)
        if previous is not None:
            self._stale_diagnostics_after_change[uri] = previous

    def _accept_diagnostics(self, uri: str, entries: list[dict[str, Any]]) -> None:
        stale = self._stale_diagnostics_after_change.get(uri)
        if stale is not None and entries == stale:
            return
        self._stale_diagnostics_after_change.pop(uri, None)
        self._diagnostics[uri] = entries

    def _forget_document(self, uri: str) -> None:
        self._opened.discard(uri)
        self._diagnostics.pop(uri, None)
        self._stale_diagnostics_after_change.pop(uri, None)
        self._document_hashes.pop(uri, None)
        self._document_versions.pop(uri, None)

    def _retarget_workspace_root(self, lowerdir: str) -> None:
        if self._stable_root is None:
            return
        target = Path(lowerdir)
        if not target.is_dir():
            raise RuntimeError(f"projection lowerdir does not exist: {lowerdir}")
        root = Path(self._stable_root)
        root.parent.mkdir(parents=True, exist_ok=True)
        tmp = root.with_name(f".{root.name}.{os.getpid()}.{id(self)}.tmp")
        with contextlib.suppress(OSError):
            tmp.unlink()
        try:
            os.symlink(target, tmp)
            os.replace(tmp, root)
        except Exception:
            with contextlib.suppress(OSError):
                tmp.unlink()
            raise


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
