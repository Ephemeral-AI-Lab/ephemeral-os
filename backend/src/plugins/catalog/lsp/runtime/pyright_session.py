"""Owns the Pyright language server subprocess and exposes typed query helpers."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import os
import shutil
from typing import Any
from urllib.parse import quote, unquote, urlparse

from plugins.catalog.lsp.runtime.lsp_jsonrpc import (
    JsonRpcError,
    LspJsonRpcClient,
)

__all__ = [
    "PyrightSession",
    "PyrightSpawnError",
]


logger = logging.getLogger(__name__)

_CONDA_HOOK = "/opt/miniconda3/etc/profile.d/conda.sh"
_DEFAULT_INIT_TIMEOUT_S = 30.0
_DEFAULT_REQUEST_TIMEOUT_S = 30.0
_REFERENCES_TIMEOUT_S = 5.0
_DIAGNOSTICS_WAIT_S = 5.0
_DIAGNOSTICS_POLL_S = 0.05


class PyrightSpawnError(RuntimeError):
    """Raised when the Pyright language-server subprocess fails to start."""


class PyrightSession:
    """Long-lived Pyright session rooted directly at the daemon overlay."""

    def __init__(
        self,
        *,
        manifest_key: str,
        workspace_root: str,
    ) -> None:
        self.manifest_key = manifest_key
        self.workspace_root = str(workspace_root or "/testbed").rstrip("/") or "/"
        self.lowerdir = self.workspace_root
        self._proc: asyncio.subprocess.Process | None = None
        self._client: LspJsonRpcClient | None = None
        self._opened: set[str] = set()
        self._lock = asyncio.Lock()
        self._started = False
        self._document_versions: dict[str, int] = {}
        self._document_hashes: dict[str, str] = {}

    async def refresh_manifest(
        self,
        *,
        manifest_key: str,
    ) -> None:
        """Mark the daemon overlay as refreshed and resync open documents."""
        if manifest_key == self.manifest_key:
            return

        async with self._lock:
            self.manifest_key = manifest_key
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
            except BaseException:
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
        timeout_s = _optional_positive_float(
            args.get("timeout_s"),
            default=_REFERENCES_TIMEOUT_S,
        )
        try:
            raw = await asyncio.wait_for(
                self._send_request("textDocument/references", params),
                timeout=timeout_s,
            )
        except TimeoutError:
            await self.evict()
            return {"references": [], "timeout": True}
        return {"references": self._normalize_locations(raw)}

    async def diagnostics(self, args: dict[str, Any]) -> dict[str, Any]:
        await self.start()
        uri = await self._open_document(args["file_path"])
        wait_for_diagnostics = bool(args.get("wait_for_diagnostics"))
        if not wait_for_diagnostics:
            return await self._pull_diagnostics(uri)

        deadline = asyncio.get_running_loop().time() + _DIAGNOSTICS_WAIT_S
        while True:
            result = await self._pull_diagnostics(uri)
            if result.get("diagnostics") or result.get("error"):
                return result
            if asyncio.get_running_loop().time() >= deadline:
                return result
            await asyncio.sleep(_DIAGNOSTICS_POLL_S)

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

    async def rename(self, args: dict[str, Any]) -> dict[str, Any]:
        await self.start()
        uri = await self._open_document(args["file_path"])
        params = {
            "textDocument": {"uri": uri},
            "position": {
                "line": int(args["line"]),
                "character": int(args["character"]),
            },
            "newName": str(args["new_name"]),
        }
        raw = await self._send_request("textDocument/rename", params)
        return raw if isinstance(raw, dict) else {}

    async def format_document(self, args: dict[str, Any]) -> dict[str, Any]:
        await self.start()
        uri = await self._open_document(args["file_path"])
        raw = await self._send_request(
            "textDocument/formatting",
            {
                "textDocument": {"uri": uri},
                "options": args.get("options") or {"tabSize": 4, "insertSpaces": True},
            },
        )
        if not isinstance(raw, list):
            return {"changes": {}}
        return {"changes": {uri: raw}}

    async def code_actions(self, args: dict[str, Any]) -> dict[str, Any]:
        await self.start()
        uri = await self._open_document(args["file_path"])
        raw_range = args.get("range")
        if isinstance(raw_range, dict):
            range_obj = raw_range
        else:
            line = int(args.get("line", 0))
            character = int(args.get("character", 0))
            range_obj = {
                "start": {"line": line, "character": character},
                "end": {"line": line, "character": character},
            }
        raw = await self._send_request(
            "textDocument/codeAction",
            {
                "textDocument": {"uri": uri},
                "range": range_obj,
                "context": {
                    "diagnostics": args.get("diagnostics") or [],
                    **(
                        {"only": args["only"]}
                        if isinstance(args.get("only"), list)
                        else {}
                    ),
                },
            },
        )
        return {"code_actions": raw if isinstance(raw, list) else []}

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
                file_path = self._from_uri(str(uri))
            except Exception:
                file_path = str(uri)
            range_obj = entry.get("range") or entry.get("targetRange")
            out.append({"file_path": file_path, "range": range_obj})
        return out

    async def _open_document(self, file_path: str) -> str:
        uri = self._to_uri(file_path)
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
            {"changes": [{"uri": self._workspace_uri(), "type": 2}]},
        )
        for uri in tuple(self._opened):
            try:
                file_path = self._from_uri(uri)
            except Exception:
                self._forget_document(uri)
                continue
            full_path = self._to_full_path(file_path)
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

    async def _pull_diagnostics(self, uri: str) -> dict[str, Any]:
        raw = await self._send_request(
            "textDocument/diagnostic",
            {"textDocument": {"uri": uri}},
        )
        if isinstance(raw, dict) and "error" in raw:
            return {"diagnostics": [], "error": raw["error"]}
        if not isinstance(raw, dict):
            return {
                "diagnostics": [],
                "error": {
                    "message": (
                        "unexpected Pyright diagnostic response type: "
                        f"{type(raw).__name__}"
                    )
                },
            }

        items = raw.get("items")
        if not isinstance(items, list):
            return {
                "diagnostics": [],
                "error": {
                    "message": "unexpected Pyright diagnostic response: missing items"
                },
            }

        diagnostics = list(items)
        return self._diagnostic_result(diagnostics, raw)

    def _diagnostic_result(
        self,
        diagnostics: list[dict[str, Any]],
        raw: dict[str, Any],
    ) -> dict[str, Any]:
        result: dict[str, Any] = {"diagnostics": diagnostics}
        if "kind" in raw:
            result["kind"] = raw["kind"]
        if "resultId" in raw:
            result["result_id"] = raw["resultId"]
        return result

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
                    "rootUri": self._workspace_uri(),
                    "workspaceFolders": [
                        {"uri": self._workspace_uri(), "name": "testbed"}
                    ],
                    "capabilities": {
                        "workspace": {
                            "workspaceFolders": True,
                            "didChangeWatchedFiles": {"dynamicRegistration": False},
                        },
                        "textDocument": {
                            "diagnostic": {
                                "dynamicRegistration": False,
                                "relatedDocumentSupport": True,
                            },
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
        del message

    def _handle_server_request(self, message: dict[str, Any]) -> Any:
        method = message.get("method")
        params = message.get("params") or {}
        if method == "workspace/configuration":
            items = params.get("items") if isinstance(params, dict) else []
            return [{} for _ in items] if isinstance(items, list) else []
        if method == "workspace/workspaceFolders":
            return [{"uri": self._workspace_uri(), "name": "testbed"}]
        return None

    def _next_document_version(self, uri: str) -> int:
        version = self._document_versions.get(uri, 0) + 1
        self._document_versions[uri] = version
        return version

    def _read_document_text(self, file_path: str) -> str:
        try:
            with open(self._to_full_path(file_path), encoding="utf-8") as fh:
                return fh.read()
        except OSError:
            return ""

    def _forget_document(self, uri: str) -> None:
        self._opened.discard(uri)
        self._document_hashes.pop(uri, None)
        self._document_versions.pop(uri, None)

    def _workspace_uri(self) -> str:
        return self._path_uri(self.workspace_root)

    def _to_uri(self, file_path: str) -> str:
        return self._path_uri(self._to_full_path(file_path))

    def _from_uri(self, uri: str) -> str:
        parsed = urlparse(uri)
        if parsed.scheme != "file":
            raise ValueError(f"unsupported uri scheme: {uri}")
        path = unquote(parsed.path)
        return self._to_agent_path(path)

    def _to_agent_path(self, path: str) -> str:
        full = os.path.normpath(path)
        root = self.workspace_root
        if full == root:
            return root
        if full.startswith(f"{root}/"):
            return full
        return full

    def _to_full_path(self, file_path: str) -> str:
        raw = str(file_path or "").strip()
        if raw.startswith("file://"):
            return self._to_full_path(self._from_uri(raw))
        if not raw:
            return self.workspace_root
        if os.path.isabs(raw):
            normalized = os.path.normpath(raw)
            if normalized == self.workspace_root or normalized.startswith(
                f"{self.workspace_root}/"
            ):
                return normalized
            return os.path.normpath(os.path.join(self.workspace_root, raw.lstrip("/")))
        return os.path.normpath(os.path.join(self.workspace_root, raw))

    def _path_uri(self, path: str) -> str:
        normalized = os.path.normpath(path)
        return "file://" + quote(normalized)


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _optional_positive_float(value: object, *, default: float) -> float:
    try:
        parsed = float(value) if value is not None else default
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default
