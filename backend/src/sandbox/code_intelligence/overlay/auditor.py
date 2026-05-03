"""Orchestrator-side overlay shell auditor."""

from __future__ import annotations

import asyncio
import base64
import logging
import subprocess as subprocess
import time
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any

from sandbox.api.transport import SandboxTransport
from sandbox.code_intelligence.overlay.command_committer import OverlayCommandCommitter
from sandbox.code_intelligence.overlay.config import (
    overlay_max_concurrent,
    overlay_upper_size_mb,
)
from sandbox.code_intelligence.overlay.daemon_local import OverlayDaemonLocalMixin
from sandbox.code_intelligence.overlay.process_exec import OverlayProcessExecMixin
from sandbox.code_intelligence.overlay.results import (
    audit_result,
    live_path,
    parse_diff_ndjson,
    reject_result,
)
from sandbox.code_intelligence.overlay import support as _overlay_support
from sandbox.code_intelligence.overlay.types import (
    OverlayDiff,
    OverlayLease,
    OverlayPolicyReject,
)
from sandbox.code_intelligence.overlay.counters import record_overlay_op

logger = logging.getLogger(__name__)

_PROGRESS_POLL_INTERVAL_SECONDS = _overlay_support.PROGRESS_POLL_INTERVAL_SECONDS


def _overlay_runtime_bundle_bytes() -> bytes:
    """Compatibility wrapper for older tests around the former monolith."""
    return _overlay_support.overlay_runtime_bundle_bytes()


class OverlayAuditor(OverlayDaemonLocalMixin, OverlayProcessExecMixin):
    """Run one command under a fresh ``unshare -Urm`` overlay and commit via OCC."""

    def __init__(
        self,
        *,
        sandbox_id: str,
        workspace_root: str,
        exec_process: Callable[..., Awaitable[Any]],
        write_coordinator: Any,
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
        self._committer = OverlayCommandCommitter(
            write_coordinator, workspace_root=self._workspace_root
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
    ) -> SimpleNamespace:
        """Run *command* under overlay and return the downstream result shape."""
        del run_id, agent_run_id, task_id
        if self._daemon_local and sandbox is None and on_progress_line is None:
            return await self._execute_daemon_local(
                command,
                timeout=timeout,
                description=description,
                agent_id=agent_id,
                stdin=stdin,
                attribute_changes=attribute_changes,
            )

        async with self._semaphore:
            lease = self._new_lease()
            stage_timings: dict[str, float] = {}
            total_started = time.perf_counter()
            result: SimpleNamespace | None = None
            error: BaseException | None = None
            record_overlay_op(ops_total=1)
            try:
                await self._timed_stage(
                    "upload_runtime",
                    stage_timings=stage_timings,
                    lease=lease,
                    command=command,
                    awaitable=self._ensure_script_uploaded(sandbox),
                )
                result = await self._run_and_commit_remote(
                    sandbox=sandbox,
                    command=command,
                    lease=lease,
                    stage_timings=stage_timings,
                    timeout=timeout,
                    stdin=stdin,
                    agent_id=agent_id,
                    description=description,
                    attribute_changes=attribute_changes,
                    on_progress_line=on_progress_line,
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
                        awaitable=self._cleanup_run_dir(sandbox, lease),
                    )
                except Exception:
                    logger.debug(
                        "overlay run-dir cleanup failed for %s",
                        lease.run_dir,
                        exc_info=True,
                    )
                stage_timings["total"] = round(time.perf_counter() - total_started, 6)
                if result is not None:
                    result.overlay_stage_timings = dict(stage_timings)
                self._log_execution_summary(
                    command=command,
                    lease=lease,
                    stage_timings=stage_timings,
                    result=result,
                    error=error,
                )

    async def _run_and_commit_remote(
        self,
        *,
        sandbox: Any,
        command: str,
        lease: OverlayLease,
        stage_timings: dict[str, float],
        timeout: int | None,
        stdin: str | None,
        agent_id: str,
        description: str,
        attribute_changes: bool,
        on_progress_line: Callable[[str], None] | None,
    ) -> SimpleNamespace:
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
        return await self._finish_remote_commit(
            sandbox=sandbox,
            command=command,
            lease=lease,
            stage_timings=stage_timings,
            stdout_text=stdout_text,
            script_exit=script_exit,
            agent_id=agent_id,
            description=description,
            attribute_changes=attribute_changes,
        )

    async def _finish_remote_commit(
        self,
        *,
        sandbox: Any,
        command: str,
        lease: OverlayLease,
        stage_timings: dict[str, float],
        stdout_text: str,
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

    async def _commit_and_assemble(
        self,
        *,
        stdout: str,
        diff: OverlayDiff,
        agent_id: str,
        description: str,
        attribute_changes: bool,
        overlay_run_timings: dict[str, float] | None = None,
    ) -> SimpleNamespace:
        gitignore_paths = [
            live_path(self._workspace_root, p) for p in diff.gitignore_paths
        ]
        gitinclude_live_paths = [
            live_path(self._workspace_root, c.path) for c in diff.gitinclude_changes
        ]

        del attribute_changes

        mixed = bool(diff.gitinclude_changes) and bool(diff.gitignore_paths)
        if not diff.gitinclude_changes:
            return audit_result(
                result_text=stdout,
                exit_code=diff.exit_code,
                gitinclude_committed=[],
                gitignore_merged=gitignore_paths,
                gitignore_merged_count=len(gitignore_paths),
                mixed_gitinclude_gitignore=mixed,
                mixed_partial_apply=False,
                ambient=[],
                git_commit_status="noop",
                git_conflict_reason=None,
                git_conflict_file=None,
                warnings=list(diff.warnings),
                overlay_run_timings=overlay_run_timings,
            )

        commit_result = await self._committer.commit(
            diff.gitinclude_changes,
            agent_id=agent_id,
            description=description,
        )
        warnings = list(diff.warnings)
        if commit_result.success:
            return audit_result(
                result_text=stdout,
                exit_code=diff.exit_code,
                gitinclude_committed=gitinclude_live_paths,
                gitignore_merged=gitignore_paths,
                gitignore_merged_count=len(gitignore_paths),
                mixed_gitinclude_gitignore=mixed,
                mixed_partial_apply=False,
                ambient=[],
                git_commit_status=commit_result.status,
                git_conflict_reason=None,
                git_conflict_file=None,
                warnings=warnings,
                overlay_run_timings=overlay_run_timings,
            )

        partial = mixed
        if partial:
            warnings.append(
                "gitinclude changes aborted by OCC; gitignore runtime changes "
                "were already applied"
            )
            record_overlay_op(
                mixed_partial_apply_ops=1,
                mixed_gitinclude_gitignore_ops=1,
                gitignore_changes_after_aborted_gitinclude=len(gitignore_paths),
            )
        elif mixed:
            record_overlay_op(mixed_gitinclude_gitignore_ops=1)
        return audit_result(
            result_text=stdout,
            exit_code=diff.exit_code,
            gitinclude_committed=[],
            gitignore_merged=gitignore_paths,
            gitignore_merged_count=len(gitignore_paths),
            mixed_gitinclude_gitignore=mixed,
            mixed_partial_apply=partial,
            ambient=gitinclude_live_paths,
            git_commit_status=commit_result.status,
            git_conflict_reason=commit_result.conflict_reason or None,
            git_conflict_file=commit_result.conflict_file,
            warnings=warnings,
            overlay_run_timings=overlay_run_timings,
        )


__all__ = ["OverlayAuditor", "parse_diff_ndjson"]
