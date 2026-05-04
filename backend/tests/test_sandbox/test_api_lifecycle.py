"""Unit tests for the public facade :mod:`sandbox.api.lifecycle`.

The facade is dormant in S4 — no callers have migrated yet — but its routing
through the registry's default + per-id slots is exercised here so the seam
is locked before S5 flips the call sites.
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _isolate_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    from sandbox.providers import registry as reg

    monkeypatch.setattr(reg, "_ADAPTERS", {}, raising=False)
    monkeypatch.setattr(reg, "_DEFAULT", None, raising=False)
    monkeypatch.setattr(reg, "_LOCK", threading.Lock(), raising=False)


def _stub_provider() -> MagicMock:
    provider = MagicMock(name="provider")
    provider.create.return_value = {
        "id": "sb-1",
        "name": "demo",
        "state": "started",
        "project_dir": "/workspace/demo",
    }
    provider.start.return_value = {
        "id": "sb-1",
        "state": "started",
        "project_dir": "/workspace/demo",
    }
    provider.stop.return_value = {"id": "sb-1", "state": "stopped"}
    provider.get.return_value = {"id": "sb-1", "state": "started"}
    provider.list.return_value = [{"id": "sb-1"}]
    provider.get_health.return_value = {"available": True}
    provider.list_snapshots.return_value = [{"name": "snap"}]
    provider.get_signed_preview_url.return_value = {"url": "https://"}
    provider.get_build_logs_url.return_value = "https://logs"
    return provider


def test_create_registers_per_id_adapter_and_runs_setup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sandbox.api import lifecycle as sb_lifecycle
    from sandbox.providers.registry import get_adapter, set_default_provider

    provider = _stub_provider()
    set_default_provider(provider)

    setup_calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        sb_lifecycle,
        "setup_after_create",
        lambda sid, ws: setup_calls.append((sid, ws)),
    )

    info = sb_lifecycle.create_sandbox(name="demo")

    provider.create.assert_called_once()
    assert info["id"] == "sb-1"
    assert get_adapter("sb-1") is provider
    assert setup_calls == [("sb-1", "/workspace/demo")]


def test_start_runs_setup_after_start(monkeypatch: pytest.MonkeyPatch) -> None:
    from sandbox.api import lifecycle as sb_lifecycle
    from sandbox.providers.registry import register_adapter

    provider = _stub_provider()
    register_adapter("sb-1", provider)

    setup_calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        sb_lifecycle,
        "setup_after_start",
        lambda sid, ws: setup_calls.append((sid, ws)),
    )

    info = sb_lifecycle.start_sandbox("sb-1")

    provider.start.assert_called_once_with("sb-1")
    assert info["state"] == "started"
    assert setup_calls == [("sb-1", "/workspace/demo")]


def test_delete_disposes_adapter() -> None:
    from sandbox.api import lifecycle as sb_lifecycle
    from sandbox.providers.registry import get_adapter, register_adapter

    provider = _stub_provider()
    register_adapter("sb-1", provider)

    sb_lifecycle.delete_sandbox("sb-1")

    provider.delete.assert_called_once_with("sb-1")
    with pytest.raises(KeyError):
        get_adapter("sb-1")


def test_read_helpers_route_through_registry() -> None:
    from sandbox.api import lifecycle as sb_lifecycle
    from sandbox.providers.registry import register_adapter, set_default_provider

    default = _stub_provider()
    set_default_provider(default)

    assert sb_lifecycle.list_sandboxes() == [{"id": "sb-1"}]
    assert sb_lifecycle.get_health() == {"available": True}
    assert sb_lifecycle.list_snapshots() == [{"name": "snap"}]

    per_id = _stub_provider()
    register_adapter("sb-1", per_id)
    assert sb_lifecycle.get_sandbox("sb-1")["id"] == "sb-1"
    per_id.get.assert_called_once_with("sb-1")
    assert sb_lifecycle.get_signed_preview_url("sb-1", 3000) == {"url": "https://"}
    assert sb_lifecycle.get_build_logs_url("sb-1") == "https://logs"


def test_facade_module_accessible_via_package_import() -> None:
    """`from sandbox.api import lifecycle as sb_lifecycle` resolves."""
    from sandbox.api import lifecycle as sb_lifecycle

    assert callable(sb_lifecycle.create_sandbox)
    assert callable(sb_lifecycle.ensure_sandbox_running)
