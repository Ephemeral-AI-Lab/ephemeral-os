"""In-sandbox plugin runtime API.

Plugin authors writing a stateful runtime (``runtime/server.py``) import
``register_plugin_op`` and ``PluginOpContext`` from this module. Concrete
op handlers receive a :class:`PluginOpContext` from the dispatcher and never
import ``sandbox.*`` directly.
"""

from __future__ import annotations

from sandbox.plugin.runtime.context import PluginOpContext
from sandbox.plugin.runtime.registry import (
    PluginOpConflictError,
    PluginOpRegistrationError,
    flush_plugin_registrations,
    pending_plugin_registrations,
    register_plugin_op,
)

__all__ = [
    "PluginOpConflictError",
    "PluginOpContext",
    "PluginOpRegistrationError",
    "flush_plugin_registrations",
    "pending_plugin_registrations",
    "register_plugin_op",
]
