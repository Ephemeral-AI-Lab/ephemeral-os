"""Adversarial harness self-test (Critic C6 / pre-mortem §7.4).

Intentionally regresses exactly ONE lease out of N=50 by writing 1 MB into
its upper dir. The harness MUST:
  (a) fail the max(lower_bytes_delta) <= 4 KiB assertion, AND
  (b) name the bad lease ID in the failure output.

If this test false-passes (harness doesn't catch the outlier), the Bound A
harness is not measuring what it claims — it's averaging or not attributing
correctly.

Skips on non-Linux or missing CAP_SYS_ADMIN.
T7 executes this in a CAP_SYS_ADMIN Docker container.
"""

from __future__ import annotations

import sys
import time
import uuid
from pathlib import Path

import pytest

from ..._harness.lease_resource_probe import (
    ResourceDelta,
    assert_bound_a,
    diff,
    snapshot_resources,
)


pytestmark = pytest.mark.skipif(
    sys.platform != "linux",
    reason="Adversarial self-test requires Linux (overlay mount syscalls)",
)

_N = 50
_LAYER_COUNT = 10
_INJECTED_BYTES = 1024 * 1024  # 1 MB regression injected into one lease


def _make_layer_dirs(base: Path, count: int) -> list[Path]:
    dirs = []
    for i in range(count):
        d = base / f"layer_{i:04d}"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"file_{i}.txt").write_text(f"layer {i}\n")
        dirs.append(d)
    return dirs


def _mount_read_only_overlay(layer_dirs: list[Path], merged: Path) -> None:
    import ctypes
    import ctypes.util
    import os

    libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
    libc.syscall.restype = ctypes.c_long

    SYS_fsopen = 430
    SYS_fsconfig = 431
    SYS_fsmount = 432
    SYS_move_mount = 429
    FSCONFIG_SET_STRING = 1
    FSCONFIG_CMD_CREATE = 6
    MOVE_MOUNT_F_EMPTY_PATH = 4
    AT_FDCWD = -100

    fd = libc.syscall(SYS_fsopen, b"overlay", 0)
    if fd < 0:
        err = ctypes.get_errno()
        raise OSError(err, f"fsopen errno={err}")
    try:
        for d in layer_dirs:
            rc = libc.syscall(SYS_fsconfig, fd, FSCONFIG_SET_STRING, b"lowerdir+", str(d).encode(), 0)
            if rc < 0:
                err = ctypes.get_errno()
                raise OSError(err, f"fsconfig lowerdir+ errno={err}")
        rc = libc.syscall(SYS_fsconfig, fd, FSCONFIG_CMD_CREATE, 0, 0, 0)
        if rc < 0:
            err = ctypes.get_errno()
            raise OSError(err, f"fsconfig CREATE errno={err}")
        mfd = libc.syscall(SYS_fsmount, fd, 0, 0)
        if mfd < 0:
            err = ctypes.get_errno()
            raise OSError(err, f"fsmount errno={err}")
        try:
            rc = libc.syscall(SYS_move_mount, mfd, b"", AT_FDCWD, str(merged).encode(), MOVE_MOUNT_F_EMPTY_PATH)
            if rc < 0:
                err = ctypes.get_errno()
                raise OSError(err, f"move_mount errno={err}")
        finally:
            os.close(mfd)
    finally:
        os.close(fd)


def _run_lease_cycle(
    base: Path,
    lease_id: str,
    layer_count: int,
    *,
    inject_bytes: int = 0,
) -> ResourceDelta:
    """Run one lease cycle. If inject_bytes > 0, write that many bytes into upper/ to simulate regression."""
    import os

    run_dir = base / lease_id
    run_dir.mkdir(parents=True)
    upper = run_dir / "upper"
    work = run_dir / "work"
    merged = run_dir / "merged"
    for d in (upper, work, merged):
        d.mkdir()

    layers = _make_layer_dirs(base / f"layers_{lease_id}", layer_count)

    pre = snapshot_resources(run_dir)
    t_start = time.perf_counter()

    try:
        _mount_read_only_overlay(layers, merged)
    except OSError as exc:
        pytest.skip(f"Overlay mount failed (missing CAP_SYS_ADMIN?): {exc}")

    mount_s = time.perf_counter() - t_start

    if inject_bytes > 0:
        # Simulate a regressing lease that materialized bytes into its upper dir
        (upper / "injected_regression.bin").write_bytes(b"x" * inject_bytes)

    post = snapshot_resources(run_dir)
    os.system(f"umount {merged} 2>/dev/null")

    return diff(
        pre, post,
        lease_id=lease_id,
        mount_layer_count=layer_count,
        mount_workspace_s=mount_s,
        materialize_s=0.0,
    )


def test_adversarial_harness_catches_single_regressing_lease(tmp_path: Path) -> None:
    """Harness MUST detect a single regressing lease out of N=50.

    One lease gets 1 MB injected into its upper dir. assert_bound_a must raise
    and the error message must name that specific lease ID.

    If this test passes without raising AssertionError inside pytest.raises,
    the harness is broken — it failed to detect a 1 MB outlier.
    """
    deltas: dict[str, ResourceDelta] = {}
    bad_lease_id = str(uuid.uuid4())[:8] + "_BAD"

    for i in range(_N):
        is_bad = i == _N // 2  # inject into the middle lease
        lease_id = bad_lease_id if is_bad else str(uuid.uuid4())[:8]
        inject = _INJECTED_BYTES if is_bad else 0
        delta = _run_lease_cycle(tmp_path, lease_id, _LAYER_COUNT, inject_bytes=inject)
        deltas[lease_id] = delta

    # The harness MUST raise AssertionError naming the bad lease
    with pytest.raises(AssertionError) as exc_info:
        assert_bound_a(deltas)

    error_msg = str(exc_info.value)
    assert bad_lease_id in error_msg, (
        f"Harness raised AssertionError but did NOT name the bad lease '{bad_lease_id}' "
        f"in the message. Got: {error_msg!r}. "
        f"The harness is not attributing per-lease correctly (may be averaging)."
    )
