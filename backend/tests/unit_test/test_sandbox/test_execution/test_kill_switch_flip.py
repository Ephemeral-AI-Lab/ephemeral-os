"""Tests for the namespace-only overlay startup precondition."""

from __future__ import annotations

import pytest

import sandbox.overlay.mount_syscalls as cap_mod


def test_mount_syscalls_probe_is_authoritative(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cap_mod, "probe_supported", lambda: True)
    assert cap_mod.mount_syscalls_supported() is True


def test_require_mount_syscalls_raises_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cap_mod, "probe_supported", lambda: False)
    with pytest.raises(RuntimeError, match="mount syscalls are unavailable"):
        cap_mod.require_mount_syscalls()
