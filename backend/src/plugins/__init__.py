"""EphemeralOS plugin catalog and core framework.

A plugin is a directory under ``backend/src/plugins/catalog/<name>/`` with a
declarative manifest (``plugin.md``), a setup script (``setup.sh``), one Python
file per agent-visible tool under ``tools/``, and an optional in-sandbox
runtime under ``runtime/``. See ``docs/architecture/plugins-refactor.md``.

This package MUST NOT import ``sandbox.*`` except through the public host-side
plugin API (``sandbox.api.plugin_dispatch`` / ``sandbox.api.plugin_install``).
In-sandbox runtime services use ``plugins.runtime_bridge`` for registration,
PPC framing, and daemon callback context.
"""

from __future__ import annotations

__all__: list[str] = []
