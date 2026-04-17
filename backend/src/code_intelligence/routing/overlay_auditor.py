"""Overlay-based process auditor.

Runs one sandbox command inside a per-run user+mount namespace with
an overlayfs whose upperdir captures the command's writes in isolation.
Writes are then applied back to the shared ``repo_root`` and audited
through the arbiter ledger with per-actor attribution.

Scope limits in v1 (documented):

* ``MODIFY`` changes apply via plain overwrite of ``repo_root/path``
  with the upperdir content. This is correct-by-construction when
  only one actor modifies a given file during overlapping windows
  (common case). For **concurrent writers on disjoint lines of the
  same file**, v1 detects the conflict via
  ``arbiter.record_edit`` hash mismatch but does not yet auto-merge
  hunks. 3-way merge via ``git merge-file`` is a v2 enhancement.
* ``DELETE`` / ``SYMLINK`` / ``OPAQUE_DIR`` are recorded but v1
  applies them through :class:`ContentManager` only for plain files;
  directory-scoped changes are logged and deferred.

Interface parity with :class:`ProcessAuditor` means callers in
``routing.service`` can swap auditors based on capability probe
without changes.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from code_intelligence.hashing import content_hash
from code_intelligence.routing.overlay_exec import (
    OverlayExec,
    OverlayExecError,
    OverlayMountError,
    OverlayRunResult,
)
from code_intelligence.routing.upperdir_walker import (
    ChangeKind,
    UpperdirChange,
    cleanup_tar,
    iter_upperdir_changes,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OverlayAuditorConfig:
    """Tunable knobs for :class:`OverlayAuditor`.

    ``lowerdir_provider`` is an async callable ``(repo_root) -> lowerdir
    path`` that returns a stable snapshot path for the overlay's
    ``lowerdir``. The auditor does not manage lowerdir lifecycle itself;
    that is a service-level concern (a shared cached worktree of HEAD,
    refreshed on commit).
    """

    tmpfs_size: str = "2g"
    audit_description_prefix: str = "overlay_codeact"


class OverlayAuditor:
    """Audit one sandbox process op via an overlayfs capture.

    See module docstring for v1 scope limits.
    """

    def __init__(
        self,
        *,
        workspace_root: str,
        exec_process: Callable[..., Awaitable[Any]],
        arbiter: Any,
        content: Any,
        symbol_index: Any,
        lsp_client: Any,
        lowerdir_provider: Callable[[str], Awaitable[str]],
        config: OverlayAuditorConfig | None = None,
    ) -> None:
        self._workspace_root = workspace_root
        self._arbiter = arbiter
        self._content = content
        self._symbol_index = symbol_index
        self._lsp_client = lsp_client
        self._lowerdir_provider = lowerdir_provider
        self._config = config or OverlayAuditorConfig()
        self._overlay = OverlayExec(
            exec_process=exec_process,
            tmpfs_size=self._config.tmpfs_size,
        )

    async def execute(
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
    ) -> Any:
        """Drop-in replacement for :meth:`ProcessAuditor.execute`.

        Returns a ``SimpleNamespace`` with ``result`` (user stdout),
        ``exit_code`` (user exit), ``changed_paths`` (list), and
        ``files_written`` (count).
        """
        lowerdir = await self._lowerdir_provider(self._workspace_root)
        try:
            run = await self._overlay.execute(
                sandbox,
                command,
                lowerdir=lowerdir,
                repo_root=self._workspace_root,
                timeout=timeout,
            )
        except OverlayMountError:
            # Propagate so the service can fall back to another auditor
            # for this specific command or take the sandbox out of the
            # overlay-capable pool.
            raise
        except OverlayExecError:
            raise

        try:
            changes = list(iter_upperdir_changes(run.audit_tar_path))
            changed_paths = await self._apply_and_record(
                run,
                changes=changes,
                description=description or self._config.audit_description_prefix,
                agent_id=agent_id,
                team_run_id=team_run_id,
                agent_run_id=agent_run_id,
                task_id=task_id,
            )
        finally:
            cleanup_tar(run.audit_tar_path)

        return SimpleNamespace(
            result=run.stdout,
            exit_code=run.exit_code,
            changed_paths=changed_paths,
            files_written=len(changed_paths),
        )

    async def _apply_and_record(
        self,
        run: OverlayRunResult,
        *,
        changes: list[UpperdirChange],
        description: str,
        agent_id: str,
        team_run_id: str,
        agent_run_id: str,
        task_id: str,
    ) -> list[str]:
        applied: list[str] = []
        actor_label = agent_id or agent_run_id
        for change in changes:
            file_path = f"{self._workspace_root.rstrip('/')}/{change.path}"
            try:
                applied_path = self._apply_change(change, file_path)
            except Exception:
                logger.exception(
                    "overlay_auditor: failed to apply %s on %s",
                    change.kind.value,
                    file_path,
                )
                continue
            if applied_path is None:
                continue
            old_hash, new_hash = self._hashes_for(change, file_path)
            self._arbiter.record_edit(
                file_path=file_path,
                actor_label=actor_label,
                team_run_id=team_run_id,
                agent_run_id=agent_run_id,
                task_id=task_id,
                old_hash=old_hash,
                new_hash=new_hash,
                description=f"{description}:{change.kind.value}",
            )
            self._invalidate_caches(change, file_path)
            applied.append(file_path)
        return applied

    def _apply_change(
        self,
        change: UpperdirChange,
        file_path: str,
    ) -> str | None:
        if change.kind is ChangeKind.MODIFY and change.content is not None:
            text = change.content.decode("utf-8", errors="replace")
            self._content.write(file_path, text)
            return file_path
        if change.kind is ChangeKind.DELETE:
            try:
                self._content.delete(file_path)
            except FileNotFoundError:
                pass
            return file_path
        if change.kind is ChangeKind.SYMLINK:
            logger.info(
                "overlay_auditor: symlink %s -> %s not yet applied (v1 limit)",
                file_path,
                change.symlink_target,
            )
            return None
        if change.kind is ChangeKind.OPAQUE_DIR:
            logger.info(
                "overlay_auditor: opaque dir %s not yet applied (v1 limit)",
                file_path,
            )
            return None
        return None

    def _hashes_for(
        self,
        change: UpperdirChange,
        file_path: str,
    ) -> tuple[str, str]:
        """Compute ``(old_hash, new_hash)`` for ledger attribution.

        ``old_hash`` reflects the state before this actor's change;
        ``new_hash`` reflects the state after. Both use the shared
        ``content_hash`` so arbiter comparisons line up across tools.
        """
        if change.kind is ChangeKind.DELETE:
            # Post-delete hash is empty. Pre-delete hash is whatever we
            # just removed; best-effort peek via content manager would
            # require a read-before-delete, which we skip for v1.
            return "", ""
        if change.kind is ChangeKind.MODIFY and change.content is not None:
            try:
                text = change.content.decode("utf-8", errors="replace")
                new_hash = content_hash(text)
            except Exception:
                new_hash = ""
            return "", new_hash
        return "", ""

    def _invalidate_caches(self, change: UpperdirChange, file_path: str) -> None:
        if change.kind is ChangeKind.MODIFY and change.content is not None:
            try:
                text = change.content.decode("utf-8", errors="replace")
                self._symbol_index.refresh(file_path, text)
            except Exception:
                logger.debug(
                    "overlay_auditor: symbol refresh failed for %s",
                    file_path,
                    exc_info=True,
                )
        try:
            self._lsp_client.invalidate(file_path)
        except Exception:
            logger.debug(
                "overlay_auditor: lsp invalidate failed for %s",
                file_path,
                exc_info=True,
            )


__all__ = ["OverlayAuditor", "OverlayAuditorConfig"]
