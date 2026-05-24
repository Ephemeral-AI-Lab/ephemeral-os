"""Sandbox package — public API, host, daemon, provider, and workspaces.

Sub-packages:
- ``sandbox.api``      — public verbs (lifecycle, read/write/edit/shell, raw_exec)
- ``sandbox.host``     — orchestrator-side setup, daemon client, and recovery
- ``sandbox.provider`` — provider adapter registry and provider implementations
- ``sandbox.daemon``   — in-sandbox dispatcher and services
- ``sandbox.main_workspace`` — persistent base repo + LayerStack/OCC facade
- ``sandbox.ephemeral_workspace`` — per-tool-call pipeline and plugin dispatch
- ``sandbox.isolated_workspace`` — opt-in per-agent pinned workspace handles

The public API surface is documented in ``docs/sandbox/api_surface.md``.
"""

from sandbox import ephemeral_workspace, isolated_workspace, main_workspace

__all__ = ["main_workspace", "ephemeral_workspace", "isolated_workspace"]
