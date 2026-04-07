"""Provider abstraction over LLM backends — clients, types, errors, and HTTP API.

Import from here instead of deep paths:

    from providers import AnthropicClient, detect_provider
"""

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
from providers.clients import AnthropicClient
from providers.api import create_models_router

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
