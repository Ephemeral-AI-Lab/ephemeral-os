"""Remote/local process-exec helpers for OverlayAuditor."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import posixpath
import shlex
import shutil
import tarfile
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sandbox.api.bash import extract_exit_code, wrap_bash_command
from sandbox.code_intelligence.overlay.results import parse_diff_ndjson
from sandbox.code_intelligence.overlay.support import (
    PROGRESS_POLL_INTERVAL_SECONDS,
    PROGRESS_READ_CHUNK_BYTES,
    RUN_DIR_PREFIX,
    overlay_runtime_bundle_bytes,
)
from sandbox.code_intelligence.overlay.types import (
    OverlayCapture,
    OverlayLease,
    OverlayPolicyReject,
    OverlayRunError,
)

logger = logging.getLogger(__name__)


class OverlayProcessExecMixin:
    """Methods that execute the overlay runtime through process exec."""

    def _new_lease(self: Any) -> OverlayLease:
        run_dir = posixpath.join(
            RUN_DIR_PREFIX, self._sandbox_id, f"run-{uuid.uuid4().hex}"
        )
        return OverlayLease(run_dir=run_dir)

    async def _ensure_script_uploaded(self: Any, sandbox: Any) -> None:
        if self._script_uploaded:
            return
        async with self._script_upload_lock:
            if self._script_uploaded:
                return
            if self._can_use_local_run_dir(sandbox):
                root = Path(RUN_DIR_PREFIX)
                root.mkdir(parents=True, exist_ok=True)
                with tarfile.open(
                    fileobj=io.BytesIO(overlay_runtime_bundle_bytes()),
                    mode="r:gz",
                ) as tar:
                    try:
                        tar.extractall(root, filter="data")
                    except TypeError:
                        tar.extractall(root)
                self._script_uploaded = True
                return

            encoded = base64.b64encode(overlay_runtime_bundle_bytes()).decode("ascii")
            upload_snippet = (
                "import base64,io,pathlib,sys,tarfile; "
                "root=pathlib.Path(sys.argv[1]); "
                "root.mkdir(parents=True, exist_ok=True); "
                "data=base64.b64decode(sys.argv[2]); "
                "tar=tarfile.open(fileobj=io.BytesIO(data), mode='r:gz'); "
                "\ntry:\n tar.extractall(root, filter='data')"
                "\nexcept TypeError:\n tar.extractall(root)"
            )
            setup_cmd = (
                f"mkdir -p {shlex.quote(RUN_DIR_PREFIX)} && "
                f"python3 -c {shlex.quote(upload_snippet)} "
                f"{shlex.quote(RUN_DIR_PREFIX)} {shlex.quote(encoded)}"
            )
            _stdout, exit_code = await self._do_exec(sandbox, setup_cmd, timeout=60)
            if exit_code != 0:
                raise OverlayRunError(
                    f"overlay_run.py upload failed: exit_code={exit_code}"
                )
            self._script_uploaded = True

    async def _run_overlay(
        self: Any,
        sandbox: Any,
        *,
        lease: OverlayLease,
        user_cmd_b64: str,
        stdin_b64: str,
        timeout: int | None,
    ) -> tuple[str, int]:
        script_path = posixpath.join(RUN_DIR_PREFIX, "overlay_run.py")
        args = [
            "--workspace-root",
            self._workspace_root,
            "--run-dir",
            lease.run_dir,
            "--upper-size-mb",
            str(self._upper_size_mb),
            "--user-cmd-b64",
            user_cmd_b64,
        ]
        if stdin_b64:
            args.extend(["--stdin-b64", stdin_b64])
        inner = f"python3 {shlex.quote(script_path)} " + " ".join(
            shlex.quote(a) for a in args
        )
        full = (
            f"mkdir -p {shlex.quote(lease.run_dir)} && "
            f"unshare -Urm bash -c {shlex.quote(inner)}"
        )
        return await self._do_exec(sandbox, full, timeout=timeout)

    async def _run_overlay_with_progress(
        self: Any,
        sandbox: Any,
        *,
        lease: OverlayLease,
        user_cmd_b64: str,
        stdin_b64: str,
        timeout: int | None,
        on_progress_line: Callable[[str], None],
    ) -> tuple[str, int]:
        task = asyncio.create_task(
            self._run_overlay(
                sandbox,
                lease=lease,
                user_cmd_b64=user_cmd_b64,
                stdin_b64=stdin_b64,
                timeout=timeout,
            )
        )
        offset = 0
        partial = ""
        try:
            while not task.done():
                await asyncio.sleep(PROGRESS_POLL_INTERVAL_SECONDS)
                offset, partial = await self._emit_stdout_progress_delta(
                    sandbox,
                    lease,
                    offset=offset,
                    partial=partial,
                    on_progress_line=on_progress_line,
                )
            stdout_text, exit_code = await task
            offset, partial = await self._emit_stdout_progress_delta(
                sandbox,
                lease,
                offset=offset,
                partial=partial,
                on_progress_line=on_progress_line,
            )
            if partial:
                on_progress_line(partial)
            return stdout_text, exit_code
        except BaseException:
            if not task.done():
                task.cancel()
            raise

    async def _emit_stdout_progress_delta(
        self: Any,
        sandbox: Any,
        lease: OverlayLease,
        *,
        offset: int,
        partial: str,
        on_progress_line: Callable[[str], None],
    ) -> tuple[int, str]:
        try:
            chunk, new_offset = await self._read_stdout_delta(
                sandbox,
                lease,
                offset=offset,
                max_bytes=PROGRESS_READ_CHUNK_BYTES,
            )
        except Exception:
            logger.debug("overlay stdout progress read failed for %s", lease.run_dir, exc_info=True)
            return offset, partial
        if not chunk:
            return new_offset, partial
        text = partial + chunk.decode("utf-8", "replace")
        if text.endswith(("\n", "\r")):
            emit_text = text
            partial = ""
        else:
            lines = text.splitlines(keepends=True)
            partial = lines[-1] if lines else text
            emit_text = "".join(lines[:-1]) if lines else ""
        if emit_text:
            on_progress_line(emit_text)
        return new_offset, partial

    async def _read_stdout(
        self: Any, sandbox: Any, lease: OverlayLease, *, fallback: str
    ) -> str:
        stdout_path = posixpath.join(lease.run_dir, "stdout.bin")
        if self._can_use_local_run_dir(sandbox):
            try:
                return Path(stdout_path).read_bytes().decode("utf-8", "replace")
            except OSError:
                return fallback
        script = (
            "import base64,pathlib,sys; "
            "sys.stdout.write(base64.b64encode(pathlib.Path(sys.argv[1]).read_bytes()).decode('ascii'))"
        )
        cmd = f"python3 -c {shlex.quote(script)} {shlex.quote(stdout_path)}"
        encoded, exit_code = await self._do_exec(sandbox, cmd, timeout=60)
        if exit_code != 0:
            return fallback
        try:
            return base64.b64decode(encoded.strip()).decode("utf-8", "replace")
        except Exception:
            logger.debug("overlay stdout decode failed for %s", stdout_path, exc_info=True)
            return fallback

    async def _read_stdout_delta(
        self: Any,
        sandbox: Any,
        lease: OverlayLease,
        *,
        offset: int,
        max_bytes: int,
    ) -> tuple[bytes, int]:
        stdout_path = posixpath.join(lease.run_dir, "stdout.bin")
        if self._can_use_local_run_dir(sandbox):
            try:
                data = Path(stdout_path).read_bytes()
            except OSError:
                return b"", offset
            size = len(data)
            start = offset if offset <= size else 0
            start = max(start, size - max_bytes)
            return data[start:size], size
        script = (
            "import base64,json,pathlib,sys; "
            "path=pathlib.Path(sys.argv[1]); "
            "offset=max(0,int(sys.argv[2])); "
            "limit=max(1,int(sys.argv[3])); "
            "data=path.read_bytes() if path.exists() else b''; "
            "size=len(data); "
            "start=offset if offset <= size else 0; "
            "start=max(start, size-limit); "
            "chunk=data[start:size]; "
            "print(json.dumps({'start': start, 'size': size, "
            "'chunk': base64.b64encode(chunk).decode('ascii')}))"
        )
        cmd = (
            f"python3 -c {shlex.quote(script)} "
            f"{shlex.quote(stdout_path)} {offset} {max_bytes}"
        )
        raw, exit_code = await self._do_exec(sandbox, cmd, timeout=60)
        if exit_code != 0:
            return b"", offset
        payload = json.loads(raw or "{}")
        size = int(payload.get("size") or 0)
        chunk_b64 = str(payload.get("chunk") or "")
        if not chunk_b64:
            return b"", size
        return base64.b64decode(chunk_b64), size

    async def _read_diff(
        self: Any,
        sandbox: Any,
        lease: OverlayLease,
        *,
        overlay_stdout: str = "",
        overlay_exit_code: int | None = None,
    ) -> OverlayCapture | OverlayPolicyReject:
        diff_path = posixpath.join(lease.run_dir, "diff.ndjson")
        if self._can_use_local_run_dir(sandbox):
            try:
                return parse_diff_ndjson(Path(diff_path).read_text(encoding="utf-8"))
            except OSError as exc:
                raise OverlayRunError(
                    "overlay diff.ndjson missing at "
                    f"{diff_path}: {exc} overlay_exit_code={overlay_exit_code!r} "
                    f"overlay_output={overlay_stdout[-2000:]!r}"
                ) from exc
        cmd = f"cat {shlex.quote(diff_path)}"
        stdout, exit_code = await self._do_exec(sandbox, cmd, timeout=60)
        if exit_code != 0:
            raise OverlayRunError(
                "overlay diff.ndjson missing at "
                f"{diff_path}: cat={stdout[-1000:]!r} "
                f"overlay_exit_code={overlay_exit_code!r} "
                f"overlay_output={overlay_stdout[-2000:]!r}"
            )
        return parse_diff_ndjson(stdout)

    async def _cleanup_run_dir(self: Any, sandbox: Any, lease: OverlayLease) -> None:
        if self._can_use_local_run_dir(sandbox):
            await asyncio.to_thread(shutil.rmtree, lease.run_dir, ignore_errors=True)
            return
        await self._do_exec(sandbox, f"rm -rf {shlex.quote(lease.run_dir)}", timeout=60)

    def _can_use_local_run_dir(self: Any, sandbox: Any) -> bool:
        return sandbox is None and self._transport is None

    async def _do_exec(
        self: Any,
        sandbox: Any,
        command: str,
        *,
        timeout: int | None,
    ) -> tuple[str, int]:
        """Exec ``command`` and return ``(stdout, exit_code)``."""
        if self._transport is not None and self._sandbox_id:
            result = await self._transport.exec(self._sandbox_id, command, timeout=timeout)
            return result.stdout, result.exit_code
        response = await self._exec_process(
            sandbox, wrap_bash_command(command), timeout=timeout
        )
        cleaned, exit_code = extract_exit_code(
            str(getattr(response, "result", "") or ""),
            fallback_exit_code=getattr(response, "exit_code", None),
        )
        return cleaned, exit_code
