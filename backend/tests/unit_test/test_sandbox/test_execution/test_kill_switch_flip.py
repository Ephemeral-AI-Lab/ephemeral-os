"""Tests for the namespace-only overlay startup precondition."""

from __future__ import annotations

import pytest

import sandbox.overlay.capability as cap_mod


def test_new_mount_api_probe_is_authoritative(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cap_mod, "probe_supported", lambda: True)
    assert cap_mod.new_mount_api_supported() is True


def test_require_new_mount_api_raises_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cap_mod, "probe_supported", lambda: False)
    with pytest.raises(RuntimeError, match="new mount API is unavailable"):
        cap_mod.require_new_mount_api()
