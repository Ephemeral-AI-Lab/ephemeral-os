"""Provider adapter seam for sandbox runtime routing."""

from __future__ import annotations

from sandbox.provider.bootstrap import bootstrap_sandbox_provider
from sandbox.provider.protocol import ProviderAdapter
from sandbox.provider.registry import (
    dispose_adapter,
    get_adapter,
    get_default_provider,
    has_registered_adapter,
    register_adapter,
    set_default_provider,
)

__all__ = [
    "ProviderAdapter",
    "bootstrap_sandbox_provider",
    "dispose_adapter",
    "get_adapter",
    "get_default_provider",
    "has_registered_adapter",
    "register_adapter",
    "set_default_provider",
]
