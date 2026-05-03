"""Runtime handler for OCC single-edit requests."""

from __future__ import annotations

from typing import Any

from sandbox.occ.engine import LocalOCCEngine
from sandbox.occ.wire import edit_request_from_dict


def handle(args: dict[str, Any]) -> Any:
    engine = LocalOCCEngine(
        workspace_root=str(args.get("workspace_root") or "/workspace")
    )
    try:
        return engine.apply(edit_request_from_dict(args["request"]))
    finally:
        engine.dispose()


__all__ = ["handle"]
