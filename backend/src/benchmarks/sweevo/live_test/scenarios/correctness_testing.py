"""Compat shim — re-exports live_e2e.scenarios.correctness_testing."""

from __future__ import annotations

from live_e2e.scenarios.correctness_testing import *  # noqa: F401, F403

try:
    from live_e2e.scenarios.correctness_testing import __all__  # type: ignore[attr-defined]  # noqa: F401
except ImportError:
    pass
