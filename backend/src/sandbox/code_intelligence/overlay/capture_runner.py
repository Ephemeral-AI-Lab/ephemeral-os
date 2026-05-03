"""Orchestrator-side overlay capture runner."""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from sandbox.api.transport import SandboxTransport
from sandbox.code_intelligence.overlay.config import (
    overlay_max_concurrent,
    overlay_upper_size_mb,
)
from sandbox.code_intelligence.overlay.daemon_local import OverlayDaemonLocalMixin
from sandbox.code_intelligence.overlay.process_exec import OverlayProcessExecMixin
from sandbox.code_intelligence.overlay.results import parse_diff_ndjson
from sandbox.code_intelligence.overlay import support as _overlay_support
from sandbox.code_intelligence.overlay.types import (
    ConflictInfo,
    OverlayCapture,
    OverlayLease,
    OverlayPolicyReject,
    OverlayRunOutcome,
)

logger = logging.getLogger(__name__)

_PROGRESS_POLL_INTERVAL_SECONDS = _overlay_support.PROGRESS_POLL_INTERVAL_SECONDS


def _overlay_runtime_bundle_bytes() -> bytes:
    """Compatibility wrapper for older tests around the former monolith."""
    return _overlay_support.overlay_runtime_bundle_bytes()


class OverlayCaptureRunner(OverlayDaemonLocalMixin, OverlayProcessExecMixin):
    """Run one command under a fresh ``unshare -Urm`` overlay and capture upperdir."""

    def __init__(
        self,
        *,
        sandbox_id: str,
        workspace_root: str,
        exec_process: Callable[..., Awaitable[Any]],
        max_concurrent: int | None = None,
        upper_size_mb: int | None = None,
        transport: SandboxTransport | None = None,
        daemon_local: bool = False,
    ) -> None:
        self._sandbox_id = sandbox_id
        self._workspace_root = workspace_root.rstrip("/")
        self._exec_process = exec_process
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
        self._last_workspace_fingerprint: Any | None = None

    async def execute(
        self,
        sandbox: Any,
        command: str,
        *,
        timeout: int | None = None,
        description: str = "",
        agent_id: str = "",
        run_id: str = "",
        agent_run_id: str = "",
        task_id: str = "",
        stdin: str | None = None,
        attribute_changes: bool = True,
        on_progress_line: Callable[[str], None] | None = None,
    ) -> OverlayRunOutcome:
        """Run *command* under overlay and hand back an OCC-free outcome."""
        del run_id, agent_run_id, task_id, agent_id, description, attribute_changes
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
                    awaitable=self._ensure_script_uploaded(sandbox),
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
        user_cmd_b64 = base64.b64encode(command.encode("utf-8")).decode("ascii")
        stdin_b64 = (
            base64.b64encode(stdin.encode("utf-8")).decode("ascii")
            if stdin is not None
            else ""
        )
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
        return await self._finish_remote_outcome(
            sandbox=sandbox,
            command=command,
            lease=lease,
            stage_timings=stage_timings,
            stdout_text=stdout_text,
            script_exit=script_exit,
        )

    async def _finish_remote_outcome(
        self,
        *,
        sandbox: Any,
        command: str,
        lease: OverlayLease,
        stage_timings: dict[str, float],
        stdout_text: str,
        script_exit: int,
    ) -> OverlayRunOutcome:
        stdout_text = await self._timed_stage(
            "read_stdout",
            stage_timings=stage_timings,
            lease=lease,
            command=command,
            awaitable=self._read_stdout(sandbox, lease, fallback=stdout_text),
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

        return self._assemble_outcome(
            stdout=stdout_text,
            diff=diff_or_reject,
        )

    def _assemble_outcome(
        self,
        *,
        stdout: str,
        diff: OverlayCapture,
    ) -> OverlayRunOutcome:
        """Build the OCC-free :class:`OverlayRunOutcome` from a parsed diff.

        The auditor never invokes OCC; the caller drives merge policy on
        :attr:`OverlayRunOutcome.upper_changes`.
        """
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


__all__ = ["OverlayCaptureRunner", "parse_diff_ndjson"]
