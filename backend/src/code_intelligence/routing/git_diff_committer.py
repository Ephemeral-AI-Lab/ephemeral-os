"""Adapt Git workspace diffs to the existing OCC write coordinator."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from code_intelligence._async_bridge import run_sync_in_executor, use_sandbox_io_loop
from code_intelligence.types import OperationChange, OperationResult
from code_intelligence.routing.git_workspace_types import WorkspaceDiff

logger = logging.getLogger(__name__)


class GitDiffCommitter:
    """Commit a :class:`WorkspaceDiff` through the existing OCC coordinator.

    This class intentionally has no knowledge of ``CodeIntelligenceService`` or
    CodeAct. It converts a diff into strict-base ``OperationChange`` entries and
    delegates all locking, verification, rollback, ledger, and cache refresh to
    ``WriteCoordinator``.
    """

    def __init__(self, write_coordinator: Any) -> None:
        self._write_coordinator = write_coordinator

    async def commit(
        self,
        diff: WorkspaceDiff,
        *,
        agent_id: str = "",
        edit_type: str = "svc_cmd_git_workspace",
        description: str = "daytona_codeact git workspace",
    ) -> OperationResult:
        """Commit *diff* as one atomic strict-base OCC batch."""

        changes = self.to_operation_changes(diff)
        if not changes:
            return OperationResult(
                success=True,
                status="committed",
                files=(),
                conflict_file=None,
                conflict_reason="",
                timings={"total": 0.0},
            )

        with use_sandbox_io_loop():
            result: OperationResult = await run_sync_in_executor(
                self._write_coordinator.commit_operation_against_base,
                changes,
                agent_id=agent_id,
                edit_type=edit_type,
                description=description,
            )
        if not result.success:
            logger.warning(
                "git workspace commit aborted: status=%s reason=%s file=%s",
                result.status,
                result.conflict_reason,
                result.conflict_file,
            )
        return result

    @staticmethod
    def to_operation_changes(diff: WorkspaceDiff) -> list[OperationChange]:
        """Convert *diff* to strict-base operation changes."""

        changes: list[OperationChange] = []
        for item in diff.files:
            changes.append(
                OperationChange(
                    file_path=_live_path(diff.workspace_root, item.path),
                    base_content=item.base_content,
                    base_hash=item.base_hash,
                    final_content=item.final_content,
                    base_existed=item.base_existed,
                    strict_base=True,
                )
            )
        return changes


def changed_live_paths(diff: WorkspaceDiff | Sequence[OperationChange]) -> list[str]:
    """Return sorted changed live paths from a diff or operation sequence."""

    if isinstance(diff, WorkspaceDiff):
        return sorted(_live_path(diff.workspace_root, item.path) for item in diff.files)
    return sorted(change.file_path for change in diff)


def _live_path(workspace_root: str, rel_path: str) -> str:
    rel = rel_path.replace("\\", "/").lstrip("/")
    return f"{workspace_root.rstrip('/')}/{rel}"


__all__ = [
    "GitDiffCommitter",
    "changed_live_paths",
]
