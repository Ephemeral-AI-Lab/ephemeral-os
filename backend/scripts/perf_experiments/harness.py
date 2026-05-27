"""Shared microbench harness for sandbox perf experiments.

Contract per docs/plans/sandbox_perf_experiments_PLAN.md §6:

1. Emit (median, p95, p99, max, n, ci95) per condition. ci95 via bootstrap (≥1000 resamples).
2. ≥30 iterations + 5 warmup per condition (warmup excluded from stats).
3. Machine identity captured in YAML front-matter of report.md.
4. Realism gate (advisor note): the baseline microbench MUST reproduce
   a tail at least as large as the production hotspot it claims to attack.
   Otherwise the experiment is INCONCLUSIVE, not promoted/killed.
5. Two shapes:
   - point_latency: N independent timed callables, returns Stats.
   - sustained_soak: a generator drives the workload for `duration_s`; the
     harness collects per-sample latencies + a "lag" or other workload-side
     metric and returns Stats + the time-series for drift detection.
"""

from __future__ import annotations

import json
import math
import os
import platform
import random
import statistics
import subprocess
import sys
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any


# ---------- statistics ----------


def percentile(values: list[float], q: float) -> float:
    """Linear-interpolation percentile (matches numpy default)."""
    if not values:
        return math.nan
    sv = sorted(values)
    if len(sv) == 1:
        return sv[0]
    pos = (len(sv) - 1) * q
    lower = math.floor(pos)
    upper = math.ceil(pos)
    if lower == upper:
        return sv[int(pos)]
    frac = pos - lower
    return sv[lower] + (sv[upper] - sv[lower]) * frac


def bootstrap_ci(
    values: list[float],
    *,
    statistic: Callable[[list[float]], float],
    resamples: int = 1000,
    confidence: float = 0.95,
    seed: int = 0xC0FFEE,
) -> tuple[float, float]:
    """Percentile-bootstrap confidence interval for ``statistic`` over ``values``."""
    if len(values) < 2:
        return (math.nan, math.nan)
    rng = random.Random(seed)
    n = len(values)
    samples = []
    for _ in range(resamples):
        resample = [values[rng.randrange(n)] for _ in range(n)]
        samples.append(statistic(resample))
    samples.sort()
    alpha = (1.0 - confidence) / 2.0
    lo = percentile(samples, alpha)
    hi = percentile(samples, 1.0 - alpha)
    return (lo, hi)


@dataclass(frozen=True)
class Stats:
    n: int
    median: float
    p95: float
    p99: float
    max_: float
    median_ci95: tuple[float, float]
    p99_ci95: tuple[float, float]
    raw: tuple[float, ...] = field(default_factory=tuple)

    @classmethod
    def from_samples(cls, samples: list[float], *, ci_resamples: int = 1000) -> Stats:
        if not samples:
            return cls(
                n=0,
                median=math.nan,
                p95=math.nan,
                p99=math.nan,
                max_=math.nan,
                median_ci95=(math.nan, math.nan),
                p99_ci95=(math.nan, math.nan),
                raw=(),
            )
        med = statistics.median(samples)
        p95 = percentile(samples, 0.95)
        p99 = percentile(samples, 0.99)
        med_ci = bootstrap_ci(
            samples,
            statistic=statistics.median,
            resamples=ci_resamples,
            seed=0xC0FFEE,
        )
        p99_ci = bootstrap_ci(
            samples,
            statistic=lambda xs: percentile(xs, 0.99),
            resamples=ci_resamples,
            seed=0xDECAF,
        )
        return cls(
            n=len(samples),
            median=med,
            p95=p95,
            p99=p99,
            max_=max(samples),
            median_ci95=med_ci,
            p99_ci95=p99_ci,
            raw=tuple(samples),
        )

    def fmt_row(self, label: str, scale_ms: bool = True) -> str:
        factor = 1000.0 if scale_ms else 1.0
        unit = "ms" if scale_ms else "s"
        return (
            f"| {label} | {self.n} | {self.median * factor:.3f} | "
            f"{self.p95 * factor:.3f} | {self.p99 * factor:.3f} | "
            f"{self.max_ * factor:.3f} | "
            f"[{self.median_ci95[0] * factor:.3f}, {self.median_ci95[1] * factor:.3f}] | "
            f"[{self.p99_ci95[0] * factor:.3f}, {self.p99_ci95[1] * factor:.3f}] |"
            + (f"  *({unit})*" if False else "")
        )


def stats_table_header() -> list[str]:
    return [
        "| condition | n | median (ms) | p95 (ms) | p99 (ms) | max (ms) | median 95% CI | p99 95% CI |",
        "|---|---:|---:|---:|---:|---:|---|---|",
    ]


# ---------- point-latency bench ----------


def point_latency(
    fn: Callable[[], Any],
    *,
    iters: int = 30,
    warmup: int = 5,
    pre_iter: Callable[[], None] | None = None,
) -> Stats:
    """Run ``fn`` ``iters`` times after ``warmup`` warmup runs; return Stats over seconds.

    ``pre_iter`` (if provided) is called BEFORE each timed iteration. Use it
    to clear caches, reset state, etc. It is NOT timed.
    """
    if iters < 1:
        raise ValueError("iters must be >= 1")
    for _ in range(warmup):
        if pre_iter is not None:
            pre_iter()
        fn()
    samples: list[float] = []
    for _ in range(iters):
        if pre_iter is not None:
            pre_iter()
        t0 = time.perf_counter()
        fn()
        samples.append(time.perf_counter() - t0)
    return Stats.from_samples(samples)


# ---------- sustained-soak bench (E1 load-divergence co-gate) ----------


@dataclass(frozen=True)
class SoakResult:
    latencies: tuple[float, ...]
    side_signal: tuple[tuple[float, float], ...]
    """(elapsed_since_start_s, side_signal_value) — for drift detection."""

    @property
    def stats(self) -> Stats:
        return Stats.from_samples(list(self.latencies))


def sustained_soak(
    one_step: Callable[[], float],
    *,
    duration_s: float,
    step_interval_s: float,
    side_signal: Callable[[], float] | None = None,
    side_signal_every_s: float = 1.0,
) -> SoakResult:
    """Run ``one_step`` every ``step_interval_s`` for ``duration_s``.

    ``one_step`` returns its own measured latency in seconds.
    ``side_signal`` (if provided) is sampled every ``side_signal_every_s`` —
    use this to record async_squasher.lag_s or queue depth for drift.
    """
    latencies: list[float] = []
    side: list[tuple[float, float]] = []
    start = time.perf_counter()
    next_step_at = start
    next_side_at = start + side_signal_every_s
    while True:
        now = time.perf_counter()
        elapsed = now - start
        if elapsed >= duration_s:
            break
        if now >= next_step_at:
            latencies.append(one_step())
            next_step_at += step_interval_s
        if side_signal is not None and now >= next_side_at:
            side.append((elapsed, side_signal()))
            next_side_at += side_signal_every_s
        sleep_target = min(
            next_step_at,
            next_side_at if side_signal is not None else float("inf"),
        )
        sleep_for = sleep_target - time.perf_counter()
        if sleep_for > 0:
            time.sleep(min(sleep_for, 0.01))
    return SoakResult(latencies=tuple(latencies), side_signal=tuple(side))


def monotonic_upward_drift(series: list[tuple[float, float]]) -> tuple[bool, float]:
    """Return (drift_detected, slope) for a (t, v) series.

    Drift detected if linear-regression slope > 0 AND last-window mean > first-window mean
    by >25%. Conservative — small noise won't trip it.
    """
    if len(series) < 4:
        return (False, 0.0)
    n = len(series)
    xs = [t for t, _ in series]
    ys = [v for _, v in series]
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((xs[i] - mean_x) * (ys[i] - mean_y) for i in range(n))
    den = sum((xs[i] - mean_x) ** 2 for i in range(n))
    slope = num / den if den > 0 else 0.0
    window = max(1, n // 4)
    first_mean = sum(ys[:window]) / window
    last_mean = sum(ys[-window:]) / window
    drift = (slope > 0) and (last_mean > first_mean * 1.25) and (first_mean > 0)
    return (drift, slope)


# ---------- realism gate ----------


@dataclass(frozen=True)
class RealismCheck:
    name: str
    baseline_p99_s: float
    hotspot_p99_s: float
    ratio_required: float = 0.5
    passed: bool = False
    note: str = ""

    @classmethod
    def evaluate(
        cls,
        *,
        name: str,
        baseline_p99_s: float,
        hotspot_p99_s: float,
        ratio_required: float = 0.5,
    ) -> RealismCheck:
        """Pass when baseline_p99 >= ratio_required * hotspot_p99.

        Default 0.5 — baseline must reproduce at least half the production tail.
        A microbench showing 8ms when production shows 200ms is INCONCLUSIVE.
        """
        ratio = baseline_p99_s / hotspot_p99_s if hotspot_p99_s > 0 else 0.0
        passed = ratio >= ratio_required
        note = (
            f"baseline p99 = {baseline_p99_s * 1000:.1f}ms; "
            f"production hotspot p99 = {hotspot_p99_s * 1000:.1f}ms; "
            f"ratio = {ratio:.2f} (required ≥ {ratio_required:.2f})"
        )
        return cls(
            name=name,
            baseline_p99_s=baseline_p99_s,
            hotspot_p99_s=hotspot_p99_s,
            ratio_required=ratio_required,
            passed=passed,
            note=note,
        )


# ---------- machine identity (YAML front-matter) ----------


def machine_identity() -> dict[str, str]:
    cpu_model = _cpu_brand()
    docker = _which_version("docker", "--version")
    return {
        "platform": platform.platform(),
        "kernel": platform.release(),
        "python_version": platform.python_version(),
        "docker_version": docker or "n/a",
        "provider_image_tag": os.environ.get("EOS_PROVIDER_IMAGE_TAG", "n/a"),
        "cpu_model": cpu_model,
        "wall_clock_at_start": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "page_cache_handling": _page_cache_handling(),
    }


def _which_version(prog: str, *args: str) -> str | None:
    try:
        out = subprocess.run(
            [prog, *args], capture_output=True, text=True, timeout=2, check=False
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None
    return None


def _cpu_brand() -> str:
    if sys.platform == "darwin":
        cpu = _which_version("sysctl", "-n", "machdep.cpu.brand_string")
        if cpu:
            return cpu
    if sys.platform.startswith("linux"):
        try:
            with open("/proc/cpuinfo", encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("model name"):
                        return line.split(":", 1)[1].strip()
        except OSError:
            pass
    return platform.processor() or "unknown"


def _page_cache_handling() -> str:
    """Linux-only drop_caches; on macOS returns "uncached" mode label."""
    if sys.platform == "darwin":
        return "macOS-uncached"
    drop = "/proc/sys/vm/drop_caches"
    if os.path.exists(drop) and os.access(drop, os.W_OK):
        return "linux-drop-caches-root"
    return "linux-no-root-uncached"


def yaml_front_matter(extra: dict[str, Any] | None = None) -> str:
    info = {**machine_identity(), **(extra or {})}
    lines = ["---"]
    for k, v in info.items():
        lines.append(f"{k}: {json.dumps(v)}")
    lines.append("---")
    return "\n".join(lines) + "\n"


# ---------- verdict + report glue ----------


def render_verdict(
    *,
    realism: RealismCheck | None,
    threshold_met: bool,
    note: str,
) -> str:
    """One-line verdict per plan §6:

    - INCONCLUSIVE: realism gate fails (baseline doesn't reproduce hotspot).
    - PROMOTED: threshold met.
    - KILLED: threshold not met (and realism passed).
    """
    if realism is not None and not realism.passed:
        return f"**VERDICT: INCONCLUSIVE** — {realism.note}; {note}"
    if threshold_met:
        return f"**VERDICT: PROMOTED** — {note}"
    return f"**VERDICT: KILLED** — {note}"


__all__ = [
    "RealismCheck",
    "SoakResult",
    "Stats",
    "bootstrap_ci",
    "machine_identity",
    "monotonic_upward_drift",
    "percentile",
    "point_latency",
    "render_verdict",
    "stats_table_header",
    "sustained_soak",
    "yaml_front_matter",
]
