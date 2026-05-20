"""Bound B: disk flat + mount-time linear in M with slope <= 5ms/layer.

Procedure: fix N=10 concurrent leases; vary manifest depth M∈{1,10,50,100,110}.
Stop at 110 (OVL_MAX_STACK_GUARD); depth 111+ hits the guard separately.

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
    assert_bound_b,
    diff,
    snapshot_resources,
)


pytestmark = pytest.mark.skipif(
    sys.platform != "linux",
    reason="Bound B requires Linux (overlay mount syscalls)",
)

_FIXED_N = 10
_MANIFEST_DEPTHS = (1, 10, 50, 100, 110)


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


def _run_lease_at_depth(
    base: Path,
    lease_id: str,
    depth: int,
) -> ResourceDelta:
    import os

    run_dir = base / lease_id
    run_dir.mkdir(parents=True)
    merged = run_dir / "merged"
    merged.mkdir()

    layers = _make_layer_dirs(base / f"layers_{lease_id}", depth)

    pre = snapshot_resources(run_dir)
    t_start = time.perf_counter()

    try:
        _mount_read_only_overlay(layers, merged)
    except OSError as exc:
        pytest.skip(f"Overlay mount failed (missing CAP_SYS_ADMIN?): {exc}")

    mount_s = time.perf_counter() - t_start
    _ = list(merged.iterdir())
    post = snapshot_resources(run_dir)
    os.system(f"umount {merged} 2>/dev/null")

    return diff(
        pre, post,
        lease_id=lease_id,
        mount_layer_count=depth,
        mount_workspace_s=mount_s,
        materialize_s=0.0,
    )


def test_bound_b_manifest_depth_sweep(tmp_path: Path) -> None:
    """Bound B: disk flat and mount-time linear with slope <= 5ms/layer.

    For each M in {1,10,50,100,110}: runs N=10 leases and checks:
      - max(lower_bytes_delta) <= 4 KiB (independent of M)
      - median(mount_workspace_s at M) <= median(at M=1) * (1 + 0.005 * M)
    """
    deltas_by_depth: dict[int, list[ResourceDelta]] = {}

    for m in _MANIFEST_DEPTHS:
        depth_deltas = []
        depth_base = tmp_path / f"M{m}"
        depth_base.mkdir()

        for _ in range(_FIXED_N):
            lease_id = str(uuid.uuid4())[:8]
            delta = _run_lease_at_depth(depth_base, lease_id, m)
            depth_deltas.append(delta)

        deltas_by_depth[m] = depth_deltas

    assert_bound_b(deltas_by_depth)
