"""SWE-EVO end-to-end smoke under EOS_SANDBOX_PROVIDER=docker.

Implements the three acceptance bars from PLAN_v4 §5.3:

(a) ≥95% of NAMESPACE-strategy execs report mount_mode=PRIVATE_NAMESPACE
(b) p95 exec latency within ±25% of the Daytona baseline file
(c) post-squash execs still report PRIVATE_NAMESPACE after auto-squash

Linux+Docker-gated; auto-skips on darwin and when EOS_HAVE_DOCKER!=1.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

_GATE = pytest.mark.skipif(
    not (
        sys.platform.startswith("linux")
        and os.environ.get("EOS_HAVE_DOCKER") == "1"
        and os.environ.get("EOS_SANDBOX_PROVIDER") == "docker"
    ),
    reason=(
        "Requires Linux + EOS_HAVE_DOCKER=1 + EOS_SANDBOX_PROVIDER=docker "
        "(PLAN_v4 §5.3)."
    ),
)

_BASELINE_PATH = (
    Path(__file__).resolve().parent / "data" / "daytona_baseline_p95.json"
)
_BASELINE_MAX_AGE_DAYS = 180


def _load_baseline() -> dict:
    assert _BASELINE_PATH.exists(), (
        f"Baseline file missing: {_BASELINE_PATH}. "
        "Re-baseline per PLAN_v4 §5.3."
    )
    return json.loads(_BASELINE_PATH.read_text())


def test_baseline_age_under_180_days() -> None:
    """Independent of platform — every test run polices baseline freshness."""
    mtime = _BASELINE_PATH.stat().st_mtime
    age_days = (time.time() - mtime) / 86400
    assert age_days < _BASELINE_MAX_AGE_DAYS, (
        f"daytona_baseline_p95.json is {age_days:.0f} days old; "
        "re-baseline required (PLAN_v4 §5.3)."
    )


@_GATE
@pytest.mark.asyncio
async def test_sweevo_docker_smoke_mount_ratio_and_perf() -> None:
    """End-to-end smoke covering (a) mount-mode ratio, (b) p95 perf, (c) post-squash."""
    instance_id = os.environ.get("EOS_SWEEVO_INSTANCE")
    assert instance_id, "set EOS_SWEEVO_INSTANCE before running this test"

    from sandbox._shared.models import MountMode, ShellProcessResult  # type: ignore
    from sandbox.provider.bootstrap import bootstrap_sandbox_provider
    from benchmarks.sweevo.run import run_sweevo_instance  # type: ignore

    bootstrap_sandbox_provider()
    run_results: list[ShellProcessResult] = await run_sweevo_instance(instance_id)

    # (a) ≥95% PRIVATE_NAMESPACE among NAMESPACE-strategy execs
    namespace_execs = [r for r in run_results if getattr(r, "strategy", None) and r.strategy.name == "NAMESPACE"]
    assert namespace_execs, "no NAMESPACE-strategy execs observed; smoke run invalid"
    private = [r for r in namespace_execs if r.mount_mode == MountMode.PRIVATE_NAMESPACE]
    ratio = len(private) / len(namespace_execs)
    assert ratio >= 0.95, (
        f"PRIVATE_NAMESPACE ratio {ratio:.2%} below 95% threshold "
        f"({len(private)} / {len(namespace_execs)})"
    )

    # (b) p95 within ±25% of Daytona baseline
    baseline = _load_baseline()
    docker_latencies = sorted(getattr(r, "elapsed_ms", 0.0) for r in namespace_execs)
    if docker_latencies:
        p95_idx = max(0, int(0.95 * len(docker_latencies)) - 1)
        docker_p95_ms = docker_latencies[p95_idx]
        daytona_p95 = float(baseline.get("p95_ms") or 0)
        assert daytona_p95 > 0, (
            "baseline daytona_baseline_p95.json has placeholder p95_ms=0; re-baseline."
        )
        assert docker_p95_ms <= daytona_p95 * 1.25, (
            f"docker p95 {docker_p95_ms:.0f}ms exceeds 125% of daytona baseline "
            f"{daytona_p95:.0f}ms"
        )

    # (c) post-squash execs still PRIVATE_NAMESPACE after auto-squash
    post_squash = [r for r in namespace_execs if getattr(r, "post_squash", False)]
    if post_squash:
        bad = [r for r in post_squash if r.mount_mode != MountMode.PRIVATE_NAMESPACE]
        assert not bad, (
            f"{len(bad)} post-squash execs lost PRIVATE_NAMESPACE; "
            "materialize()-then-mount design regressed."
        )
