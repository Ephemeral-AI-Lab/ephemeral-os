"""Public shell concurrency scaling probes."""

from __future__ import annotations

import time

import pytest

from .._harness.concurrency import gather_with_barrier
from .._harness.integrated_cases import (
    RuntimeCallMetric,
    assert_committed,
    assert_read,
    emit_metric,
    percentile,
    q,
    timed_call,
)
from .._harness.sandbox_fixture import SandboxHandle


pytestmark = pytest.mark.asyncio

_CONCURRENCY_LEVELS = (1, 5, 10, 20)


async def _run_shell_batch(
    handle: SandboxHandle,
    *,
    concurrency: int,
) -> tuple[list[RuntimeCallMetric], float, dict[str, str]]:
    expected: dict[str, str] = {}
    factories = []

    for index in range(concurrency):
        path = f"dist/scaling/c{concurrency:02d}-{index:02d}.txt"
        content = f"concurrency={concurrency} index={index}\n"
        expected[path] = content
        command = (
            "set -e; "
            "mkdir -p dist/scaling; "
            f"printf {q(content)} > {q(path)}"
        )

        async def run_shell(
            index: int = index,
            command: str = command,
        ):
            return await timed_call(
                f"scaling_shell_c{concurrency:02d}_{index:02d}",
                handle.tool.shell(
                    command,
                    timeout=45,
                    description=(
                        f"phase3 scaling shell c={concurrency} index={index}"
                    ),
                ),
            )

        factories.append(run_shell)

    batch_start = time.perf_counter()
    rows = await gather_with_barrier(factories)
    batch_wall_ms = (time.perf_counter() - batch_start) * 1000.0

    metrics: list[RuntimeCallMetric] = []
    for index, (result, metric) in enumerate(rows):
        path = f"dist/scaling/c{concurrency:02d}-{index:02d}.txt"
        assert_committed(result, path=path)
        assert result.exit_code == 0, result.stderr or result.stdout
        metrics.append(metric)

    return metrics, batch_wall_ms, expected


async def test_public_shell_parallel_factor_1_5_10_20(
    integrated_sandbox: SandboxHandle,
) -> None:
    ignore = await integrated_sandbox.tool.write_file(
        ".gitignore",
        "dist/\n",
        description="phase3 seed gitignore for shell scaling",
    )
    assert_committed(ignore, path=".gitignore")

    baseline_call_ms: float | None = None
    summaries: list[dict[str, float | int]] = []

    for concurrency in _CONCURRENCY_LEVELS:
        metrics, batch_wall_ms, expected = await _run_shell_batch(
            integrated_sandbox,
            concurrency=concurrency,
        )

        for path, content in expected.items():
            await assert_read(integrated_sandbox, path, content)

        call_wall_ms = [metric.elapsed_ms for metric in metrics]
        if concurrency == 1:
            baseline_call_ms = call_wall_ms[0]
        assert baseline_call_ms is not None

        serial_equivalent_ms = baseline_call_ms * concurrency
        parallel_factor = serial_equivalent_ms / batch_wall_ms
        parallel_efficiency = parallel_factor / concurrency
        throughput_ops_s = concurrency / (batch_wall_ms / 1000.0)

        summaries.append(
            {
                "concurrency": concurrency,
                "calls": len(metrics),
                "batch_wall_ms": round(batch_wall_ms, 3),
                "per_call_p50_ms": round(percentile(call_wall_ms, 50), 3),
                "per_call_p99_ms": round(percentile(call_wall_ms, 99), 3),
                "per_call_max_ms": round(max(call_wall_ms), 3),
                "serial_equivalent_ms": round(serial_equivalent_ms, 3),
                "parallel_factor": round(parallel_factor, 3),
                "parallel_efficiency": round(parallel_efficiency, 3),
                "throughput_ops_s": round(throughput_ops_s, 3),
            }
        )

    assert all(row["parallel_factor"] > 0 for row in summaries)
    assert all(row["throughput_ops_s"] > 0 for row in summaries)
    emit_metric(
        "concurrency_scaling.public_shell",
        {
            "levels": summaries,
            "baseline_call_ms": round(float(baseline_call_ms or 0.0), 3),
        },
    )
