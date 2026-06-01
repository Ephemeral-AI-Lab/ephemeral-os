"""Wire the 3 sweevo workflow stages. Pure orchestration."""

from __future__ import annotations

import argparse

from test_runner.benchmarks.sweevo.eval import format_verdict
from test_runner.benchmarks.sweevo.run import build_run_config
from test_runner.benchmarks.sweevo.setup import preflight, provision_sandbox


async def run_benchmark_sweevo(args: argparse.Namespace) -> int:
    from test_runner.core.engine import run_pipeline

    ctx = await preflight(args)
    sandbox_id = await provision_sandbox(ctx)
    config = build_run_config(ctx, sandbox_id)
    report = await run_pipeline(config)
    line, rc = format_verdict(report)
    print(line)
    return rc


__all__ = ["run_benchmark_sweevo"]
