"""Local/sandbox-aware file content reader and writer."""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

from code_intelligence._async_bridge import run_sync
from tools.daytona_toolkit._daytona_utils import (
    _build_read_text_file_command,
    _build_write_text_file_command,
    _extract_exit_code,
    _supports_exec_transport,
    _wrap_bash_command,
)


class ContentManager:
    """Read and write file content, routing to a sandbox when one is bound."""

    def __init__(self, workspace_root: str, sandbox: Any = None) -> None:
        self._workspace_root = workspace_root
        self._sandbox = sandbox

    def bind_sandbox(self, sandbox: Any) -> None:
        """Update the sandbox handle for subsequent reads/writes."""
        self._sandbox = sandbox

    def read(self, file_path: str, *, allow_missing: bool = False) -> tuple[str, bool]:
        """Read *file_path* returning ``(content, existed)``."""
        if self._sandbox is None:
            return self._read_local(file_path, allow_missing=allow_missing)
        return self._read_remote(file_path, allow_missing=allow_missing)

    def write(self, file_path: str, content: str) -> None:
        """Write *content* to *file_path*, preferring the sandbox when bound."""
        if self._sandbox is None:
            path = Path(file_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return
        self._write_remote(file_path, content.encode("utf-8"))

    def delete(self, file_path: str) -> None:
        """Delete *file_path*, preferring the sandbox when one is bound."""
        if self._sandbox is None:
            path = Path(file_path)
            try:
                path.unlink()
            except FileNotFoundError:
                return
            return
        self._delete_remote(file_path)

    # -- Private --------------------------------------------------------------

    @staticmethod
    def _read_local(file_path: str, *, allow_missing: bool) -> tuple[str, bool]:
        path = Path(file_path)
        if not path.exists():
            if allow_missing:
                return "", False
            raise FileNotFoundError(file_path)
        return path.read_text(encoding="utf-8"), True

    def _read_remote(self, file_path: str, *, allow_missing: bool) -> tuple[str, bool]:
        process = getattr(self._sandbox, "process", None)
        if _supports_exec_transport(self._sandbox):
            try:
                response = run_sync(process.exec(_wrap_bash_command(_build_read_text_file_command(file_path))))
                cleaned, exit_code = _extract_exit_code(
                    getattr(response, "result", "") or "",
                    fallback_exit_code=getattr(response, "exit_code", None),
                )
                if exit_code in (0, None):
                    import json

                    payload = json.loads(cleaned or "{}")
                    if not payload.get("exists"):
                        if allow_missing:
                            return "", False
                        raise FileNotFoundError(file_path)
                    return str(payload.get("content", "") or ""), True
            except Exception as exc:
                if allow_missing and self._is_missing_error(exc):
                    return "", False
                raise
        fs = getattr(self._sandbox, "fs", None)
        download_fn = getattr(fs, "download_file", None)
        if callable(download_fn):
            try:
                raw = run_sync(download_fn(file_path))
            except Exception as exc:
                if allow_missing and self._is_missing_error(exc):
                    return "", False
                raise
            if isinstance(raw, bytes):
                return raw.decode("utf-8"), True
            return str(raw), True
        raise RuntimeError("Sandbox process.exec text read is unavailable")

    def _write_remote(self, file_path: str, payload: bytes) -> None:
        process = getattr(self._sandbox, "process", None)
        if _supports_exec_transport(self._sandbox):
            try:
                text = payload.decode("utf-8")
                response = run_sync(
                    process.exec(_wrap_bash_command(_build_write_text_file_command(file_path, text)))
                )
                cleaned, exit_code = _extract_exit_code(
                    getattr(response, "result", "") or "",
                    fallback_exit_code=getattr(response, "exit_code", None),
                )
                if exit_code in (0, None):
                    return
                raise RuntimeError(cleaned or f"write failed for {file_path}")
            except UnicodeDecodeError:
                raise RuntimeError("Binary payload requires sandbox fs fallback")
            raise
        fs = getattr(self._sandbox, "fs", None)
        upload_fn = getattr(fs, "upload_file", None)
        if callable(upload_fn):
            try:
                result = upload_fn(payload, file_path)
            except (AttributeError, TypeError) as exc:
                if "decode" not in str(exc) and "bytes-like object" not in str(exc):
                    raise
                result = upload_fn(file_path, payload)
            run_sync(result)
            return
        raise RuntimeError("Sandbox process.exec text write is unavailable")

    def _delete_remote(self, file_path: str) -> None:
        process = getattr(self._sandbox, "process", None)
        if not _supports_exec_transport(self._sandbox):
            raise RuntimeError("Sandbox process has no exec method")
        response = run_sync(process.exec(_wrap_bash_command(f"rm -f {shlex.quote(file_path)}")))
        cleaned, exit_code = _extract_exit_code(
            getattr(response, "result", "") or "",
            fallback_exit_code=getattr(response, "exit_code", None),
        )
        if exit_code not in (0, None):
            raise RuntimeError(cleaned or f"delete failed for {file_path}")

    @staticmethod
    def _is_missing_error(exc: Exception) -> bool:
        if isinstance(exc, FileNotFoundError):
            return True
        text = str(exc).lower()
        return "not found" in text or "no such file" in text or "does not exist" in text
