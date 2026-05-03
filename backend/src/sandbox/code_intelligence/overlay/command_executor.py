"""Audited sandbox command execution for code intelligence services.

Commands run through the overlay auditor. Each command executes inside a fresh
``unshare -Urm`` namespace with a tmpfs upperdir over the live workspace. The
auditor returns an :class:`OverlayRunOutcome` carrying the OCC-bound
``dirty_changes``; this executor — never overlay — drives the OCC commit and
assembles the downstream ``SimpleNamespace`` response so upstream callers
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

from sandbox.api.errors import SandboxTransportError
from sandbox.api.transport import SandboxTransport
from sandbox.client.async_bridge import run_sync_in_executor, use_sandbox_io_loop
from sandbox.code_intelligence.core.types import OperationResult
from sandbox.code_intelligence.overlay.auditor import OverlayAuditor
from sandbox.code_intelligence.overlay.results import (
    audit_result,
    reject_result,
)
from sandbox.code_intelligence.overlay.types import (
    ConflictInfo,
    OverlayRunOutcome,
)

logger = logging.getLogger(__name__)


# Substring signals that an OCC commit failed because the underlying
# transport could not deliver the apply payload through argv (E2BIG /
# ARG_MAX). Streaming the payload via stdin is the proper fix and is
# tracked separately; Slice 5a's job is purely to surface the condition
# as a structured ConflictInfo instead of a bare-string error.
_ARGV_OVERFLOW_SIGNALS = (
    "argument list too long",
    "checked batch apply failed",
    "argv_too_large",
)


def _looks_like_argv_overflow(message: str) -> bool:
    if not message:
        return False
    lowered = message.lower()
    return any(signal in lowered for signal in _ARGV_OVERFLOW_SIGNALS)


def _occ_status_to_conflict_reason(commit_result: OperationResult) -> str:
    """Map raw OCC ``OperationStatus`` to the slice-5a ``ConflictInfo.reason``.

    OCC's ``aborted_*`` / ``failed`` verdicts all funnel into
    ``patch_failed`` here so the caller sees one normalized reason.
    The original OCC status flows through to ``git_commit_status``
    on the downstream SimpleNamespace verbatim — no information lost.
    """
    if _looks_like_argv_overflow(commit_result.conflict_reason):
        return "argv_too_large"
    return "patch_failed"


class AuditedCommandExecutor:
    """Runs sandbox commands through the OCC-gated audit path.

    The overlay auditor is initialized lazily on first use.
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
        self._overlay_auditor: OverlayAuditor | None = None
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
        overlay = await self._ensure_overlay_auditor()
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
        """Drive OCC on dirty_changes and project the legacy SimpleNamespace shape."""
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

        gitignore_paths_list = list(outcome.gitignore_paths)
        gitinclude_live_paths_list = list(outcome.gitinclude_live_paths)
        warnings = list(outcome.warnings)

        if not outcome.dirty_changes:
            result = audit_result(
                result_text=outcome.stdout,
                exit_code=outcome.exit_code,
                gitinclude_committed=[],
                gitignore_merged=gitignore_paths_list,
                gitignore_merged_count=len(gitignore_paths_list),
                mixed_gitinclude_gitignore=outcome.mixed_gitinclude_gitignore,
                mixed_partial_apply=False,
                ambient=[],
                git_commit_status="noop",
                git_conflict_reason=None,
                git_conflict_file=None,
                warnings=warnings,
                overlay_run_timings=outcome.overlay_run_timings,
            )
            result.overlay_stage_timings = dict(outcome.overlay_stage_timings)
            return result

        commit_result, transport_error = await self._commit_dirty_changes(
            outcome,
            agent_id=agent_id,
            description=description,
        )

        if transport_error is not None:
            conflict = ConflictInfo(
                reason="argv_too_large",
                conflict_file=(
                    gitinclude_live_paths_list[0]
                    if gitinclude_live_paths_list
                    else None
                ),
                message=str(transport_error),
                upper_layer_path=(
                    gitinclude_live_paths_list[0]
                    if gitinclude_live_paths_list
                    else None
                ),
            )
            partial = outcome.mixed_gitinclude_gitignore
            warnings_with_argv = list(warnings)
            warnings_with_argv.append(
                f"OCC commit failed before argv could fit: {transport_error}"
            )
            if partial:
                warnings_with_argv.append(
                    "gitinclude changes aborted before OCC; gitignore runtime "
                    "changes were already applied"
                )
            result = audit_result(
                result_text=outcome.stdout,
                exit_code=outcome.exit_code,
                gitinclude_committed=[],
                gitignore_merged=gitignore_paths_list,
                gitignore_merged_count=len(gitignore_paths_list),
                mixed_gitinclude_gitignore=outcome.mixed_gitinclude_gitignore,
                mixed_partial_apply=partial,
                ambient=gitinclude_live_paths_list,
                git_commit_status="failed",
                git_conflict_reason=conflict.reason,
                git_conflict_file=conflict.conflict_file,
                warnings=warnings_with_argv,
                overlay_run_timings=outcome.overlay_run_timings,
            )
            result.overlay_stage_timings = dict(outcome.overlay_stage_timings)
            return result

        assert commit_result is not None
        if commit_result.success:
            result = audit_result(
                result_text=outcome.stdout,
                exit_code=outcome.exit_code,
                gitinclude_committed=gitinclude_live_paths_list,
                gitignore_merged=gitignore_paths_list,
                gitignore_merged_count=len(gitignore_paths_list),
                mixed_gitinclude_gitignore=outcome.mixed_gitinclude_gitignore,
                mixed_partial_apply=False,
                ambient=[],
                git_commit_status=commit_result.status,
                git_conflict_reason=None,
                git_conflict_file=None,
                warnings=warnings,
                overlay_run_timings=outcome.overlay_run_timings,
            )
            result.overlay_stage_timings = dict(outcome.overlay_stage_timings)
            return result

        # OCC failure after overlay success → patch_failed (or argv_too_large
        # if the bare-string E2BIG signal slipped through). Capture the
        # overlay upper layer in ambient_changed_paths for diagnosis.
        reason = _occ_status_to_conflict_reason(commit_result)
        partial = outcome.mixed_gitinclude_gitignore
        if partial:
            warnings.append(
                "gitinclude changes aborted by OCC; gitignore runtime changes "
                "were already applied"
            )
        if not commit_result.success:
            logger.warning(
                "overlay OCC commit aborted: status=%s raw_reason=%s file=%s "
                "translated=%s",
                commit_result.status,
                commit_result.conflict_reason,
                commit_result.conflict_file,
                reason,
            )
        result = audit_result(
            result_text=outcome.stdout,
            exit_code=outcome.exit_code,
            gitinclude_committed=[],
            gitignore_merged=gitignore_paths_list,
            gitignore_merged_count=len(gitignore_paths_list),
            mixed_gitinclude_gitignore=outcome.mixed_gitinclude_gitignore,
            mixed_partial_apply=partial,
            ambient=gitinclude_live_paths_list,
            git_commit_status=commit_result.status,
            git_conflict_reason=reason,
            git_conflict_file=commit_result.conflict_file,
            warnings=warnings,
            overlay_run_timings=outcome.overlay_run_timings,
        )
        result.overlay_stage_timings = dict(outcome.overlay_stage_timings)
        return result

    async def _commit_dirty_changes(
        self,
        outcome: OverlayRunOutcome,
        *,
        agent_id: str,
        description: str,
    ) -> tuple[OperationResult | None, Exception | None]:
        """Run the OCC commit on overlay's ``dirty_changes``.

        Returns ``(OperationResult, None)`` on a clean OCC verdict (success
        OR semantic abort). Returns ``(None, exc)`` when the underlying
        transport raises before OCC can verdict — this is the argv-overflow
        path the slice surfaces as ``argv_too_large``.
        """
        try:
            with use_sandbox_io_loop():
                commit_result: OperationResult = await run_sync_in_executor(
                    self._write_coordinator.commit_operation_against_base,
                    outcome.dirty_changes,
                    agent_id=agent_id,
                    edit_type="svc_cmd_overlay",
                    description=description,
                )
            return commit_result, None
        except SandboxTransportError as exc:
            return None, exc
        except RuntimeError as exc:
            # ContentManager surfaces transport failures as RuntimeError(str(exc)).
            if _looks_like_argv_overflow(str(exc)):
                return None, exc
            raise

    async def _ensure_overlay_auditor(self) -> OverlayAuditor:
        cached = self._overlay_auditor
        if cached is not None:
            return cached
        async with self._init_lock:
            cached = self._overlay_auditor
            if cached is not None:
                return cached
            self._overlay_auditor = OverlayAuditor(
                sandbox_id=self.sandbox_id,
                workspace_root=self.workspace_root,
                exec_process=self._exec_sandbox_process,
                transport=self._transport,
                daemon_local=self._daemon_local,
            )
            return self._overlay_auditor

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
