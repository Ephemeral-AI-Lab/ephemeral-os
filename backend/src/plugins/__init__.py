"""EphemeralOS plugin catalog and core framework.

A plugin is a directory under ``backend/src/plugins/catalog/<name>/`` with a
declarative manifest (``plugin.md``), a setup script (``setup.sh``), one Python
file per agent-visible tool under ``tools/``, and an optional in-sandbox
runtime under ``runtime/``. See ``docs/architecture/plugins-refactor.md``.

This package MUST NOT import ``sandbox.*`` except through the public plugin
adapter surface (``sandbox.ephemeral_workspace.plugin.call_plugin`` host-side,
``sandbox.ephemeral_workspace.plugin.runtime.{register_plugin_op, PluginOpContext}`` in-sandbox).
"""

from __future__ import annotations

__all__: list[str] = []
