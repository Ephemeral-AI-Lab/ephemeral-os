"""Daemon-local overlay execution helpers for OverlayAuditor."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import posixpath
import shlex
import shutil
import subprocess
import time
from collections.abc import Awaitable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from sandbox.code_intelligence.overlay.results import reject_result
from sandbox.code_intelligence.overlay.support import (
    RUN_DIR_PREFIX,
    SLOW_OVERLAY_STAGE_SECONDS,
    SLOW_OVERLAY_TOTAL_SECONDS,
    command_sample,
    workspace_fingerprint,
)
from sandbox.code_intelligence.overlay.types import (
    OverlayLease,
    OverlayPolicyReject,
    OverlayRunError,
)
from sandbox.code_intelligence.overlay.counters import record_overlay_op

logger = logging.getLogger(__name__)


class OverlayDaemonLocalMixin:
    """Methods that run the overlay path inside the CI daemon process."""

    async def _execute_daemon_local(
        self: Any,
        command: str,
        *,
        timeout: int | None,
        description: str,
        agent_id: str,
        stdin: str | None,
        attribute_changes: bool,
    ) -> SimpleNamespace:
        async with self._semaphore:
            lease = self._new_lease()
            stage_timings: dict[str, float] = {}
            total_started = time.perf_counter()
            result: SimpleNamespace | None = None
            error: BaseException | None = None
            fingerprint_guard_started = False
            record_overlay_op(ops_total=1)
            try:
                await self._begin_workspace_fingerprint_guard()
                fingerprint_guard_started = True
                await self._timed_stage(
                    "upload_runtime",
                    stage_timings=stage_timings,
                    lease=lease,
                    command=command,
                    awaitable=self._ensure_script_uploaded(None),
                )
                user_cmd_b64 = base64.b64encode(command.encode("utf-8")).decode("ascii")
                stdin_b64 = (
                    base64.b64encode(stdin.encode("utf-8")).decode("ascii")
                    if stdin is not None
                    else ""
                )
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
                result = await self._finish_daemon_local_commit(
                    command=command,
                    lease=lease,
                    stage_timings=stage_timings,
                    overlay_stdout=overlay_stdout,
                    script_exit=script_exit,
                    agent_id=agent_id,
                    description=description,
                    attribute_changes=attribute_changes,
                )
                return result
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
                        awaitable=self._cleanup_daemon_local_run_dir(lease),
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
                if result is not None:
                    result.overlay_stage_timings = dict(stage_timings)
                if fingerprint_guard_started:
                    await self._end_workspace_fingerprint_guard()
                self._log_execution_summary(
                    command=command,
                    lease=lease,
                    stage_timings=stage_timings,
                    result=result,
                    error=error,
                )

    async def _finish_daemon_local_commit(
        self: Any,
        *,
        command: str,
        lease: OverlayLease,
        stage_timings: dict[str, float],
        overlay_stdout: str,
        script_exit: int,
        agent_id: str,
        description: str,
        attribute_changes: bool,
    ) -> SimpleNamespace:
        stdout_text = await self._timed_stage(
            "read_stdout",
            stage_timings=stage_timings,
            lease=lease,
            command=command,
            awaitable=self._read_stdout(None, lease, fallback=overlay_stdout),
        )
        diff_or_reject = await self._timed_stage(
            "read_diff",
            stage_timings=stage_timings,
            lease=lease,
            command=command,
            awaitable=self._read_diff(
                None,
                lease,
                overlay_stdout=stdout_text,
                overlay_exit_code=script_exit,
            ),
        )
        if isinstance(diff_or_reject, OverlayPolicyReject):
            record_overlay_op(
                ops_rejected=1,
                dotgit_rejects=(
                    1 if diff_or_reject.reason.endswith("dotgit_writes") else 0
                ),
            )
            return reject_result(
                stdout=stdout_text,
                exit_code=script_exit,
                reject=diff_or_reject,
                overlay_run_timings=diff_or_reject.run_timings,
            )
        diff = diff_or_reject
        record_overlay_op(
            upper_bytes=diff.upper_bytes,
            upper_files=diff.upper_files,
            gitinclude_changes=len(diff.gitinclude_changes),
            gitignore_changes=len(diff.gitignore_paths),
            direct_merged_bytes=diff.direct_merged_bytes,
            whiteouts_gitinclude=diff.whiteouts_gitinclude,
            whiteouts_gitignore_refused=diff.whiteouts_gitignore_refused,
        )
        return await self._timed_stage(
            "commit",
            stage_timings=stage_timings,
            lease=lease,
            command=command,
            awaitable=self._commit_and_assemble(
                stdout=stdout_text,
                diff=diff,
                agent_id=agent_id,
                description=description or "shell overlay",
                attribute_changes=attribute_changes,
                overlay_run_timings=diff.run_timings,
            ),
        )

    async def _begin_workspace_fingerprint_guard(self: Any) -> None:
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

    async def _end_workspace_fingerprint_guard(self: Any) -> None:
        async with self._fingerprint_lock:
            if self._active_fingerprint_guards > 0:
                self._active_fingerprint_guards -= 1
            if self._active_fingerprint_guards == 0:
                self._last_workspace_fingerprint = workspace_fingerprint(
                    self._workspace_root
                )

    async def _run_overlay_daemon_local(
        self: Any,
        *,
        lease: OverlayLease,
        user_cmd_b64: str,
        stdin_b64: str,
        timeout: int | None,
    ) -> tuple[str, int]:
        script_path = posixpath.join(RUN_DIR_PREFIX, "overlay_run.py")
        Path(lease.run_dir).mkdir(parents=True, exist_ok=True)
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

    def _daemon_local_shell_script(self: Any, command: str) -> str:
        return "\n".join(
            [
                "unset LC_ALL",
                'export PATH="$HOME/.local/bin:$PATH"',
                f"cd {shlex.quote(self._workspace_root)}",
                'if [ -d .venv/bin ]; then export PATH="$PWD/.venv/bin:$PATH"; fi',
                f"exec {command}",
            ]
        )

    async def _read_result_envelope(
        self: Any,
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

    async def _cleanup_daemon_local_run_dir(self: Any, lease: OverlayLease) -> None:
        await asyncio.to_thread(shutil.rmtree, lease.run_dir, ignore_errors=True)

    async def _timed_stage(
        self: Any,
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
        self: Any,
        *,
        command: str,
        lease: OverlayLease,
        stage_timings: dict[str, float],
        result: SimpleNamespace | None,
        error: BaseException | None,
    ) -> None:
        total = stage_timings.get("total", 0.0)
        status = getattr(result, "git_commit_status", None)
        failed_status = status not in (None, "committed", "noop")
        if error is None and not failed_status and total < SLOW_OVERLAY_TOTAL_SECONDS:
            return
        error_text = f"{type(error).__name__}: {error}" if error is not None else None
        logger.warning(
            "overlay command summary: total=%.3fs status=%s exit_code=%s "
            "conflict_file=%s conflict_reason=%s error=%s sandbox_id=%s "
            "run_dir=%s timings=%s overlay_run_timings=%s command=%r",
            total,
            status,
            getattr(result, "exit_code", None),
            getattr(result, "git_conflict_file", None),
            getattr(result, "git_conflict_reason", None),
            error_text,
            self._sandbox_id,
            lease.run_dir,
            dict(stage_timings),
            dict(getattr(result, "overlay_run_timings", {}) or {}),
            command_sample(command),
        )
