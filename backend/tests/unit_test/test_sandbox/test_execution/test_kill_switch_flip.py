"""Test that EOS_OVERLAY_FORCE_MATERIALIZE kill switch takes effect per-call."""

from __future__ import annotations

import pytest

import sandbox.overlay.capability as cap_mod


def test_kill_switch_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EOS_OVERLAY_FORCE_MATERIALIZE", raising=False)
    result = cap_mod.new_mount_api_supported()
    assert isinstance(result, bool)


def test_kill_switch_on_forces_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EOS_OVERLAY_FORCE_MATERIALIZE", "1")
    assert cap_mod.new_mount_api_supported() is False


def test_kill_switch_off_allows_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EOS_OVERLAY_FORCE_MATERIALIZE", raising=False)
    # Patch at the capability module boundary (where it's called)
    monkeypatch.setattr(cap_mod, "probe_supported", lambda: True)

    assert cap_mod.new_mount_api_supported() is True


def test_kill_switch_mid_flight(monkeypatch: pytest.MonkeyPatch) -> None:
    """Turning on the kill switch after a probe-True call must immediately force False."""
    monkeypatch.delenv("EOS_OVERLAY_FORCE_MATERIALIZE", raising=False)
    monkeypatch.setattr(cap_mod, "probe_supported", lambda: True)

    assert cap_mod.new_mount_api_supported() is True

    monkeypatch.setenv("EOS_OVERLAY_FORCE_MATERIALIZE", "1")

    assert cap_mod.new_mount_api_supported() is False


def test_kill_switch_value_1_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the exact value '1' triggers the kill switch; 'true' or '0' do not."""
    monkeypatch.setattr(cap_mod, "probe_supported", lambda: True)

    for non_trigger in ("0", "true", "yes", "TRUE", ""):
        monkeypatch.setenv("EOS_OVERLAY_FORCE_MATERIALIZE", non_trigger)
        assert cap_mod.new_mount_api_supported() is True, (
            f"Expected True for value {non_trigger!r}"
        )

    monkeypatch.setenv("EOS_OVERLAY_FORCE_MATERIALIZE", "1")
    assert cap_mod.new_mount_api_supported() is False
