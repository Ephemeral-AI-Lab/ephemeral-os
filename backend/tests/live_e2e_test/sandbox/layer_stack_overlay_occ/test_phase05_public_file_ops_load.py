"""Phase 05 public file-op load matrix for read/write/edit/shell/mixed."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

import pytest

from .._harness.concurrency import gather_with_barrier
from .._harness.integrated_cases import (
    RuntimeCallMetric,
    assert_committed,
    emit_metric,
    q,
    summarize_calls,
    timed_call,
)
from .._harness.phase05_public_file_ops import (
    CONCURRENCIES,
    env_float,
    phase05_call_row,
    phase05_summary_row,
    public_reconcile,
    seed_phase05_imported_base,
    write_phase05_jsonl_artifact,
)
from .._harness.sandbox_fixture import SandboxHandle


pytestmark = pytest.mark.asyncio

_Factory = Callable[[], Awaitable[tuple[object, RuntimeCallMetric]]]

_C20_BUDGETS_MS = {
    "read_file": {"batch": 5000.0, "wall_p99": 3000.0, "runtime_p99": 1000.0},
    "write_file": {"batch": 8000.0, "wall_p99": 5000.0, "runtime_p99": 2500.0},
    "edit_file": {"batch": 8000.0, "wall_p99": 5000.0, "runtime_p99": 2500.0},
    "shell": {"batch": 12000.0, "wall_p99": 7000.0, "runtime_p99": 4000.0},
    "mixed": {"batch": 12000.0, "wall_p99": 7000.0, "runtime_p99": 4000.0},
}


async def test_phase05_public_file_ops_load_matrix_c1_c5_c10_c20(
    workspace_base_sandbox: SandboxHandle,
) -> None:
    handle = workspace_base_sandbox
    binding = await seed_phase05_imported_base(handle)
    await _precommit_read_targets(handle)

    rows: list[dict[str, object]] = []
    all_metrics: list[RuntimeCallMetric] = []

    for workload in ("read_file", "write_file", "edit_file", "shell", "mixed"):
        for concurrency in CONCURRENCIES:
            expected: dict[str, str] = {}
            factories = _factories_for(
                handle,
                workload=workload,
                concurrency=concurrency,
                expected=expected,
            )
            started = time.perf_counter()
            results = await gather_with_barrier(factories)
            batch_wall_ms = (time.perf_counter() - started) * 1000.0
            metrics = [metric for _, metric in results]
            all_metrics.extend(metrics)

            assert len(results) == concurrency
            for result, metric in results:
                _assert_load_result(result, metric)
                assert metric.timings, metric
            await public_reconcile(handle, expected)

            correctness = {
                "all_calls_accounted": len(results) == concurrency,
                "all_expected_paths_visible": True,
                "unexpected_conflicts": sum(
                    1 for metric in metrics if metric.conflict_reason
                ),
                "final_reconciliation": True,
            }
            pass_bars = _pass_bars(workload)
            summary = phase05_summary_row(
                case=workload,
                binding=binding,
                concurrency=concurrency,
                metrics=metrics,
                batch_wall_ms=batch_wall_ms,
                correctness=correctness,
                pass_bars=pass_bars,
            )
            _assert_load_summary(summary, workload=workload, concurrency=concurrency)
            rows.append(summary)
            rows.extend(
                phase05_call_row(
                    case=workload,
                    metric=metric,
                    concurrency=concurrency,
                )
                for metric in metrics
            )
            emit_metric(
                f"phase05.public_file_ops.load.{workload}.c{concurrency}",
                {
                    **summarize_calls(metrics),
                    "batch_wall_ms": summary["batch_wall_ms"],
                    "runtime_p99_ms": summary["runtime_p99_ms"],
                    "parallel_factor": summary["parallel_factor"],
                    "parallel_efficiency": summary["parallel_efficiency"],
                    "throughput_ops_s": summary["throughput_ops_s"],
                },
            )

    artifact = write_phase05_jsonl_artifact(case="load_matrix", rows=rows)
    print(f"\n[phase05:public_file_ops_load] artifact={artifact}")
    emit_metric(
        "phase05.public_file_ops.load_matrix",
        {
            **summarize_calls(all_metrics),
            "artifact": str(artifact),
        },
    )


async def _precommit_read_targets(handle: SandboxHandle) -> None:
    for index in range(20):
        path = f"tracked/load/committed/read-{index:02d}.txt"
        result = await handle.tool.write_file(
            path,
            f"committed-read-{index:02d}\n",
            description=f"phase05 load setup committed read target {index:02d}",
        )
        assert_committed(result, path=path)


def _factories_for(
    handle: SandboxHandle,
    *,
    workload: str,
    concurrency: int,
    expected: dict[str, str],
) -> list[_Factory]:
    if workload == "read_file":
        return _read_factories(handle, concurrency=concurrency)
    if workload == "write_file":
        return _write_factories(handle, concurrency=concurrency, expected=expected)
    if workload == "edit_file":
        return _edit_factories(handle, concurrency=concurrency, expected=expected)
    if workload == "shell":
        return _shell_factories(handle, concurrency=concurrency, expected=expected)
    if workload == "mixed":
        return _mixed_factories(handle, concurrency=concurrency, expected=expected)
    raise AssertionError(f"unknown workload: {workload}")


def _read_factories(handle: SandboxHandle, *, concurrency: int) -> list[_Factory]:
    factories: list[_Factory] = []
    for index in range(concurrency):
        if index % 2 == 0:
            path = f"tracked/load/read/read-{index:02d}.txt"
        else:
            path = f"tracked/load/committed/read-{index:02d}.txt"

        async def run(index: int = index, path: str = path):
            return await timed_call(
                f"phase05_load_read_c{concurrency:02d}_{index:02d}",
                handle.tool.read_file(path),
            )

        factories.append(run)
    return factories


def _write_factories(
    handle: SandboxHandle,
    *,
    concurrency: int,
    expected: dict[str, str],
) -> list[_Factory]:
    factories: list[_Factory] = []
    for index in range(concurrency):
        path = f"tracked/load/write/c{concurrency:02d}-{index:02d}.txt"
        content = f"write-c{concurrency:02d}-{index:02d}\n"
        expected[path] = content

        async def run(index: int = index, path: str = path, content: str = content):
            return await timed_call(
                f"phase05_load_write_c{concurrency:02d}_{index:02d}",
                handle.tool.write_file(
                    path,
                    content,
                    description=f"phase05 load write c={concurrency} i={index}",
                ),
            )

        factories.append(run)
    return factories


def _edit_factories(
    handle: SandboxHandle,
    *,
    concurrency: int,
    expected: dict[str, str],
) -> list[_Factory]:
    factories: list[_Factory] = []
    for index in range(concurrency):
        path = f"tracked/load/edit/c{concurrency:02d}-{index:02d}.txt"
        old = f"edit-base-c{concurrency:02d}-{index:02d}"
        new = f"edit-c{concurrency:02d}-{index:02d}"
        expected[path] = f"{new}\n"

        async def run(
            index: int = index,
            path: str = path,
            old: str = old,
            new: str = new,
        ):
            return await timed_call(
                f"phase05_load_edit_c{concurrency:02d}_{index:02d}",
                handle.tool.edit_file(
                    path,
                    [(old, new)],
                    description=f"phase05 load edit c={concurrency} i={index}",
                ),
            )

        factories.append(run)
    return factories


def _shell_factories(
    handle: SandboxHandle,
    *,
    concurrency: int,
    expected: dict[str, str],
) -> list[_Factory]:
    factories: list[_Factory] = []
    for index in range(concurrency):
        read_path = f"tracked/load/read/read-{index:02d}.txt"
        write_path = f"tracked/load/shell/c{concurrency:02d}-{index:02d}.txt"
        content = f"shell-c{concurrency:02d}-{index:02d}\n"
        expected[write_path] = content
        command = (
            "set -e; "
            f"grep -q {q(f'read-base-{index:02d}')} {q(read_path)}; "
            "mkdir -p tracked/load/shell; "
            f"printf {q(content)} > {q(write_path)}"
        )

        async def run(index: int = index, command: str = command):
            return await timed_call(
                f"phase05_load_shell_c{concurrency:02d}_{index:02d}",
                handle.tool.shell(
                    command,
                    timeout=30,
                    description=f"phase05 load shell c={concurrency} i={index}",
                ),
            )

        factories.append(run)
    return factories


def _mixed_factories(
    handle: SandboxHandle,
    *,
    concurrency: int,
    expected: dict[str, str],
) -> list[_Factory]:
    pattern = ["read", "read", "edit", "write", "read", "edit", "shell", "write"]
    operations = [pattern[index % len(pattern)] for index in range(concurrency)]
    factories: list[_Factory] = []
    read_slot = 0
    edit_slot = 0
    write_slot = 0
    shell_slot = 0

    for index, operation in enumerate(operations):
        if operation == "read":
            path = f"tracked/load/read/read-{read_slot:02d}.txt"
            read_slot += 1

            async def run_read(index: int = index, path: str = path):
                return await timed_call(
                    f"phase05_load_mixed_c{concurrency:02d}_read_{index:02d}",
                    handle.tool.read_file(path),
                )

            factories.append(run_read)
            continue

        if operation == "edit":
            path = f"tracked/load/mixed/edit-c{concurrency:02d}-{edit_slot:02d}.txt"
            old = f"mixed-edit-base-c{concurrency:02d}-{edit_slot:02d}"
            new = f"mixed-edit-c{concurrency:02d}-{edit_slot:02d}"
            expected[path] = f"{new}\n"
            edit_slot += 1

            async def run_edit(
                index: int = index,
                path: str = path,
                old: str = old,
                new: str = new,
            ):
                return await timed_call(
                    f"phase05_load_mixed_c{concurrency:02d}_edit_{index:02d}",
                    handle.tool.edit_file(
                        path,
                        [(old, new)],
                        description=f"phase05 mixed edit c={concurrency} i={index}",
                    ),
                )

            factories.append(run_edit)
            continue

        if operation == "write":
            path = f"tracked/load/mixed/write-c{concurrency:02d}-{write_slot:02d}.txt"
            content = f"mixed-write-c{concurrency:02d}-{write_slot:02d}\n"
            expected[path] = content
            write_slot += 1

            async def run_write(
                index: int = index,
                path: str = path,
                content: str = content,
            ):
                return await timed_call(
                    f"phase05_load_mixed_c{concurrency:02d}_write_{index:02d}",
                    handle.tool.write_file(
                        path,
                        content,
                        description=f"phase05 mixed write c={concurrency} i={index}",
                    ),
                )

            factories.append(run_write)
            continue

        path = f"tracked/load/mixed/shell-c{concurrency:02d}-{shell_slot:02d}.txt"
        content = f"mixed-shell-c{concurrency:02d}-{shell_slot:02d}\n"
        expected[path] = content
        shell_slot += 1
        command = (
            "set -e; "
            "mkdir -p tracked/load/mixed; "
            f"cat {q('tracked/load/read/read-00.txt')} >/dev/null; "
            f"printf {q(content)} > {q(path)}"
        )

        async def run_shell(index: int = index, command: str = command):
            return await timed_call(
                f"phase05_load_mixed_c{concurrency:02d}_shell_{index:02d}",
                handle.tool.shell(
                    command,
                    timeout=30,
                    description=f"phase05 mixed shell c={concurrency} i={index}",
                ),
            )

        factories.append(run_shell)

    return factories


def _assert_load_result(result: object, metric: RuntimeCallMetric) -> None:
    if metric.op == "read_file":
        assert result.success  # type: ignore[attr-defined]
        assert result.exists  # type: ignore[attr-defined]
        return
    assert_committed(result)  # type: ignore[arg-type]


def _assert_load_summary(
    summary: dict[str, object],
    *,
    workload: str,
    concurrency: int,
) -> None:
    assert float(summary["parallel_factor"]) > 0
    assert float(summary["throughput_ops_s"]) > 0
    correctness = summary["correctness"]
    assert isinstance(correctness, dict)
    assert correctness["all_calls_accounted"] is True
    assert correctness["all_expected_paths_visible"] is True
    assert correctness["unexpected_conflicts"] == 0
    assert correctness["final_reconciliation"] is True

    if concurrency != 20:
        return
    bars = _pass_bars(workload)
    assert float(summary["batch_wall_ms"]) <= float(bars["c20_batch_wall_budget_ms"])
    assert float(summary["per_call_wall_p99_ms"]) <= float(
        bars["c20_wall_p99_budget_ms"]
    )
    assert float(summary["runtime_p99_ms"]) <= float(bars["c20_runtime_p99_budget_ms"])


def _pass_bars(workload: str) -> dict[str, float]:
    defaults = _C20_BUDGETS_MS[workload]
    workload_key = workload.removesuffix("_file").upper()
    return {
        "c20_batch_wall_budget_ms": env_float(
            f"EPHEMERALOS_PHASE05_{workload_key}_C20_BATCH_WALL_BUDGET_MS",
            defaults["batch"],
        ),
        "c20_wall_p99_budget_ms": env_float(
            "EPHEMERALOS_PHASE05_C20_WALL_P99_BUDGET_MS",
            defaults["wall_p99"],
        ),
        "c20_runtime_p99_budget_ms": env_float(
            "EPHEMERALOS_PHASE05_C20_RUNTIME_P99_BUDGET_MS",
            defaults["runtime_p99"],
        ),
    }
