"""Bound C: negative-lookup CPU slope <= 50 µs/layer as manifest depth M grows.

Procedure: for each M∈{1,10,50,100,110}: mount a workspace with M layers;
inside the mount, run `find . -name __NONEXISTENT_FILE__` 3 times (median
wall-clock CPU). Check slope between adjacent (M_lo, M_hi) pairs.

Skips on non-Linux or missing CAP_SYS_ADMIN.
T7 executes this in a CAP_SYS_ADMIN Docker container.
"""

from __future__ import annotations

import statistics
import subprocess
import sys
import time
from pathlib import Path

import pytest

from ..._harness.lease_resource_probe import assert_bound_c


pytestmark = pytest.mark.skipif(
    sys.platform != "linux",
    reason="Bound C requires Linux (overlay mount syscalls)",
)

_MANIFEST_DEPTHS = (1, 10, 50, 100, 110)
_NEGATIVE_LOOKUP_REPEATS = 3


def _make_layer_dirs(base: Path, count: int) -> list[Path]:
    dirs = []
    for i in range(count):
        d = base / f"layer_{i:04d}"
        d.mkdir(parents=True, exist_ok=True)
        # Add enough files to make the lookup realistic
        for j in range(20):
            (d / f"file_{j}.txt").write_text(f"layer {i} file {j}\n")
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


def _measure_negative_lookup_cpu_ms(workspace_dir: Path, repeats: int = 3) -> float:
    """Run `find . -name __NONEXISTENT_FILE__` inside workspace_dir, return median CPU ms."""
    samples = []
    for _ in range(repeats):
        t0 = time.process_time()
        subprocess.run(
            ["find", ".", "-name", "__NONEXISTENT_FILE__"],
            cwd=str(workspace_dir),
            capture_output=True,
            timeout=30,
        )
        elapsed_ms = (time.process_time() - t0) * 1000.0
        samples.append(elapsed_ms)
    return statistics.median(samples)


def test_bound_c_per_read_cpu_slope(tmp_path: Path) -> None:
    """Bound C: negative-lookup CPU slope <= 50 µs/layer across M in {1,10,50,100,110}.

    Checks slope between every adjacent (M_lo, M_hi) depth pair.
    A slope violation means steady-state shell read cost is growing too fast.
    """
    import os

    cpu_ms_by_depth: dict[int, float] = {}

    for m in _MANIFEST_DEPTHS:
        depth_base = tmp_path / f"M{m}"
        depth_base.mkdir()
        layers = _make_layer_dirs(depth_base / "layers", m)
        merged = depth_base / "merged"
        merged.mkdir()

        try:
            _mount_read_only_overlay(layers, merged)
        except OSError as exc:
            pytest.skip(f"Overlay mount failed (missing CAP_SYS_ADMIN?): {exc}")

        try:
            cpu_ms = _measure_negative_lookup_cpu_ms(merged, _NEGATIVE_LOOKUP_REPEATS)
        finally:
            os.system(f"umount {merged} 2>/dev/null")

        cpu_ms_by_depth[m] = cpu_ms

    assert_bound_c(cpu_ms_by_depth)
