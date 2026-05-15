"""Tests for shared Daytona client helpers (post-SandboxProxy deletion)."""

from __future__ import annotations

from sandbox.provider.daytona.client import (
    normalize_dict,
    normalize_optional_text,
    timeout_seconds_from_env,
)


class TestNormalizeHelpers:
    def test_normalize_optional_text_strips(self):
        assert normalize_optional_text("  hello  ") == "hello"

    def test_normalize_optional_text_none_returns_none(self):
        assert normalize_optional_text(None) is None

    def test_normalize_optional_text_empty_returns_none(self):
        assert normalize_optional_text("   ") is None

    def test_normalize_dict(self):
        assert normalize_dict({"  key  ": "  value  "}) == {"key": "value"}

    def test_normalize_dict_skips_empty_keys(self):
        assert normalize_dict({"  ": "value"}) == {}

    def test_normalize_dict_none_returns_empty(self):
        assert normalize_dict(None) == {}


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
