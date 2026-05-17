"""Deprecated in-sandbox plugin runtime API shim.

Plugin authors writing a stateful runtime (``runtime/server.py``) import
``register_plugin_op`` and ``PluginOpContext`` from this module. Concrete
op handlers receive a :class:`PluginOpContext` from the dispatcher and never
import ``sandbox.*`` directly.
"""

from __future__ import annotations

import warnings

from sandbox.plugin.op_context import PluginOpContext
from sandbox.plugin.op_registry import (
    PluginOpConflictError,
    PluginOpRegistrationError,
    flush_plugin_registrations,
    pending_plugin_registrations,
    register_plugin_op,
)

warnings.warn(
    "sandbox.plugin.runtime is deprecated; use "
    "sandbox.plugin.op_context/op_registry",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = [
    "PluginOpConflictError",
    "PluginOpContext",
    "PluginOpRegistrationError",
    "flush_plugin_registrations",
    "pending_plugin_registrations",
    "register_plugin_op",
]
