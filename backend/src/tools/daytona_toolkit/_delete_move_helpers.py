"""Shared helpers for delete and move tools."""

from __future__ import annotations

import json
from typing import Any


def operation_payload(
    *,
    status: str,
    paths: list[str],
    warnings: list[str],
    conflict_reason: str | None = None,
    message: str | None = None,
) -> str:
    payload: dict[str, Any] = {
        "status": status,
        "paths": paths,
        "warnings": warnings,
    }
    if conflict_reason:
        payload["conflict_reason"] = conflict_reason
    if message:
        payload["message"] = message
    return json.dumps(payload)


def move_payload(
    *,
    status: str,
    src: str,
    dst: str,
    warnings: list[str],
    paths: list[str] | None = None,
    conflict_reason: str | None = None,
    message: str | None = None,
) -> str:
    payload: dict[str, Any] = {
        "status": status,
        "src_path": src,
        "target_path": dst,
        "paths": (
            paths
            if paths is not None
            else ([src, dst] if status == "moved" else [])
        ),
        "warnings": warnings,
    }
    if conflict_reason:
        payload["conflict_reason"] = conflict_reason
    if message:
        payload["message"] = message
    return json.dumps(payload)


def normalized_path(path: str) -> str:
    if path == "/":
        return path
    return path.rstrip("/") or path


def failure_status(result: Any, *, move: bool) -> tuple[str, str]:
    status = str(getattr(result, "status", "") or "failed")
    conflict_reason = str(getattr(result, "conflict_reason", "") or "")
    if conflict_reason == "not_found":
        return "not_found", "not_found"
    if move and conflict_reason == "dst_exists":
        return "dst_exists", "dst_exists"
    return status, conflict_reason or status


__all__ = [
    "failure_status",
    "move_payload",
    "normalized_path",
    "operation_payload",
]
