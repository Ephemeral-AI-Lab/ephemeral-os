"""Provider/auth capability helpers and API client factory."""

from __future__ import annotations

from dataclasses import dataclass

from ephemeralos.config.settings import Settings
from ephemeralos.models.types import SupportsStreamingMessages


@dataclass(frozen=True)
class ProviderInfo:
    """Resolved provider metadata for UI and diagnostics."""

    name: str
    auth_kind: str
    voice_supported: bool
    voice_reason: str


def detect_provider(settings: Settings) -> ProviderInfo:
    """Infer the active provider and rough capability set."""
    base_url = (settings.base_url or "").lower()
    model = settings.model.lower()
    if "moonshot" in base_url or model.startswith("kimi"):
        return ProviderInfo(
            name="moonshot-anthropic-compatible",
            auth_kind="api_key",
            voice_supported=False,
            voice_reason="voice mode requires a Claude.ai-style authenticated voice backend",
        )
    if "dashscope" in base_url or model.startswith("qwen"):
        return ProviderInfo(
            name="dashscope-openai-compatible",
            auth_kind="api_key",
            voice_supported=False,
            voice_reason="voice mode is not supported for DashScope providers",
        )
    if "models.inference.ai.azure.com" in base_url or "github" in base_url:
        return ProviderInfo(
            name="github-models-openai-compatible",
            auth_kind="api_key",
            voice_supported=False,
            voice_reason="voice mode is not supported for GitHub Models",
        )
    if "bedrock" in base_url:
        return ProviderInfo(
            name="bedrock-compatible",
            auth_kind="aws",
            voice_supported=False,
            voice_reason="voice mode is not wired for Bedrock in this build",
        )
    if "vertex" in base_url or "aiplatform" in base_url:
        return ProviderInfo(
            name="vertex-compatible",
            auth_kind="gcp",
            voice_supported=False,
            voice_reason="voice mode is not wired for Vertex in this build",
        )
    if base_url:
        return ProviderInfo(
            name="anthropic-compatible",
            auth_kind="api_key",
            voice_supported=False,
            voice_reason="voice mode currently requires a dedicated Claude.ai-style provider",
        )
    return ProviderInfo(
        name="anthropic",
        auth_kind="api_key",
        voice_supported=False,
        voice_reason="voice mode shell exists, but live voice auth/streaming is not configured in this build",
    )


def auth_status(settings: Settings) -> str:
    """Return a compact auth status string."""
    if settings.api_key:
        return "configured"
    return "missing"


def make_api_client(
    settings: Settings,
    external: SupportsStreamingMessages | None = None,
    *,
    db_kwargs: dict | None = None,
    db_class_path: str | None = None,
) -> SupportsStreamingMessages:
    """Build an API client from settings, or return the external one.

    When *db_kwargs* / *db_class_path* are provided (from the active model
    registration in the DB) they supply ``api_key``, ``base_url``, and the
    provider type — falling back to ``settings`` only when a value is absent.
    """
    if external is not None:
        return external

    from ephemeralos.models.clients.anthropic import AnthropicApiClient
    from ephemeralos.models.clients.openai_compat import OpenAICompatibleClient

    # Resolve from DB-registered model first, then settings
    api_key = (db_kwargs or {}).get("api_key") or settings.resolve_api_key()
    base_url = (db_kwargs or {}).get("base_url") or settings.base_url

    # Determine provider: DB class_path > settings.api_format
    is_openai = (
        (db_class_path or "").lower() in ("openai", "openai_compat")
        or settings.api_format == "openai"
    )

    if is_openai:
        return OpenAICompatibleClient(api_key=api_key, base_url=base_url)
    return AnthropicApiClient(api_key=api_key, base_url=base_url)
