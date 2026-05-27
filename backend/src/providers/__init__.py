"""Provider exports."""

from __future__ import annotations

from providers.types import (
    MessageRequest,
    SupportsStreamingMessages,
    UsageSnapshot,
)
from providers.errors import (
    AuthenticationFailure,
    EphemeralOSApiError,
    RateLimitFailure,
    RequestFailure,
)
from providers.provider import make_api_client

__all__ = [
    # Types & protocol
    "MessageRequest",
    "SupportsStreamingMessages",
    "UsageSnapshot",
    # Errors
    "EphemeralOSApiError",
    "AuthenticationFailure",
    "RateLimitFailure",
    "RequestFailure",
    # Provider factory
    "make_api_client",
]
