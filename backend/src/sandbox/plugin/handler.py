"""In-sandbox handlers for ``api.plugin.ensure`` and ``api.plugin.status``.

``api.plugin.ensure {"plugin": "<name>"}`` imports the plugin's
``runtime/server.py`` (which decorates handlers with
:func:`sandbox.plugin.runtime.register_plugin_op`) and flushes the pending
registrations into the daemon dispatcher under the public op name
``plugin.<name>.<op>``. Idempotent — re-calling for an already-loaded plugin
is a no-op.

``api.plugin.status {}`` returns the set of loaded plugins and their op names.
"""

from __future__ import annotations

import importlib
import inspect
import logging
import sys
from typing import Any

from sandbox.models import SandboxCaller
from sandbox.plugin.projection import WorkspaceProjection
from sandbox.plugin.runtime.context import PluginOpContext
from sandbox.plugin.runtime.registry import (
    flush_plugin_registrations,
    pending_plugin_registrations,
    clear_plugin_registrations,
)

__all__ = [
    "PluginEnsureError",
    "loaded_plugins_snapshot",
    "plugin_ensure",
    "plugin_status",
]


logger = logging.getLogger(__name__)


class PluginEnsureError(RuntimeError):
    """Raised when api.plugin.ensure fails to load a plugin runtime."""


# Process-local registry of loaded plugins → list of registered op names.
_LOADED: dict[str, list[str]] = {}
_LOADED_DIGEST: dict[str, str] = {}
# Per-layer-stack-root WorkspaceProjection cache so plugin sessions reuse
# the same projection across calls.
_PROJECTIONS: dict[str, WorkspaceProjection] = {}


async def plugin_ensure(args: dict[str, Any]) -> dict[str, Any]:
    plugin_name = str(args.get("plugin") or "").strip()
    if not plugin_name:
        raise PluginEnsureError("api.plugin.ensure requires plugin name")
    digest = str(args.get("digest") or "").strip()

    if (
        plugin_name in _LOADED
        and (not digest or _LOADED_DIGEST.get(plugin_name) == digest)
    ):
        warm_result = await _warm_plugin_runtime(plugin_name, args)
        return {
            "success": True,
            "plugin": plugin_name,
            "digest": _LOADED_DIGEST.get(plugin_name, ""),
            "registered_ops": list(_LOADED[plugin_name]),
            "runtime_loaded": True,
            "already_loaded": True,
            **warm_result,
        }
    if plugin_name in _LOADED:
        await _unload_plugin_runtime(plugin_name)

    runtime_module = f"plugins.catalog.{plugin_name}.runtime.server"
    runtime_loaded = False
    try:
        importlib.import_module(runtime_module)
        runtime_loaded = True
    except ModuleNotFoundError:
        # Manifest declared no runtime (or runtime layout is missing). The
        # registrations dict will be empty for stateless plugins.
        runtime_loaded = False
    except Exception as exc:  # pragma: no cover - surface the import error
        raise PluginEnsureError(
            f"plugin runtime import failed for {plugin_name!r}: {exc}"
        ) from exc

    register_op = _import_dispatcher_register_op()
    registered_ops = flush_plugin_registrations(
        plugin_name,
        register_op,
        context_factory=_plugin_op_context_factory,
    )
    # Warm BEFORE writing _LOADED/_LOADED_DIGEST so a failed warm doesn't
    # wedge the registry (BL-01). On warm failure roll back the dispatcher
    # entries we just registered so the next call retries cleanly.
    try:
        warm_result = (
            await _warm_plugin_runtime(plugin_name, args)
            if runtime_loaded
            else {"runtime_warmed": False}
        )
    except Exception:
        from sandbox.runtime.daemon.rpc.dispatcher import OP_TABLE

        for op in registered_ops:
            OP_TABLE.pop(op, None)
        # Leave _PENDING populated so the next ensure call's flush
        # re-registers without needing to re-import the runtime module
        # (decorators only fire at import time; sys.modules caches the
        # module so import_module would no-op on retry).
        raise
    _LOADED[plugin_name] = registered_ops
    _LOADED_DIGEST[plugin_name] = digest
    if not registered_ops and not runtime_loaded:
        # Stateless plugin with no runtime — fine, idempotent.
        logger.debug(
            "plugin %s: no runtime, no ops registered", plugin_name
        )
    return {
        "success": True,
        "plugin": plugin_name,
        "digest": digest,
        "registered_ops": list(registered_ops),
        "runtime_loaded": runtime_loaded,
        "already_loaded": False,
        **warm_result,
    }


async def plugin_status(args: dict[str, Any]) -> dict[str, Any]:
    del args
    return {
        "success": True,
        "loaded_plugins": [
            {"name": name, "ops": list(ops)}
            for name, ops in sorted(_LOADED.items())
        ],
        "pending": [
            {
                "plugin": entry.plugin_name,
                "op": entry.op_name,
            }
            for entry in pending_plugin_registrations()
        ],
    }


def loaded_plugins_snapshot() -> dict[str, list[str]]:
    """Read-only view of the in-process loaded-plugin map (for tests)."""
    return {name: list(ops) for name, ops in _LOADED.items()}


async def _warm_plugin_runtime(
    plugin_name: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Run an optional plugin warm hook after runtime registration."""
    module = sys.modules.get(f"plugins.catalog.{plugin_name}.runtime.server")
    warm = getattr(module, "warm_plugin_runtime", None)
    if not callable(warm):
        return {"runtime_warmed": False}

    ctx = await _plugin_op_context_factory(args, plugin_name, "__warm__")
    try:
        result = warm(args, ctx)
        if inspect.isawaitable(result):
            result = await result
    except Exception as exc:  # pragma: no cover - surfaced through daemon
        raise PluginEnsureError(
            f"plugin runtime warm failed for {plugin_name!r}: {exc}"
        ) from exc

    warm_payload = result if isinstance(result, dict) else {}
    return {
        "runtime_warmed": True,
        "warm_result": warm_payload,
    }


async def _unload_plugin_runtime(plugin_name: str) -> None:
    await _evict_plugin_sessions(plugin_name)
    from sandbox.runtime.daemon.rpc.dispatcher import OP_TABLE

    for op in _LOADED.pop(plugin_name, []):
        OP_TABLE.pop(op, None)
    _LOADED_DIGEST.pop(plugin_name, None)
    clear_plugin_registrations(plugin_name)
    prefix = f"plugins.catalog.{plugin_name}"
    for module_name in [
        name
        for name in sys.modules
        if name == prefix or name.startswith(f"{prefix}.")
    ]:
        sys.modules.pop(module_name, None)
    importlib.invalidate_caches()


async def _evict_plugin_sessions(plugin_name: str) -> None:
    module = sys.modules.get(
        f"plugins.catalog.{plugin_name}.runtime.session_manager"
    )
    evict_all = getattr(module, "evict_all", None)
    if not callable(evict_all):
        return
    result = evict_all()
    if inspect.isawaitable(result):
        await result


def _import_dispatcher_register_op() -> Any:
    from sandbox.runtime.daemon.rpc.dispatcher import register_op

    def _idempotent_register_op(op: str, handler: Any) -> None:
        from sandbox.runtime.daemon.rpc.dispatcher import OP_TABLE

        existing = OP_TABLE.get(op)
        if existing is handler:
            return
        register_op(op, handler)

    return _idempotent_register_op


async def _plugin_op_context_factory(
    args: dict[str, Any], plugin_name: str, op_name: str
) -> PluginOpContext:
    """Build a PluginOpContext from the daemon-envelope args.

    Plugin handlers don't see (or need) the layer_stack_root / caller fields
    directly — they're stripped from the args mapping before reaching the
    plugin handler (the dispatcher passes the raw envelope, but the wrapper
    in registry._wrap_with_context forwards the same dict).
    """
    layer_stack_root = str(args.get("layer_stack_root", "")).strip()
    caller_dict = args.get("caller") or {}
    if isinstance(caller_dict, dict):
        caller = SandboxCaller(
            agent_id=str(caller_dict.get("agent_id", "")),
            run_id=str(caller_dict.get("run_id", "")),
            agent_run_id=str(caller_dict.get("agent_run_id", "")),
            task_id=str(caller_dict.get("task_id", "")),
        )
    else:
        caller = SandboxCaller(agent_id="", run_id="", agent_run_id="", task_id="")
    projection = _PROJECTIONS.get(layer_stack_root)
    if projection is None:
        projection = WorkspaceProjection(layer_stack_root)
        _PROJECTIONS[layer_stack_root] = projection
    return PluginOpContext(
        layer_stack_root=layer_stack_root,
        caller=caller,
        projection=projection,
        logger=logging.getLogger(f"plugin.{plugin_name}"),
        metadata={
            "op_name": op_name,
            "workspace_root": str(args.get("workspace_root", "")),
        },
    )
