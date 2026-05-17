"""Latency attribution sweep across the public sandbox API.

Runs each public verb (read, write, edit, shell) as **independent** agent calls
at multiple concurrency levels and emits per-call JSONL with the full timing
breakdown -- including the new instrumentation for the runtime boot, process
commit gate, flock wait, gitignore oracle, and overlay capture conversion.

Includes a no-op `: ` shell baseline so the fixed cost of one round trip
(provider exec + runtime boot + mount + gate + bash startup) can be subtracted
from the user-command cost in `overlay.run_command_s`.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

import pytest

from .._harness.concurrency import gather_with_barrier
from .._harness.integrated_cases import (
    RuntimeCallMetric,
    assert_committed,
    emit_metric,
    percentile,
    timed_call,
    write_timing_record,
)
from .._harness.sandbox_fixture import SandboxHandle


pytestmark = pytest.mark.asyncio

_CONCURRENCIES = (1, 4, 8, 16)
_Runner = Callable[[SandboxHandle, int], Awaitable[list[RuntimeCallMetric]]]


def _summary(label: str, metrics: list[RuntimeCallMetric]) -> dict[str, object]:
    elapsed = [m.elapsed_ms for m in metrics]
    return {
        "label": label,
        "calls": len(metrics),
        "wall_p50_ms": round(percentile(elapsed, 50), 3),
        "wall_p99_ms": round(percentile(elapsed, 99), 3),
        "wall_max_ms": round(max(elapsed, default=0.0), 3),
    }


def _stage_p99(metrics: list[RuntimeCallMetric], key: str) -> float:
    values = [m.timings.get(key, 0.0) * 1000.0 for m in metrics if key in m.timings]
    return round(percentile(values, 99), 3)


def _emit_anatomy(label: str, metrics: list[RuntimeCallMetric]) -> None:
    payload = _summary(label, metrics)
    payload["stage_p99_ms"] = {
        "runtime.boot_to_dispatch": _stage_p99(metrics, "runtime.boot_to_dispatch_s"),
        "runtime.dispatch": _stage_p99(metrics, "runtime.dispatch_s"),
        "api.shell.prepare": _stage_p99(metrics, "api.shell.prepare_s"),
        "api.shell.commit": _stage_p99(metrics, "api.shell.commit_s"),
        "api.shell.process_gate_wait": _stage_p99(
            metrics, "api.shell.process_gate_wait_s"
        ),
        "api.shell.flock_wait": _stage_p99(metrics, "api.shell.flock_wait_s"),
        "api.shell.overlay_capture_to_changes": _stage_p99(
            metrics, "api.shell.overlay_capture_to_changes_s"
        ),
        "api.edit.prepare": _stage_p99(metrics, "api.edit.prepare_s"),
        "api.edit.commit": _stage_p99(metrics, "api.edit.commit_s"),
        "api.edit.process_gate_wait": _stage_p99(
            metrics, "api.edit.process_gate_wait_s"
        ),
        "api.edit.flock_wait": _stage_p99(metrics, "api.edit.flock_wait_s"),
        "api.write.prepare": _stage_p99(metrics, "api.write.prepare_s"),
        "api.write.commit": _stage_p99(metrics, "api.write.commit_s"),
        "api.write.process_gate_wait": _stage_p99(
            metrics, "api.write.process_gate_wait_s"
        ),
        "api.write.flock_wait": _stage_p99(metrics, "api.write.flock_wait_s"),
        "overlay.run_command": _stage_p99(metrics, "overlay.run_command_s"),
        "occ.prepare.total": _stage_p99(metrics, "occ.prepare.total_s"),
        "occ.commit.total": _stage_p99(metrics, "occ.commit.total_s"),
    }
    emit_metric(label, payload)


def _persist(metrics: list[RuntimeCallMetric]) -> None:
    for metric in metrics:
        write_timing_record(metric)


async def _run_read(handle: SandboxHandle, c: int) -> list[RuntimeCallMetric]:
    seeded: list[str] = []
    for index in range(c):
        path = f"tracked/attr/read/c{c:02d}-{index:02d}.txt"
        seeded.append(path)
        result = await handle.tool.write_file(
            path,
            f"read attr c={c} i={index}\n",
            description=f"seed read attr c={c} i={index}",
        )
        assert_committed(result, path=path)

    factories = []
    for index, path in enumerate(seeded):

        async def run(index: int = index, path: str = path):
            return await timed_call(
                f"attr_read_c{c:02d}_{index:02d}",
                handle.tool.read_file(path),
            )

        factories.append(run)

    rows = await gather_with_barrier(factories)
    return [metric for _, metric in rows]


async def _run_write(handle: SandboxHandle, c: int) -> list[RuntimeCallMetric]:
    factories = []
    for index in range(c):
        path = f"tracked/attr/write/c{c:02d}-{index:02d}.txt"
        content = f"write attr c={c} i={index}\n"

        async def run(
            index: int = index,
            path: str = path,
            content: str = content,
        ):
            return await timed_call(
                f"attr_write_c{c:02d}_{index:02d}",
                handle.tool.write_file(
                    path,
                    content,
                    description=f"attr write c={c} i={index}",
                ),
            )

        factories.append(run)

    rows = await gather_with_barrier(factories)
    return [metric for _, metric in rows]


async def _run_edit(handle: SandboxHandle, c: int) -> list[RuntimeCallMetric]:
    seeded: list[tuple[str, str]] = []
    for index in range(c):
        path = f"tracked/attr/edit/c{c:02d}-{index:02d}.txt"
        original = f"edit attr c={c} i={index} v0\n"
        result = await handle.tool.write_file(
            path,
            original,
            description=f"seed edit attr c={c} i={index}",
        )
        assert_committed(result, path=path)
        seeded.append((path, original))

    factories = []
    for index, (path, original) in enumerate(seeded):
        new_text = f"edit attr c={c} i={index} v1\n"

        async def run(
            index: int = index,
            path: str = path,
            old_text: str = original,
            new_text: str = new_text,
        ):
            return await timed_call(
                f"attr_edit_c{c:02d}_{index:02d}",
                handle.tool.edit_file(
                    path,
                    [(old_text, new_text)],
                    description=f"attr edit c={c} i={index}",
                ),
            )

        factories.append(run)

    rows = await gather_with_barrier(factories)
    return [metric for _, metric in rows]


async def _run_shell_real(handle: SandboxHandle, c: int) -> list[RuntimeCallMetric]:
    factories = []
    for index in range(c):
        path = f"tracked/attr/shell/c{c:02d}-{index:02d}.txt"
        command = (
            f"mkdir -p tracked/attr/shell && "
            f"echo attr c={c} i={index} > {path}"
        )

        async def run(
            index: int = index,
            command: str = command,
        ):
            return await timed_call(
                f"attr_shell_c{c:02d}_{index:02d}",
                handle.tool.shell(
                    command,
                    description=f"attr shell c={c} i={index}",
                ),
            )

        factories.append(run)

    rows = await gather_with_barrier(factories)
    return [metric for _, metric in rows]


async def _run_shell_baseline(handle: SandboxHandle, c: int) -> list[RuntimeCallMetric]:
    factories = []
    for index in range(c):

        async def run(index: int = index):
            return await timed_call(
                f"attr_baseline_c{c:02d}_{index:02d}",
                handle.tool.shell(
                    ":",
                    description=f"attr baseline c={c} i={index}",
                ),
            )

        factories.append(run)

    rows = await gather_with_barrier(factories)
    return [metric for _, metric in rows]


_VERBS: tuple[tuple[str, _Runner], ...] = (
    ("read", _run_read),
    ("write", _run_write),
    ("edit", _run_edit),
    ("shell_baseline", _run_shell_baseline),
    ("shell_real", _run_shell_real),
)


@pytest.mark.live
async def test_latency_attribution_sweep(live_sandbox: SandboxHandle) -> None:
    """Sweep verb x concurrency, persisting per-call timings to JSONL.

    Phase 3.x.2 records the manifest depth after each verb group. The public
    stack-shrinking path has been removed, so this sweep intentionally measures
    the system as the layer stack grows during the run.
    """
    handle = live_sandbox
    sweep_started = time.perf_counter()
    overall: dict[str, list[RuntimeCallMetric]] = {}

    for verb, runner in _VERBS:
        for c in _CONCURRENCIES:
            label = f"attr_{verb}_c{c:02d}"
            metrics = await runner(handle, c)
            _persist(metrics)
            _emit_anatomy(label, metrics)
            overall[label] = metrics
        layer_metrics = await handle.tool.layer_metrics()
        emit_metric(
            f"attr_{verb}_post_group",
            {
                "manifest_depth": layer_metrics.get("manifest_depth"),
                "active_leases": layer_metrics.get("active_leases"),
                "staging_dirs": layer_metrics.get("staging_dirs"),
            },
        )

    emit_metric(
        "attr_sweep_done",
        {
            "verbs": [verb for verb, _ in _VERBS],
            "concurrencies": list(_CONCURRENCIES),
            "elapsed_s": round(time.perf_counter() - sweep_started, 3),
        },
    )
