"""Models module — LLM providers, clients, registration, and management.

Import from here instead of deep paths:

    from models import OpenAICompatibleClient, ModelStore, detect_provider
"""

from models.core import (
    ApiCancelEvent,
    ApiMessageCompleteEvent,
    ApiMessageRequest,
    ApiStreamEvent,
    ApiTextDeltaEvent,
    ApiThinkingDeltaEvent,
    ApiToolUseDeltaEvent,
    SupportsStreamingMessages,
    UsageSnapshot,
    AuthenticationFailure,
    EphemeralOSApiError,
    RateLimitFailure,
    RequestFailure,
    ProviderInfo,
    auth_status,
    detect_provider,
    make_api_client,
)
from models.clients import (
    OpenAICompatibleClient,
)
from models.db import (
    ModelRegistrationRecord,
    ModelStore,
)
from models.api import create_models_router

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
    "OpenAICompatibleClient",
    # DB
    "ModelRegistrationRecord",
    "ModelStore",
    # API
    "create_models_router",
]
