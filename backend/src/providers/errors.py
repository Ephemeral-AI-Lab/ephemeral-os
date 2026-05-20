"""API error types for EphemeralOS."""

from __future__ import annotations


class EphemeralOSApiError(RuntimeError):
    """Base class for upstream API failures.

    Carries an optional ``status_code`` so error categorisation can branch on
    the typed exception alone (plan §A17). ``None`` means the failure was not
    HTTP-shaped (transport error, JSON decode, etc.).
    """

    def __init__(
        self,
        message: str = "",
        *,
        status_code: int | None = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.request_id = request_id


class AuthenticationFailure(EphemeralOSApiError):
    """Raised when the upstream service rejects the provided credentials."""


class RateLimitFailure(EphemeralOSApiError):
    """Raised when the upstream service rejects the request due to rate limits."""


class RequestFailure(EphemeralOSApiError):
    """Raised for generic request or transport failures."""
