"""Unit tests for the provider registry default slot + adapter dict shapes."""

from __future__ import annotations

import threading
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _isolate_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test gets a fresh registry state."""
    from sandbox.provider import registry as reg

    monkeypatch.setattr(reg, "_ADAPTERS", {}, raising=False)
    monkeypatch.setattr(reg, "_DEFAULT", None, raising=False)
    monkeypatch.setattr(reg, "_LOCK", threading.Lock(), raising=False)


# ---------------------------------------------------------------------------
# Default provider slot
# ---------------------------------------------------------------------------


def test_get_default_raises_when_unset() -> None:
    from sandbox.provider.registry import get_default_provider

    with pytest.raises(RuntimeError, match="No default sandbox provider"):
        get_default_provider()


def test_set_default_then_get_returns_same_instance() -> None:
    from sandbox.provider.registry import get_default_provider, set_default_provider

    fake = MagicMock(name="fake_adapter")
    set_default_provider(fake)
    assert get_default_provider() is fake


def test_default_and_id_keyed_registries_are_independent() -> None:
    from sandbox.provider.registry import (
        get_adapter,
        get_default_provider,
        register_adapter,
        set_default_provider,
    )

    default = MagicMock(name="default_adapter")
    per_id = MagicMock(name="per_id_adapter")
    set_default_provider(default)
    register_adapter("sb-1", per_id)

    assert get_default_provider() is default
    assert get_adapter("sb-1") is per_id


def test_get_adapter_falls_back_to_default_provider_without_caching() -> None:
    """WR-01: fallback to default must NOT cache the binding. has_registered_adapter
    must stay False for ids that only ever resolved via fallback, so callers
    can distinguish "explicit register" from "fallback cached"."""
    from sandbox.provider.registry import (
        get_adapter,
        has_registered_adapter,
        set_default_provider,
    )

    default = MagicMock(name="default_adapter")
    set_default_provider(default)

    assert has_registered_adapter("sb-existing") is False
    assert get_adapter("sb-existing") is default
    # WR-01: fallback does NOT flip has_registered_adapter — the id was
    # never explicitly registered.
    assert has_registered_adapter("sb-existing") is False
    # Repeat lookup still resolves to default (no cache pollution).
    assert get_adapter("sb-existing") is default
    assert has_registered_adapter("sb-existing") is False


# ---------------------------------------------------------------------------
# DaytonaProviderAdapter dict shape
# ---------------------------------------------------------------------------


def _fake_raw(**overrides: Any) -> Any:
    base = {
        "id": "sb-abc",
        "name": "demo",
        "state": "started",
        "labels": {"managed_by": "ephemeralos", "ephemeralos_image": "img:1"},
        "created_at": "2026-01-01T00:00:00Z",
        "project_dir": "/workspace/demo",
    }
    base.update(overrides)
    return type("RawSandbox", (), base)()


def test_serialize_raw_returns_canonical_dict() -> None:
    from sandbox.provider.daytona.adapter import _serialize_raw

    raw = _fake_raw()
    result = _serialize_raw(raw, assigned_agents=["agent-1"])

    assert result["id"] == "sb-abc"
    assert result["name"] == "demo"
    assert result["state"] == "started"
    assert result["image"] == "img:1"
    assert result["managed_by_app"] is True
    assert result["project_dir"] == "/workspace/demo"
    assert result["assigned_agents"] == ["agent-1"]


def test_serialize_raw_strips_sandboxstate_prefix() -> None:
    from sandbox.provider.daytona.adapter import _serialize_raw

    raw = _fake_raw(state="SandboxState.STOPPED")
    result = _serialize_raw(raw)
    assert result["state"] == "stopped"


def test_serialize_raw_falls_back_to_label_project_dir() -> None:
    from sandbox.provider.daytona.adapter import _serialize_raw

    raw = _fake_raw(
        project_dir=None,
        labels={"managed_by": "ephemeralos", "project_dir": "/labels/dir"},
    )
    result = _serialize_raw(raw)
    assert result["project_dir"] == "/labels/dir"


def test_adapter_get_returns_serialized_dict() -> None:
    from sandbox.provider.daytona.adapter import DaytonaProviderAdapter

    raw = _fake_raw(state="stopped")
    with patch(
        "sandbox.provider.daytona.adapter.fetch_sandbox",
        return_value=raw,
    ):
        adapter = DaytonaProviderAdapter()
        info = adapter.get("sb-abc")

    assert info["id"] == "sb-abc"
    assert info["state"] == "stopped"
    assert info["managed_by_app"] is True


def test_adapter_start_calls_raw_start_and_serializes() -> None:
    from sandbox.provider.daytona.adapter import DaytonaProviderAdapter

    raw = MagicMock()
    raw.id = "sb-xyz"
    raw.name = "demo"
    raw.state = "stopped"
    raw.labels = {"managed_by": "ephemeralos"}
    raw.created_at = "2026-01-01T00:00:00Z"
    raw.project_dir = "/workspace/demo"
    raw.refresh_data = MagicMock()

    with patch(
        "sandbox.provider.daytona.adapter.fetch_sandbox",
        return_value=raw,
    ):
        adapter = DaytonaProviderAdapter()
        info = adapter.start("sb-xyz")

    raw.start.assert_called_once()
    raw.refresh_data.assert_called_once()
    assert info["id"] == "sb-xyz"


def test_adapter_delete_calls_raw_delete() -> None:
    from sandbox.provider.daytona.adapter import DaytonaProviderAdapter

    raw = MagicMock()
    raw.id = "sb-del"

    with patch(
        "sandbox.provider.daytona.adapter.fetch_sandbox",
        return_value=raw,
    ):
        adapter = DaytonaProviderAdapter()
        adapter.delete("sb-del")

    raw.delete.assert_called_once()


def test_adapter_set_labels_calls_raw_set_labels_and_serializes() -> None:
    from sandbox.provider.daytona.adapter import DaytonaProviderAdapter

    raw = _fake_raw(labels={"managed_by": "ephemeralos"})
    raw.set_labels = MagicMock()
    raw.refresh_data = MagicMock()

    with patch(
        "sandbox.provider.daytona.adapter.fetch_sandbox",
        return_value=raw,
    ):
        adapter = DaytonaProviderAdapter()
        info = adapter.set_labels("sb-abc", {" project_dir ": " /testbed "})

    raw.set_labels.assert_called_once_with({"project_dir": "/testbed"})
    raw.refresh_data.assert_called_once()
    assert info["id"] == "sb-abc"


def test_adapter_build_logs_url_uses_daytona_private_api() -> None:
    from sandbox.provider.daytona.adapter import DaytonaProviderAdapter

    raw = MagicMock()
    raw._sandbox_api.get_build_logs_url.return_value = MagicMock(
        url="https://logs.example/build"
    )

    with patch(
        "sandbox.provider.daytona.adapter.fetch_sandbox",
        return_value=raw,
    ):
        adapter = DaytonaProviderAdapter()
        url = adapter.get_build_logs_url("sb-abc")

    assert url == "https://logs.example/build"
    raw._sandbox_api.get_build_logs_url.assert_called_once_with("sb-abc")


def test_bootstrap_registers_daytona_as_default() -> None:
    from sandbox.provider.daytona.adapter import DaytonaProviderAdapter
    from sandbox.provider.daytona.bootstrap import bootstrap_daytona_provider
    from sandbox.provider.registry import get_default_provider

    bootstrap_daytona_provider()
    assert isinstance(get_default_provider(), DaytonaProviderAdapter)
