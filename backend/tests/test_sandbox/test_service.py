"""Tests for sandbox.service — SandboxProxy and helpers."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock


class TestSandboxProxy:
    def test_id_returns_raw_id(self):
        from sandbox.service import SandboxProxy

        raw = MagicMock()
        raw.configure_mock(id="sb-abc123")
        proxy = SandboxProxy(raw)

        assert proxy.id == "sb-abc123"

    def test_name(self):
        from sandbox.service import SandboxProxy

        raw = MagicMock()
        raw.configure_mock(name="my-sandbox")
        proxy = SandboxProxy(raw)

        assert proxy.name == "my-sandbox"

    def test_created_at(self):
        from sandbox.service import SandboxProxy

        raw = MagicMock()
        raw.configure_mock(created_at="2025-01-01T00:00:00Z")
        proxy = SandboxProxy(raw)

        assert proxy.created_at == "2025-01-01T00:00:00Z"

    def test_labels_dict(self):
        from sandbox.service import SandboxProxy

        raw = MagicMock()
        raw.configure_mock(labels={"key": "value"})
        proxy = SandboxProxy(raw)

        assert proxy.labels == {"key": "value"}

    def test_labels_falls_back_to_empty_dict(self):
        from sandbox.service import SandboxProxy

        raw = MagicMock(spec=[])
        raw.configure_mock(labels=None)
        proxy = SandboxProxy(raw)

        assert proxy.labels == {}

    def test_state_unknown_when_none(self):
        from sandbox.service import SandboxProxy

        raw = MagicMock()
        raw.configure_mock(state=None)
        proxy = SandboxProxy(raw)

        assert proxy.state == "unknown"

    def test_state_strips_sandboxstate_prefix(self):
        from sandbox.service import SandboxProxy

        class MockState:
            value = "sandboxstate.started"

        raw = MagicMock()
        raw.configure_mock(state=MockState())
        proxy = SandboxProxy(raw)

        assert proxy.state == "started"

    def test_state_uses_raw_string(self):
        from sandbox.service import SandboxProxy

        raw = MagicMock()
        raw.configure_mock(state="stopped")
        proxy = SandboxProxy(raw)

        assert proxy.state == "stopped"

    def test_image_from_snapshot_label(self):
        from sandbox.service import SandboxProxy

        raw = MagicMock()
        raw.configure_mock(
            labels={"ephemeralos_snapshot": "my-snapshot"},
            image=None,
            image_name=None,
            snapshot=None,
        )
        proxy = SandboxProxy(raw)

        assert proxy.image == "my-snapshot"

    def test_image_from_image_label(self):
        from sandbox.service import SandboxProxy

        raw = MagicMock()
        raw.configure_mock(
            labels={"ephemeralos_image": "my-image"},
            image=None,
            image_name=None,
            snapshot=None,
        )
        proxy = SandboxProxy(raw)

        assert proxy.image == "my-image"

    def test_managed_by_app_true(self):
        from sandbox.service import SandboxProxy

        raw = MagicMock()
        raw.configure_mock(labels={"managed_by": "ephemeralos"})
        proxy = SandboxProxy(raw)

        assert proxy.managed_by_app is True

    def test_managed_by_app_false(self):
        from sandbox.service import SandboxProxy

        raw = MagicMock()
        raw.configure_mock(labels={"managed_by": "other"})
        proxy = SandboxProxy(raw)

        assert proxy.managed_by_app is False

    def test_refresh_calls_refresh_data(self):
        from sandbox.service import SandboxProxy

        raw = MagicMock()
        refresh_mock = MagicMock()
        raw.configure_mock(refresh_data=refresh_mock)
        proxy = SandboxProxy(raw)

        proxy.refresh()

        refresh_mock.assert_called_once()

    def test_refresh_skips_when_missing(self):
        from sandbox.service import SandboxProxy

        raw = MagicMock(spec=[])
        proxy = SandboxProxy(raw)

        proxy.refresh()

    def test_serialize(self):
        from sandbox.service import SandboxProxy

        raw = MagicMock()
        raw.configure_mock(
            id="sb-123",
            name="test-name",
            created_at="2025-01-01",
            labels={"managed_by": "ephemeralos"},
            state="started",
            image=None,
            image_name=None,
            snapshot=None,
        )
        proxy = SandboxProxy(raw)

        result = proxy.serialize(assigned_agents=["agent-1"])

        assert result["id"] == "sb-123"
        assert result["name"] == "test-name"
        assert result["state"] == "started"
        assert result["assigned_agents"] == ["agent-1"]
        assert result["managed_by_app"] is True

    def test_ensure_git_skips_when_git_present(self):
        from sandbox.service import SandboxProxy

        raw = MagicMock()
        resp = MagicMock()
        resp.configure_mock(result="ok")
        exec_mock = MagicMock(return_value=resp)
        raw.configure_mock(process=MagicMock(exec=exec_mock))
        proxy = SandboxProxy(raw)

        proxy.ensure_git()

        assert exec_mock.call_count == 1

    def test_ensure_git_installs_when_missing(self):
        from sandbox.service import SandboxProxy

        resp_missing = MagicMock()
        resp_missing.configure_mock(result="missing")
        resp_install = MagicMock()
        exec_mock = MagicMock(side_effect=[resp_missing, resp_install])
        raw = MagicMock()
        raw.configure_mock(process=MagicMock(exec=exec_mock))
        proxy = SandboxProxy(raw)

        proxy.ensure_git()

        assert exec_mock.call_count == 2


class TestNormalizeHelpers:
    def test_normalize_optional_text_strips(self):
        from sandbox.service import _normalize_optional_text

        assert _normalize_optional_text("  hello  ") == "hello"

    def test_normalize_optional_text_none_returns_none(self):
        from sandbox.service import _normalize_optional_text

        assert _normalize_optional_text(None) is None

    def test_normalize_optional_text_empty_returns_none(self):
        from sandbox.service import _normalize_optional_text

        assert _normalize_optional_text("   ") is None

    def test_normalize_dict(self):
        from sandbox.service import _normalize_dict

        result = _normalize_dict({"  key  ": "  value  "})
        assert result == {"key": "value"}

    def test_normalize_dict_skips_empty_keys(self):
        from sandbox.service import _normalize_dict

        result = _normalize_dict({"  ": "value"})
        assert result == {}

    def test_normalize_dict_none_returns_empty(self):
        from sandbox.service import _normalize_dict

        assert _normalize_dict(None) == {}
