"""Command dispatch and workspace-write bypass guard for sandbox-local CI."""

from __future__ import annotations

import logging
import os
import time
import traceback
from pathlib import Path
from typing import Any

from sandbox.code_intelligence.daemon.handlers import DISPATCH
from sandbox.code_intelligence.daemon.state import DAEMON_STATE, QUERY_OPS

logger = logging.getLogger(__name__)

GUARD_SCAN_WINDOW = 1.0
GUARD_IGNORE_DIRS = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "venv",
    "node_modules",
    "build",
    "dist",
    ".cache",
    ".idea",
    ".vscode",
    "target",
    ".gradle",
}


def _ledger_total_edits() -> int:
    svc = DAEMON_STATE.svc
    if svc is None:
        return 0
    arbiter = getattr(svc, "arbiter", None)
    metrics = getattr(arbiter, "metrics", None) if arbiter is not None else None
    return int(getattr(metrics, "total_edits", 0))


def _ledger_paths_since(window_start: float) -> set[str]:
    """Return file paths recorded in the ledger after ``window_start``."""
    ledger = DAEMON_STATE.ledger
    if ledger is None:
        return set()
    try:
        records = ledger.changes_since(window_start)
    except Exception:  # pragma: no cover - defensive
        logger.debug("ledger.changes_since failed for window=%s", window_start, exc_info=True)
        return set()
    return {r.file_path for r in records}


def _scan_unledgered_changes(
    workspace_root: str, window_start: float, ledger_paths: set[str]
) -> list[str]:
    """Return modified files in the request window that are absent from ledger."""
    root = Path(workspace_root)
    if not root.exists():
        return []
    bypassed: list[str] = []
    try:
        for current_dir, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in GUARD_IGNORE_DIRS]
            for name in filenames:
                full = os.path.join(current_dir, name)
                try:
                    mtime = os.path.getmtime(full)
                except OSError:
                    continue
                if mtime <= window_start or full in ledger_paths:
                    continue
                bypassed.append(full)
    except OSError:  # pragma: no cover - defensive
        logger.debug("guard scan failed under %s", root, exc_info=True)
    return bypassed


class CommandSchemaError(Exception):
    """Raised when a command body does not match the expected shape."""


def _parse_command(body: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    request_id = str(body.get("id") or "")
    op = body.get("op")
    args = body.get("args", {})
    if not request_id:
        request_id = "process-exec"
    if not isinstance(op, str) or not op:
        raise CommandSchemaError("request op must be a non-empty string")
    if not isinstance(args, dict):
        raise CommandSchemaError("request args must be a dict")
    return request_id, op, args


def _schema_error_response(body: dict[str, Any], exc: CommandSchemaError) -> dict[str, Any]:
    return {
        "id": str(body.get("id") or ""),
        "ok": False,
        "error": {"kind": "InvalidSchema", "message": str(exc), "details": {}},
    }


def _unsupported_op_response(request_id: str, op: str) -> dict[str, Any]:
    return {
        "id": request_id,
        "ok": False,
        "error": {
            "kind": "UnsupportedOp",
            "message": f"unknown op: {op}",
            "details": {},
        },
    }


def _handler_error_response(request_id: str, exc: Exception) -> dict[str, Any]:
    return {
        "id": request_id,
        "ok": False,
        "error": {
            "kind": "InternalError",
            "message": str(exc),
            "details": {"traceback": traceback.format_exc()},
        },
    }


async def _dispatch_request(body: dict[str, Any]) -> dict[str, Any]:
    """Run one validated daemon command request and return a response envelope."""
    try:
        request_id, op, args = _parse_command(body)
    except CommandSchemaError as exc:
        return _schema_error_response(body, exc)

    handler = DISPATCH.get(op) or DAEMON_STATE.extra_dispatch.get(op)
    if handler is None:
        return _unsupported_op_response(request_id, op)

    is_query = op in QUERY_OPS
    window_start = time.time() - GUARD_SCAN_WINDOW
    pre_seq = 0 if is_query else _ledger_total_edits()

    try:
        result = await handler(args)
    except Exception as exc:  # pragma: no cover - defensive envelope path
        logger.exception("ci daemon handler failed for op=%s", op)
        return _handler_error_response(request_id, exc)

    success_envelope = {
        "id": request_id,
        "ok": True,
        "result": result,
    }
    if is_query or not DAEMON_STATE.guard_enabled or DAEMON_STATE.svc is None:
        return success_envelope
    if not DAEMON_STATE.workspace_root:
        return success_envelope

    post_seq = _ledger_total_edits()
    ledger_paths = _ledger_paths_since(window_start) if post_seq > pre_seq else set()
    bypassed = _scan_unledgered_changes(
        DAEMON_STATE.workspace_root, window_start, ledger_paths
    )
    if not bypassed:
        return success_envelope

    logger.error("WORKSPACE WRITE BYPASS: handler=%s bypassed paths=%s", op, bypassed)
    if not DAEMON_STATE.guard_strict:
        return success_envelope
    return {
        "id": request_id,
        "ok": False,
        "error": {
            "kind": "WorkspaceBypass",
            "message": f"unledgered writes during op={op}: {bypassed}",
            "details": {"paths": bypassed},
        },
    }


def _reset_daemon_state_for_tests(extra_dispatch: dict[str, Any] | None = None) -> None:
    """Reset module-level state between unit tests. Not a public API."""
    DAEMON_STATE.svc = None
    DAEMON_STATE.ledger = None
    DAEMON_STATE.workspace_root = ""
    DAEMON_STATE.started_at = 0.0
    DAEMON_STATE.guard_enabled = True
    DAEMON_STATE.guard_strict = False
    DAEMON_STATE.state_dir = None
    DAEMON_STATE.test_ops_enabled = False
    DAEMON_STATE.extra_dispatch = dict(extra_dispatch or {})
