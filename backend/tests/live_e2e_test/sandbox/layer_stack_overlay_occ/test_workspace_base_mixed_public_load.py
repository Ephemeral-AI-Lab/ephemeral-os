"""Mixed public API load profile over an imported workspace base."""

from __future__ import annotations

import os
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
    summarize_calls,
    timed_call,
)
from .._harness.sandbox_fixture import SandboxHandle
from .._harness.workspace_base_metrics import (
    base_summary,
    call_row,
    write_jsonl_artifact,
)
from .._harness.workspace_base_public import (
    seed_imported_base,
    selected_runtime_ms,
)


pytestmark = pytest.mark.asyncio

_Factory = Callable[[], Awaitable[tuple[object, RuntimeCallMetric]]]


async def test_workspace_base_mixed_public_api_profile_c1_c5_c10_c20(
    workspace_base_sandbox: SandboxHandle,
) -> None:
    handle = workspace_base_sandbox
    binding = await seed_imported_base(
        handle,
        _base_fixture_files(max_concurrency=20),
    )

    all_metrics: list[RuntimeCallMetric] = []
    rows: list[dict[str, object]] = []
    expected_content: dict[str, str] = {}
    timings: dict[str, float] = {}

    for concurrency in (1, 5, 10, 20):
        started = time.perf_counter()
        factories = _mixed_factories(
            handle,
            concurrency=concurrency,
            expected_content=expected_content,
        )
        results = await gather_with_barrier(factories)
        batch_wall_ms = (time.perf_counter() - started) * 1000.0
        metrics = [metric for _, metric in results]
        all_metrics.extend(metrics)

        for result, metric in results:
            if metric.op == "read_file":
                assert result.success  # type: ignore[attr-defined]
                assert result.exists  # type: ignore[attr-defined]
            else:
                assert_committed(result)  # type: ignore[arg-type]

        compact = await handle.tool.compact(max_depth=4)
        assert compact["success"] is True

        wall_values = [metric.elapsed_ms for metric in metrics]
        runtime_values = [selected_runtime_ms(metric) for metric in metrics]
        timings[f"phase01.mixed.c{concurrency}.batch_wall_s"] = (
            batch_wall_ms / 1000.0
        )
        timings[f"phase01.mixed.c{concurrency}.wall_p99_s"] = (
            percentile(wall_values, 99) / 1000.0
        )
        timings[f"phase01.mixed.c{concurrency}.runtime_p99_s"] = (
            percentile(runtime_values, 99) / 1000.0
        )
        rows.extend(
            call_row(
                case="mixed_public_api_profile",
                label=metric.label,
                success=metric.success,
                wall_ms=metric.elapsed_ms,
                runtime_ms=selected_runtime_ms(metric),
                timings=metric.timings,
                extra={
                    "concurrency": concurrency,
                    "op": metric.op,
                    "status": metric.status,
                    "changed_paths": list(metric.changed_paths),
                    "compact_after_depth": compact["after_depth"],
                },
            )
            for metric in metrics
        )
        emit_metric(
            f"workspace_base.mixed_public_api_c{concurrency}",
            {
                **summarize_calls(metrics),
                "batch_wall_ms": round(batch_wall_ms, 3),
                "runtime_p99_ms": round(percentile(runtime_values, 99), 3),
                "compact_before_depth": compact["before_depth"],
                "compact_after_depth": compact["after_depth"],
            },
        )

    for path, content in expected_content.items():
        await assert_read(handle, path, content)

    c10_p99_ms = timings["phase01.mixed.c10.wall_p99_s"] * 1000.0
    c20_p99_ms = timings["phase01.mixed.c20.wall_p99_s"] * 1000.0
    budget_ms = _env_float("EPHEMERALOS_PHASE01_MIXED_WALL_P99_BUDGET_MS", 10000.0)
    assert c10_p99_ms <= budget_ms
    assert c20_p99_ms <= budget_ms

    artifact = write_jsonl_artifact(
        case="mixed_public_api_profile",
        summary=base_summary(
            case="mixed_public_api_profile",
            binding=binding,
            workspace_inventory={
                "files": len(_base_fixture_files(max_concurrency=20)),
                "dirs": 0,
                "symlinks": 0,
                "bytes": 0,
                "sample_hashes": {},
            },
            timings=timings,
            pass_bars={
                "concurrencies": [1, 5, 10, 20],
                "periodic_compact": True,
                "final_reconciliation": True,
                "c10_c20_wall_p99_budget_ms": budget_ms,
            },
        ),
        rows=rows,
    )
    print(f"\n[phase01:mixed_public_api_profile] artifact={artifact}")


def _base_fixture_files(*, max_concurrency: int) -> dict[str, str]:
    files = {".gitignore": "dist/\n"}
    for index in range(max_concurrency * 2):
        files[f"tracked/mixed/read-{index:02d}.txt"] = f"read-base-{index:02d}\n"
    for concurrency in (1, 5, 10, 20):
        for index in range(max_concurrency):
            files[f"tracked/mixed/edit-c{concurrency:02d}-{index:02d}.txt"] = (
                f"edit-base-c{concurrency:02d}-{index:02d}\n"
            )
    return files


def _mixed_factories(
    handle: SandboxHandle,
    *,
    concurrency: int,
    expected_content: dict[str, str],
) -> list[_Factory]:
    operations = _operation_plan(concurrency)
    factories: list[_Factory] = []
    edit_slot = 0
    write_slot = 0
    shell_slot = 0
    read_slot = 0

    for index, operation in enumerate(operations):
        if operation == "read":
            path = f"tracked/mixed/read-{read_slot:02d}.txt"
            read_slot += 1

            async def run_read(index: int = index, path: str = path):
                return await timed_call(
                    f"mixed_c{concurrency:02d}_read_{index:02d}",
                    handle.tool.read_file(path),
                )

            factories.append(run_read)
            continue

        if operation == "write":
            path = f"tracked/mixed/write-c{concurrency:02d}-{write_slot:02d}.txt"
            content = f"write-c{concurrency:02d}-{write_slot:02d}\n"
            expected_content[path] = content
            write_slot += 1

            async def run_write(
                index: int = index,
                path: str = path,
                content: str = content,
            ):
                return await timed_call(
                    f"mixed_c{concurrency:02d}_write_{index:02d}",
                    handle.tool.write_file(
                        path,
                        content,
                        description=f"phase01 mixed write c={concurrency} i={index}",
                    ),
                )

            factories.append(run_write)
            continue

        if operation == "edit":
            path = f"tracked/mixed/edit-c{concurrency:02d}-{edit_slot:02d}.txt"
            old = f"edit-base-c{concurrency:02d}-{edit_slot:02d}"
            new = f"edit-c{concurrency:02d}-{edit_slot:02d}"
            expected_content[path] = f"{new}\n"
            edit_slot += 1

            async def run_edit(
                index: int = index,
                path: str = path,
                old: str = old,
                new: str = new,
            ):
                return await timed_call(
                    f"mixed_c{concurrency:02d}_edit_{index:02d}",
                    handle.tool.edit_file(
                        path,
                        [(old, new)],
                        description=f"phase01 mixed edit c={concurrency} i={index}",
                    ),
                )

            factories.append(run_edit)
            continue

        path = f"dist/mixed/shell-c{concurrency:02d}-{shell_slot:02d}.txt"
        content = f"shell-c{concurrency:02d}-{shell_slot:02d}\n"
        expected_content[path] = content
        shell_slot += 1
        command = f"mkdir -p dist/mixed; printf {q(content)} > {q(path)}"

        async def run_shell(
            index: int = index,
            command: str = command,
        ):
            return await timed_call(
                f"mixed_c{concurrency:02d}_shell_{index:02d}",
                handle.tool.shell(
                    command,
                    timeout=30,
                    description=f"phase01 mixed shell c={concurrency} i={index}",
                ),
            )

        factories.append(run_shell)

    return factories


def _operation_plan(concurrency: int) -> list[str]:
    pattern = ["read", "read", "write", "edit", "read", "shell", "write", "read"]
    operations = [pattern[index % len(pattern)] for index in range(concurrency)]
    if concurrency >= 5 and "shell" not in operations:
        operations[-1] = "shell"
    return operations


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return float(raw)
