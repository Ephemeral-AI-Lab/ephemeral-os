"""Sandbox factory + cleanup helpers for E2E and live integration tests."""

from __future__ import annotations

import time

from sandbox.api import status as sb_status


def get_sandbox_service():
    """Return the sandbox.api.status module — exposes the public verbs."""
    return sb_status


def create_test_sandbox(name: str = "e2e-test") -> dict:
    return sb_status.create_sandbox(
        name=f"{name}-{int(time.time())}",
        language="python",
        labels={"purpose": f"e2e-{name}"},
    )


def delete_test_sandbox(sandbox_id: str) -> None:
    try:
        sb_status.delete_sandbox(sandbox_id)
    except Exception:
        pass


__all__ = ["create_test_sandbox", "delete_test_sandbox", "get_sandbox_service"]
