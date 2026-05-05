"""Public sandbox API concurrency scaling probes."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

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
_Runner = Callable[[SandboxHandle, int], Awaitable[tuple[list[RuntimeCallMetric], float]]]


async def _run_read_concurrency_level(
    handle: SandboxHandle,
    concurrency: int,
) -> tuple[list[RuntimeCallMetric], float]:
    factories = []
    expected: dict[str, str] = {}

    for index in range(concurrency):
        path = f"tracked/scaling/read/c{concurrency:02d}-{index:02d}.txt"
        content = f"read concurrency={concurrency} index={index}\n"
        expected[path] = content
        seeded = await handle.tool.write_file(
            path,
            content,
            description=f"seed read scaling c={concurrency} index={index}",
        )
        assert_committed(seeded, path=path)

        async def run_read(
            index: int = index,
            path: str = path,
        ):
            return await timed_call(
                f"scaling_read_c{concurrency:02d}_{index:02d}",
                handle.tool.read_file(path),
            )

        factories.append(run_read)

    batch_start = time.perf_counter()
    rows = await gather_with_barrier(factories)
    batch_wall_ms = (time.perf_counter() - batch_start) * 1000.0

    metrics: list[RuntimeCallMetric] = []
    for index, (result, metric) in enumerate(rows):
        path = f"tracked/scaling/read/c{concurrency:02d}-{index:02d}.txt"
        assert result.success
        assert result.exists
        assert result.content == expected[path]
        metrics.append(metric)

    return metrics, batch_wall_ms


async def _run_write_concurrency_level(
    handle: SandboxHandle,
    concurrency: int,
) -> tuple[list[RuntimeCallMetric], float]:
    factories = []
    expected: dict[str, str] = {}

    for index in range(concurrency):
        path = f"tracked/scaling/write/c{concurrency:02d}-{index:02d}.txt"
        content = f"write concurrency={concurrency} index={index}\n"
        expected[path] = content

        async def run_write(
            index: int = index,
            path: str = path,
            content: str = content,
        ):
            return await timed_call(
                f"scaling_write_c{concurrency:02d}_{index:02d}",
                handle.tool.write_file(
                    path,
                    content,
                    description=f"write scaling c={concurrency} index={index}",
                ),
            )

        factories.append(run_write)

    batch_start = time.perf_counter()
    rows = await gather_with_barrier(factories)
    batch_wall_ms = (time.perf_counter() - batch_start) * 1000.0

    metrics: list[RuntimeCallMetric] = []
    for index, (result, metric) in enumerate(rows):
        path = f"tracked/scaling/write/c{concurrency:02d}-{index:02d}.txt"
        assert_committed(result, path=path)
        await assert_read(handle, path, expected[path])
        metrics.append(metric)

    return metrics, batch_wall_ms


async def _run_edit_concurrency_level(
    handle: SandboxHandle,
    concurrency: int,
) -> tuple[list[RuntimeCallMetric], float]:
    factories = []
    expected: dict[str, str] = {}

    for index in range(concurrency):
        path = f"tracked/scaling/edit/c{concurrency:02d}-{index:02d}.txt"
        old = f"edit concurrency={concurrency} index={index} old\n"
        new = f"edit concurrency={concurrency} index={index} new\n"
        expected[path] = new
        seeded = await handle.tool.write_file(
            path,
            old,
            description=f"seed edit scaling c={concurrency} index={index}",
        )
        assert_committed(seeded, path=path)

        async def run_edit(
            index: int = index,
            path: str = path,
            old: str = old,
            new: str = new,
        ):
            return await timed_call(
                f"scaling_edit_c{concurrency:02d}_{index:02d}",
                handle.tool.edit_file(
                    path,
                    [(old, new)],
                    description=f"edit scaling c={concurrency} index={index}",
                ),
            )

        factories.append(run_edit)

    batch_start = time.perf_counter()
    rows = await gather_with_barrier(factories)
    batch_wall_ms = (time.perf_counter() - batch_start) * 1000.0

    metrics: list[RuntimeCallMetric] = []
    for index, (result, metric) in enumerate(rows):
        path = f"tracked/scaling/edit/c{concurrency:02d}-{index:02d}.txt"
        assert_committed(result, path=path)
        assert result.applied_edits == 1
        await assert_read(handle, path, expected[path])
        metrics.append(metric)

    return metrics, batch_wall_ms


async def _run_shell_concurrency_level(
    handle: SandboxHandle,
    concurrency: int,
) -> tuple[list[RuntimeCallMetric], float]:
    factories = []
    expected: dict[str, str] = {}

    for index in range(concurrency):
        path = f"dist/scaling/shell/c{concurrency:02d}-{index:02d}.txt"
        content = f"shell concurrency={concurrency} index={index}\n"
        expected[path] = content
        command = (
            "set -e; "
            "mkdir -p dist/scaling/shell; "
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
                    description=f"shell scaling c={concurrency} index={index}",
                ),
            )

        factories.append(run_shell)

    batch_start = time.perf_counter()
    rows = await gather_with_barrier(factories)
    batch_wall_ms = (time.perf_counter() - batch_start) * 1000.0

    metrics: list[RuntimeCallMetric] = []
    for index, (result, metric) in enumerate(rows):
        path = f"dist/scaling/shell/c{concurrency:02d}-{index:02d}.txt"
        assert_committed(result, path=path)
        assert result.exit_code == 0, result.stderr or result.stdout
        await assert_read(handle, path, expected[path])
        metrics.append(metric)

    return metrics, batch_wall_ms


async def _measure_concurrency_levels(
    handle: SandboxHandle,
    *,
    api_name: str,
    runner: _Runner,
) -> dict[str, object]:
    baseline_call_ms: float | None = None
    summaries: list[dict[str, float | int]] = []

    for concurrency in _CONCURRENCY_LEVELS:
        metrics, batch_wall_ms = await runner(handle, concurrency)
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
    return {
        "levels": summaries,
        "baseline_call_ms": round(float(baseline_call_ms or 0.0), 3),
        "api": api_name,
    }


async def test_public_api_parallel_factor_1_5_10_20(
    integrated_sandbox: SandboxHandle,
) -> None:
    ignore = await integrated_sandbox.tool.write_file(
        ".gitignore",
        "dist/\n",
        description="seed gitignore for public api scaling",
    )
    assert_committed(ignore, path=".gitignore")

    op_summaries = {
        "read_file": await _measure_concurrency_levels(
            integrated_sandbox,
            api_name="read_file",
            runner=_run_read_concurrency_level,
        ),
        "write_file": await _measure_concurrency_levels(
            integrated_sandbox,
            api_name="write_file",
            runner=_run_write_concurrency_level,
        ),
        "edit_file": await _measure_concurrency_levels(
            integrated_sandbox,
            api_name="edit_file",
            runner=_run_edit_concurrency_level,
        ),
        "shell": await _measure_concurrency_levels(
            integrated_sandbox,
            api_name="shell",
            runner=_run_shell_concurrency_level,
        ),
    }
    emit_metric(
        "concurrency_scaling.public_api",
        {
            "levels": op_summaries,
        },
    )
