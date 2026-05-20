"""Per-lease resource measurement primitives for Bounds A, B, C audit harness.

All measurements are per-lease scoped (du -sb <run_dir>/upper, not df).
This is the correct attribution per §5.1 of the O1 overlay mount plan.

Bound A: max(lower_bytes_delta) <= 4 KiB across N concurrent leases.
Bound B: disk flat + mount-time slope <= 5 ms/layer across manifest depths.
Bound C: negative-lookup CPU slope <= 50 µs/layer across manifest depths.
"""

from __future__ import annotations

import statistics
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class ResourceSnapshot:
    """Point-in-time per-lease resource reading."""
    lower_bytes: int = 0       # du -sb transient lowerdir for this lease (0 if new API)
    upperdir_bytes: int = 0    # du -sb <run_dir>/upper
    workdir_bytes: int = 0     # du -sb <run_dir>/work
    rss_kb: int = 0            # /proc/self/status VmRSS


@dataclass
class ResourceDelta:
    """Difference between two ResourceSnapshots for one lease."""
    lease_id: str
    lower_bytes_delta: int = 0
    upperdir_bytes: int = 0
    workdir_bytes: int = 0
    rss_delta_kb: int = 0
    mount_layer_count: int = 0
    mount_workspace_s: float = 0.0
    materialize_s: float = 0.0


def _du_bytes(path: Path) -> int:
    """Return byte count for path via du -sb, 0 on any error."""
    try:
        result = subprocess.run(
            ["du", "-sb", str(path)],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            parts = result.stdout.strip().split()
            if parts:
                return int(parts[0])
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return 0


def _rss_kb() -> int:
    """Read VmRSS from /proc/self/status, 0 on non-Linux or error."""
    if sys.platform != "linux":
        return 0
    try:
        with open("/proc/self/status") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    parts = line.split()
                    return int(parts[1]) if len(parts) >= 2 else 0
    except (OSError, ValueError):
        pass
    return 0


def snapshot_resources(
    run_dir: Path,
    transient_lowerdir: Optional[Path] = None,
) -> ResourceSnapshot:
    """Capture per-lease resource snapshot.

    run_dir: this lease's run_dir (contains upper/ and work/ subdirs).
    transient_lowerdir: the materialize=True lowerdir for this lease, or None
        if using the new mount API (layer_paths path — no transient lowerdir).
    """
    lower_bytes = _du_bytes(transient_lowerdir) if transient_lowerdir else 0
    upper_path = run_dir / "upper"
    work_path = run_dir / "work"
    return ResourceSnapshot(
        lower_bytes=lower_bytes,
        upperdir_bytes=_du_bytes(upper_path) if upper_path.exists() else 0,
        workdir_bytes=_du_bytes(work_path) if work_path.exists() else 0,
        rss_kb=_rss_kb(),
    )


def diff(
    pre: ResourceSnapshot,
    post: ResourceSnapshot,
    *,
    lease_id: str,
    mount_layer_count: int = 0,
    mount_workspace_s: float = 0.0,
    materialize_s: float = 0.0,
) -> ResourceDelta:
    """Compute per-lease ResourceDelta from pre/post snapshots."""
    return ResourceDelta(
        lease_id=lease_id,
        lower_bytes_delta=post.lower_bytes - pre.lower_bytes,
        upperdir_bytes=post.upperdir_bytes,
        workdir_bytes=post.workdir_bytes,
        rss_delta_kb=post.rss_kb - pre.rss_kb,
        mount_layer_count=mount_layer_count,
        mount_workspace_s=mount_workspace_s,
        materialize_s=materialize_s,
    )


def assert_bound_a(
    deltas_by_lease: dict[str, ResourceDelta],
    *,
    lower_bytes_limit: int = 4096,    # 4 KiB
    upper_work_limit: int = 65536,    # 64 KiB
    materialize_s_limit: float = 0.005,
) -> None:
    """Assert Bound A: O(1) disk cost per lease, regardless of N.

    Uses max(), NOT avg(), per §5.2 Critic M2 requirement.
    On failure, emits top-3 outlier lease IDs in the assertion message.
    """
    if not deltas_by_lease:
        return

    deltas = list(deltas_by_lease.values())

    # lower_bytes_delta: max across all leases
    by_lower = sorted(deltas, key=lambda d: d.lower_bytes_delta, reverse=True)
    max_lower = by_lower[0].lower_bytes_delta
    top3_lower = [d.lease_id for d in by_lower[:3]]

    if max_lower > lower_bytes_limit:
        raise AssertionError(
            f"Bound A FAIL: max(lower_bytes_delta)={max_lower} > {lower_bytes_limit} bytes. "
            f"Top-3 outlier leases: {top3_lower}"
        )

    # upperdir+workdir: max across all leases
    by_upper = sorted(deltas, key=lambda d: d.upperdir_bytes + d.workdir_bytes, reverse=True)
    max_upper_work = by_upper[0].upperdir_bytes + by_upper[0].workdir_bytes
    top3_upper = [d.lease_id for d in by_upper[:3]]

    if max_upper_work > upper_work_limit:
        raise AssertionError(
            f"Bound A FAIL: max(upperdir+workdir)={max_upper_work} > {upper_work_limit} bytes. "
            f"Top-3 outlier leases: {top3_upper}"
        )

    # materialize_s: max across all leases
    by_mat = sorted(deltas, key=lambda d: d.materialize_s, reverse=True)
    max_mat = by_mat[0].materialize_s
    top3_mat = [d.lease_id for d in by_mat[:3]]

    if max_mat > materialize_s_limit:
        raise AssertionError(
            f"Bound A FAIL: max(materialize_s)={max_mat:.4f} > {materialize_s_limit}. "
            f"Top-3 outlier leases: {top3_mat}"
        )

    # Sum check (informational — still asserted as a sanity guard)
    n = len(deltas)
    sum_upper_work = sum(d.upperdir_bytes + d.workdir_bytes for d in deltas)
    if sum_upper_work > n * upper_work_limit:
        raise AssertionError(
            f"Bound A FAIL: sum(upperdir+workdir)={sum_upper_work} > {n}×{upper_work_limit}="
            f"{n * upper_work_limit}. N={n} leases."
        )


def assert_bound_b(
    deltas_by_depth: dict[int, list[ResourceDelta]],
    *,
    lower_bytes_limit: int = 4096,
    mount_time_slope_per_layer: float = 0.005,  # 5 ms/layer
) -> None:
    """Assert Bound B: disk flat + mount-time linear in M with small slope.

    deltas_by_depth: {manifest_depth_M: [ResourceDelta, ...]} from N=10 concurrent leases.
    """
    if not deltas_by_depth:
        return

    depths = sorted(deltas_by_depth.keys())

    for m in depths:
        deltas = deltas_by_depth[m]
        # Disk flat: max(lower_bytes_delta) <= 4 KiB for every M
        by_lower = sorted(deltas, key=lambda d: d.lower_bytes_delta, reverse=True)
        max_lower = by_lower[0].lower_bytes_delta
        top3 = [d.lease_id for d in by_lower[:3]]
        if max_lower > lower_bytes_limit:
            raise AssertionError(
                f"Bound B FAIL at M={m}: max(lower_bytes_delta)={max_lower} > {lower_bytes_limit}. "
                f"Top-3 outliers: {top3}"
            )

    # Mount-time slope: compare each M against M=1 baseline
    if len(depths) < 2:
        return

    m_base = depths[0]
    base_times = [d.mount_workspace_s for d in deltas_by_depth[m_base]]
    median_base = statistics.median(base_times)

    for m in depths[1:]:
        times = [d.mount_workspace_s for d in deltas_by_depth[m]]
        median_m = statistics.median(times)
        allowed = median_base * (1 + mount_time_slope_per_layer * m)
        if median_m > allowed:
            raise AssertionError(
                f"Bound B FAIL: mount-time at M={m}: median={median_m:.4f}s > "
                f"allowed={allowed:.4f}s (base={median_base:.4f}s, slope={mount_time_slope_per_layer}/layer)"
            )


def assert_bound_c(
    cpu_ms_by_depth: dict[int, float],
    *,
    slope_limit_us_per_layer: float = 50.0,
) -> None:
    """Assert Bound C: negative-lookup CPU slope <= 50 µs/layer.

    cpu_ms_by_depth: {manifest_depth_M: median_cpu_ms_for_negative_lookup_benchmark}.
    Checks slope between every adjacent (M_lo, M_hi) pair.
    """
    if len(cpu_ms_by_depth) < 2:
        return

    depths = sorted(cpu_ms_by_depth.keys())

    for i in range(len(depths) - 1):
        m_lo = depths[i]
        m_hi = depths[i + 1]
        cpu_lo = cpu_ms_by_depth[m_lo]
        cpu_hi = cpu_ms_by_depth[m_hi]
        delta_layers = m_hi - m_lo
        slope_us = (cpu_hi - cpu_lo) * 1000.0 / delta_layers
        if slope_us > slope_limit_us_per_layer:
            raise AssertionError(
                f"Bound C FAIL: CPU slope between M={m_lo} and M={m_hi}: "
                f"{slope_us:.1f} µs/layer > {slope_limit_us_per_layer} µs/layer. "
                f"cpu_ms: {cpu_lo:.3f} → {cpu_hi:.3f}"
            )
