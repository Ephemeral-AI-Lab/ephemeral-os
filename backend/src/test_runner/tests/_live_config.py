"""Central-config helpers for task-center-runner live tests."""

from __future__ import annotations

from config import get_central_config


def database_configured() -> bool:
    return bool(get_central_config().database.url)


def live_e2e_heavy_enabled() -> bool:
    return get_central_config().runner.live_e2e.heavy_enabled


def live_e2e_capacity_enabled() -> bool:
    return get_central_config().runner.live_e2e.capacity_enabled


def real_agent_max_duration_s() -> float:
    return get_central_config().runner.live_e2e.real_agent_max_duration_s
