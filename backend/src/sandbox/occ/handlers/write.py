"""Runtime handler for OCC write pipeline requests."""

from __future__ import annotations

from typing import Any

from sandbox.occ.wire import writespec_from_dict
from sandbox.runtime.pipelines import write_pipeline


def handle(args: dict[str, Any]) -> Any:
    specs = [writespec_from_dict(spec) for spec in args.get("specs", ())]
    return write_pipeline(
        specs,
        workspace_root=str(args.get("workspace_root") or "/workspace"),
        agent_id=str(args.get("agent_id") or ""),
        description=str(args.get("description") or ""),
    )


__all__ = ["handle"]
