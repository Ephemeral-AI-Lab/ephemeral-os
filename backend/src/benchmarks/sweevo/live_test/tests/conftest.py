"""pytest conftest — re-exports framework fixtures for live e2e tests."""

from __future__ import annotations

pytest_plugins = ["benchmarks.sweevo.live_test.fixtures"]
