"""Tests for sandbox.service — SandboxProxy and helpers."""

from __future__ import annotations

from unittest.mock import MagicMock

from sandbox.service import SandboxProxy, _normalize_dict, _normalize_optional_text


def _make_proxy(**attrs) -> SandboxProxy:
    """Create a SandboxProxy backed by a MagicMock configured with *attrs*."""
    raw = MagicMock()
    raw.configure_mock(**attrs)
    return SandboxProxy(raw)


class TestSandboxProxy:
    def test_id_returns_raw_id(self):
        assert _make_proxy(id="sb-abc123").id == "sb-abc123"

    def test_name(self):
        assert _make_proxy(name="my-sandbox").name == "my-sandbox"

    def test_created_at(self):
        assert _make_proxy(created_at="2025-01-01T00:00:00Z").created_at == "2025-01-01T00:00:00Z"

    def test_labels_dict(self):
        assert _make_proxy(labels={"key": "value"}).labels == {"key": "value"}

    def test_labels_falls_back_to_empty_dict(self):
        raw = MagicMock(spec=[])
        raw.configure_mock(labels=None)
        assert SandboxProxy(raw).labels == {}

    def test_state_unknown_when_none(self):
        assert _make_proxy(state=None).state == "unknown"

    def test_state_strips_sandboxstate_prefix(self):
        class MockState:
            value = "sandboxstate.started"

        assert _make_proxy(state=MockState()).state == "started"

    def test_state_uses_raw_string(self):
        assert _make_proxy(state="stopped").state == "stopped"

    def test_image_from_snapshot_label(self):
        proxy = _make_proxy(
            labels={"ephemeralos_snapshot": "my-snapshot"},
            image=None, image_name=None, snapshot=None,
        )
        assert proxy.image == "my-snapshot"

    def test_image_from_image_label(self):
        proxy = _make_proxy(
            labels={"ephemeralos_image": "my-image"},
            image=None, image_name=None, snapshot=None,
        )
        assert proxy.image == "my-image"

    def test_managed_by_app_true(self):
        assert _make_proxy(labels={"managed_by": "ephemeralos"}).managed_by_app is True

    def test_managed_by_app_false(self):
        assert _make_proxy(labels={"managed_by": "other"}).managed_by_app is False

    def test_refresh_calls_refresh_data(self):
        refresh_mock = MagicMock()
        proxy = _make_proxy(refresh_data=refresh_mock)
        proxy.refresh()
        refresh_mock.assert_called_once()

    def test_refresh_skips_when_missing(self):
        raw = MagicMock(spec=[])
        SandboxProxy(raw).refresh()  # must not raise

    def test_serialize(self):
        proxy = _make_proxy(
            id="sb-123", name="test-name", created_at="2025-01-01",
            labels={"managed_by": "ephemeralos"}, state="started",
            image=None, image_name=None, snapshot=None,
        )
        result = proxy.serialize(assigned_agents=["agent-1"])
        assert result["id"] == "sb-123"
        assert result["name"] == "test-name"
        assert result["state"] == "started"
        assert result["assigned_agents"] == ["agent-1"]
        assert result["managed_by_app"] is True

    def test_ensure_git_skips_when_git_present(self):
        resp = MagicMock()
        resp.configure_mock(result="ok")
        exec_mock = MagicMock(return_value=resp)
        proxy = _make_proxy(process=MagicMock(exec=exec_mock))
        proxy.ensure_git()
        assert exec_mock.call_count == 1

    def test_ensure_git_installs_when_missing(self):
        resp_missing = MagicMock()
        resp_missing.configure_mock(result="missing")
        exec_mock = MagicMock(side_effect=[resp_missing, MagicMock()])
        proxy = _make_proxy(process=MagicMock(exec=exec_mock))
        proxy.ensure_git()
        assert exec_mock.call_count == 2


class TestNormalizeHelpers:
    def test_normalize_optional_text_strips(self):
        assert _normalize_optional_text("  hello  ") == "hello"

    def test_normalize_optional_text_none_returns_none(self):
        assert _normalize_optional_text(None) is None

    def test_normalize_optional_text_empty_returns_none(self):
        assert _normalize_optional_text("   ") is None

    def test_normalize_dict(self):
        assert _normalize_dict({"  key  ": "  value  "}) == {"key": "value"}

    def test_normalize_dict_skips_empty_keys(self):
        assert _normalize_dict({"  ": "value"}) == {}

    def test_normalize_dict_none_returns_empty(self):
        assert _normalize_dict(None) == {}
