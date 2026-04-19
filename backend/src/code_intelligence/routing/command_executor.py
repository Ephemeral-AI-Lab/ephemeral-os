"""Audited sandbox command execution for code intelligence services."""

from __future__ import annotations

import asyncio
import inspect
from typing import Any, Callable

from code_intelligence.routing.git_diff_committer import GitDiffCommitter
from code_intelligence.routing.git_workspace_auditor import GitWorkspaceAuditor
from code_intelligence.routing.git_workspace_pool import GitWorkspacePool


class AuditedCommandExecutor:
    """Runs sandbox commands through the OCC-gated Git workspace audit path."""

    def __init__(
        self,
        *,
        sandbox_id: str,
        workspace_root: str,
        write_coordinator: Any,
        rebind_sandbox: Callable[[Any], None],
    ) -> None:
        self.sandbox_id = sandbox_id
        self.workspace_root = workspace_root
        self._write_coordinator = write_coordinator
        self._rebind_sandbox = rebind_sandbox
        self._git_workspace_pool: GitWorkspacePool | None = None
        self._git_workspace_auditor: GitWorkspaceAuditor | None = None
        self._git_workspace_init_lock = asyncio.Lock()

    async def cmd(
        self,
        sandbox: Any,
        command: str,
        *,
        timeout: int | None = None,
        description: str = "",
        agent_id: str = "",
        team_run_id: str = "",
        agent_run_id: str = "",
        task_id: str = "",
        attribute_changes: bool = True,
    ) -> Any:
        """Run one command through the fail-closed OCC audit path."""
        self._rebind_sandbox(sandbox)
        auditor = await self._ensure_git_workspace_auditor()
        return await auditor.execute(
            sandbox,
            command,
            timeout=timeout,
            description=description,
            agent_id=agent_id,
            team_run_id=team_run_id,
            agent_run_id=agent_run_id,
            task_id=task_id,
            attribute_changes=attribute_changes,
        )

    async def _ensure_git_workspace_auditor(self) -> GitWorkspaceAuditor:
        cached = self._git_workspace_auditor
        if cached is not None:
            return cached

        async with self._git_workspace_init_lock:
            cached = self._git_workspace_auditor
            if cached is not None:
                return cached
            pool = GitWorkspacePool(
                sandbox_id=self.sandbox_id,
                workspace_root=self.workspace_root,
                exec_process=self._exec_sandbox_process,
            )
            self._git_workspace_pool = pool
            self._git_workspace_auditor = GitWorkspaceAuditor(
                workspace_root=self.workspace_root,
                exec_process=self._exec_sandbox_process,
                pool=pool,
                committer=GitDiffCommitter(self._write_coordinator),
            )
            return self._git_workspace_auditor

    async def _exec_sandbox_process(
        self,
        sandbox: Any,
        command: str,
        *,
        timeout: int | None,
    ) -> Any:
        process = getattr(sandbox, "process", None)
        exec_fn = getattr(process, "exec", None) if process is not None else None
        if not callable(exec_fn):
            raise RuntimeError("Sandbox process.exec is unavailable")
        if not inspect.iscoroutinefunction(exec_fn):
            raise RuntimeError("Sandbox process.exec must be async")
        return await exec_fn(command, timeout=timeout) if timeout is not None else await exec_fn(command)
