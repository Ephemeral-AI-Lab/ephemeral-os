"""Provider config.

Retry settings are env-overridable as ``EOS__PROVIDERS__RETRY__...``.
Minimax routing settings are env-overridable as ``EOS__PROVIDERS__MINIMAX__...``
or through the legacy ``MINIMAX_BASE_URL`` and ``MINIMAX_MODEL`` bindings. API
keys remain env-only and are not represented here unless a provider section
needs to own one explicitly.
"""

from __future__ import annotations

from pydantic import Field

from config.base import ModuleConfigBase


class RetryConfig(ModuleConfigBase):
    """Provider retry policy."""

    max_retries: int = Field(default=3, ge=0)
    base_delay_s: float = Field(default=1.0, ge=0)
    max_delay_s: float = Field(default=30.0, ge=0)
    status_codes: frozenset[int] = frozenset({429, 500, 502, 503, 529})


class MinimaxConfig(ModuleConfigBase):
    """Minimax provider routing config."""

    base_url: str = ""
    model: str = ""


class ProvidersConfig(ModuleConfigBase):
    """Provider-level runtime configuration."""

    retry: RetryConfig = Field(default_factory=RetryConfig)
    minimax: MinimaxConfig = Field(default_factory=MinimaxConfig)
