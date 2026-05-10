"""Compat shim — re-exports live_e2e.audit."""

from __future__ import annotations

from live_e2e.audit import *  # noqa: F401, F403

try:
    from live_e2e.audit import __all__  # type: ignore[attr-defined]  # noqa: F401
except ImportError:
    pass
