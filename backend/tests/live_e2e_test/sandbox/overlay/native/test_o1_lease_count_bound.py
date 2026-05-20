"""Bound A: O(1) per-lease disk cost independent of N concurrent leases.

Procedure: hold fixed manifest depth M=10; run N∈{1,10,50,100,200} prepare→mount→
release cycles. Per-lease lower_bytes_delta must be ≤4 KiB (max, not avg).

Skips on non-Linux or missing CAP_SYS_ADMIN (OSError on mount attempt).
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
    reason="Bound A requires Linux (overlay mount syscalls)",
)

_BOUND_A_DEPTHS = (1, 10, 50, 100, 200)
_FIXED_MANIFEST_DEPTH = 10


def _make_layer_dirs(base: Path, count: int) -> list[Path]:
    """Create `count` overlay lower dirs each containing a marker file."""
    dirs = []
    for i in range(count):
        d = base / f"layer_{i:04d}"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"file_{i}.txt").write_text(f"layer {i}\n")
        dirs.append(d)
    return dirs


def _mount_read_only_overlay(layer_dirs: list[Path], merged: Path) -> None:
    """Mount a read-only overlay using fsopen+lowerdir+ (newest-first = first call = top)."""
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
        raise OSError(err, f"fsopen failed (missing CAP_SYS_ADMIN?): errno={err}")
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
            import os as _os
            _os.close(mfd)
    finally:
        import os as _os
        _os.close(fd)


def _run_lease_cycle(
    base: Path,
    lease_id: str,
    layer_count: int,
) -> ResourceDelta:
    """Run one prepare→mount→unmount lease cycle; return ResourceDelta."""
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

    # Simulate trivial command read
    _ = list(merged.iterdir())

    post = snapshot_resources(run_dir)

    # Unmount
    os.system(f"umount {merged} 2>/dev/null")

    return diff(
        pre, post,
        lease_id=lease_id,
        mount_layer_count=layer_count,
        mount_workspace_s=mount_s,
        materialize_s=0.0,  # new API: no materialize
    )


def test_bound_a_lease_count_sweep(tmp_path: Path) -> None:
    """Bound A: max(lower_bytes_delta) <= 4 KiB for N in {1,10,50,100,200}.

    Uses max() not avg() — a single regressing lease cannot hide by averaging.
    Emits top-3 outlier lease IDs on failure.
    """
    for n in _BOUND_A_DEPTHS:
        deltas: dict[str, ResourceDelta] = {}
        sweep_base = tmp_path / f"N{n}"
        sweep_base.mkdir()

        for _ in range(n):
            lease_id = str(uuid.uuid4())[:8]
            delta = _run_lease_cycle(sweep_base, lease_id, _FIXED_MANIFEST_DEPTH)
            deltas[lease_id] = delta

        assert_bound_a(deltas)
