"""Generic in-sandbox daemon dispatcher.

Host-to-guest contract: the resident AF_UNIX daemon decodes one JSON object
such as ``{"op": "api.v1.shell", "args": {...}}`` and dispatches the decoded
envelope here. Handlers return JSON-safe values or dataclasses matching the
public sandbox API result types.
"""

from __future__ import annotations

import dataclasses
import asyncio
import inspect
import logging
import os
from collections.abc import Callable, Mapping
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from audit.jsonl import append_jsonl_event
from sandbox._shared.clock import monotonic_now
from sandbox.daemon.rpc.in_flight import get_in_flight_registry
from sandbox.isolated_workspace.helper.manager import get_active_pipeline

logger = logging.getLogger("sandbox.daemon.rpc.dispatcher")

_BOOT_T0 = monotonic_now()

Handler = Callable[[dict[str, Any]], Any]

OP_TABLE: dict[str, Handler] = {}
PLUGIN_GATE_AUDIT_EVENTS: list[dict[str, Any]] = []


def register_op(op: str, handler: Handler) -> None:
    """Register a daemon operation handler.

    Peer bootstrap modules call this at import time. Re-registering the
    *same* handler under the same op is a no-op (so bootstrap can re-run
    safely from tests); registering a *different* handler under an
    already-claimed op raises so peer collisions surface loudly.
    """
    if not isinstance(op, str) or not op:
        raise ValueError("op must be a non-empty string")
    existing = OP_TABLE.get(op)
    if existing is handler:
        return
    if existing is not None:
        raise ValueError(f"runtime op already registered: {op}")
    OP_TABLE[op] = handler


async def dispatch_envelope_async(
    envelope: Mapping[str, Any],
    *,
    boot_t0: float | None = None,
) -> dict[str, Any]:
    """Dispatch an envelope from the daemon's running asyncio loop.

    ``boot_t0`` overrides the module-level ``_BOOT_T0`` for the
    ``runtime.boot_to_dispatch_s`` metric. The daemon passes a per-call
    timestamp captured just before reading the request line, so the metric
    measures socket-receive + parse cost rather than the daemon's wall
    uptime — which would otherwise grow monotonically and break the
    Phase 3 pass bar (``runtime.boot_to_dispatch_s ≤ 2 ms``).
    """
    dispatch_entered_at = monotonic_now()
    validation_error, op, args_raw, invocation_id = _validate_envelope(envelope)
    if validation_error is not None:
        return validation_error

    registry = get_in_flight_registry()
    task = asyncio.current_task()
    if task is not None:
        registry.register(
            invocation_id,
            task,
            agent_id=_agent_id(args_raw),
            op=op,
            background=bool(args_raw.get("background", False)),
        )
    try:
        plugin_block = _check_plugin_block(args_raw, op)
        if plugin_block is not None:
            return plugin_block
        handler = OP_TABLE.get(op)
        if handler is None:
            return _error("unknown_op", f"unknown op: {op}", {"op": op})
        result = handler(dict(args_raw))
        if inspect.isawaitable(result):
            result = await result
        jsonable = _to_response_dict(result)
        _attach_runtime_boot_timings(
            jsonable,
            dispatch_entered_at=dispatch_entered_at,
            boot_t0=boot_t0,
        )
        return jsonable
    except Exception as exc:
        error_id = uuid4().hex
        logger.exception(
            "daemon op failed",
            extra={"op": op, "error_id": error_id},
        )
        return _error(
            "internal_error",
            str(exc),
            {"op": op, "error_id": error_id},
        )
    finally:
        registry.deregister(invocation_id)


def _validate_envelope(
    envelope: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, str, dict[str, Any], str]:
    op = envelope.get("op")
    if not isinstance(op, str) or not op:
        return (
            _error(
                "invalid_envelope",
                "daemon envelope requires a non-empty string op",
            ),
            "",
            {},
            "",
        )
    invocation_id = str(envelope.get("invocation_id") or "").strip()
    if not invocation_id:
        invocation_id = uuid4().hex
        logger.warning("daemon envelope missing invocation_id for op=%s", op)
    args_raw = envelope.get("args", {})
    if args_raw is None:
        args_raw = {}
    if not isinstance(args_raw, dict):
        return (
            _error(
                "invalid_envelope",
                "daemon envelope args must be a JSON object",
                {"op": op},
            ),
            op,
            {},
            invocation_id,
        )
    args_raw.setdefault("invocation_id", invocation_id)
    return None, op, args_raw, invocation_id


def _agent_id(args: Mapping[str, Any]) -> str:
    caller = args.get("caller")
    if isinstance(caller, Mapping):
        raw = caller.get("agent_id") or caller.get("agent_run_id")
        if raw:
            return str(raw)
    raw = args.get("agent_id")
    return str(raw or "").strip()


def _attach_runtime_boot_timings(
    response: Any,
    *,
    dispatch_entered_at: float,
    boot_t0: float | None = None,
) -> None:
    if not isinstance(response, dict):
        return
    timings = response.get("timings")
    if not isinstance(timings, dict):
        timings = {}
        response["timings"] = timings
    origin = boot_t0 if boot_t0 is not None else _BOOT_T0
    timings["runtime.boot_to_dispatch_s"] = max(0.0, dispatch_entered_at - origin)
    timings["runtime.dispatch_s"] = max(
        0.0, monotonic_now() - dispatch_entered_at
    )


def _error(
    kind: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "success": False,
        "warnings": [],
        "timings": {},
        "error": {
            "kind": kind,
            "message": message,
            "details": details or {},
        },
    }


def _to_jsonable(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: _to_jsonable(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, SimpleNamespace):
        return {str(k): _to_jsonable(v) for k, v in vars(obj).items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(v) for v in obj]
    if isinstance(obj, dict):
        return {str(k): _to_jsonable(v) for k, v in obj.items()}
    return obj


def _to_response_dict(result: Any) -> dict[str, Any]:
    jsonable = _to_jsonable(result)
    if not isinstance(jsonable, dict):
        raise TypeError("runtime handler returned a non-object response")
    return jsonable


def _check_plugin_block(args: Mapping[str, Any], op_name: str) -> dict[str, Any] | None:
    if not (op_name.startswith("api.plugin.") or op_name.startswith("plugin.")):
        return None
    iws = get_active_pipeline()
    agent_id = _agent_id(args)
    if iws is None:
        _emit_plugin_gate_audit(op_name, agent_id)
        return None
    if agent_id and iws.get_handle(agent_id) is not None:
        return {
            "success": False,
            "warnings": [],
            "timings": {},
            "error": {
                "kind": "forbidden_in_isolated_workspace",
                "message": "plugin access is blocked while isolated_workspace is open",
                "details": {"op": op_name, "agent_id": agent_id},
            },
        }
    return None


def _emit_plugin_gate_audit(op_name: str, agent_id: str) -> None:
    event = {
        "type": "workspace_lifecycle.plugin_check_unbootstrapped",
        "payload": {"op": op_name, "agent_id": agent_id},
    }
    PLUGIN_GATE_AUDIT_EVENTS.append(event)
    append_jsonl_event(os.environ.get("EOS_WORKSPACE_LIFECYCLE_AUDIT_PATH"), event)


def _iws_error_payload(exc: object) -> dict[str, Any]:
    return {
        "success": False,
        "error": {
            "kind": getattr(exc, "kind", "internal_error"),
            "message": str(exc),
            "details": getattr(exc, "details", {}),
        },
    }


async def _iws_enter(args: dict[str, Any]) -> dict[str, Any]:
    from sandbox.isolated_workspace.helper.manager import (
        IsolatedWorkspaceError,
        _ensure_manager,
        require_arg,
    )

    try:
        manager = await _ensure_manager(args)
        handle = await manager.enter(require_arg(args, "agent_id"))
    except IsolatedWorkspaceError as exc:
        return _iws_error_payload(exc)
    return {
        "success": True,
        "manifest_version": handle.manifest_version,
        "manifest_root_hash": handle.manifest_root_hash,
    }


async def _iws_exit(args: dict[str, Any]) -> dict[str, Any]:
    from sandbox.isolated_workspace.helper.manager import (
        IsolatedWorkspaceError,
        require_arg,
        require_pipeline,
    )

    try:
        return await require_pipeline().exit(require_arg(args, "agent_id"))
    except IsolatedWorkspaceError as exc:
        return _iws_error_payload(exc)


async def _iws_status(args: dict[str, Any]) -> dict[str, Any]:
    from sandbox.isolated_workspace.helper.manager import (
        IsolatedWorkspaceError,
        require_arg,
        require_pipeline,
    )

    try:
        manager = require_pipeline()
    except IsolatedWorkspaceError as exc:
        return _iws_error_payload(exc)
    handle = manager.get_handle(require_arg(args, "agent_id"))
    if handle is None:
        return {"success": True, "open": False}
    return {
        "success": True,
        "open": True,
        "manifest_version": handle.manifest_version,
        "created_at": handle.created_at,
        "last_activity": handle.last_activity,
    }


async def _iws_list_open(args: dict[str, Any]) -> dict[str, Any]:
    from sandbox.isolated_workspace.helper.manager import (
        IsolatedWorkspaceError,
        require_pipeline,
    )

    try:
        manager = require_pipeline()
    except IsolatedWorkspaceError:
        return {"success": True, "open_agent_ids": []}
    return {"success": True, "open_agent_ids": manager.list_open_agents()}


async def _iws_test_reset(args: dict[str, Any]) -> dict[str, Any]:
    if os.environ.get(
        "EOS_ISOLATED_WORKSPACE_TEST_HARNESS", ""
    ).strip().lower() != "true":
        return {
            "success": False,
            "error": {
                "kind": "forbidden",
                "message": (
                    "api.isolated_workspace.test_reset requires "
                    "EOS_ISOLATED_WORKSPACE_TEST_HARNESS=true"
                ),
                "details": {},
            },
        }
    from sandbox.isolated_workspace.helper.manager import (
        IsolatedWorkspaceError,
        require_pipeline,
    )

    try:
        manager = require_pipeline()
    except IsolatedWorkspaceError:
        return {"success": True, "exited_agents": []}
    result = await manager.test_reset()
    return {"success": True, **result}


def _load_peer_bootstraps() -> None:
    from sandbox.daemon import handlers
    from sandbox.ephemeral_workspace.plugin import handler as plugin_handler

    bootstrap: dict[str, Handler] = {
        "api.isolated_workspace.enter": _iws_enter,
        "api.isolated_workspace.exit": _iws_exit,
        "api.isolated_workspace.status": _iws_status,
        "api.isolated_workspace.list_open": _iws_list_open,
        "api.isolated_workspace.test_reset": _iws_test_reset,
        "api.ensure_workspace_base": handlers.ensure_workspace_base,
        "api.build_workspace_base": handlers.build_workspace_base,
        "api.prepare_workspace_snapshot": handlers.prepare_workspace_snapshot,
        "api.release_lease": handlers.release_lease,
        "api.layer_stack.fence_stale_staging": handlers.fence_stale_staging,
        "api.edit_file": handlers.edit_file,
        "api.v1.edit_file": handlers.edit_file,
        "api.glob": handlers.glob,
        "api.v1.glob": handlers.glob,
        "api.grep": handlers.grep,
        "api.v1.grep": handlers.grep,
        "api.layer_metrics": handlers.layer_metrics,
        "api.plugin.ensure": plugin_handler.plugin_ensure,
        "api.plugin.status": plugin_handler.plugin_status,
        "api.read_file": handlers.read_file,
        "api.v1.read_file": handlers.read_file,
        "api.runtime.ready": handlers.runtime_ready,
        "api.v1.shell": handlers.shell,
        "api.v1.cancel": handlers.cancel,
        "api.v1.heartbeat": handlers.heartbeat,
        "api.v1.inflight_count": handlers.inflight_count,
        "api.workspace_binding": handlers.workspace_binding,
        "api.write_file": handlers.write_file,
        "api.v1.write_file": handlers.write_file,
    }
    for op, handler in bootstrap.items():
        register_op(op, handler)


_load_peer_bootstraps()


__all__ = [
    "Handler",
    "OP_TABLE",
    "dispatch_envelope_async",
    "register_op",
]
