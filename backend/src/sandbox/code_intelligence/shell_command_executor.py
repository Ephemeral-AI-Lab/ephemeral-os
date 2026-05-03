"""OCC-gated sandbox command execution for code intelligence services.

Commands run through the overlay capture runner. Each command executes inside a
fresh ``unshare -Urm`` namespace with a tmpfs upperdir over the live workspace.
The capture runner returns an :class:`OverlayRunOutcome` carrying raw upperdir
changes; this executor drives the OCC changeset policy and assembles the
downstream ``SimpleNamespace`` response so upstream callers
(``InProcessBackend.cmd``, agent tools) see an unchanged contract.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import subprocess
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

from sandbox.api.transport import SandboxTransport
from sandbox.client.async_bridge import run_sync_in_executor, use_sandbox_io_loop
from sandbox.code_intelligence.mutations.changeset import ChangesetResult
from sandbox.code_intelligence.overlay.capture_runner import OverlayCaptureRunner
from sandbox.code_intelligence.overlay.types import (
    OverlayPolicyReject,
    OverlayRunOutcome,
)

logger = logging.getLogger(__name__)


def audit_result(
    *,
    result_text: str,
    exit_code: int,
    gitinclude_committed: list[str],
    gitignore_merged: list[str],
    gitignore_merged_count: int,
    mixed_gitinclude_gitignore: bool,
    mixed_partial_apply: bool,
    ambient: list[str],
    git_commit_status: str | None,
    git_conflict_reason: str | None,
    git_conflict_file: str | None,
    warnings: list[str],
    overlay_run_timings: dict[str, float] | None = None,
) -> SimpleNamespace:
    """Preserve the downstream SimpleNamespace contract."""
    return SimpleNamespace(
        result=result_text,
        exit_code=exit_code,
        changed_paths=sorted(gitinclude_committed),
        ambient_changed_paths=sorted(ambient),
        files_written=len(gitinclude_committed),
        git_commit_status=git_commit_status,
        git_conflict_file=git_conflict_file,
        git_conflict_reason=git_conflict_reason,
        gitinclude_changed_paths=sorted(gitinclude_committed),
        gitignore_direct_merged_paths=sorted(gitignore_merged),
        gitignore_direct_merged_count=gitignore_merged_count,
        mixed_gitinclude_gitignore=mixed_gitinclude_gitignore,
        mixed_partial_apply=mixed_partial_apply,
        warnings=list(warnings),
        overlay_run_timings=dict(overlay_run_timings or {}),
    )


def reject_result(
    *,
    stdout: str,
    exit_code: int,
    reject: OverlayPolicyReject,
    overlay_run_timings: dict[str, float] | None = None,
) -> SimpleNamespace:
    detail = (
        f"{reject.reason}: {','.join(reject.paths)}"
        if reject.paths
        else reject.reason
    )
    return SimpleNamespace(
        result=stdout,
        exit_code=exit_code,
        changed_paths=[],
        ambient_changed_paths=[],
        files_written=0,
        git_commit_status="rejected",
        git_conflict_file=reject.paths[0] if reject.paths else None,
        git_conflict_reason=detail,
        gitinclude_changed_paths=[],
        gitignore_direct_merged_paths=[],
        gitignore_direct_merged_count=0,
        mixed_gitinclude_gitignore=False,
        mixed_partial_apply=False,
        warnings=[detail],
        overlay_run_timings=dict(overlay_run_timings or {}),
    )


class AuditedCommandExecutor:
    """Runs sandbox commands through the OCC-gated audit path.

    The overlay capture runner is initialized lazily on first use.
    """

    def __init__(
        self,
        *,
        sandbox_id: str,
        workspace_root: str,
        write_coordinator: Any,
        rebind_sandbox: Callable[[Any], None],
        transport: SandboxTransport | None = None,
        daemon_local: bool = False,
    ) -> None:
        self.sandbox_id = sandbox_id
        self.workspace_root = workspace_root
        self._write_coordinator = write_coordinator
        self._rebind_sandbox = rebind_sandbox
        self._transport = transport
        self._daemon_local = daemon_local
        self._capture_runner: OverlayCaptureRunner | None = None
        self._init_lock = asyncio.Lock()

    async def cmd(
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
        """Run one command through the fail-closed OCC audit path."""
        del attribute_changes  # legacy flag; OCC always gates writes now.
        self._rebind_sandbox(sandbox)
        overlay = await self._ensure_capture_runner()
        outcome = await overlay.execute(
            sandbox,
            command,
            timeout=timeout,
            description=description,
            agent_id=agent_id,
            run_id=run_id,
            agent_run_id=agent_run_id,
            task_id=task_id,
            stdin=stdin,
            on_progress_line=on_progress_line,
        )
        return await self._render_outcome(
            outcome,
            agent_id=agent_id,
            description=description or "shell overlay",
        )

    async def _render_outcome(
        self,
        outcome: OverlayRunOutcome,
        *,
        agent_id: str,
        description: str,
    ) -> SimpleNamespace:
        """Drive OCC on raw upper changes and project the legacy shape."""
        if outcome.overlay_rejected:
            assert outcome.policy_reject is not None
            result = reject_result(
                stdout=outcome.stdout,
                exit_code=outcome.exit_code,
                reject=outcome.policy_reject,
                overlay_run_timings=outcome.overlay_run_timings,
            )
            result.overlay_stage_timings = dict(outcome.overlay_stage_timings)
            return result

        warnings = list(outcome.warnings)
        changeset_result = await self._apply_changeset(
            outcome,
            agent_id=agent_id,
            description=description,
        )
        direct_merged = list(changeset_result.direct_merged)
        ledgered = list(changeset_result.ledgered)
        mixed = bool(direct_merged) and bool(
            ledgered or changeset_result.conflict_file
        )

        if changeset_result.success:
            result = audit_result(
                result_text=outcome.stdout,
                exit_code=outcome.exit_code,
                gitinclude_committed=ledgered,
                gitignore_merged=direct_merged,
                gitignore_merged_count=len(direct_merged),
                mixed_gitinclude_gitignore=mixed,
                mixed_partial_apply=False,
                ambient=[],
                git_commit_status=changeset_result.status,
                git_conflict_reason=None,
                git_conflict_file=None,
                warnings=warnings,
                overlay_run_timings=outcome.overlay_run_timings,
            )
            result.overlay_stage_timings = dict(outcome.overlay_stage_timings)
            return result

        partial = bool(direct_merged)
        if partial:
            warnings.append(
                "gitinclude changes aborted by OCC; direct-merged changes "
                "were already applied"
            )
        logger.warning(
            "overlay OCC changeset aborted: status=%s reason=%s file=%s",
            changeset_result.status,
            changeset_result.conflict_reason,
            changeset_result.conflict_file,
        )
        result = audit_result(
            result_text=outcome.stdout,
            exit_code=outcome.exit_code,
            gitinclude_committed=[],
            gitignore_merged=direct_merged,
            gitignore_merged_count=len(direct_merged),
            mixed_gitinclude_gitignore=mixed,
            mixed_partial_apply=partial,
            ambient=(
                [changeset_result.conflict_file]
                if changeset_result.conflict_file
                else []
            ),
            git_commit_status=changeset_result.status,
            git_conflict_reason=changeset_result.conflict_reason,
            git_conflict_file=changeset_result.conflict_file,
            warnings=warnings,
            overlay_run_timings=outcome.overlay_run_timings,
        )
        result.overlay_stage_timings = dict(outcome.overlay_stage_timings)
        return result

    async def _apply_changeset(
        self,
        outcome: OverlayRunOutcome,
        *,
        agent_id: str,
        description: str,
    ) -> ChangesetResult:
        """Run OCC policy on overlay's raw upper changes."""
        with use_sandbox_io_loop():
            result: ChangesetResult = await run_sync_in_executor(
                self._write_coordinator.apply_changeset,
                outcome.upper_changes,
                agent_id=agent_id,
                edit_type="svc_cmd_overlay",
                description=description,
            )
        return result

    async def _ensure_capture_runner(self) -> OverlayCaptureRunner:
        cached = self._capture_runner
        if cached is not None:
            return cached
        async with self._init_lock:
            cached = self._capture_runner
            if cached is not None:
                return cached
            self._capture_runner = OverlayCaptureRunner(
                sandbox_id=self.sandbox_id,
                workspace_root=self.workspace_root,
                exec_process=self._exec_sandbox_process,
                transport=self._transport,
                daemon_local=self._daemon_local,
            )
            return self._capture_runner

    async def _exec_sandbox_process(
        self,
        sandbox: Any,
        command: str,
        *,
        timeout: int | None,
    ) -> Any:
        if sandbox is None:
            completed = await asyncio.to_thread(
                subprocess.run,
                command,
                shell=True,
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
            return SimpleNamespace(
                result=completed.stdout + completed.stderr,
                exit_code=completed.returncode,
            )
        process = getattr(sandbox, "process", None)
        exec_fn = getattr(process, "exec", None) if process is not None else None
        if not callable(exec_fn):
            raise RuntimeError("Sandbox process.exec is unavailable")
        if not inspect.iscoroutinefunction(exec_fn):
            raise RuntimeError("Sandbox process.exec must be async")
        return await exec_fn(command, timeout=timeout) if timeout is not None else await exec_fn(command)
