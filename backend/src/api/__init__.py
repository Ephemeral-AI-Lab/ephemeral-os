"""API exports."""

from ephemeralos.api.client import AnthropicApiClient
from ephemeralos.api.errors import EphemeralOSApiError
from ephemeralos.api.openai_client import OpenAICompatibleClient
from ephemeralos.api.provider import ProviderInfo, auth_status, detect_provider
from ephemeralos.api.usage import UsageSnapshot

__all__ = [
    "AnthropicApiClient",
    "OpenAICompatibleClient",
    "EphemeralOSApiError",
    "ProviderInfo",
    "UsageSnapshot",
    "auth_status",
    "detect_provider",
]
