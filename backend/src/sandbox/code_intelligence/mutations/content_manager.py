"""Local/sandbox-aware file content reader and writer."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import shlex
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from sandbox.api.errors import SandboxTransportError
from sandbox.api.models import CheckedWriteSpec
from sandbox.api.transport import SandboxTransport
from sandbox.code_intelligence.core.hashing import content_hash
from sandbox.code_intelligence.core.path_utils import resolve_workspace_path

from sandbox.client.async_bridge import run_sync

logger = logging.getLogger(__name__)

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

    def __init__(
        self,
        workspace_root: str,
        sandbox: Any = None,
        *,
        transport: SandboxTransport | None = None,
        sandbox_id: str = "",
    ) -> None:
        self._workspace_root = str(workspace_root or "")
        self._sandbox = sandbox
        self._transport = transport
        self._sandbox_id = sandbox_id

    def bind_sandbox(self, sandbox: Any) -> None:
        """Update the sandbox handle for subsequent reads/writes."""
        self._sandbox = sandbox

    def _use_transport(self) -> bool:
        return self._transport is not None and bool(self._sandbox_id)

    def read(self, file_path: str, *, allow_missing: bool = False) -> FileReadResult:
        """Read *file_path* returning ``(content, existed)``."""
        resolved_path = self._resolve_path(file_path)
        if self._use_transport():
            return self._read_via_transport(resolved_path, allow_missing=allow_missing)
        fs = getattr(self._sandbox, "fs", None) if self._sandbox is not None else None
        if fs is not None and callable(getattr(fs, "download_file", None)):
            return self._read_fs(resolved_path, allow_missing=allow_missing)
        return self._read_local(resolved_path, allow_missing=allow_missing)

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
        resolved_by_path = {path: self._resolve_path(path) for path in unique_paths}
        if self._use_transport():
            return self._read_many_via_transport(
                unique_paths,
                resolved_by_path,
                allow_missing=allow_missing,
            )
        if self._sandbox is None:
            return {
                path: self._read_local(resolved_by_path[path], allow_missing=allow_missing)
                for path in unique_paths
            }
        resolved_paths = list(dict.fromkeys(resolved_by_path.values()))
        via_fs = self._read_fs_batch(resolved_paths, allow_missing=allow_missing)
        if via_fs is not None:
            return {path: via_fs[resolved_by_path[path]] for path in unique_paths}
        return {path: self.read(path, allow_missing=allow_missing) for path in unique_paths}

    def list_folder_files(self, folder: str) -> list[str]:
        """Return every regular file under *folder* as absolute paths."""
        resolved_folder = self._resolve_path(folder)
        root = Path(resolved_folder)
        if not root.exists():
            raise FileNotFoundError(folder)
        if not root.is_dir():
            raise NotADirectoryError(folder)
        return sorted(str(path) for path in root.rglob("*") if path.is_file())

    def write(self, file_path: str, content: str) -> None:
        """Write *content* to *file_path*, preferring the sandbox when bound."""
        resolved_path = self._resolve_path(file_path)
        if self._use_transport():
            run_sync(
                self._transport.write_bytes(
                    self._sandbox_id, resolved_path, content.encode("utf-8"),
                )
            )
            return
        fs = getattr(self._sandbox, "fs", None) if self._sandbox is not None else None
        if fs is not None and callable(getattr(fs, "upload_file", None)):
            run_sync(fs.upload_file(content.encode("utf-8"), resolved_path))
            return
        path = Path(resolved_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def delete(self, file_path: str) -> None:
        """Delete *file_path*, preferring the sandbox when one is bound."""
        resolved_path = self._resolve_path(file_path)
        if self._use_transport():
            result = run_sync(
                self._transport.exec(
                    self._sandbox_id, f"rm -f {shlex.quote(resolved_path)}",
                )
            )
            if result.exit_code not in (0, None):
                raise RuntimeError(
                    result.stdout or f"delete failed for {file_path}"
                )
            return
        fs = getattr(self._sandbox, "fs", None) if self._sandbox is not None else None
        delete_fn = getattr(fs, "delete_file", None) if fs is not None else None
        if callable(delete_fn):
            run_sync(delete_fn(resolved_path))
            return
        path = Path(resolved_path)
        try:
            path.unlink()
        except FileNotFoundError:
            return

    def apply_many(self, changes: list[tuple[str, str | None]]) -> None:
        """Apply many writes/deletes through one sandbox round trip when possible."""
        if not changes:
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
        if self._use_transport():
            return self._apply_via_transport(changes)
        return self._apply_local_batch_checked(changes)

    # -- Private --------------------------------------------------------------

    def _resolve_path(self, file_path: str) -> str:
        return resolve_workspace_path(file_path, self._workspace_root)

    @staticmethod
    def _read_local(file_path: str, *, allow_missing: bool) -> FileReadResult:
        path = Path(file_path)
        if not path.exists():
            if allow_missing:
                return "", False
            raise FileNotFoundError(file_path)
        return path.read_text(encoding="utf-8"), True

    def _read_fs(self, file_path: str, *, allow_missing: bool) -> FileReadResult:
        fs = self._sandbox.fs
        try:
            payload = run_sync(fs.download_file(file_path))
        except FileNotFoundError:
            if allow_missing:
                return "", False
            raise
        if isinstance(payload, bytes):
            return payload.decode("utf-8"), True
        return str(payload), True

    def _read_fs_batch(
        self,
        file_paths: list[str],
        *,
        allow_missing: bool,
    ) -> FileReadResults | None:
        fs = getattr(self._sandbox, "fs", None)
        download_files_fn = getattr(fs, "download_files", None) if fs is not None else None
        if not callable(download_files_fn):
            return None

        try:
            requests = [SimpleNamespace(source=path) for path in file_paths]
            responses = run_sync(download_files_fn(requests))
        except Exception:
            logger.debug("Batch download_files failed", exc_info=True)
            return None

        payload_by_path: dict[str, Any] = {}
        for response in responses or ():
            source = getattr(response, "source", None)
            if isinstance(source, str):
                payload_by_path[source] = response

        results: FileReadResults = {}
        for path in file_paths:
            response = payload_by_path.get(path)
            if response is None or getattr(response, "error", None):
                if allow_missing:
                    results[path] = ("", False)
                    continue
                raise FileNotFoundError(path)
            payload = getattr(response, "result", None)
            if payload is None:
                if allow_missing:
                    results[path] = ("", False)
                    continue
                raise FileNotFoundError(path)
            content = payload.decode("utf-8") if isinstance(payload, bytes) else str(payload)
            results[path] = (content, True)
        return results

    def _read_remote(self, file_path: str, *, allow_missing: bool) -> FileReadResult:
        process = self._process()
        response = run_sync(
            process.exec(wrap_bash_command(build_read_text_file_command(file_path)))
        )
        cleaned, exit_code = extract_exit_code(
            getattr(response, "result", "") or "",
            fallback_exit_code=getattr(response, "exit_code", None),
        )
        if exit_code not in (0, None):
            raise RuntimeError(cleaned or f"read failed for {file_path}")
        payload = json.loads(cleaned or "{}")
        if not payload.get("exists"):
            if allow_missing:
                return "", False
            raise FileNotFoundError(file_path)
        return str(payload.get("content", "") or ""), True

    def _read_remote_batch(
        self,
        file_paths: list[str],
        *,
        allow_missing: bool,
    ) -> FileReadResults:
        process = self._process()
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
        command = f"python3 -c {shlex.quote(script)} " + " ".join(
            shlex.quote(path) for path in file_paths
        )
        response = run_sync(process.exec(wrap_bash_command(command)))
        cleaned, exit_code = extract_exit_code(
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

    def _list_remote_folder_files(self, folder: str) -> list[str]:
        process = self._process()
        probe_cmd = (
            f"if [ ! -e {shlex.quote(folder)} ]; then echo __MISSING__; "
            f"elif [ ! -d {shlex.quote(folder)} ]; then echo __NOTDIR__; "
            f"else find {shlex.quote(folder)} -type f -print; fi"
        )
        response = run_sync(process.exec(wrap_bash_command(probe_cmd)))
        cleaned, exit_code = extract_exit_code(
            getattr(response, "result", "") or "",
            fallback_exit_code=getattr(response, "exit_code", None),
        )
        if exit_code not in (0, None):
            raise RuntimeError(cleaned or f"enumerate failed for {folder}")
        lines = [line for line in cleaned.splitlines() if line.strip()]
        if lines and lines[0].strip() == "__MISSING__":
            raise FileNotFoundError(folder)
        if lines and lines[0].strip() == "__NOTDIR__":
            raise NotADirectoryError(folder)
        return lines

    def _write_remote(self, file_path: str, payload: bytes) -> None:
        process = self._process()
        text = payload.decode("utf-8")
        commands, tmp_path = build_write_text_file_commands(file_path, text)
        try:
            for command in commands:
                response = run_sync(process.exec(wrap_bash_command(command)))
                cleaned, exit_code = extract_exit_code(
                    getattr(response, "result", "") or "",
                    fallback_exit_code=getattr(response, "exit_code", None),
                )
                if exit_code not in (0, None):
                    raise RuntimeError(cleaned or f"write failed for {file_path}")
        except Exception:
            if tmp_path:
                try:
                    run_sync(process.exec(wrap_bash_command(build_remove_file_command(tmp_path))))
                except Exception:
                    logger.debug("remote temp cleanup failed for %s", tmp_path, exc_info=True)
            raise

    def _delete_remote(self, file_path: str) -> None:
        process = self._process()
        response = run_sync(process.exec(wrap_bash_command(f"rm -f {shlex.quote(file_path)}")))
        cleaned, exit_code = extract_exit_code(
            getattr(response, "result", "") or "",
            fallback_exit_code=getattr(response, "exit_code", None),
        )
        if exit_code not in (0, None):
            raise RuntimeError(cleaned or f"delete failed for {file_path}")

    def _exec_remote(self, process: Any, command: str) -> tuple[str, int | None]:
        """Run *command* through the sandbox and return ``(cleaned_stdout, exit_code)``."""
        response = run_sync(process.exec(wrap_bash_command(command)))
        return extract_exit_code(
            getattr(response, "result", "") or "",
            fallback_exit_code=getattr(response, "exit_code", None),
        )

    def _cleanup_remote_tmp(self, process: Any, tmp_path: str) -> None:
        try:
            run_sync(
                process.exec(
                    wrap_bash_command(build_remove_file_command(tmp_path)),
                ),
            )
        except Exception:
            logger.debug(
                "remote batch tmp cleanup failed for %s",
                tmp_path,
                exc_info=True,
            )

    def _stage_remote_payload(self, process: Any, payload: bytes) -> str:
        """Write *payload* to a unique remote tmp file via chunked base64 appends.

        Returns the tmp path. Used to pass large batch payloads to apply scripts
        without inlining them into the command line (which trips ARG_MAX/E2BIG).
        Caller is responsible for removing the tmp file via ``_cleanup_remote_tmp``.
        """
        tmp_path = f"/tmp/codex-batch-apply-{uuid.uuid4().hex}.json"
        cleaned, exit_code = self._exec_remote(
            process, build_truncate_text_file_command(tmp_path),
        )
        if exit_code not in (0, None):
            raise RuntimeError(cleaned or "stage payload truncate failed")
        chunk_size = REMOTE_WRITE_CHUNK_BYTES
        for index in range(0, len(payload), chunk_size):
            chunk = payload[index : index + chunk_size]
            chunk_b64 = base64.b64encode(chunk).decode("ascii")
            cleaned, exit_code = self._exec_remote(
                process,
                build_append_text_file_chunk_command(tmp_path, chunk_b64),
            )
            if exit_code not in (0, None):
                self._cleanup_remote_tmp(process, tmp_path)
                raise RuntimeError(cleaned or "stage payload chunk failed")
        return tmp_path

    def _apply_remote_batch(self, changes: list[tuple[str, str | None]]) -> None:
        process = self._process()
        payload = [
            {
                "path": self._resolve_path(path),
                "content_b64": (
                    None
                    if content is None
                    else base64.b64encode(content.encode("utf-8")).decode("ascii")
                ),
            }
            for path, content in changes
        ]
        payload_bytes = json.dumps(payload).encode("utf-8")
        if len(payload_bytes) > REMOTE_WRITE_CHUNK_BYTES:
            self._apply_remote_batch_staged(process, payload_bytes)
            return
        script = _build_inline_apply_script(payload_bytes, _APPLY_BODY)
        command = f"python3 -c {shlex.quote(script)}"
        cleaned, exit_code = self._exec_remote(process, command)
        if exit_code not in (0, None):
            raise RuntimeError(cleaned or "batch apply failed")

    def _apply_remote_batch_staged(self, process: Any, payload_bytes: bytes) -> None:
        tmp_path = self._stage_remote_payload(process, payload_bytes)
        try:
            command = (
                f"python3 -c {shlex.quote(_BATCH_APPLY_FROM_FILE_SCRIPT)} "
                f"{shlex.quote(tmp_path)}"
            )
            cleaned, exit_code = self._exec_remote(process, command)
            if exit_code not in (0, None):
                raise RuntimeError(cleaned or "batch apply failed")
        finally:
            self._cleanup_remote_tmp(process, tmp_path)

    def _apply_local_batch_checked(
        self,
        changes: list[CheckedApplyChange],
    ) -> CheckedApplyResult:
        backups: list[tuple[Path, bool, str]] = []
        for change in changes:
            if change.final_content is None and not change.base_existed:
                return CheckedApplyResult(
                    success=False,
                    conflict_path=change.file_path,
                    conflict_reason="base_mismatch",
                    message="file content changed before delete",
                )
            path = Path(self._resolve_path(change.file_path))
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
                path = Path(self._resolve_path(change.file_path))
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
        process = self._process()
        payload = [
            {
                "original_path": change.file_path,
                "path": self._resolve_path(change.file_path),
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
        payload_bytes = json.dumps(payload).encode("utf-8")
        if len(payload_bytes) > REMOTE_WRITE_CHUNK_BYTES:
            return self._apply_remote_batch_checked_staged(process, payload_bytes)
        script = _build_inline_apply_script(payload_bytes, _CHECKED_APPLY_BODY)
        command = f"python3 -c {shlex.quote(script)}"
        cleaned, exit_code = self._exec_remote(process, command)
        return _parse_checked_apply_response(cleaned, exit_code)

    def _apply_remote_batch_checked_staged(
        self,
        process: Any,
        payload_bytes: bytes,
    ) -> CheckedApplyResult:
        tmp_path = self._stage_remote_payload(process, payload_bytes)
        try:
            command = (
                f"python3 -c {shlex.quote(_CHECKED_BATCH_APPLY_FROM_FILE_SCRIPT)} "
                f"{shlex.quote(tmp_path)}"
            )
            cleaned, exit_code = self._exec_remote(process, command)
            return _parse_checked_apply_response(cleaned, exit_code)
        finally:
            self._cleanup_remote_tmp(process, tmp_path)

    # -- Transport-backed branches -------------------------------------------

    def _read_via_transport(
        self, file_path: str, *, allow_missing: bool,
    ) -> FileReadResult:
        try:
            payload = run_sync(self._transport.read_bytes(self._sandbox_id, file_path))
        except FileNotFoundError:
            if allow_missing:
                return "", False
            raise
        except SandboxTransportError as exc:
            raise RuntimeError(str(exc)) from exc
        if isinstance(payload, bytes):
            return payload.decode("utf-8"), True
        return str(payload), True

    def _read_many_via_transport(
        self,
        unique_paths: list[str],
        resolved_by_path: dict[str, str],
        *,
        allow_missing: bool,
    ) -> FileReadResults:
        resolved_paths = list(dict.fromkeys(resolved_by_path.values()))
        try:
            payload = run_sync(
                self._transport.read_bytes_batch(self._sandbox_id, resolved_paths)
            )
        except SandboxTransportError as exc:
            raise RuntimeError(str(exc)) from exc
        results: FileReadResults = {}
        for path in unique_paths:
            resolved = resolved_by_path[path]
            content_bytes = payload.get(resolved)
            if content_bytes is None:
                if allow_missing:
                    results[path] = ("", False)
                    continue
                raise FileNotFoundError(path)
            results[path] = (content_bytes.decode("utf-8"), True)
        return results

    def _apply_via_transport(
        self, changes: list[CheckedApplyChange],
    ) -> CheckedApplyResult:
        # Translate engine-side CheckedApplyChange into transport-side
        # CheckedWriteSpec. Two semantic gaps to handle:
        #   1. ``base_existed=False`` + ``final_content=None`` is a delete-of-
        #      absent — the checked apply path returns ``base_mismatch`` immediately
        #      without even calling the apply script. Mirror that here.
        #   2. ``expected_sha`` is ``None`` only when create-only is intended
        #      (``base_existed=False`` + ``final_content`` set), otherwise the
        #      caller's ``base_hash`` flows through as the expected sha.
        specs: list[CheckedWriteSpec] = []
        for change in changes:
            if change.final_content is None and not change.base_existed:
                return CheckedApplyResult(
                    success=False,
                    conflict_path=change.file_path,
                    conflict_reason="base_mismatch",
                    message="file content changed before delete",
                )
            specs.append(
                CheckedWriteSpec(
                    path=self._resolve_path(change.file_path),
                    content=(
                        None
                        if change.final_content is None
                        else change.final_content.encode("utf-8")
                    ),
                    expected_sha=(
                        None if not change.base_existed else change.base_hash
                    ),
                )
            )
        try:
            transport_result = run_sync(
                self._transport.apply_diff_batch_checked(self._sandbox_id, specs)
            )
        except SandboxTransportError as exc:
            return CheckedApplyResult(
                success=False,
                conflict_reason="transport_error",
                message=str(exc),
            )
        if transport_result.success:
            return CheckedApplyResult(success=True)
        conflict_path = (
            transport_result.conflict_paths[0]
            if transport_result.conflict_paths
            else None
        )
        # Preserve the engine-side path (workspace-relative) for the conflict
        # report rather than the transport-resolved absolute path.
        if conflict_path is not None:
            for change in changes:
                if self._resolve_path(change.file_path) == conflict_path:
                    conflict_path = change.file_path
                    break
        return CheckedApplyResult(
            success=False,
            conflict_path=conflict_path,
            conflict_reason=transport_result.conflict_reason or "failed",
        )
