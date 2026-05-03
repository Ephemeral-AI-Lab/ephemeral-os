"""Runtime handler for OCC edit pipeline requests."""

from __future__ import annotations

from typing import Any

from sandbox.occ.engine import LocalOCCEngine
from sandbox.occ.wire import editspec_from_dict
from sandbox.occ.wire import edit_request_from_dict
from sandbox.runtime.pipelines import edit_pipeline


def handle(args: dict[str, Any]) -> Any:
    specs = [editspec_from_dict(spec) for spec in args.get("specs", ())]
    return edit_pipeline(
        specs,
        workspace_root=str(args.get("workspace_root") or "/workspace"),
        agent_id=str(args.get("agent_id") or ""),
        description=str(args.get("description") or ""),
    )


def handle_apply(args: dict[str, Any]) -> Any:
    engine = LocalOCCEngine(
        workspace_root=str(args.get("workspace_root") or "/workspace")
    )
    try:
        return engine.apply(edit_request_from_dict(args["request"]))
    finally:
        engine.dispose()


__all__ = ["handle", "handle_apply"]
