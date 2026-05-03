"""Overlay execution engine.

The engine owns overlay capture only: lease lifecycle, runtime setup, command
execution, readback, cleanup, and timing. OCC policy is intentionally outside
this module.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import posixpath
import shlex
import shutil
import subprocess
import tarfile
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Protocol

from sandbox.api.bash import extract_exit_code, wrap_bash_command
from sandbox.api.transport import SandboxTransport
from sandbox.overlay.config import overlay_max_concurrent, overlay_upper_size_mb
from sandbox.overlay.types import (
    ConflictInfo,
    OverlayCapture,
    OverlayLease,
    OverlayPolicyReject,
    OverlayRunError,
    OverlayRunOutcome,
)
from sandbox.overlay.wire import parse_diff_ndjson

logger = logging.getLogger(__name__)

RUN_DIR_PREFIX = "/tmp/eos-shell-overlay"
PROGRESS_POLL_INTERVAL_SECONDS = 2.0
PROGRESS_READ_CHUNK_BYTES = 64 * 1024
SLOW_OVERLAY_STAGE_SECONDS = 1.0
SLOW_OVERLAY_TOTAL_SECONDS = 5.0
COMMAND_SAMPLE_LIMIT = 160

WorkspaceFingerprint = tuple[tuple[str, int, int, int, int], ...]


class OverlayEngine(Protocol):
    """Minimal overlay capture surface used by runtime pipelines."""

    async def execute(
        self,
        command: str,
        *,
        sandbox: Any = None,
        timeout: int | None = None,
        stdin: str | None = None,
        description: str = "",
        agent_id: str = "",
        run_id: str = "",
        agent_run_id: str = "",
        task_id: str = "",
        on_progress_line: Callable[[str], None] | None = None,
    ) -> OverlayRunOutcome: ...


class LocalOverlayEngine:
    """Run one command under a fresh overlay namespace and capture upperdir."""

    def __init__(
        self,
        *,
        sandbox_id: str,
        workspace_root: str,
        exec_process: Callable[..., Awaitable[Any]] | None = None,
        max_concurrent: int | None = None,
        upper_size_mb: int | None = None,
        transport: SandboxTransport | None = None,
        daemon_local: bool = True,
    ) -> None:
        self._sandbox_id = sandbox_id
        self._workspace_root = workspace_root.rstrip("/")
        self._exec_process = exec_process or self._local_process_exec
        self._transport = transport
        self._daemon_local = daemon_local
        self._semaphore = asyncio.Semaphore(
            max_concurrent if max_concurrent is not None else overlay_max_concurrent()
        )
        self._upper_size_mb = (
            upper_size_mb if upper_size_mb is not None else overlay_upper_size_mb()
        )
        self._script_upload_lock = asyncio.Lock()
        self._script_uploaded = False
        self._fingerprint_lock = asyncio.Lock()
        self._active_fingerprint_guards = 0
        self._last_workspace_fingerprint: WorkspaceFingerprint | None = None

    async def execute(
        self,
        command: str,
        *,
        sandbox: Any = None,
        timeout: int | None = None,
        stdin: str | None = None,
        description: str = "",
        agent_id: str = "",
        run_id: str = "",
        agent_run_id: str = "",
        task_id: str = "",
        on_progress_line: Callable[[str], None] | None = None,
    ) -> OverlayRunOutcome:
        """Run *command* under overlay and hand back an OCC-free outcome."""
        del run_id, agent_run_id, task_id, agent_id, description
        if self._daemon_local and sandbox is None and on_progress_line is None:
            return await self._execute_daemon_local(
                command,
                timeout=timeout,
                stdin=stdin,
            )

        async with self._semaphore:
            lease = self._new_lease()
            stage_timings: dict[str, float] = {}
            total_started = time.perf_counter()
            outcome: OverlayRunOutcome | None = None
            error: BaseException | None = None
            try:
                await self._timed_stage(
                    "upload_runtime",
                    stage_timings=stage_timings,
                    lease=lease,
                    command=command,
                    awaitable=self._ensure_runtime_available(sandbox),
                )
                outcome = await self._run_and_assemble_outcome(
                    sandbox=sandbox,
                    command=command,
                    lease=lease,
                    stage_timings=stage_timings,
                    timeout=timeout,
                    stdin=stdin,
                    on_progress_line=on_progress_line,
                )
                return outcome
            except BaseException as exc:
                error = exc
                raise
            finally:
                try:
                    await self._timed_stage(
                        "cleanup",
                        stage_timings=stage_timings,
                        lease=lease,
                        command=command,
                        awaitable=self._cleanup_run_dir(sandbox, lease),
                    )
                except Exception:
                    logger.debug(
                        "overlay run-dir cleanup failed for %s",
                        lease.run_dir,
                        exc_info=True,
                    )
                stage_timings["total"] = round(time.perf_counter() - total_started, 6)
                if outcome is not None:
                    outcome.overlay_stage_timings = dict(stage_timings)
                self._log_execution_summary(
                    command=command,
                    lease=lease,
                    stage_timings=stage_timings,
                    outcome=outcome,
                    error=error,
                )

    async def _execute_daemon_local(
        self,
        command: str,
        *,
        timeout: int | None,
        stdin: str | None,
    ) -> OverlayRunOutcome:
        async with self._semaphore:
            lease = self._new_lease()
            stage_timings: dict[str, float] = {}
            total_started = time.perf_counter()
            outcome: OverlayRunOutcome | None = None
            error: BaseException | None = None
            fingerprint_guard_started = False
            try:
                await self._begin_workspace_fingerprint_guard()
                fingerprint_guard_started = True
                await self._timed_stage(
                    "upload_runtime",
                    stage_timings=stage_timings,
                    lease=lease,
                    command=command,
                    awaitable=self._ensure_runtime_available(None),
                )
                user_cmd_b64, stdin_b64 = _encode_command(command, stdin)
                overlay_stdout, script_exit = await self._timed_stage(
                    "unshare",
                    stage_timings=stage_timings,
                    lease=lease,
                    command=command,
                    awaitable=self._run_overlay_daemon_local(
                        lease=lease,
                        user_cmd_b64=user_cmd_b64,
                        stdin_b64=stdin_b64,
                        timeout=timeout,
                    ),
                )
                await self._timed_stage(
                    "read_envelope",
                    stage_timings=stage_timings,
                    lease=lease,
                    command=command,
                    awaitable=self._read_result_envelope(
                        lease,
                        overlay_stdout=overlay_stdout,
                        overlay_exit_code=script_exit,
                    ),
                )
                outcome = await self._finish_outcome(
                    sandbox=None,
                    command=command,
                    lease=lease,
                    stage_timings=stage_timings,
                    overlay_stdout=overlay_stdout,
                    script_exit=script_exit,
                )
                return outcome
            except BaseException as exc:
                error = exc
                raise
            finally:
                try:
                    await self._timed_stage(
                        "cleanup",
                        stage_timings=stage_timings,
                        lease=lease,
                        command=command,
                        awaitable=self._cleanup_run_dir(None, lease),
                    )
                except OSError:
                    logger.warning(
                        "overlay daemon-local run-dir cleanup failed for %s",
                        lease.run_dir,
                        exc_info=True,
                    )
                except Exception:
                    logger.debug(
                        "overlay daemon-local run-dir cleanup failed for %s",
                        lease.run_dir,
                        exc_info=True,
                    )
                stage_timings["total"] = round(time.perf_counter() - total_started, 6)
                if outcome is not None:
                    outcome.overlay_stage_timings = dict(stage_timings)
                if fingerprint_guard_started:
                    await self._end_workspace_fingerprint_guard()
                self._log_execution_summary(
                    command=command,
                    lease=lease,
                    stage_timings=stage_timings,
                    outcome=outcome,
                    error=error,
                )

    async def _run_and_assemble_outcome(
        self,
        *,
        sandbox: Any,
        command: str,
        lease: OverlayLease,
        stage_timings: dict[str, float],
        timeout: int | None,
        stdin: str | None,
        on_progress_line: Callable[[str], None] | None,
    ) -> OverlayRunOutcome:
        user_cmd_b64, stdin_b64 = _encode_command(command, stdin)
        if on_progress_line is None:
            stdout_text, script_exit = await self._timed_stage(
                "run_overlay",
                stage_timings=stage_timings,
                lease=lease,
                command=command,
                awaitable=self._run_overlay(
                    sandbox,
                    lease=lease,
                    user_cmd_b64=user_cmd_b64,
                    stdin_b64=stdin_b64,
                    timeout=timeout,
                ),
            )
        else:
            stdout_text, script_exit = await self._timed_stage(
                "run_overlay",
                stage_timings=stage_timings,
                lease=lease,
                command=command,
                awaitable=self._run_overlay_with_progress(
                    sandbox,
                    lease=lease,
                    user_cmd_b64=user_cmd_b64,
                    stdin_b64=stdin_b64,
                    timeout=timeout,
                    on_progress_line=on_progress_line,
                ),
            )
        return await self._finish_outcome(
            sandbox=sandbox,
            command=command,
            lease=lease,
            stage_timings=stage_timings,
            overlay_stdout=stdout_text,
            script_exit=script_exit,
        )

    async def _finish_outcome(
        self,
        *,
        sandbox: Any,
        command: str,
        lease: OverlayLease,
        stage_timings: dict[str, float],
        overlay_stdout: str,
        script_exit: int,
    ) -> OverlayRunOutcome:
        stdout_text = await self._timed_stage(
            "read_stdout",
            stage_timings=stage_timings,
            lease=lease,
            command=command,
            awaitable=self._read_stdout(sandbox, lease, fallback=overlay_stdout),
        )
        diff_or_reject = await self._timed_stage(
            "read_diff",
            stage_timings=stage_timings,
            lease=lease,
            command=command,
            awaitable=self._read_diff(
                sandbox,
                lease,
                overlay_stdout=stdout_text,
                overlay_exit_code=script_exit,
            ),
        )
        if isinstance(diff_or_reject, OverlayPolicyReject):
            return self._reject_outcome(
                stdout=stdout_text,
                exit_code=script_exit,
                reject=diff_or_reject,
            )
        return self._assemble_outcome(stdout=stdout_text, diff=diff_or_reject)

    def _assemble_outcome(
        self,
        *,
        stdout: str,
        diff: OverlayCapture,
    ) -> OverlayRunOutcome:
        return OverlayRunOutcome(
            exit_code=diff.exit_code,
            stdout=stdout,
            upper_changes=diff.upper_changes,
            overlay_rejected=False,
            conflict=None,
            warnings=tuple(diff.warnings),
            overlay_run_timings=dict(diff.run_timings),
            policy_reject=None,
        )

    def _reject_outcome(
        self,
        *,
        stdout: str,
        exit_code: int,
        reject: OverlayPolicyReject,
    ) -> OverlayRunOutcome:
        detail = (
            f"{reject.reason}: {','.join(reject.paths)}"
            if reject.paths
            else reject.reason
        )
        conflict = ConflictInfo(
            reason=reject.reason,
            conflict_file=reject.paths[0] if reject.paths else None,
            message=detail,
        )
        return OverlayRunOutcome(
            exit_code=exit_code,
            stdout=stdout,
            upper_changes=(),
            overlay_rejected=True,
            conflict=conflict,
            warnings=(detail,),
            overlay_run_timings=dict(reject.run_timings),
            policy_reject=reject,
        )

    def _new_lease(self) -> OverlayLease:
        run_dir = posixpath.join(
            RUN_DIR_PREFIX, self._sandbox_id, f"run-{uuid.uuid4().hex}"
        )
        return OverlayLease(run_dir=run_dir)

    async def _ensure_runtime_available(self, sandbox: Any) -> None:
        if self._script_uploaded:
            return
        async with self._script_upload_lock:
            if self._script_uploaded:
                return
            if self._can_use_local_run_dir(sandbox):
                root = Path(RUN_DIR_PREFIX)
                root.mkdir(parents=True, exist_ok=True)
                with tarfile.open(
                    fileobj=io.BytesIO(_overlay_runtime_bundle_bytes()),
                    mode="r:gz",
                ) as tar:
                    try:
                        tar.extractall(root, filter="data")
                    except TypeError:
                        tar.extractall(root)
                self._script_uploaded = True
                return

            encoded = base64.b64encode(_overlay_runtime_bundle_bytes()).decode("ascii")
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
                    f"overlay runtime upload failed: exit_code={exit_code}"
                )
            self._script_uploaded = True

    async def _run_overlay(
        self,
        sandbox: Any,
        *,
        lease: OverlayLease,
        user_cmd_b64: str,
        stdin_b64: str,
        timeout: int | None,
    ) -> tuple[str, int]:
        args = self._runtime_args(
            lease=lease,
            user_cmd_b64=user_cmd_b64,
            stdin_b64=stdin_b64,
        )
        inner = _runtime_command(args)
        full = (
            f"mkdir -p {shlex.quote(lease.run_dir)} && "
            f"unshare -Urm bash -c {shlex.quote(inner)}"
        )
        return await self._do_exec(sandbox, full, timeout=timeout)

    async def _run_overlay_with_progress(
        self,
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

    async def _run_overlay_daemon_local(
        self,
        *,
        lease: OverlayLease,
        user_cmd_b64: str,
        stdin_b64: str,
        timeout: int | None,
    ) -> tuple[str, int]:
        Path(lease.run_dir).mkdir(parents=True, exist_ok=True)
        inner = _runtime_command(
            self._runtime_args(
                lease=lease,
                user_cmd_b64=user_cmd_b64,
                stdin_b64=stdin_b64,
            )
        )
        argv = [
            "unshare",
            "-Urm",
            "bash",
            "-o",
            "pipefail",
            "-lc",
            self._daemon_local_shell_script(inner),
        ]
        logger.debug(
            "overlay daemon-local subprocess.run start: kind=unshare "
            "sandbox_id=%s run_dir=%s command=%r",
            self._sandbox_id,
            lease.run_dir,
            command_sample(inner),
        )
        started = time.perf_counter()
        completed = await asyncio.to_thread(
            subprocess.run,
            argv,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        logger.debug(
            "overlay daemon-local subprocess.run done: kind=unshare elapsed=%.3fs "
            "exit_code=%s sandbox_id=%s run_dir=%s",
            time.perf_counter() - started,
            completed.returncode,
            self._sandbox_id,
            lease.run_dir,
        )
        return (completed.stdout or "") + (completed.stderr or ""), completed.returncode

    def _runtime_args(
        self,
        *,
        lease: OverlayLease,
        user_cmd_b64: str,
        stdin_b64: str,
    ) -> list[str]:
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
        return args

    def _daemon_local_shell_script(self, command: str) -> str:
        return "\n".join(
            [
                "unset LC_ALL",
                'export PATH="$HOME/.local/bin:$PATH"',
                f"cd {shlex.quote(self._workspace_root)}",
                'if [ -d .venv/bin ]; then export PATH="$PWD/.venv/bin:$PATH"; fi',
                f"exec {command}",
            ]
        )

    async def _emit_stdout_progress_delta(
        self,
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
            logger.debug(
                "overlay stdout progress read failed for %s",
                lease.run_dir,
                exc_info=True,
            )
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
        self, sandbox: Any, lease: OverlayLease, *, fallback: str
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
        self,
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
        self,
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

    async def _read_result_envelope(
        self,
        lease: OverlayLease,
        *,
        overlay_stdout: str,
        overlay_exit_code: int,
    ) -> dict[str, Any]:
        path = Path(lease.run_dir) / "result.json"
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise OverlayRunError(
                "overlay result.json missing at "
                f"{path}: {exc} overlay_exit_code={overlay_exit_code!r} "
                f"overlay_output={overlay_stdout[-2000:]!r}"
            ) from exc
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise OverlayRunError(f"invalid overlay result.json at {path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise OverlayRunError(
                f"overlay result.json at {path} must be an object: {payload!r}"
            )
        logger.debug(
            "overlay daemon-local result envelope read: sandbox_id=%s run_dir=%s "
            "exit_code=%s rejected=%s",
            self._sandbox_id,
            lease.run_dir,
            payload.get("exit_code"),
            payload.get("rejected"),
        )
        return payload

    async def _cleanup_run_dir(self, sandbox: Any, lease: OverlayLease) -> None:
        if self._can_use_local_run_dir(sandbox):
            await asyncio.to_thread(shutil.rmtree, lease.run_dir, ignore_errors=True)
            return
        await self._do_exec(sandbox, f"rm -rf {shlex.quote(lease.run_dir)}", timeout=60)

    def _can_use_local_run_dir(self, sandbox: Any) -> bool:
        return sandbox is None and self._transport is None

    async def _do_exec(
        self,
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

    async def _begin_workspace_fingerprint_guard(self) -> None:
        async with self._fingerprint_lock:
            if self._active_fingerprint_guards == 0:
                current = workspace_fingerprint(self._workspace_root)
                previous = self._last_workspace_fingerprint
                if previous is not None and current != previous:
                    raise OverlayRunError(
                        "workspace changed outside the overlay OCC path; "
                        "refusing lowerdir snapshot"
                    )
            self._active_fingerprint_guards += 1

    async def _end_workspace_fingerprint_guard(self) -> None:
        async with self._fingerprint_lock:
            if self._active_fingerprint_guards > 0:
                self._active_fingerprint_guards -= 1
            if self._active_fingerprint_guards == 0:
                self._last_workspace_fingerprint = workspace_fingerprint(
                    self._workspace_root
                )

    async def _timed_stage(
        self,
        stage: str,
        *,
        stage_timings: dict[str, float],
        lease: OverlayLease,
        command: str,
        awaitable: Awaitable[Any],
    ) -> Any:
        started = time.perf_counter()
        logger.debug(
            "overlay command stage start: stage=%s sandbox_id=%s run_dir=%s command=%r",
            stage,
            self._sandbox_id,
            lease.run_dir,
            command_sample(command),
        )
        try:
            return await awaitable
        finally:
            elapsed = round(time.perf_counter() - started, 6)
            stage_timings[stage] = elapsed
            logger.debug(
                "overlay command stage done: stage=%s elapsed=%.3fs "
                "sandbox_id=%s run_dir=%s command=%r timings=%s",
                stage,
                elapsed,
                self._sandbox_id,
                lease.run_dir,
                command_sample(command),
                dict(stage_timings),
            )
            if elapsed >= SLOW_OVERLAY_STAGE_SECONDS:
                logger.warning(
                    "overlay command stage slow: stage=%s elapsed=%.3fs "
                    "sandbox_id=%s run_dir=%s command=%r timings=%s",
                    stage,
                    elapsed,
                    self._sandbox_id,
                    lease.run_dir,
                    command_sample(command),
                    dict(stage_timings),
                )

    def _log_execution_summary(
        self,
        *,
        command: str,
        lease: OverlayLease,
        stage_timings: dict[str, float],
        outcome: OverlayRunOutcome | None,
        error: BaseException | None,
    ) -> None:
        total = stage_timings.get("total", 0.0)
        rejected = bool(outcome and outcome.overlay_rejected)
        conflict = outcome.conflict if outcome is not None else None
        failed = rejected or conflict is not None
        if error is None and not failed and total < SLOW_OVERLAY_TOTAL_SECONDS:
            return
        error_text = f"{type(error).__name__}: {error}" if error is not None else None
        logger.warning(
            "overlay command summary: total=%.3fs rejected=%s exit_code=%s "
            "conflict_file=%s conflict_reason=%s error=%s sandbox_id=%s "
            "run_dir=%s timings=%s overlay_run_timings=%s command=%r",
            total,
            rejected,
            getattr(outcome, "exit_code", None),
            getattr(conflict, "conflict_file", None),
            getattr(conflict, "reason", None),
            error_text,
            self._sandbox_id,
            lease.run_dir,
            dict(stage_timings),
            dict(getattr(outcome, "overlay_run_timings", {}) or {}),
            command_sample(command),
        )

    async def _local_process_exec(
        self,
        _sandbox: Any,
        command: str,
        *,
        timeout: int | None,
    ) -> Any:
        completed = await asyncio.to_thread(
            subprocess.run,
            command,
            shell=True,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        return type(
            "LocalProcessResult",
            (),
            {
                "result": completed.stdout + completed.stderr,
                "exit_code": completed.returncode,
            },
        )()


def command_sample(command: str) -> str:
    compact = " ".join(command.split())
    if len(compact) <= COMMAND_SAMPLE_LIMIT:
        return compact
    return compact[:COMMAND_SAMPLE_LIMIT] + "..."


def workspace_fingerprint(workspace_root: str) -> WorkspaceFingerprint:
    root = Path(workspace_root)
    rows: list[tuple[str, int, int, int, int]] = []
    for path in (root,):
        try:
            st = path.stat()
        except OSError:
            rows.append((str(path), -1, -1, -1, -1))
            continue
        rows.append((str(path), st.st_dev, st.st_ino, st.st_mtime_ns, st.st_size))
    return tuple(rows)


def _encode_command(command: str, stdin: str | None) -> tuple[str, str]:
    user_cmd_b64 = base64.b64encode(command.encode("utf-8")).decode("ascii")
    stdin_b64 = (
        base64.b64encode(stdin.encode("utf-8")).decode("ascii")
        if stdin is not None
        else ""
    )
    return user_cmd_b64, stdin_b64


def _runtime_command(args: list[str]) -> str:
    return (
        f"PYTHONPATH={shlex.quote(RUN_DIR_PREFIX)}${{PYTHONPATH:+:$PYTHONPATH}} "
        "python3 -m overlay_runtime.cli "
        + " ".join(shlex.quote(a) for a in args)
    )


_OVERLAY_RUNTIME_BUNDLE_CACHE: bytes | None = None


def _overlay_runtime_bundle_bytes() -> bytes:
    """Return a tar.gz containing the sandbox-side overlay runtime."""
    global _OVERLAY_RUNTIME_BUNDLE_CACHE
    if _OVERLAY_RUNTIME_BUNDLE_CACHE is not None:
        return _OVERLAY_RUNTIME_BUNDLE_CACHE

    root = Path(__file__).parent
    runtime_dir = root / "runtime"
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for path in sorted(runtime_dir.rglob("*.py")):
            rel = path.relative_to(runtime_dir).as_posix()
            tar.add(path, arcname=f"overlay_runtime/{rel}")
    _OVERLAY_RUNTIME_BUNDLE_CACHE = buffer.getvalue()
    return _OVERLAY_RUNTIME_BUNDLE_CACHE


__all__ = [
    "LocalOverlayEngine",
    "OverlayEngine",
    "RUN_DIR_PREFIX",
    "command_sample",
    "workspace_fingerprint",
]
