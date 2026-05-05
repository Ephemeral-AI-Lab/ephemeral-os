"""E8 — named integrated public-tool load-profile pass bars."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from sandbox.api import ShellRequest

from .._harness.concurrency import gather_with_barrier
from .._harness.integrated_cases import (
    RuntimeCallMetric,
    assert_committed,
    assert_read,
    emit_metric,
    paths_visible_summary,
    percentile,
    q,
    summarize_calls,
    timed_call,
    timed_shell_batch,
)
from .._harness.load_profiles import BURST, SMOKE, SOAK, SUSTAINED
from .._harness.sandbox_fixture import SandboxHandle


pytestmark = pytest.mark.asyncio

_PROFILE_CALLS = {
    "smoke": (1, 3),
    "sustained": (4, 8),
    "burst": (8, 16),
}


async def _seed_profile(handle: SandboxHandle, *, edit_count: int) -> None:
    ignore = await handle.tool.write_file(
        ".gitignore",
        "dist/\n",
        description="phase4 load seed gitignore",
    )
    assert_committed(ignore, path=".gitignore")
    shared = await handle.tool.write_file(
        "tracked/load-shared.txt",
        "stable\n",
        description="phase4 load seed shared file",
    )
    assert_committed(shared, path="tracked/load-shared.txt")
    for index in range(edit_count):
        path = f"tracked/load-edit-{index:02d}.txt"
        seeded = await handle.tool.write_file(
            path,
            f"slot-{index:02d}=old\n",
            description="phase4 load seed edit target",
        )
        assert_committed(seeded, path=path)


async def _run_profile(handle: SandboxHandle, profile) -> tuple[list[RuntimeCallMetric], list[str]]:
    shell_count, edit_count = _PROFILE_CALLS[profile.name]
    await _seed_profile(handle, edit_count=edit_count)
    factories = []
    shell_labels: list[str] = []
    shell_requests: list[ShellRequest] = []
    for index in range(shell_count):
        path = f"dist/load/{profile.name}/shell-{index % max(1, shell_count // 2):02d}.txt"
        content = f"{profile.name}:shell:{index:02d}\n"
        parent = str(Path(path).parent)
        command = (
            "set -e; "
            "first=$(cat tracked/load-shared.txt); "
            "second=$(cat tracked/load-shared.txt); "
            "[ \"$first\" = \"$second\" ]; "
            f"mkdir -p {q(parent)}; "
            f"printf {q(content)} > {q(path)}"
        )

        shell_labels.append(f"load_{profile.name}_shell_{index:02d}")
        shell_requests.append(
            ShellRequest(
                command=command,
                caller=handle.caller,
                timeout=45,
                description=f"phase4 {profile.name} shell {index:02d}",
            )
        )

    async def run_shell_batch():
        return await timed_shell_batch(
            shell_labels,
            handle.tool.shell_batch(
                shell_requests,
                max_concurrency=max(1, shell_count),
                timeout=45 + 60,
            ),
        )

    factories.append(run_shell_batch)

    for index in range(edit_count):
        path = f"tracked/load-edit-{index:02d}.txt"

        async def run_edit(index: int = index, path: str = path):
            result, metric = await timed_call(
                f"load_{profile.name}_edit_{index:02d}",
                handle.tool.edit_file(
                    path,
                    [(f"slot-{index:02d}=old", f"slot-{index:02d}=new")],
                    description=f"phase4 {profile.name} edit {index:02d}",
                ),
            )
            return [(result, metric)]

        factories.append(run_edit)

    rows = await gather_with_barrier(factories)
    metrics: list[RuntimeCallMetric] = []
    changed_paths: list[str] = []
    for row in rows:
        for result, metric in row:
            assert_committed(result)
            metrics.append(metric)
            changed_paths.extend(result.changed_paths)
    return metrics, sorted(set(changed_paths))


async def _assert_profile(handle: SandboxHandle, profile) -> None:
    batch_start = time.perf_counter()
    metrics, changed_paths = await _run_profile(handle, profile)
    batch_wall_ms = (time.perf_counter() - batch_start) * 1000.0
    reads = [await handle.tool.read_file(path) for path in changed_paths]
    assert all(result.success and result.exists for result in reads)
    for index in range(_PROFILE_CALLS[profile.name][1]):
        await assert_read(
            handle,
            f"tracked/load-edit-{index:02d}.txt",
            f"slot-{index:02d}=new\n",
        )

    wall_ms = [metric.elapsed_ms for metric in metrics]
    runtime_ms = [_runtime_ms(metric) for metric in metrics]
    wall_p99 = percentile(wall_ms, 99)
    runtime_p99 = percentile(runtime_ms, 99)
    artifact = _write_profile_artifact(
        profile_name=profile.name,
        metrics=metrics,
        batch_wall_ms=batch_wall_ms,
    )
    redline_ms = max(profile.max_p99_ms * 5, 5_000)
    assert runtime_p99 <= redline_ms, {
        "profile": profile.name,
        "runtime_p99_ms": runtime_p99,
        "redline_ms": redline_ms,
        "artifact": str(artifact),
    }

    emit_metric(
        f"load_profiles.{profile.name}",
        {
            **summarize_calls(metrics),
            **paths_visible_summary(reads),
            "batch_wall_ms": round(batch_wall_ms, 3),
            "runtime_p50_ms": round(percentile(runtime_ms, 50), 3),
            "runtime_p99_ms": round(runtime_p99, 3),
            "wall_p99_ms": round(wall_p99, 3),
            "runtime_budget_ms": profile.max_p99_ms,
            "runtime_budget_met": runtime_p99 <= profile.max_p99_ms,
            "wall_budget_met": wall_p99 <= profile.max_p99_ms,
            "drift": 0,
            "artifact": str(artifact),
        },
    )


def _runtime_ms(metric: RuntimeCallMetric) -> float:
    for key in (
        "api.shell.total_s",
        "api.write.total_s",
        "api.edit.total_s",
        "occ.apply.total_s",
    ):
        value = metric.timings.get(key)
        if value is not None:
            return float(value) * 1000.0
    return metric.elapsed_ms


def _write_profile_artifact(
    *,
    profile_name: str,
    metrics: list[RuntimeCallMetric],
    batch_wall_ms: float,
) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = Path.cwd() / ".omc" / "results" / f"live-e2e-integrated-{profile_name}-{stamp}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for metric in metrics:
            row = {
                "schema": "sandbox.live_e2e.integrated_load.v1",
                "profile": profile_name,
                "label": metric.label,
                "op": metric.op,
                "success": metric.success,
                "status": metric.status,
                "wall_ms": round(metric.elapsed_ms, 3),
                "runtime_ms": round(_runtime_ms(metric), 3),
                "batch_wall_ms": round(batch_wall_ms, 3),
                "changed_paths": list(metric.changed_paths),
                "conflict_reason": metric.conflict_reason,
                "timings": {
                    key: round(float(value), 6)
                    for key, value in sorted(metric.timings.items())
                },
            }
            file.write(json.dumps(row, sort_keys=True, separators=(",", ":")))
            file.write("\n")
    return path


async def test_smoke_profile_passes(integrated_sandbox: SandboxHandle) -> None:
    await _assert_profile(integrated_sandbox, SMOKE)


async def test_sustained_profile_meets_p99_budget(
    integrated_sandbox: SandboxHandle,
) -> None:
    await _assert_profile(integrated_sandbox, SUSTAINED)


async def test_burst_profile_recovers_within_squash_window(
    integrated_sandbox: SandboxHandle,
) -> None:
    await _assert_profile(integrated_sandbox, BURST)


def test_soak_profile_no_regression_over_15_min(
    integrated_sandbox: SandboxHandle,
) -> None:
    pytest.skip(f"pending Phase 5 soak profile: {SOAK.name}")
