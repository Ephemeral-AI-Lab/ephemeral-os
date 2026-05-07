"""One-call provider bootstrap for the Daytona adapter.

Imported once during app startup (see :mod:`server.main` lifespan). After
this call returns, ``sandbox.provider.registry.get_default_provider()``
returns a :class:`DaytonaProviderAdapter` instance and the rest of the
orchestrator no longer needs to know that daytona exists.
"""

from __future__ import annotations

from sandbox.provider.daytona.adapter import DaytonaProviderAdapter
from sandbox.provider.registry import set_default_provider


def bootstrap_daytona_provider() -> None:
    """Register the Daytona adapter as the process-wide default provider."""
    set_default_provider(DaytonaProviderAdapter())


__all__ = ["bootstrap_daytona_provider"]
