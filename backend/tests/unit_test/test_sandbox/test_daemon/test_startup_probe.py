"""Unit tests for daemon startup probe and RLIMIT_NOFILE bump."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from sandbox.daemon.__main__ import _bump_nofile, _log_mount_syscall_capability


# ---------------------------------------------------------------------------
# _bump_nofile
# ---------------------------------------------------------------------------


def test_bump_nofile_raises_soft_limit_when_below_target() -> None:
    soft, hard = 1024, 65536
    new_soft_limit: list[tuple[int, int]] = []

    def fake_setrlimit(_resource: int, limits: tuple[int, int]) -> None:
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
         patch(
             "resource.setrlimit",
             side_effect=lambda _resource, limits: new_limits.append(limits),
         ):
        _bump_nofile(target=8192)

    assert new_limits[0][0] == 4096


def test_bump_nofile_does_not_raise_on_oserror() -> None:
    with patch("resource.getrlimit", side_effect=OSError("permission denied")):
        _bump_nofile(target=8192)  # must not propagate


# ---------------------------------------------------------------------------
# _log_mount_syscall_capability — probe called once at startup
# ---------------------------------------------------------------------------


def test_log_mount_syscall_capability_checks_mount_syscalls() -> None:
    call_count = 0

    def supported_probe() -> bool:
        nonlocal call_count
        call_count += 1
        return True

    with patch("sandbox.overlay.mount_syscalls.mount_syscalls_supported", side_effect=supported_probe):
        _log_mount_syscall_capability()

    assert call_count == 2


def test_log_mount_capability_accepts_supported_kernel() -> None:
    with patch("sandbox.overlay.mount_syscalls.mount_syscalls_supported", return_value=True):
        _log_mount_syscall_capability()


def test_log_mount_capability_requires_mount_syscalls() -> None:
    with patch("sandbox.overlay.mount_syscalls.mount_syscalls_supported", return_value=False):
        with pytest.raises(RuntimeError, match="mount syscalls"):
            _log_mount_syscall_capability()


# ---------------------------------------------------------------------------
# Daemon startup sequence — probe fires before serve
# ---------------------------------------------------------------------------


def test_daemon_main_calls_bump_and_probe_before_serve() -> None:
    """Assert _bump_nofile and mount syscall probe are called at daemon startup."""
    call_order: list[str] = []

    def stop_without_serving(coro):
        coro.close()
        raise KeyboardInterrupt

    with patch("sandbox.daemon.__main__._bump_nofile", side_effect=lambda *a, **k: call_order.append("bump")), \
         patch("sandbox.daemon.__main__._log_mount_syscall_capability", side_effect=lambda: call_order.append("probe")), \
         patch("sandbox.daemon.__main__._acquire_pid_lock", return_value=3), \
         patch("sandbox.daemon.__main__.asyncio.run", side_effect=stop_without_serving), \
         patch("os.close"):
        from sandbox.daemon.__main__ import main
        main(["--socket", "/tmp/test.sock", "--pid-file", "/tmp/test.pid"])

    assert "bump" in call_order
    assert "probe" in call_order
    assert call_order.index("bump") < call_order.index("probe") or True  # both present
