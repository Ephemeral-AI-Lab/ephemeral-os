"""Sandbox svc.cmd latency benchmark driver (mock-runner-based scaffold).

Produces a JSON report with ``svc_cmd_p50`` / ``svc_cmd_p95`` keys for
regression-checking sandbox-reframe waves per RFC §8.3.

This is the **scaffold** required by PREP-0b: it captures p50/p95 of a
synthetic command-execution loop against the in-process MockSquadRunner.
For a real-provider regression baseline (RFC §6 Observability row), the
caller must wrap the script around a real-provider session — the seam is
the ``run_iteration`` callable that defaults to an in-process timer.

The script honors ``EOS_TIER_RUN_ID`` per memory
``eos_tier_run_id_artifact_stability.md`` — if set, the run id is embedded
in the report so multi-stage tier runs can correlate samples.

Usage:
    bench_sandbox_e2e.py --commands 10 --report=baseline.json
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
import uuid
from pathlib import Path
from typing import Callable


def _default_iteration() -> float:
    """Run one synthetic svc.cmd iteration; return elapsed seconds.

    The scaffold uses an in-process no-op loop sized to approximate the
    overlay cost breakdown documented in memory
    ``codeact_overlay_cost_breakdown.md`` (``_commit_changes`` ~0.65s,
    ``overlay_run`` ~0.43s). This keeps the harness self-contained when no
    real provider is wired — real-provider regression must replace this
    callable.
    """
    t0 = time.perf_counter()
    # ~10ms scaffold workload — meant to be visible in p50/p95 without
    # blowing CI budget. Real harness substitutes this for a daemon RPC.
    n = 0
    for _ in range(50_000):
        n += 1
    return time.perf_counter() - t0


def run_bench(
    commands: int,
    *,
    iteration: Callable[[], float] = _default_iteration,
) -> dict[str, float | int | str]:
    samples_ms: list[float] = []
    for _ in range(commands):
        samples_ms.append(iteration() * 1000.0)
    samples_ms.sort()
    return {
        "commands": commands,
        "svc_cmd_p50": statistics.median(samples_ms),
        "svc_cmd_p95": samples_ms[int(0.95 * (len(samples_ms) - 1))],
        "svc_cmd_min": samples_ms[0],
        "svc_cmd_max": samples_ms[-1],
        "samples_ms": samples_ms,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--commands",
        type=int,
        default=10,
        help="Number of synthetic svc.cmd iterations to sample.",
    )
    parser.add_argument(
        "--report",
        type=str,
        required=True,
        help="Path to write JSON report (overwrites if exists).",
    )
    args = parser.parse_args(argv)

    run_id = os.environ.get("EOS_TIER_RUN_ID") or f"local-{uuid.uuid4().hex[:12]}"

    report = run_bench(args.commands)
    report["run_id"] = run_id
    report["scaffold"] = True  # surfaces in diffs if anyone forgets to replace

    out = Path(args.report)
    out.write_text(json.dumps(report, indent=2, sort_keys=True))
    print(
        f"wrote {out} (p50={report['svc_cmd_p50']:.3f}ms "
        f"p95={report['svc_cmd_p95']:.3f}ms run_id={run_id})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
