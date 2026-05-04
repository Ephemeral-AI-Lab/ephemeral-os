"""Runtime handler for OCC ``apply_changeset`` requests."""

from __future__ import annotations

from typing import Any

from sandbox.client.async_bridge import run_sync
from sandbox.occ.content.manager import ContentManager
from sandbox.occ.direct.direct_merge_coordinator import DirectMergeCoordinator
from sandbox.occ.gated.gated_coordinator import OCCGatedCoordinator
from sandbox.occ.orchestrator import ChangesetOrchestrator
from sandbox.occ.routing.gitignore import GitignoreOracle
from sandbox.occ.wire import change_from_dict, changeset_result_to_dict


def handle(args: dict[str, Any]) -> dict[str, Any]:
    workspace_root = str(args.get("workspace_root") or "/workspace")
    changes = [change_from_dict(record) for record in args.get("changes", ())]
    content = ContentManager(workspace_root)
    orchestrator = ChangesetOrchestrator(
        gitignore=GitignoreOracle(workspace_root),
        direct=DirectMergeCoordinator(content),
        gated=OCCGatedCoordinator(content),
    )
    return changeset_result_to_dict(run_sync(orchestrator.apply(changes)))


__all__ = ["handle"]
