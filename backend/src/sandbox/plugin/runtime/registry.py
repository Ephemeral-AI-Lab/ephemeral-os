"""In-sandbox plugin op registry.

The :func:`register_plugin_op` decorator records ``(plugin_name, op_name,
handler)`` triples at module import time. :func:`flush_plugin_registrations`
hands them off to the daemon dispatcher under the public op name
``plugin.<plugin>.<op>``.

The decorator enforces the namespace rule from
``docs/architecture/plugins-refactor.md`` §2: a module that calls
``register_plugin_op('lsp', 'hover')`` MUST be importable as
``plugins.catalog.lsp.runtime.<something>``. The check uses ``inspect.stack``
to read the caller frame's ``__name__``.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any

from sandbox.plugin.runtime.context import PluginOpContext

__all__ = [
    "ContextFactory",
    "DispatcherHandler",
    "PluginOpConflictError",
    "PluginOpHandler",
    "PluginOpRegistrationError",
    "flush_plugin_registrations",
    "pending_plugin_registrations",
    "register_plugin_op",
]


PluginOpHandler = Callable[..., Awaitable[Any]]
DispatcherHandler = Callable[[dict[str, Any]], Awaitable[Any]]
ContextFactory = Callable[
    [dict[str, Any], str, str],
    Awaitable[PluginOpContext],
]


class PluginOpRegistrationError(RuntimeError):
    """Raised when register_plugin_op is invoked from a forbidden module."""


class PluginOpConflictError(RuntimeError):
    """Raised when two distinct handlers try to register the same op."""


@dataclass(frozen=True)
class _PendingRegistration:
    plugin_name: str
    op_name: str
    handler: PluginOpHandler


_PENDING: dict[tuple[str, str], _PendingRegistration] = {}


def register_plugin_op(
    plugin_name: str, op_name: str
) -> Callable[[PluginOpHandler], PluginOpHandler]:
    """Decorator that records a plugin op handler.

    Identical re-registration (same plugin/op/handler) is a no-op. Conflicting
    registration with a different handler raises ``PluginOpConflictError``.
    """
    plugin_name = (plugin_name or "").strip()
    op_name = (op_name or "").strip()
    if not plugin_name or not op_name:
        raise PluginOpRegistrationError(
            "register_plugin_op requires non-empty plugin_name and op_name"
        )
    expected_module_prefix = f"plugins.catalog.{plugin_name}."
    caller_module = _caller_module_name()
    if not caller_module.startswith(expected_module_prefix):
        raise PluginOpRegistrationError(
            f"register_plugin_op({plugin_name!r}, {op_name!r}) called from "
            f"{caller_module!r}; only modules under "
            f"{expected_module_prefix}* may register ops for this plugin"
        )

    def decorator(handler: PluginOpHandler) -> PluginOpHandler:
        key = (plugin_name, op_name)
        existing = _PENDING.get(key)
        if existing is not None:
            if existing.handler is handler:
                return handler
            raise PluginOpConflictError(
                f"plugin op {plugin_name!r}.{op_name!r} already has a "
                f"different handler registered"
            )
        _PENDING[key] = _PendingRegistration(
            plugin_name=plugin_name,
            op_name=op_name,
            handler=handler,
        )
        return handler

    return decorator


def pending_plugin_registrations(
    plugin_name: str | None = None,
) -> tuple[_PendingRegistration, ...]:
    """Return pending registrations, filtered by plugin name when provided."""
    if plugin_name is None:
        return tuple(_PENDING.values())
    return tuple(
        entry
        for entry in _PENDING.values()
        if entry.plugin_name == plugin_name
    )


def flush_plugin_registrations(
    plugin_name: str,
    dispatcher_register_op: Callable[[str, DispatcherHandler], None],
    *,
    context_factory: ContextFactory | None = None,
) -> list[str]:
    """Flush pending registrations for *plugin_name* into the dispatcher.

    When ``context_factory`` is provided, each plugin handler is wrapped so
    the dispatcher receives a 1-argument coroutine (``args -> response``)
    while the underlying plugin handler is invoked as
    ``await handler(args, ctx)``. Without a factory, raw handlers are
    registered (used by tests that call handlers directly with mocked args).
    """
    plugin_name = (plugin_name or "").strip()
    if not plugin_name:
        raise PluginOpRegistrationError(
            "flush_plugin_registrations requires a non-empty plugin_name"
        )
    registered: list[str] = []
    for entry in _filter_pending(plugin_name):
        public_op = f"plugin.{entry.plugin_name}.{entry.op_name}"
        if context_factory is None:
            handler: DispatcherHandler = entry.handler
        else:
            handler = _wrap_with_context(
                entry.handler,
                context_factory=context_factory,
                plugin_name=entry.plugin_name,
                op_name=entry.op_name,
            )
        dispatcher_register_op(public_op, handler)
        registered.append(public_op)
    return registered


def _wrap_with_context(
    plugin_handler: PluginOpHandler,
    *,
    context_factory: ContextFactory,
    plugin_name: str,
    op_name: str,
) -> DispatcherHandler:
    async def dispatcher_handler(args: dict[str, Any]) -> Any:
        ctx = await context_factory(args, plugin_name, op_name)
        return await plugin_handler(args, ctx)

    return dispatcher_handler


def _filter_pending(plugin_name: str) -> Iterable[_PendingRegistration]:
    return [
        entry
        for entry in _PENDING.values()
        if entry.plugin_name == plugin_name
    ]


def _caller_module_name() -> str:
    """Return the ``__name__`` of the module that called register_plugin_op.

    Skips the registry module itself; returns ``''`` when no caller frame is
    available (mostly a defensive guard for synthetic frames).
    """
    here = __name__
    frame = inspect.currentframe()
    try:
        # Walk up: we are in _caller_module_name → register_plugin_op → caller.
        if frame is None:
            return ""
        for _ in range(8):
            frame = frame.f_back
            if frame is None:
                return ""
            mod_name = frame.f_globals.get("__name__", "")
            if mod_name and mod_name != here:
                return str(mod_name)
        return ""
    finally:
        del frame
