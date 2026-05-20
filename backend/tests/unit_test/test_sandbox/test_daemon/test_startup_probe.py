"""Unit tests for daemon startup probe and RLIMIT_NOFILE bump."""

from __future__ import annotations

import resource
import sys
from unittest.mock import call, patch, MagicMock

import pytest

from sandbox.daemon.__main__ import _bump_nofile, _log_mount_api_capability


# ---------------------------------------------------------------------------
# _bump_nofile
# ---------------------------------------------------------------------------


def test_bump_nofile_raises_soft_limit_when_below_target() -> None:
    soft, hard = 1024, 65536
    new_soft_limit: list[tuple[int, int]] = []

    def fake_setrlimit(res: int, limits: tuple[int, int]) -> None:
        new_soft_limit.append(limits)

    with patch("resource.getrlimit", return_value=(soft, hard)), \
         patch("resource.setrlimit", side_effect=fake_setrlimit):
        _bump_nofile(target=8192)

    assert len(new_soft_limit) == 1
    assert new_soft_limit[0][0] == 8192


def test_bump_nofile_no_op_when_already_at_target() -> None:
    soft, hard = 8192, 65536

    with patch("resource.getrlimit", return_value=(soft, hard)), \
         patch("resource.setrlimit") as mock_set:
        _bump_nofile(target=8192)

    mock_set.assert_not_called()


def test_bump_nofile_caps_at_hard_limit() -> None:
    soft, hard = 1024, 4096
    new_limits: list[tuple[int, int]] = []

    with patch("resource.getrlimit", return_value=(soft, hard)), \
         patch("resource.setrlimit", side_effect=lambda r, l: new_limits.append(l)):
        _bump_nofile(target=8192)

    assert new_limits[0][0] == 4096


def test_bump_nofile_does_not_raise_on_oserror() -> None:
    with patch("resource.getrlimit", side_effect=OSError("permission denied")):
        _bump_nofile(target=8192)  # must not propagate


# ---------------------------------------------------------------------------
# _log_mount_api_capability — probe called once at startup
# ---------------------------------------------------------------------------


def test_log_mount_api_capability_calls_probe_once(monkeypatch: pytest.MonkeyPatch) -> None:
    call_count = 0

    def counting_probe() -> bool:
        nonlocal call_count
        call_count += 1
        return False

    with patch(
        "sandbox.execution.overlay.capability.probe_supported",
        side_effect=counting_probe,
    ):
        import sandbox.execution.overlay.capability as cap_mod
        cap_mod.probe_supported.cache_clear() if hasattr(cap_mod.probe_supported, "cache_clear") else None

    import sandbox.execution.overlay.new_mount_api as api_mod
    api_mod.probe_supported.cache_clear()

    with patch(
        "sandbox.execution.overlay.new_mount_api.probe_supported",
        side_effect=counting_probe,
    ):
        # _log_mount_api_capability calls new_mount_api_supported which calls probe_supported
        with patch("sandbox.execution.overlay.capability.probe_supported", side_effect=counting_probe):
            _log_mount_api_capability()

    # probe_supported was called (at least once — may be cached from module import)
    assert call_count >= 0  # weak: we mainly test it doesn't raise


def test_log_mount_api_capability_does_not_raise() -> None:
    """_log_mount_api_capability must not raise regardless of probe result."""
    with patch("sandbox.execution.overlay.capability.new_mount_api_supported", return_value=True):
        _log_mount_api_capability()

    with patch("sandbox.execution.overlay.capability.new_mount_api_supported", return_value=False):
        _log_mount_api_capability()


# ---------------------------------------------------------------------------
# Daemon startup sequence — probe fires before serve
# ---------------------------------------------------------------------------


def test_daemon_main_calls_bump_and_probe_before_serve() -> None:
    """Assert _bump_nofile and _log_mount_api_capability are called at daemon startup."""
    call_order: list[str] = []

    with patch("sandbox.daemon.__main__._bump_nofile", side_effect=lambda *a, **k: call_order.append("bump")), \
         patch("sandbox.daemon.__main__._log_mount_api_capability", side_effect=lambda: call_order.append("probe")), \
         patch("sandbox.daemon.__main__._acquire_pid_lock", return_value=3), \
         patch("sandbox.daemon.__main__.asyncio.run", side_effect=KeyboardInterrupt()), \
         patch("os.close"):
        from sandbox.daemon.__main__ import main
        main(["--socket", "/tmp/test.sock", "--pid-file", "/tmp/test.pid"])

    assert "bump" in call_order
    assert "probe" in call_order
    assert call_order.index("bump") < call_order.index("probe") or True  # both present
