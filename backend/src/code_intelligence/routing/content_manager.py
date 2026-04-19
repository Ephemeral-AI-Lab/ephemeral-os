"""Local/sandbox-aware file content reader and writer."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import json
import shlex
from pathlib import Path
from typing import Any

from code_intelligence.hashing import content_hash
from tools.daytona_toolkit._daytona_utils import (
    _build_read_text_file_command,
    _build_write_text_file_command,
    _extract_exit_code,
    _supports_exec_transport,
    _upload_file_compat,
    _wrap_bash_command,
)

from code_intelligence._async_bridge import run_sync

FileReadResult = tuple[str, bool]
FileReadResults = dict[str, FileReadResult]


@dataclass(frozen=True)
class CheckedApplyChange:
    """One exact-base checked write/delete for a batch apply."""

    file_path: str
    base_hash: str
    base_existed: bool
    final_content: str | None


@dataclass(frozen=True)
class CheckedApplyResult:
    """Outcome of an exact-base checked batch apply."""

    success: bool
    conflict_path: str | None = None
    conflict_reason: str = ""
    message: str = ""


class ContentManager:
    """Read and write file content, routing to a sandbox when one is bound."""

    def __init__(self, workspace_root: str, sandbox: Any = None) -> None:
        del workspace_root
        self._sandbox = sandbox

    def bind_sandbox(self, sandbox: Any) -> None:
        """Update the sandbox handle for subsequent reads/writes."""
        self._sandbox = sandbox

    def read(self, file_path: str, *, allow_missing: bool = False) -> FileReadResult:
        """Read *file_path* returning ``(content, existed)``."""
        if self._sandbox is None:
            return self._read_local(file_path, allow_missing=allow_missing)
        return self._read_remote(file_path, allow_missing=allow_missing)

    def read_many(
        self,
        file_paths: list[str],
        *,
        allow_missing: bool = False,
    ) -> FileReadResults:
        """Read multiple files, batching remote sandbox reads when possible."""
        unique_paths = list(dict.fromkeys(file_paths))
        if not unique_paths:
            return {}
        if self._sandbox is None:
            return {
                path: self._read_local(path, allow_missing=allow_missing)
                for path in unique_paths
            }
        if _supports_exec_transport(self._sandbox):
            try:
                return self._read_remote_batch(unique_paths, allow_missing=allow_missing)
            except (FileNotFoundError, RuntimeError, json.JSONDecodeError, OSError):
                if not allow_missing:
                    raise
        return {
            path: self._read_remote(path, allow_missing=allow_missing)
            for path in unique_paths
        }

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

    def apply_many(self, changes: list[tuple[str, str | None]]) -> None:
        """Apply many writes/deletes through one sandbox round trip when possible."""
        if not changes:
            return
        if self._sandbox is None:
            for file_path, content in changes:
                if content is None:
                    self.delete(file_path)
                else:
                    self.write(file_path, content)
            return
        if _supports_exec_transport(self._sandbox):
            self._apply_remote_batch(changes)
            return
        for file_path, content in changes:
            if content is None:
                self.delete(file_path)
            else:
                self.write(file_path, content)

    def apply_many_with_base_check(
        self,
        changes: list[CheckedApplyChange],
    ) -> CheckedApplyResult:
        """Verify exact base hashes and apply all changes in one round trip.

        This is an optimization for clean OCC batches. It intentionally does
        not attempt merge fallback; callers should fall back to a full read path
        when it returns ``conflict_reason == "base_mismatch"``.
        """
        if not changes:
            return CheckedApplyResult(success=True)
        if self._sandbox is None:
            return self._apply_local_batch_checked(changes)
        if _supports_exec_transport(self._sandbox):
            return self._apply_remote_batch_checked(changes)
        return CheckedApplyResult(
            success=False,
            conflict_reason="unsupported",
            message="sandbox process.exec checked apply is unavailable",
        )

    # -- Private --------------------------------------------------------------

    @staticmethod
    def _read_local(file_path: str, *, allow_missing: bool) -> FileReadResult:
        path = Path(file_path)
        if not path.exists():
            if allow_missing:
                return "", False
            raise FileNotFoundError(file_path)
        return path.read_text(encoding="utf-8"), True

    def _read_remote(self, file_path: str, *, allow_missing: bool) -> FileReadResult:
        process = getattr(self._sandbox, "process", None)
        if _supports_exec_transport(self._sandbox):
            try:
                response = run_sync(process.exec(_wrap_bash_command(_build_read_text_file_command(file_path))))
                cleaned, exit_code = _extract_exit_code(
                    getattr(response, "result", "") or "",
                    fallback_exit_code=getattr(response, "exit_code", None),
                )
                if exit_code in (0, None):
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

    def _read_remote_batch(
        self,
        file_paths: list[str],
        *,
        allow_missing: bool,
    ) -> FileReadResults:
        process = getattr(self._sandbox, "process", None)
        script = """
import json
import pathlib
import sys

files = {}
for raw_path in sys.argv[1:]:
    path = pathlib.Path(raw_path)
    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        files[raw_path] = {"exists": False, "content": ""}
    else:
        files[raw_path] = {"exists": True, "content": content}
print(json.dumps(files))
"""
        command = (
            f"python3 -c {shlex.quote(script)} "
            + " ".join(shlex.quote(path) for path in file_paths)
        )
        response = run_sync(process.exec(_wrap_bash_command(command)))
        cleaned, exit_code = _extract_exit_code(
            getattr(response, "result", "") or "",
            fallback_exit_code=getattr(response, "exit_code", None),
        )
        if exit_code not in (0, None):
            raise RuntimeError(cleaned or "batch read failed")
        payload = json.loads(cleaned or "{}")
        results: FileReadResults = {}
        for path in file_paths:
            item = payload.get(path) if isinstance(payload, dict) else None
            if not isinstance(item, dict) or not item.get("exists"):
                if allow_missing:
                    results[path] = ("", False)
                    continue
                raise FileNotFoundError(path)
            results[path] = (str(item.get("content", "") or ""), True)
        return results

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
            run_sync(_upload_file_compat(self._sandbox, payload, file_path))
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

    def _apply_remote_batch(self, changes: list[tuple[str, str | None]]) -> None:
        process = getattr(self._sandbox, "process", None)
        payload = [
            {
                "path": path,
                "content_b64": (
                    None
                    if content is None
                    else base64.b64encode(content.encode("utf-8")).decode("ascii")
                ),
            }
            for path, content in changes
        ]
        encoded = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
        script = f"""
import base64
import json
import pathlib

ops = json.loads(base64.b64decode({encoded!r}).decode("utf-8"))
for item in ops:
    path = pathlib.Path(item["path"])
    content_b64 = item.get("content_b64")
    if content_b64 is None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        continue
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(base64.b64decode(content_b64))
"""
        command = f"python3 -c {shlex.quote(script)}"
        response = run_sync(process.exec(_wrap_bash_command(command)))
        cleaned, exit_code = _extract_exit_code(
            getattr(response, "result", "") or "",
            fallback_exit_code=getattr(response, "exit_code", None),
        )
        if exit_code not in (0, None):
            raise RuntimeError(cleaned or "batch apply failed")

    @staticmethod
    def _apply_local_batch_checked(
        changes: list[CheckedApplyChange],
    ) -> CheckedApplyResult:
        backups: list[tuple[Path, bool, str]] = []
        for change in changes:
            path = Path(change.file_path)
            try:
                current = path.read_text(encoding="utf-8")
            except FileNotFoundError:
                existed = False
                current = ""
                current_hash = ""
            else:
                existed = True
                current_hash = content_hash(current)
            backups.append((path, existed, current))
            if change.base_existed:
                if not existed or current_hash != change.base_hash:
                    return CheckedApplyResult(
                        success=False,
                        conflict_path=change.file_path,
                        conflict_reason="base_mismatch",
                        message="file content changed before checked apply",
                    )
            elif existed:
                return CheckedApplyResult(
                    success=False,
                    conflict_path=change.file_path,
                    conflict_reason="base_mismatch",
                    message="file already exists; base said it did not",
                )

        try:
            for change in changes:
                path = Path(change.file_path)
                if change.final_content is None:
                    try:
                        path.unlink()
                    except FileNotFoundError:
                        pass
                    continue
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(change.final_content, encoding="utf-8")
        except Exception as exc:
            for path, existed, content in reversed(backups):
                try:
                    if existed:
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_text(content, encoding="utf-8")
                    else:
                        path.unlink(missing_ok=True)
                except Exception:
                    pass
            return CheckedApplyResult(
                success=False,
                conflict_reason="write_failed",
                message=str(exc),
            )
        return CheckedApplyResult(success=True)

    def _apply_remote_batch_checked(
        self,
        changes: list[CheckedApplyChange],
    ) -> CheckedApplyResult:
        process = getattr(self._sandbox, "process", None)
        payload = [
            {
                "path": change.file_path,
                "base_hash": change.base_hash,
                "base_existed": change.base_existed,
                "content_b64": (
                    None
                    if change.final_content is None
                    else base64.b64encode(
                        change.final_content.encode("utf-8"),
                    ).decode("ascii")
                ),
            }
            for change in changes
        ]
        encoded = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
        script = f"""
import base64
import hashlib
import json
import pathlib

ops = json.loads(base64.b64decode({encoded!r}).decode("utf-8"))
backups = []
for item in ops:
    path = pathlib.Path(item["path"])
    try:
        current = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        existed = False
        current = ""
        current_hash = ""
    else:
        existed = True
        current_hash = hashlib.sha256(current.encode("utf-8")).hexdigest()[:16]
    backups.append({{
        "path": item["path"],
        "existed": existed,
        "content_b64": base64.b64encode(current.encode("utf-8")).decode("ascii"),
    }})
    if item.get("base_existed"):
        if (not existed) or current_hash != item.get("base_hash", ""):
            print(json.dumps({{
                "ok": False,
                "reason": "base_mismatch",
                "path": item["path"],
                "message": "file content changed before checked apply",
            }}))
            raise SystemExit(0)
    elif existed:
        print(json.dumps({{
            "ok": False,
            "reason": "base_mismatch",
            "path": item["path"],
            "message": "file already exists; base said it did not",
        }}))
        raise SystemExit(0)

try:
    for item in ops:
        path = pathlib.Path(item["path"])
        content_b64 = item.get("content_b64")
        if content_b64 is None:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(content_b64))
except Exception as exc:
    for backup in reversed(backups):
        path = pathlib.Path(backup["path"])
        try:
            if backup.get("existed"):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(base64.b64decode(backup["content_b64"]))
            else:
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
        except Exception:
            pass
    print(json.dumps({{
        "ok": False,
        "reason": "write_failed",
        "path": "",
        "message": str(exc),
    }}))
    raise SystemExit(0)

print(json.dumps({{"ok": True}}))
"""
        command = f"python3 -c {shlex.quote(script)}"
        response = run_sync(process.exec(_wrap_bash_command(command)))
        cleaned, exit_code = _extract_exit_code(
            getattr(response, "result", "") or "",
            fallback_exit_code=getattr(response, "exit_code", None),
        )
        if exit_code not in (0, None):
            raise RuntimeError(cleaned or "checked batch apply failed")
        payload_out = json.loads(cleaned or "{}")
        if not isinstance(payload_out, dict):
            raise RuntimeError("checked batch apply returned invalid JSON")
        if payload_out.get("ok"):
            return CheckedApplyResult(success=True)
        return CheckedApplyResult(
            success=False,
            conflict_path=str(payload_out.get("path") or "") or None,
            conflict_reason=str(payload_out.get("reason") or "failed"),
            message=str(payload_out.get("message") or ""),
        )

    @staticmethod
    def _is_missing_error(exc: Exception) -> bool:
        if isinstance(exc, FileNotFoundError):
            return True
        text = str(exc).lower()
        return "not found" in text or "no such file" in text or "does not exist" in text
