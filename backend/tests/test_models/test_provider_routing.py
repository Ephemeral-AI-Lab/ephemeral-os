"""Unit tests for Settings API key resolution and provider client routing."""

from __future__ import annotations

import pytest

from config.settings import Settings, _apply_env_overrides
from providers.provider import make_api_client
from providers.clients.anthropic_native import AnthropicClient


# ---------------------------------------------------------------------------
# Settings.resolve_api_key tests
# ---------------------------------------------------------------------------


def test_resolve_api_key_from_instance(monkeypatch):
    """Instance api_key is returned immediately without consulting env vars."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    settings = Settings(api_key="sk-test")
    assert settings.resolve_api_key() == "sk-test"


def test_resolve_api_key_from_openai_env(monkeypatch):
    """OPENAI_API_KEY env var is used when no instance key is set."""
    monkeypatch.setenv("OPENAI_API_KEY", "openai-env-key")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    settings = Settings(api_key="")
    assert settings.resolve_api_key() == "openai-env-key"


def test_resolve_api_key_from_anthropic_env(monkeypatch):
    """ANTHROPIC_API_KEY env var is used when OPENAI_API_KEY is absent."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-env-key")
    settings = Settings(api_key="")
    assert settings.resolve_api_key() == "anthropic-env-key"


def test_resolve_api_key_openai_takes_precedence(monkeypatch):
    """OPENAI_API_KEY wins when both env vars are set."""
    monkeypatch.setenv("OPENAI_API_KEY", "openai-wins")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-loses")
    settings = Settings(api_key="")
    assert settings.resolve_api_key() == "openai-wins"


def test_resolve_api_key_raises_when_none(monkeypatch):
    """ValueError is raised when no key is available from any source."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    settings = Settings(api_key="")
    with pytest.raises(ValueError, match="No API key found"):
        settings.resolve_api_key()


def test_apply_env_overrides_anthropic_key(monkeypatch):
    """_apply_env_overrides picks up ANTHROPIC_API_KEY when OPENAI_API_KEY is absent."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-override")
    settings = _apply_env_overrides(Settings(api_key=""))
    assert settings.api_key == "anthropic-override"


# ---------------------------------------------------------------------------
# make_api_client routing tests
# ---------------------------------------------------------------------------


def test_make_api_client_returns_anthropic(monkeypatch):
    """make_api_client always returns an AnthropicClient."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-anthropic")
    settings = Settings()
    client = make_api_client(settings)
    assert isinstance(client, AnthropicClient)


def test_make_api_client_external_passthrough(monkeypatch):
    """An externally supplied client is returned unchanged regardless of settings."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-anthropic")
    settings = Settings()
    external = AnthropicClient(api_key="sk-external")
    result = make_api_client(settings, external=external)
    assert result is external


def test_make_api_client_db_kwargs_override(monkeypatch):
    """db_kwargs api_key/base_url override settings."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    settings = Settings()
    db_kwargs = {"api_key": "sk-db-anthropic", "base_url": "https://example.test"}
    client = make_api_client(settings, db_kwargs=db_kwargs)
    assert isinstance(client, AnthropicClient)
