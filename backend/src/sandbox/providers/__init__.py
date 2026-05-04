"""Provider adapter seam for sandbox runtime routing."""

from __future__ import annotations

from sandbox.providers.protocol import ProviderAdapter
from sandbox.providers.registry import (
    dispose_adapter,
    get_adapter,
    get_default_provider,
    register_adapter,
    set_default_provider,
)

__all__ = [
    "ProviderAdapter",
    "dispose_adapter",
    "get_adapter",
    "get_default_provider",
    "register_adapter",
    "set_default_provider",
]
