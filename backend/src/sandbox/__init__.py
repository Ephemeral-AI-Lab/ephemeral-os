"""Sandbox package - public API, host, daemon, provider, and workspaces.

Sub-packages:
- ``sandbox.api``      — public verbs (lifecycle, read/write/edit/shell, raw_exec)
- ``sandbox.host``     — orchestrator-side setup, daemon client, and recovery
- ``sandbox.provider`` — provider adapter registry and provider implementations
- ``sandbox.daemon``   — in-sandbox dispatcher and services
- ``sandbox.main_workspace`` — persistent base workspace ownership anchor
- ``sandbox.ephemeral_workspace`` — per-tool-call pipeline and plugin dispatch
- ``sandbox.isolated_workspace`` — opt-in per-agent pinned workspace handles

The public API surface is documented in ``docs/architecture/sandbox``.
"""

from __future__ import annotations

__all__ = ["main_workspace", "ephemeral_workspace", "isolated_workspace"]


def __getattr__(name: str) -> object:
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    module = import_module(f"{__name__}.{name}")
    globals()[name] = module
    return module
