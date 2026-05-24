"""Unit tests for new_mount_api.py ctypes wrappers.

Tests that require actual Linux kernel syscalls are xfail on macOS/non-Linux.
Probe-errno tests use monkeypatching to simulate libc responses.
"""

from __future__ import annotations

import ctypes
import errno
import sys
from unittest.mock import MagicMock

import pytest

import sandbox.overlay.new_mount_api as api
from sandbox.overlay.new_mount_api import (
    AT_FDCWD,
    FSCONFIG_CMD_CREATE,
    FSCONFIG_SET_STRING,
    MOVE_MOUNT_F_EMPTY_PATH,
    OVL_MAX_STACK,
    SYS_fsconfig,
    SYS_fsmount,
    SYS_fsopen,
    SYS_move_mount,
    MountAPIUnavailable,
)

_IS_LINUX = sys.platform == "linux"


# ---------------------------------------------------------------------------
# Syscall number stability
# ---------------------------------------------------------------------------


def test_syscall_numbers_stable_across_x86_64_and_aarch64() -> None:
    """Assert x86_64 and aarch64 share the same generic ABI syscall numbers.

    These values have been stable since Linux 5.2 on both arches. If a future
    contributor adds riscv64 or another arch with diverging numbers, this test
    will fail and force an explicit table update.
    """
    # Canonical source: arch/x86/entry/syscalls/syscall_64.tbl
    #                   arch/arm64/include/uapi/asm/unistd.h (generic ABI)
    EXPECTED = {
        "SYS_move_mount": 429,
        "SYS_fsopen": 430,
        "SYS_fsconfig": 431,
        "SYS_fsmount": 432,
    }
    assert SYS_move_mount == EXPECTED["SYS_move_mount"]
    assert SYS_fsopen == EXPECTED["SYS_fsopen"]
    assert SYS_fsconfig == EXPECTED["SYS_fsconfig"]
    assert SYS_fsmount == EXPECTED["SYS_fsmount"]


def test_constants_values() -> None:
    assert FSCONFIG_SET_STRING == 1
    assert FSCONFIG_CMD_CREATE == 6
    assert MOVE_MOUNT_F_EMPTY_PATH == 0x00000004
    assert AT_FDCWD == -100
    assert OVL_MAX_STACK == 500


# ---------------------------------------------------------------------------
# probe_supported — mocked libc
# ---------------------------------------------------------------------------


def _make_libc_mock(syscall_return: int, errno_val: int) -> MagicMock:
    """Return a mock libc where syscall() returns syscall_return and sets errno."""
    mock = MagicMock()

    def fake_syscall(*args, **kwargs):
        ctypes.set_errno(errno_val)
        return syscall_return

    mock.syscall.side_effect = fake_syscall
    return mock


@pytest.fixture(autouse=True)
def _reset_probe_cache():
    """Clear the @cache on probe_supported before each test."""
    api.probe_supported.cache_clear()
    yield
    api.probe_supported.cache_clear()


def test_probe_supported_returns_false_on_enosys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    libc_mock = _make_libc_mock(-1, errno.ENOSYS)
    monkeypatch.setattr(api, "_get_libc", lambda: libc_mock)

    result = api.probe_supported()

    assert result is False


def test_probe_supported_returns_false_on_eperm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    libc_mock = _make_libc_mock(-1, errno.EPERM)
    monkeypatch.setattr(api, "_get_libc", lambda: libc_mock)

    result = api.probe_supported()

    assert result is False


def test_probe_supported_returns_false_on_ebadf(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    libc_mock = _make_libc_mock(-1, errno.EBADF)
    monkeypatch.setattr(api, "_get_libc", lambda: libc_mock)

    result = api.probe_supported()

    assert result is False


def test_probe_supported_returns_false_when_libc_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(api, "_get_libc", lambda: None)

    result = api.probe_supported()

    assert result is False


def test_probe_supported_returns_false_on_non_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")

    result = api.probe_supported()

    assert result is False


def test_probe_supported_returns_true_when_fsopen_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    mock = MagicMock()
    mock.syscall.return_value = 42  # fake fd
    monkeypatch.setattr(api, "_get_libc", lambda: mock)

    closed: list[int] = []
    monkeypatch.setattr(api.os, "close", lambda fd: closed.append(fd))

    result = api.probe_supported()

    assert result is True
    assert closed == [42]


def test_probe_supported_caches_result(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    call_count = 0

    def counting_libc():
        nonlocal call_count
        call_count += 1
        mock = MagicMock()
        mock.syscall.return_value = -1
        return mock

    monkeypatch.setattr(api, "_get_libc", counting_libc)

    api.probe_supported()
    api.probe_supported()
    api.probe_supported()

    assert call_count == 1


@pytest.mark.skipif(not _IS_LINUX, reason="Linux only: live syscall probe")
def test_probe_supported_smokes_on_linux() -> None:
    """Smoke test: probe_supported() completes without exception on Linux.

    Result may be True or False depending on kernel version and capabilities.
    """
    result = api.probe_supported()
    assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# fsconfig_string — errno propagation
# ---------------------------------------------------------------------------


def test_fsconfig_string_propagates_errno(monkeypatch: pytest.MonkeyPatch) -> None:
    libc_mock = _make_libc_mock(-1, errno.EINVAL)
    monkeypatch.setattr(api, "_get_libc", lambda: libc_mock)

    with pytest.raises(OSError) as exc_info:
        api.fsconfig_string(3, b"lowerdir+", b"/some/path")

    assert exc_info.value.errno == errno.EINVAL


def test_fsconfig_string_succeeds_on_zero_return(monkeypatch: pytest.MonkeyPatch) -> None:
    mock = MagicMock()
    mock.syscall.return_value = 0
    monkeypatch.setattr(api, "_get_libc", lambda: mock)

    api.fsconfig_string(3, b"upperdir", b"/upper")  # must not raise


def test_fsconfig_create_propagates_errno(monkeypatch: pytest.MonkeyPatch) -> None:
    libc_mock = _make_libc_mock(-1, errno.EINVAL)
    monkeypatch.setattr(api, "_get_libc", lambda: libc_mock)

    with pytest.raises(OSError) as exc_info:
        api.fsconfig_create(3)

    assert exc_info.value.errno == errno.EINVAL


# ---------------------------------------------------------------------------
# fsopen / fsmount / move_mount — basic error propagation
# ---------------------------------------------------------------------------


def test_fsopen_propagates_errno(monkeypatch: pytest.MonkeyPatch) -> None:
    libc_mock = _make_libc_mock(-1, errno.EPERM)
    monkeypatch.setattr(api, "_get_libc", lambda: libc_mock)

    with pytest.raises(OSError) as exc_info:
        api.fsopen(b"overlay")

    assert exc_info.value.errno == errno.EPERM


def test_fsmount_propagates_errno(monkeypatch: pytest.MonkeyPatch) -> None:
    libc_mock = _make_libc_mock(-1, errno.EPERM)
    monkeypatch.setattr(api, "_get_libc", lambda: libc_mock)

    with pytest.raises(OSError) as exc_info:
        api.fsmount(3)

    assert exc_info.value.errno == errno.EPERM


def test_move_mount_propagates_errno(monkeypatch: pytest.MonkeyPatch) -> None:
    libc_mock = _make_libc_mock(-1, errno.EPERM)
    monkeypatch.setattr(api, "_get_libc", lambda: libc_mock)

    with pytest.raises(OSError) as exc_info:
        api.move_mount(3, b"/workspace")

    assert exc_info.value.errno == errno.EPERM


def test_libc_or_raise_raises_when_no_libc(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api, "_get_libc", lambda: None)

    with pytest.raises(MountAPIUnavailable):
        api.fsopen(b"overlay")


# ---------------------------------------------------------------------------
# Exception class hierarchy
# ---------------------------------------------------------------------------


def test_mount_api_unavailable_is_oserror() -> None:
    exc = MountAPIUnavailable("no libc")
    assert isinstance(exc, OSError)
