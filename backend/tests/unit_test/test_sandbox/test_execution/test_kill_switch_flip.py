"""Tests for the namespace-only overlay startup precondition."""

from __future__ import annotations

import pytest

import sandbox.overlay.capability as cap_mod


def test_new_mount_api_probe_is_authoritative(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cap_mod, "probe_supported", lambda: True)
    assert cap_mod.new_mount_api_supported() is True


def test_new_mount_api_required_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EOS_REQUIRE_NEW_MOUNT_API", raising=False)
    assert cap_mod.new_mount_api_required() is True


def test_require_new_mount_api_raises_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("EOS_REQUIRE_NEW_MOUNT_API", raising=False)
    monkeypatch.setattr(cap_mod, "probe_supported", lambda: False)
    with pytest.raises(RuntimeError, match="new mount API is unavailable"):
        cap_mod.require_new_mount_api()


def test_rollout_flag_allows_degraded_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EOS_REQUIRE_NEW_MOUNT_API", "0")
    monkeypatch.setattr(cap_mod, "probe_supported", lambda: False)
    cap_mod.require_new_mount_api()
