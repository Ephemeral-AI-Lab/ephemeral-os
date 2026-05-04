"""Unit tests for the provider registry default slot + adapter dict shapes."""

from __future__ import annotations

import threading
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _isolate_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test gets a fresh registry state."""
    from sandbox.providers import registry as reg

    monkeypatch.setattr(reg, "_ADAPTERS", {}, raising=False)
    monkeypatch.setattr(reg, "_DEFAULT", None, raising=False)
    monkeypatch.setattr(reg, "_LOCK", threading.Lock(), raising=False)


# ---------------------------------------------------------------------------
# Default provider slot
# ---------------------------------------------------------------------------


def test_get_default_raises_when_unset() -> None:
    from sandbox.providers.registry import get_default_provider

    with pytest.raises(RuntimeError, match="No default sandbox provider"):
        get_default_provider()


def test_set_default_then_get_returns_same_instance() -> None:
    from sandbox.providers.registry import get_default_provider, set_default_provider

    fake = MagicMock(name="fake_adapter")
    set_default_provider(fake)
    assert get_default_provider() is fake


def test_default_and_id_keyed_registries_are_independent() -> None:
    from sandbox.providers.registry import (
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
    from sandbox.providers.daytona.adapter import _serialize_raw

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
    from sandbox.providers.daytona.adapter import _serialize_raw

    raw = _fake_raw(state="SandboxState.STOPPED")
    result = _serialize_raw(raw)
    assert result["state"] == "stopped"


def test_serialize_raw_falls_back_to_label_project_dir() -> None:
    from sandbox.providers.daytona.adapter import _serialize_raw

    raw = _fake_raw(
        project_dir=None,
        labels={"managed_by": "ephemeralos", "project_dir": "/labels/dir"},
    )
    result = _serialize_raw(raw)
    assert result["project_dir"] == "/labels/dir"


def test_adapter_get_returns_serialized_dict() -> None:
    from sandbox.providers.daytona.adapter import DaytonaProviderAdapter

    raw = _fake_raw(state="stopped")
    with patch(
        "sandbox.providers.daytona.adapter.fetch_sandbox",
        return_value=raw,
    ):
        adapter = DaytonaProviderAdapter()
        info = adapter.get("sb-abc")

    assert info["id"] == "sb-abc"
    assert info["state"] == "stopped"
    assert info["managed_by_app"] is True


def test_adapter_start_calls_raw_start_and_serializes() -> None:
    from sandbox.providers.daytona.adapter import DaytonaProviderAdapter

    raw = MagicMock()
    raw.id = "sb-xyz"
    raw.name = "demo"
    raw.state = "stopped"
    raw.labels = {"managed_by": "ephemeralos"}
    raw.created_at = "2026-01-01T00:00:00Z"
    raw.project_dir = "/workspace/demo"
    raw.refresh_data = MagicMock()

    with patch(
        "sandbox.providers.daytona.adapter.fetch_sandbox",
        return_value=raw,
    ):
        adapter = DaytonaProviderAdapter()
        info = adapter.start("sb-xyz")

    raw.start.assert_called_once()
    raw.refresh_data.assert_called_once()
    assert info["id"] == "sb-xyz"


def test_adapter_delete_calls_raw_delete() -> None:
    from sandbox.providers.daytona.adapter import DaytonaProviderAdapter

    raw = MagicMock()
    raw.id = "sb-del"

    with patch(
        "sandbox.providers.daytona.adapter.fetch_sandbox",
        return_value=raw,
    ):
        adapter = DaytonaProviderAdapter()
        adapter.delete("sb-del")

    raw.delete.assert_called_once()


def test_bootstrap_registers_daytona_as_default() -> None:
    from sandbox.providers.daytona.adapter import DaytonaProviderAdapter
    from sandbox.providers.daytona.bootstrap import bootstrap_daytona_provider
    from sandbox.providers.registry import get_default_provider

    bootstrap_daytona_provider()
    assert isinstance(get_default_provider(), DaytonaProviderAdapter)
