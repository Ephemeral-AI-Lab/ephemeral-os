"""OCC gated coordinator: per-path file appliers fanned out via asyncio.gather.

One :class:`FileChangeApplier` exists per workspace path; cross-path tasks
parallelize. Same-path changes are pre-grouped so the per-file lock only has
to be acquired once per path.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Sequence

from sandbox.occ.changeset.types import FileResult, GatedChange
from sandbox.occ.content.manager import ContentManager
from sandbox.occ.gated.file_change_applier import FileChangeApplier


class OCCGatedCoordinator:
    """Fan out gated changes to per-file appliers in parallel."""

    def __init__(self, content: ContentManager) -> None:
        self._content = content
        self._appliers: dict[str, FileChangeApplier] = {}
        self._appliers_lock = threading.Lock()

    async def apply(
        self,
        changes: Sequence[GatedChange],
    ) -> list[FileResult]:
        if not changes:
            return []

        # Group by path so each FileChangeApplier receives an ordered list and
        # cross-path applies can race via asyncio.gather without lock contention.
        by_path: dict[str, list[GatedChange]] = {}
        for change in changes:
            by_path.setdefault(change.path, []).append(change)

        groups = await asyncio.gather(
            *(
                self._applier_for(path).apply_many(group)
                for path, group in by_path.items()
            )
        )
        return [result for group in groups for result in group]

    def _applier_for(self, path: str) -> FileChangeApplier:
        with self._appliers_lock:
            applier = self._appliers.get(path)
            if applier is None:
                applier = FileChangeApplier(path, self._content)
                self._appliers[path] = applier
            return applier


__all__ = ["OCCGatedCoordinator"]
