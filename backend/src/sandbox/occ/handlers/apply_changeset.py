"""Runtime handler for OCC raw changeset requests."""

from __future__ import annotations

from typing import Any

from sandbox.occ.engine import LocalOCCEngine
from sandbox.occ.wire import upper_change_from_dict


def handle(args: dict[str, Any]) -> Any:
    engine = LocalOCCEngine(
        workspace_root=str(args.get("workspace_root") or "/workspace")
    )
    try:
        return engine.apply_changeset(
            [upper_change_from_dict(change) for change in args.get("upper_changes", ())],
            agent_id=str(args.get("agent_id") or ""),
            edit_type=str(args.get("edit_type") or "apply_changeset"),
            description=str(args.get("description") or ""),
        )
    finally:
        engine.dispose()


__all__ = ["handle"]
