"""Provider exports with lazy optional-dependency imports."""

from __future__ import annotations

from providers.types import (
    ApiCancelEvent,
    ApiMessageCompleteEvent,
    ApiMessageRequest,
    ApiStreamEvent,
    ApiTextDeltaEvent,
    ApiThinkingDeltaEvent,
    ApiToolUseDeltaEvent,
    SupportsStreamingMessages,
    UsageSnapshot,
)
from providers.errors import (
    AuthenticationFailure,
    EphemeralOSApiError,
    RateLimitFailure,
    RequestFailure,
)
from providers.provider import (
    ProviderInfo,
    auth_status,
    detect_provider,
    make_api_client,
)

__all__ = [
    # Types & protocol
    "ApiCancelEvent",
    "ApiMessageRequest",
    "ApiTextDeltaEvent",
    "ApiThinkingDeltaEvent",
    "ApiToolUseDeltaEvent",
    "ApiMessageCompleteEvent",
    "ApiStreamEvent",
    "SupportsStreamingMessages",
    "UsageSnapshot",
    # Errors
    "EphemeralOSApiError",
    "AuthenticationFailure",
    "RateLimitFailure",
    "RequestFailure",
    # Provider
    "ProviderInfo",
    "detect_provider",
    "auth_status",
    "make_api_client",
    # Clients
    "AnthropicClient",
    # API
    "create_models_router",
]


def __getattr__(name: str):
    if name == "AnthropicClient":
        from providers.clients import AnthropicClient

        return AnthropicClient
    if name == "create_models_router":
        from providers.api import create_models_router

        return create_models_router
    raise AttributeError(name)
