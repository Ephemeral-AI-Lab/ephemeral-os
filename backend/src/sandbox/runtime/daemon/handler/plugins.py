"""OP_TABLE shim: register ``api.plugin.ensure`` and ``api.plugin.status``.

Plugin-specific ops (``plugin.<name>.<op>``) are not registered here; they
appear when ``api.plugin.ensure`` flushes the pending registrations from
:mod:`sandbox.plugin.runtime.registry` into the dispatcher.
"""

from __future__ import annotations

from sandbox.plugin.handler import plugin_ensure, plugin_status

__all__ = ["plugin_ensure", "plugin_status"]
