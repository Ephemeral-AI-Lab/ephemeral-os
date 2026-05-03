"""Runtime handlers for explicit-base OCC commit requests."""

from __future__ import annotations

from typing import Any

from sandbox.occ.engine import LocalOCCEngine
from sandbox.occ.wire import (
    editspec_from_dict,
    operation_change_from_dict,
    writespec_from_dict,
)


def handle_against_base(args: dict[str, Any]) -> Any:
    engine = LocalOCCEngine(
        workspace_root=str(args.get("workspace_root") or "/workspace")
    )
    try:
        return engine.commit_operation_against_base(
            [operation_change_from_dict(change) for change in args.get("changes", ())],
            agent_id=str(args.get("agent_id") or ""),
            edit_type=str(args.get("edit_type") or "commit_against_base"),
            description=str(args.get("description") or ""),
        )
    finally:
        engine.dispose()


def handle_many(args: dict[str, Any]) -> Any:
    engine = LocalOCCEngine(
        workspace_root=str(args.get("workspace_root") or "/workspace")
    )
    try:
        return engine.commit_specs_many(
            [_commit_request_from_dict(req) for req in args.get("requests", ())]
        )
    finally:
        engine.dispose()


def _commit_request_from_dict(raw: dict[str, Any]) -> dict[str, Any]:
    op = str(raw.get("op") or "")
    specs_raw = raw.get("specs") or ()
    if op == "write":
        specs = tuple(writespec_from_dict(spec) for spec in specs_raw)
    elif op == "edit":
        specs = tuple(editspec_from_dict(spec) for spec in specs_raw)
    else:
        specs = ()
    return {
        "op": op,
        "specs": specs,
        "agent_id": str(raw.get("agent_id") or ""),
        "description": str(raw.get("description") or ""),
    }


__all__ = ["handle_against_base", "handle_many"]
