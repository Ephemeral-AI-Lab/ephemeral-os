"""Top-level OCC orchestrator: route changes to the gated or direct path.

The orchestrator is the sole entry point for the new gate. It:

* drops ``.git`` writes silently (matches today's behaviour);
* sends every :class:`DirectChange` to the direct merger regardless of path;
* routes :class:`GatedChange` by gitignore status and external-path check;
* runs direct and gated subsets in parallel via ``asyncio.gather``.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Sequence

from sandbox.occ.changeset.types import (
    Change,
    ChangesetResult,
    DirectChange,
    GatedChange,
)
from sandbox.occ.direct.direct_merge_coordinator import DirectMergeCoordinator
from sandbox.occ.gated.gated_coordinator import OCCGatedCoordinator
from sandbox.occ.routing.gitignore import GitignoreOracle


class ChangesetOrchestrator:
    """Sole mutation entry point for the new OCC gate."""

    def __init__(
        self,
        *,
        gitignore: GitignoreOracle,
        direct: DirectMergeCoordinator,
        gated: OCCGatedCoordinator,
    ) -> None:
        self._gitignore = gitignore
        self._direct = direct
        self._gated = gated

    async def apply(self, changes: Sequence[Change]) -> ChangesetResult:
        direct_subset: list[Change] = []
        gated_subset: list[GatedChange] = []

        for change in changes:
            if _is_dotgit(change.path):
                continue
            if isinstance(change, DirectChange):
                direct_subset.append(change)
                continue
            if _is_external(change.path):
                direct_subset.append(change)
                continue
            if self._gitignore.is_ignored(change.path):
                direct_subset.append(change)
                continue
            gated_subset.append(change)

        direct_results, gated_results = await asyncio.gather(
            self._direct.apply(direct_subset),
            self._gated.apply(gated_subset),
        )
        return ChangesetResult(files=tuple(direct_results + gated_results))


def _is_dotgit(rel: str) -> bool:
    return rel == ".git" or rel.startswith(".git/")


def _is_external(rel: str) -> bool:
    return os.path.isabs(rel) or rel == ".." or rel.startswith("../")


__all__ = ["ChangesetOrchestrator"]
