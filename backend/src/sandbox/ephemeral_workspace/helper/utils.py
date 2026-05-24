"""Small helpers shared by ephemeral workspace pipeline modules."""

from __future__ import annotations

import hashlib
import os

from sandbox.ephemeral_workspace.events import PathChange


def foreign_watch_interval_s() -> float:
    raw = os.environ.get("EOS_OVERLAY_FOREIGN_WATCH_INTERVAL_S", "").strip()
    if not raw:
        return 0.25
    try:
        return max(0.05, float(raw))
    except ValueError:
        return 0.25


def event_path_change(change: object) -> PathChange:
    if hasattr(change, "path") and hasattr(change, "kind"):
        return PathChange.from_overlay_change(change)  # type: ignore[arg-type]
    return PathChange(path=str(change), kind="write", existed_before=False)


def safe_request_part(value: str) -> str:
    safe = "".join(
        char if char.isalnum() or char in ("-", "_") else "-"
        for char in str(value)
    ).strip("-")
    return safe or "operation"


def runtime_key(workspace_ref: str, workspace_root: str) -> str:
    raw = f"{workspace_ref}\0{workspace_root}".encode("utf-8", "surrogateescape")
    return hashlib.sha256(raw).hexdigest()[:16]


__all__ = [
    "event_path_change",
    "foreign_watch_interval_s",
    "runtime_key",
    "safe_request_part",
]
