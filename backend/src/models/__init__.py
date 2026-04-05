"""Models module — LLM providers, clients, registration, and management.

Import from here instead of deep paths:

    from models import AnthropicApiClient, ModelStore, detect_provider
"""

from models.types import (
    ApiMessageCompleteEvent,
    ApiMessageRequest,
    ApiStreamEvent,
    ApiTextDeltaEvent,
    SupportsStreamingMessages,
    UsageSnapshot,
)
from models.errors import (
    AuthenticationFailure,
    EphemeralOSApiError,
    RateLimitFailure,
    RequestFailure,
)
from models.provider import (
    ProviderInfo,
    auth_status,
    detect_provider,
)
from models.clients import (
    AnthropicApiClient,
    OpenAICompatibleClient,
)
from models.db import (
    ModelRegistrationRecord,
    ModelStore,
)
from models.api import create_models_router

__all__ = [
    # Types & protocol
    "ApiMessageRequest",
    "ApiTextDeltaEvent",
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
    # Clients
    "AnthropicApiClient",
    "OpenAICompatibleClient",
    # DB
    "ModelRegistrationRecord",
    "ModelStore",
    # API
    "create_models_router",
]
