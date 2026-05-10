"""Compat shim — re-exports live_e2e.squad.sandbox_probe."""

from __future__ import annotations

from live_e2e.squad.sandbox_probe import *  # noqa: F401, F403

try:
    from live_e2e.squad.sandbox_probe import __all__  # type: ignore[attr-defined]  # noqa: F401
except ImportError:
    pass
