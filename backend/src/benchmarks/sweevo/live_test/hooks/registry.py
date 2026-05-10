"""Compat shim — re-exports live_e2e.hooks.registry."""

from __future__ import annotations

from live_e2e.hooks.registry import *  # noqa: F401, F403

try:
    from live_e2e.hooks.registry import __all__  # type: ignore[attr-defined]  # noqa: F401
except ImportError:
    pass
