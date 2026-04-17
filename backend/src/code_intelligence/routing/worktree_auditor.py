"""Scratch-git-worktree auditor (fallback for envs without overlayfs).

This revives the pre-migration design: each audited command runs inside
a detached ``git worktree`` seeded from ``HEAD``; the auditor computes
per-actor patches via ``git diff`` against the scratch worktree's own
base tree. The patches are then applied through
:class:`WriteCoordinator` with the same arbiter-backed ledger the
overlay auditor uses.

v1 status: **stub**. Implementation is deferred -- the overlay probe is
positive on our primary target environment (Daytona) so the fallback is
not on the hot path. This module exists to lock in the interface so the
service can dispatch on probe results and to document the fallback
contract.

Callers in non-Daytona environments should plumb through
:class:`ProcessAuditor` with an explicit opt-in log warning until this
is implemented. Tracked as a follow-up.
"""

from __future__ import annotations

import logging
from typing import Any

from code_intelligence.routing.process_auditor import ProcessAuditor

logger = logging.getLogger(__name__)


class WorktreeAuditor:
    """Placeholder that delegates to :class:`ProcessAuditor` with a warning.

    Intentionally matches :meth:`ProcessAuditor.execute` so callers can
    substitute it without code changes once a real implementation lands.
    """

    def __init__(self, *, process_auditor: ProcessAuditor) -> None:
        self._delegate = process_auditor
        self._warned = False

    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        if not self._warned:
            logger.warning(
                "WorktreeAuditor is a v1 stub and currently delegates to "
                "ProcessAuditor, which can falsely attribute concurrent "
                "writers. Use OverlayAuditor where available; a real "
                "scratch-worktree implementation is tracked as follow-up."
            )
            self._warned = True
        return await self._delegate.execute(*args, **kwargs)


__all__ = ["WorktreeAuditor"]
