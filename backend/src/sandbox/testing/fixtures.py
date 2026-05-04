"""Sandbox factory + cleanup helpers for E2E and live integration tests."""

from __future__ import annotations

import time


def get_sandbox_service():
    from sandbox.providers.daytona.lifecycle import SandboxService

    return SandboxService()


def create_test_sandbox(name: str = "e2e-test") -> dict:
    svc = get_sandbox_service()
    return svc.create_sandbox(
        name=f"{name}-{int(time.time())}",
        language="python",
        labels={"purpose": f"e2e-{name}"},
    )


def delete_test_sandbox(sandbox_id: str) -> None:
    try:
        svc = get_sandbox_service()
        svc.delete_sandbox(sandbox_id)
    except Exception:
        pass


__all__ = ["create_test_sandbox", "delete_test_sandbox", "get_sandbox_service"]
