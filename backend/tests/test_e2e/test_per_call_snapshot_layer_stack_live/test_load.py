"""Daytona-backed sandbox API load coverage."""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import pytest

from sandbox.api import SearchReplaceEdit

from .conftest import (
    LiveSnapshotSandbox,
    barrier_overlay,
    make_workdir,
    print_live_metric,
    read_live_file,
    run_live_command,
    write_live_file,
)

pytestmark = [pytest.mark.e2e, pytest.mark.live, pytest.mark.asyncio]

LOAD_LEVELS = (1, 5, 10, 20)
VERBOSE_OP_TIMINGS = os.environ.get("EOS_SANDBOX_API_LOAD_VERBOSE_OPS") == "1"


@dataclass(frozen=True)
class OperationSample:
    index: int
    operation: str
    success: bool
    status: str
    elapsed_s: float
    timings: Mapping[str, float]


@dataclass(frozen=True)
class LoadBatchReport:
    label: str
    concurrency: int
    wall_elapsed_s: float
    samples: tuple[OperationSample, ...]
    stats: Mapping[str, float]
    parallel_factor: float

    @property
    def successes(self) -> int:
        return sum(1 for sample in self.samples if sample.success)

    @property
    def failures(self) -> int:
        return len(self.samples) - self.successes


async def test_daytona_read_api_load(live_snapshot_sandbox: LiveSnapshotSandbox) -> None:
    workdir = await make_workdir(live_snapshot_sandbox, "load-read")
    seen: set[int] = set()
    for level in _load_levels():
        for index in range(level):
            await write_live_file(
                live_snapshot_sandbox,
                f"{workdir}/read/{level}/{index}.txt",
                f"read-{level}-{index}\n",
                label=f"load.read.seed.{level}.{index}",
            )

        async def op(index: int):
            return await live_snapshot_sandbox.read_file(
                path=f"{workdir}/read/{level}/{index}.txt",
                actor=live_snapshot_sandbox.actor(f"load.read.{level}.{index}"),
            )

        report = await _run_load_batch(
            live_snapshot_sandbox,
            label="read",
            concurrency=level,
            operation=op,
        )
        _assert_all_success(report)
        _assert_timing_keys(report, ("api.read.total_s",))
        seen.add(level)
    assert seen == set(_load_levels())


async def test_daytona_write_api_load(live_snapshot_sandbox: LiveSnapshotSandbox) -> None:
    workdir = await make_workdir(live_snapshot_sandbox, "load-write")
    seen: set[int] = set()
    for level in _load_levels():
        async def op(index: int):
            return await live_snapshot_sandbox.write_file(
                path=f"{workdir}/write/{level}/{index}.txt",
                content=f"write-{level}-{index}\n",
                actor=live_snapshot_sandbox.actor(f"load.write.{level}.{index}"),
                description=f"load.write.{level}.{index}",
            )

        report = await _run_load_batch(
            live_snapshot_sandbox,
            label="write",
            concurrency=level,
            operation=op,
        )
        _assert_all_success(report)
        _assert_timing_keys(report, ("api.write.total_s", "occ.commit.total_s"))
        for index in range(level):
            assert await read_live_file(
                live_snapshot_sandbox,
                f"{workdir}/write/{level}/{index}.txt",
                label=f"load.write.verify.{level}.{index}",
            ) == f"write-{level}-{index}\n"
        await live_snapshot_sandbox.compact(max_depth=4)
        seen.add(level)
    assert seen == set(_load_levels())


async def test_daytona_edit_api_load(live_snapshot_sandbox: LiveSnapshotSandbox) -> None:
    workdir = await make_workdir(live_snapshot_sandbox, "load-edit")
    seen: set[int] = set()
    for level in _load_levels():
        for index in range(level):
            await write_live_file(
                live_snapshot_sandbox,
                f"{workdir}/edit/{level}/{index}.txt",
                f"name = 'base-{index}'\n",
                label=f"load.edit.seed.{level}.{index}",
            )

        async def op(index: int):
            return await live_snapshot_sandbox.edit_file(
                path=f"{workdir}/edit/{level}/{index}.txt",
                edits=(
                    SearchReplaceEdit(
                        old_text=f"base-{index}",
                        new_text=f"edited-{index}",
                    ),
                ),
                actor=live_snapshot_sandbox.actor(f"load.edit.{level}.{index}"),
                description=f"load.edit.{level}.{index}",
            )

        report = await _run_load_batch(
            live_snapshot_sandbox,
            label="edit",
            concurrency=level,
            operation=op,
        )
        _assert_all_success(report)
        _assert_timing_keys(report, ("api.edit.total_s", "occ.commit.total_s"))
        for index in range(level):
            assert await read_live_file(
                live_snapshot_sandbox,
                f"{workdir}/edit/{level}/{index}.txt",
                label=f"load.edit.verify.{level}.{index}",
            ) == f"name = 'edited-{index}'\n"
        await live_snapshot_sandbox.compact(max_depth=4)
        seen.add(level)
    assert seen == set(_load_levels())


async def test_daytona_shell_api_load(live_snapshot_sandbox: LiveSnapshotSandbox) -> None:
    workdir = await make_workdir(live_snapshot_sandbox, "load-shell")
    seen: set[int] = set()
    for level in _load_levels():
        async def op(index: int):
            path = f"{workdir}/shell/{level}/{index}.txt"
            payload = f"shell-{level}-{index}\n"
            return await run_live_command(
                live_snapshot_sandbox,
                (
                    f"mkdir -p {shlex.quote(os.path.dirname(path))}; "
                    f"printf {shlex.quote(payload)} > {shlex.quote(path)}; "
                    f"cat {shlex.quote(path)}"
                ),
                timeout=30,
                label=f"load.shell.{level}.{index}",
            )

        report = await _run_load_batch(
            live_snapshot_sandbox,
            label="shell",
            concurrency=level,
            operation=op,
        )
        _assert_all_success(report)
        _assert_timing_keys(
            report,
            (
                "api.shell.total_s",
                "overlay.run_command_s",
                "overlay.capture_changes_s",
                "occ.commit.total_s",
            ),
        )
        for index in range(level):
            assert await read_live_file(
                live_snapshot_sandbox,
                f"{workdir}/shell/{level}/{index}.txt",
                label=f"load.shell.verify.{level}.{index}",
            ) == f"shell-{level}-{index}\n"
        await live_snapshot_sandbox.compact(max_depth=4)
        seen.add(level)
    assert seen == set(_load_levels())


async def test_daytona_mixed_edit_write_shell_load(
    live_snapshot_sandbox: LiveSnapshotSandbox,
) -> None:
    workdir = await make_workdir(live_snapshot_sandbox, "load-mixed")
    seen: set[int] = set()
    for level in _load_levels():
        for index in range(level):
            if index % 3 == 0:
                await write_live_file(
                    live_snapshot_sandbox,
                    f"{workdir}/mixed/{level}/{index}/edit.txt",
                    f"state = 'base-{index}'\n",
                    label=f"load.mixed.seed.{level}.{index}",
                )

        async def op(index: int):
            operation_kind = index % 3
            if operation_kind == 0:
                return await live_snapshot_sandbox.edit_file(
                    path=f"{workdir}/mixed/{level}/{index}/edit.txt",
                    edits=(
                        SearchReplaceEdit(
                            old_text=f"base-{index}",
                            new_text=f"edited-{index}",
                        ),
                    ),
                    actor=live_snapshot_sandbox.actor(f"load.mixed.edit.{level}.{index}"),
                    description=f"load.mixed.edit.{level}.{index}",
                )
            if operation_kind == 1:
                return await live_snapshot_sandbox.write_file(
                    path=f"{workdir}/mixed/{level}/{index}/write.txt",
                    content=f"write-{level}-{index}\n",
                    actor=live_snapshot_sandbox.actor(f"load.mixed.write.{level}.{index}"),
                    description=f"load.mixed.write.{level}.{index}",
                )
            shell_path = f"{workdir}/mixed/{level}/{index}/shell.txt"
            shell_payload = f"shell-{level}-{index}\n"
            return await run_live_command(
                live_snapshot_sandbox,
                (
                    f"mkdir -p {shlex.quote(os.path.dirname(shell_path))}; "
                    f"printf {shlex.quote(shell_payload)} > "
                    f"{shlex.quote(shell_path)}"
                ),
                timeout=30,
                label=f"load.mixed.shell.{level}.{index}",
            )

        report = await _run_load_batch(
            live_snapshot_sandbox,
            label="mixed",
            concurrency=level,
            operation=op,
        )
        _assert_all_success(report)
        _assert_any_timing_keys(
            report,
            _mixed_required_timing_keys(level),
        )
        await live_snapshot_sandbox.compact(max_depth=4)
        seen.add(level)
    assert seen == set(_load_levels())


async def test_daytona_shell_same_file_conflict_load(
    live_snapshot_sandbox: LiveSnapshotSandbox,
) -> None:
    workdir = await make_workdir(live_snapshot_sandbox, "load-conflict")
    for level in (3, 5):
        path = f"{workdir}/conflict/{level}/shared.txt"

        async def op(index: int):
            payload = f"winner-{index}\n"
            return await run_live_command(
                live_snapshot_sandbox,
                (
                    "sleep 0.1; "
                    f"mkdir -p {shlex.quote(os.path.dirname(path))}; "
                    f"printf {shlex.quote(payload)} > {shlex.quote(path)}"
                ),
                timeout=30,
                label=f"load.shell_conflict.{level}.{index}",
            )

        with barrier_overlay(live_snapshot_sandbox, parties=level):
            report = await _run_load_batch(
                live_snapshot_sandbox,
                label="shell_conflict",
                concurrency=level,
                operation=op,
            )
        successes = [sample for sample in report.samples if sample.success]
        conflicts = [sample for sample in report.samples if not sample.success]
        assert len(successes) == 1
        assert len(conflicts) == level - 1
        assert {sample.status for sample in conflicts} == {"aborted_version"}
        assert await read_live_file(
            live_snapshot_sandbox,
            path,
            label=f"load.shell_conflict.verify.{level}",
        ) in {f"winner-{index}\n" for index in range(level)}


async def test_daytona_layer_stack_compaction_after_load(
    live_snapshot_sandbox: LiveSnapshotSandbox,
) -> None:
    workdir = await make_workdir(live_snapshot_sandbox, "load-compact")
    level = max(_load_levels())
    for index in range(level):
        await write_live_file(
            live_snapshot_sandbox,
            f"{workdir}/compact/{index}.txt",
            f"compact-{index}\n",
            label=f"load.compact.write.{index}",
        )
    before = await live_snapshot_sandbox.layer_metrics()
    compacted = await live_snapshot_sandbox.compact(max_depth=4)
    after = await live_snapshot_sandbox.layer_metrics()
    print_live_metric(
        "load.compact.summary",
        before_depth=before["manifest_depth"],
        after_depth=after["manifest_depth"],
        compacted=compacted,
    )
    assert int(after["manifest_depth"]) <= 4
    for index in range(level):
        assert await read_live_file(
            live_snapshot_sandbox,
            f"{workdir}/compact/{index}.txt",
            label=f"load.compact.verify.{index}",
        ) == f"compact-{index}\n"


async def _run_load_batch(
    env: LiveSnapshotSandbox,
    *,
    label: str,
    concurrency: int,
    operation: Callable[[int], Awaitable[Any]],
) -> LoadBatchReport:
    start_event = asyncio.Event()
    samples: list[OperationSample] = []

    async def one(index: int) -> OperationSample:
        await start_event.wait()
        start = time.perf_counter()
        result = await operation(index)
        elapsed = time.perf_counter() - start
        return OperationSample(
            index=index,
            operation=label,
            success=bool(getattr(result, "success", False)),
            status=str(getattr(result, "status", "ok" if getattr(result, "success", False) else "")),
            elapsed_s=elapsed,
            timings=dict(getattr(result, "timings", {}) or {}),
        )

    metrics_before = await env.layer_metrics()
    tasks = [asyncio.create_task(one(index)) for index in range(concurrency)]
    batch_start = time.perf_counter()
    start_event.set()
    samples = list(await asyncio.gather(*tasks))
    wall_elapsed = time.perf_counter() - batch_start
    report = LoadBatchReport(
        label=label,
        concurrency=concurrency,
        wall_elapsed_s=wall_elapsed,
        samples=tuple(samples),
        stats=_elapsed_stats([sample.elapsed_s for sample in samples]),
        parallel_factor=(
            sum(sample.elapsed_s for sample in samples) / wall_elapsed
            if wall_elapsed > 0
            else 0.0
        ),
    )
    metrics_after = await env.layer_metrics()
    print_live_metric(
        "load.batch_done",
        label=label,
        concurrency=concurrency,
        wall_elapsed_s=wall_elapsed,
        parallel_factor=report.parallel_factor,
        successes=report.successes,
        failures=report.failures,
        stats=dict(report.stats),
        timing_stats=_timing_stats(report.samples),
        layer_stack_before=metrics_before,
        layer_stack_after=metrics_after,
    )
    assert report.parallel_factor > 0.0
    return report


def _load_levels() -> tuple[int, ...]:
    configured = os.environ.get("EOS_SANDBOX_API_LOAD_LEVELS")
    if configured:
        levels = tuple(
            int(part.strip())
            for part in configured.split(",")
            if part.strip()
        )
        if not levels or any(level < 1 for level in levels):
            raise ValueError("EOS_SANDBOX_API_LOAD_LEVELS must contain positive integers")
        return levels
    return LOAD_LEVELS


def _mixed_required_timing_keys(concurrency: int) -> tuple[str, ...]:
    keys: set[str] = set()
    operation_kinds = {index % 3 for index in range(concurrency)}
    if 0 in operation_kinds:
        keys.add("api.edit.total_s")
    if 1 in operation_kinds:
        keys.add("api.write.total_s")
    if 2 in operation_kinds:
        keys.add("api.shell.total_s")
    return tuple(sorted(keys))


def _assert_all_success(report: LoadBatchReport) -> None:
    assert report.failures == 0, _summary(report)
    assert report.successes == report.concurrency


def _assert_timing_keys(report: LoadBatchReport, required_keys: Sequence[str]) -> None:
    for sample in report.samples:
        missing = [key for key in required_keys if key not in sample.timings]
        assert not missing, (
            f"missing timing keys for {report.label}#{sample.index}: {missing}; "
            f"available={sorted(sample.timings)}"
        )


def _assert_any_timing_keys(report: LoadBatchReport, required_keys: Sequence[str]) -> None:
    available = {key for sample in report.samples for key in sample.timings}
    missing = [key for key in required_keys if key not in available]
    assert not missing, (
        f"missing timing keys for {report.label}: {missing}; "
        f"available={sorted(available)}"
    )


def _elapsed_stats(samples: Sequence[float]) -> dict[str, float]:
    ordered = sorted(float(sample) for sample in samples)
    if not ordered:
        return {"min": 0.0, "max": 0.0, "p50": 0.0, "p95": 0.0, "p99": 0.0}
    return {
        "min": ordered[0],
        "max": ordered[-1],
        "p50": _percentile(ordered, 0.50),
        "p95": _percentile(ordered, 0.95),
        "p99": _percentile(ordered, 0.99),
    }


def _timing_stats(samples: Sequence[OperationSample]) -> dict[str, dict[str, float]]:
    grouped: dict[str, list[float]] = {}
    for sample in samples:
        for key, value in sample.timings.items():
            grouped.setdefault(key, []).append(float(value))
    return {key: _elapsed_stats(values) for key, values in sorted(grouped.items())}


def _percentile(ordered: Sequence[float], percentile: float) -> float:
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * percentile))))
    return ordered[index]


def _summary(report: LoadBatchReport) -> str:
    return json.dumps(
        {
            "label": report.label,
            "concurrency": report.concurrency,
            "successes": report.successes,
            "failures": report.failures,
            "parallel_factor": report.parallel_factor,
            "statuses": [
                {
                    "index": sample.index,
                    "success": sample.success,
                    "status": sample.status,
                    "elapsed_s": sample.elapsed_s,
                    "timings": sample.timings if VERBOSE_OP_TIMINGS else sorted(sample.timings),
                }
                for sample in report.samples
            ],
        },
        indent=2,
        sort_keys=True,
    )
