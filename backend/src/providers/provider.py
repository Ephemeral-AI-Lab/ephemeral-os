"""Provider/auth capability helpers and API client factory."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from providers.types import SupportsStreamingMessages


@dataclass(frozen=True)
class ProviderInfo:
    """Resolved provider metadata for UI and diagnostics."""

    name: str
    auth_kind: str
    voice_supported: bool
    voice_reason: str


def _active_kwargs() -> dict[str, Any]:
    from config.model_config import try_get_active_model_kwargs

    return try_get_active_model_kwargs() or {}


def detect_provider() -> ProviderInfo:
    """Return provider metadata derived from the active model registration's class_path."""
    from config.model_config import try_get_active_model_kwargs  # noqa: F401

    from server.app_factory import model_store

    name = "anthropic"
    try:
        if getattr(model_store, "is_available", False):
            active = model_store.get_active_resolved()
            if active:
                class_path = str(active.get("class_path") or "")
                if class_path:
                    name = class_path.rsplit(".", 1)[-1] or class_path
    except Exception:
        pass
    return ProviderInfo(
        name=name,
        auth_kind="api_key",
        voice_supported=False,
        voice_reason="voice mode is not configured in this build",
    )


def auth_status() -> str:
    """Return a compact auth status string based on the active model registration."""
    kwargs = _active_kwargs()
    return "configured" if kwargs.get("api_key") else "missing"


def make_api_client(
    external: SupportsStreamingMessages | None = None,
    *,
    db_kwargs: dict[str, Any] | None = None,
) -> SupportsStreamingMessages:
    """Build an Anthropic API client from the active model registration.

    When *db_kwargs* is not supplied, resolves them from the DB store.
    Raises :class:`config.model_config.NoActiveModelError` if the active
    model is unavailable and no *external* client is provided.
    """
    if external is not None:
        return external

    from providers.clients.anthropic_native import AnthropicClient

    if db_kwargs is None:
        from config.model_config import get_active_model_kwargs

        db_kwargs = get_active_model_kwargs()

    api_key = db_kwargs.get("api_key") or ""
    base_url = db_kwargs.get("base_url")
    if not api_key:
        from config.model_config import NoActiveModelError

        raise NoActiveModelError("Active model registration has no api_key")

    return AnthropicClient(api_key=api_key, base_url=base_url)
