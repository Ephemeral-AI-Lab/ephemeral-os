"""Provider routing tests — now against the DB-based active model resolver."""

from __future__ import annotations

import pytest

from providers.provider import make_api_client
from providers.clients.anthropic_native import AnthropicClient
from config.model_config import NoActiveModelError


def test_make_api_client_external_passthrough():
    """An externally supplied client is returned unchanged."""
    external = AnthropicClient(api_key="sk-external")
    result = make_api_client(external=external)
    assert result is external


def test_make_api_client_db_kwargs_override():
    """Explicit db_kwargs override DB lookup and build an AnthropicClient."""
    db_kwargs = {"api_key": "sk-db-anthropic", "base_url": "https://example.test"}
    client = make_api_client(db_kwargs=db_kwargs)
    assert isinstance(client, AnthropicClient)


def test_make_api_client_missing_api_key_raises():
    """Empty api_key in db_kwargs should raise NoActiveModelError."""
    with pytest.raises(NoActiveModelError):
        make_api_client(db_kwargs={"api_key": "", "base_url": None})
