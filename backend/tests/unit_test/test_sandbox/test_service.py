"""Tests for provider normalization helpers (post-SandboxProxy deletion)."""

from __future__ import annotations

from sandbox.provider.daytona.adapter import (
    _normalize_optional_text as normalize_optional_text,
)
from sandbox.provider.daytona.client import timeout_seconds_from_env
from sandbox.provider._payloads import normalize_string_dict


class TestNormalizeHelpers:
    def test_normalize_optional_text_strips(self):
        assert normalize_optional_text("  hello  ") == "hello"

    def test_normalize_optional_text_none_returns_none(self):
        assert normalize_optional_text(None) is None

    def test_normalize_optional_text_empty_returns_none(self):
        assert normalize_optional_text("   ") is None

    def test_normalize_string_dict(self):
        assert normalize_string_dict({"  key  ": "  value  "}) == {"key": "value"}

    def test_normalize_string_dict_skips_empty_keys(self):
        assert normalize_string_dict({"  ": "value"}) == {}

    def test_normalize_string_dict_none_returns_empty(self):
        assert normalize_string_dict(None) == {}


class TestTimeoutConfig:
    def test_timeout_defaults_to_long_cold_start_window(self, monkeypatch):
        monkeypatch.delenv("EPHEMERALOS_SANDBOX_TIMEOUT_SECONDS", raising=False)

        assert timeout_seconds_from_env() == 300.0

    def test_timeout_reads_env_override(self, monkeypatch):
        monkeypatch.setenv("EPHEMERALOS_SANDBOX_TIMEOUT_SECONDS", "420")

        assert timeout_seconds_from_env() == 420.0

    def test_timeout_invalid_env_uses_default(self, monkeypatch):
        monkeypatch.setenv("EPHEMERALOS_SANDBOX_TIMEOUT_SECONDS", "not-a-number")

        assert timeout_seconds_from_env() == 300.0
