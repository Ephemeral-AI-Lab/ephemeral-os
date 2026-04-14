"""Local/sandbox-aware file content reader and writer."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from code_intelligence._async_bridge import run_sync


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
            Path(file_path).write_text(content, encoding="utf-8")
            return
        self._write_remote(file_path, content.encode("utf-8"))

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
        try:
            raw = run_sync(self._sandbox.fs.download_file(file_path))
        except Exception as exc:
            if allow_missing and self._is_missing_error(exc):
                return "", False
            raise
        if isinstance(raw, bytes):
            return raw.decode("utf-8"), True
        return str(raw), True

    def _write_remote(self, file_path: str, payload: bytes) -> None:
        fs = self._sandbox.fs
        upload_fn = getattr(fs, "upload_file", None)
        if not callable(upload_fn):
            raise RuntimeError("Sandbox fs has no upload_file method")
        # Prefer canonical (payload, path) order; fallback to (path, payload).
        try:
            result = upload_fn(payload, file_path)
        except (AttributeError, TypeError) as exc:
            if "decode" not in str(exc) and "bytes-like object" not in str(exc):
                raise
            result = upload_fn(file_path, payload)
        run_sync(result)

    @staticmethod
    def _is_missing_error(exc: Exception) -> bool:
        if isinstance(exc, FileNotFoundError):
            return True
        text = str(exc).lower()
        return "not found" in text or "no such file" in text or "does not exist" in text
