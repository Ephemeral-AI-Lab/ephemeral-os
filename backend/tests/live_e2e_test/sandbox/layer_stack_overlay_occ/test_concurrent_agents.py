"""E4 integrated public-tool concurrency probes."""

from __future__ import annotations

import asyncio

import pytest

from .._harness.concurrency import gather_with_barrier
from .._harness.integrated_cases import (
    RuntimeCallMetric,
    assert_committed,
    assert_read,
    emit_metric,
    paths_visible_summary,
    q,
    remove_tmp,
    summarize_calls,
    timed_call,
    timed_raw_exec,
    tmp_path,
    token,
    touch_tmp,
    wait_for_tmp,
)
from .._harness.sandbox_fixture import SandboxHandle


pytestmark = pytest.mark.asyncio


async def _seed_mixed_workload(handle: SandboxHandle, *, edit_count: int) -> None:
    ignore = await handle.tool.write_file(
        ".gitignore",
        "dist/\n",
        description="phase3 seed gitignore for direct route",
    )
    assert_committed(ignore, path=".gitignore")
    shared = await handle.tool.write_file(
        "tracked/shared.txt",
        "stable\n",
        description="phase3 seed shared tracked read",
    )
    assert_committed(shared, path="tracked/shared.txt")
    for index in range(edit_count):
        result = await handle.tool.write_file(
            f"tracked/edit-{index:02d}.txt",
            f"slot-{index:02d}=old\n",
            description="phase3 seed edit target",
        )
        assert_committed(result, path=f"tracked/edit-{index:02d}.txt")


async def _run_mixed_workload(
    handle: SandboxHandle,
    *,
    shell_count: int,
    edit_count: int,
) -> tuple[list[RuntimeCallMetric], list[str]]:
    await _seed_mixed_workload(handle, edit_count=edit_count)

    factories = []
    for index in range(shell_count):
        path = f"dist/shell-{index:02d}.txt"
        command = (
            "set -e; "
            "first=$(cat tracked/shared.txt); "
            "sleep 0.02; "
            "second=$(cat tracked/shared.txt); "
            "[ \"$first\" = \"$second\" ]; "
            "mkdir -p dist; "
            f"printf 'shell-{index:02d}:$first\\n' > {q(path)}"
        )

        async def run_shell(index: int = index, command: str = command):
            return await timed_call(
                f"mixed_shell_{index:02d}",
                handle.tool.shell(
                    command,
                    timeout=30,
                    description=f"phase3 mixed shell {index:02d}",
                ),
            )

        factories.append(run_shell)

    for index in range(edit_count):
        path = f"tracked/edit-{index:02d}.txt"

        async def run_edit(index: int = index, path: str = path):
            return await timed_call(
                f"mixed_edit_{index:02d}",
                handle.tool.edit_file(
                    path,
                    [(f"slot-{index:02d}=old", f"slot-{index:02d}=new")],
                    description=f"phase3 mixed edit {index:02d}",
                ),
            )

        factories.append(run_edit)

    rows = await gather_with_barrier(factories)
    metrics: list[RuntimeCallMetric] = []
    changed_paths: list[str] = []
    for result, metric in rows:
        assert_committed(result)
        metrics.append(metric)
        changed_paths.extend(result.changed_paths)
    return metrics, changed_paths


async def test_sustained_mixed_shell_edit_sample_has_no_torn_reads(
    integrated_sandbox: SandboxHandle,
) -> None:
    metrics, changed_paths = await _run_mixed_workload(
        integrated_sandbox,
        shell_count=8,
        edit_count=16,
    )
    reads = [await integrated_sandbox.tool.read_file(path) for path in changed_paths]
    assert all(result.success and result.exists for result in reads)
    emit_metric(
        "concurrent_agents.mixed_sample",
        {
            **summarize_calls(metrics),
            **paths_visible_summary(reads),
            "shells": 8,
            "edits": 16,
            "drift": 0,
        },
    )


async def test_every_accepted_write_visible_in_final_view(
    integrated_sandbox: SandboxHandle,
) -> None:
    metrics, changed_paths = await _run_mixed_workload(
        integrated_sandbox,
        shell_count=3,
        edit_count=5,
    )
    reads = [await integrated_sandbox.tool.read_file(path) for path in changed_paths]
    assert all(result.success and result.exists for result in reads)
    for index in range(5):
        await assert_read(
            integrated_sandbox,
            f"tracked/edit-{index:02d}.txt",
            f"slot-{index:02d}=new\n",
        )
    emit_metric(
        "concurrent_agents.accepted_visible",
        {
            **summarize_calls(metrics),
            **paths_visible_summary(reads),
            "accepted_paths": len(set(changed_paths)),
        },
    )


async def test_every_rejected_write_left_no_trace(
    integrated_sandbox: SandboxHandle,
) -> None:
    path = "tracked/rejected.txt"
    seed = await integrated_sandbox.tool.write_file(path, "base\n")
    assert_committed(seed, path=path)

    run = token("concurrent-reject")
    started = tmp_path(f"{run}-started")
    proceed = tmp_path(f"{run}-go")
    await remove_tmp(integrated_sandbox, started, proceed)
    command = (
        "set -e; "
        f"touch {q(started)}; "
        f"while [ ! -f {q(proceed)} ]; do sleep 0.01; done; "
        f"printf 'stale-shell\\n' > {q(path)}"
    )
    shell_task = asyncio.create_task(
        timed_call(
            "rejected_stale_shell",
            integrated_sandbox.tool.shell(
                command,
                timeout=30,
                description="phase3 stale shell write should reject",
            ),
        )
    )
    await wait_for_tmp(integrated_sandbox, started)
    winner, winner_metric = await timed_call(
        "winning_api_write",
        integrated_sandbox.tool.write_file(
            path,
            "winner\n",
            description="phase3 winning API write before stale shell publish",
        ),
    )
    assert_committed(winner, path=path)
    await touch_tmp(integrated_sandbox, proceed)
    rejected, rejected_metric = await shell_task
    assert not rejected.success
    assert rejected.changed_paths == ()
    await assert_read(integrated_sandbox, path, "winner\n")
    emit_metric(
        "concurrent_agents.rejected_absent",
        {
            **summarize_calls([winner_metric, rejected_metric]),
            "rejected_write_visible": False,
        },
    )


async def test_overlapping_50pct_gitignored_paths_use_lww(
    integrated_sandbox: SandboxHandle,
) -> None:
    seed = await integrated_sandbox.raw_exec(
        integrated_sandbox.sandbox_id,
        "printf 'dist/\\n' > .gitignore",
        cwd=integrated_sandbox.workspace_root,
        timeout=15,
    )
    assert seed.success, seed.stderr or seed.stdout

    run = token("overlap-lww")
    proceed = tmp_path(f"{run}-go")
    starts = [tmp_path(f"{run}-{index:02d}-started") for index in range(8)]
    await remove_tmp(integrated_sandbox, proceed, *starts)

    async def run_process_exec(index: int):
        path = f"dist/overlap-{index % 4}.txt"
        command = (
            "set -e; "
            f"touch {q(starts[index])}; "
            f"while [ ! -f {q(proceed)} ]; do sleep 0.01; done; "
            "mkdir -p dist; "
            f"printf 'writer-{index:02d}\\n' > {q(path)}"
        )
        return await timed_raw_exec(
            f"overlap_process_exec_{index:02d}",
            integrated_sandbox,
            command,
            cwd=integrated_sandbox.workspace_root,
            timeout=30,
            changed_paths=(path,),
        )

    tasks = [asyncio.create_task(run_process_exec(index)) for index in range(8)]
    for started in starts:
        await wait_for_tmp(integrated_sandbox, started)
    await touch_tmp(integrated_sandbox, proceed)
    rows = await asyncio.gather(*tasks)

    metrics: list[RuntimeCallMetric] = []
    by_path: dict[str, set[str]] = {f"dist/overlap-{index}.txt": set() for index in range(4)}
    for result, metric in rows:
        assert result.success, result.stderr or result.stdout
        metrics.append(metric)
        for path in metric.changed_paths:
            writer = metric.label.rsplit("_", 1)[-1]
            by_path.setdefault(path, set()).add(f"writer-{writer}\n")

    final_values = {}
    for path, accepted_values in by_path.items():
        read = await integrated_sandbox.raw_exec(
            integrated_sandbox.sandbox_id,
            f"cat -- {q(path)}",
            cwd=integrated_sandbox.workspace_root,
            timeout=15,
        )
        assert read.success, read.stderr or read.stdout
        final_value = read.stdout.strip()
        assert final_value in {value.strip() for value in accepted_values}
        final_values[path] = final_value

    emit_metric(
        "concurrent_agents.overlap_lww",
        {
            **summarize_calls(metrics),
            "execution": "process_exec",
            "overlap_ratio": 0.5,
            "final_values": final_values,
        },
    )
