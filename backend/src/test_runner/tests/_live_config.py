"""Central-config helpers for test-runner live tests."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from config import get_central_config
from sandbox.host.runtime_artifact import EOSD_SHA256


_REPO_ROOT = Path(__file__).resolve().parents[4]


def database_configured() -> bool:
    return bool(get_central_config().database.url)


def live_e2e_heavy_enabled() -> bool:
    return get_central_config().runner.live_e2e.heavy_enabled


def live_e2e_capacity_enabled() -> bool:
    return get_central_config().runner.live_e2e.capacity_enabled


def concurrent_sandbox_runners() -> int:
    return get_central_config().runner.live_e2e.concurrent_sandbox_runners


def real_agent_max_duration_s() -> float:
    return get_central_config().runner.live_e2e.real_agent_max_duration_s


def sandbox_runtime() -> str:
    return os.environ.get("EOS_SANDBOX_RUNTIME", "python").strip().lower() or "python"


def rust_sandbox_runtime_unavailable_reason() -> str | None:
    if sandbox_runtime() != "rust":
        return "EOS_SANDBOX_RUNTIME=rust not selected"
    artifact = _REPO_ROOT / "sandbox" / "dist" / "eosd-linux-amd64"
    if not artifact.exists():
        return f"missing pinned eosd artifact: {artifact}"
    expected = EOSD_SHA256.get("amd64")
    actual = hashlib.sha256(artifact.read_bytes()).hexdigest()
    if actual != expected:
        return f"eosd artifact hash mismatch for amd64: got {actual}, expected {expected}"
    return None
