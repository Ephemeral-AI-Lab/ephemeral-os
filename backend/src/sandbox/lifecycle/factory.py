"""Provider-neutral sandbox lifecycle factory."""

from __future__ import annotations

from sandbox.providers.protocol import SandboxLifecycleProvider


def lifecycle_provider_for(
    *,
    provider: str = "daytona",
) -> SandboxLifecycleProvider:
    """Return the provider-owned lifecycle implementation."""
    if provider != "daytona":
        raise ValueError(f"Unsupported sandbox provider: {provider}")
    from sandbox.providers.daytona.lifecycle import DaytonaSandboxLifecycle

    return DaytonaSandboxLifecycle()


__all__ = ["lifecycle_provider_for"]
