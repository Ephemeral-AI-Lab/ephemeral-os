"""API client factory."""

from __future__ import annotations

from typing import Any

from providers.types import SupportsStreamingMessages


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

    api_key = db_kwargs.get("api_key")
    base_url = db_kwargs.get("base_url")
    if not api_key:
        from config.model_config import NoActiveModelError

        raise NoActiveModelError("Active model registration has no api_key")

    return AnthropicClient(api_key=api_key, base_url=base_url)
