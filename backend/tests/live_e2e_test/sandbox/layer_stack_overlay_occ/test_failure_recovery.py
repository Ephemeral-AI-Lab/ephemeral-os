"""E9 integrated failure-recovery probes."""

from __future__ import annotations

import asyncio

import pytest

from .._harness.integrated_cases import (
    assert_committed,
    assert_read,
    emit_metric,
    q,
    remove_tmp,
    summarize_calls,
    timed_call,
    tmp_path,
    token,
    touch_tmp,
    wait_for_tmp,
)
from .._harness.sandbox_fixture import SandboxHandle


pytestmark = pytest.mark.asyncio

_LAYER_STACK_ROOT = "/tmp/eos-sandbox-runtime/layer-stack"


async def _inject_orphan_staging(handle: SandboxHandle, name: str) -> None:
    command = (
        "set -e; "
        f"mkdir -p {q(_LAYER_STACK_ROOT + '/staging/' + name)}; "
        f"printf orphan > {q(_LAYER_STACK_ROOT + '/staging/' + name + '/payload.txt')}"
    )
    result = await handle.raw_exec(handle.sandbox_id, command, timeout=15)
    if result.exit_code != 0:
        pytest.fail(f"failed to inject orphan staging: {result.stderr or result.stdout}")


async def test_kill_runtime_mid_layer_publish_no_dangling_manifest(
    integrated_sandbox: SandboxHandle,
) -> None:
    path = "recovery/publish.txt"
    seed = await integrated_sandbox.tool.write_file(path, "base\n")
    assert_committed(seed, path=path)

    run = token("recovery-publish")
    started = tmp_path(f"{run}-started")
    proceed = tmp_path(f"{run}-go")
    await remove_tmp(integrated_sandbox, started, proceed)
    command = (
        "set -e; "
        f"touch {q(started)}; "
        f"while [ ! -f {q(proceed)} ]; do sleep 0.01; done; "
        f"printf 'stale-publish\\n' > {q(path)}"
    )
    shell_task = asyncio.create_task(
        timed_call(
            "recovery_stale_publish_shell",
            integrated_sandbox.tool.shell(
                command,
                timeout=30,
                description="phase3 publish conflict recovery shell",
            ),
        )
    )
    await wait_for_tmp(integrated_sandbox, started)
    winner, winner_metric = await timed_call(
        "recovery_winning_write",
        integrated_sandbox.tool.write_file(
            path,
            "winner\n",
            description="phase3 publish conflict winning write",
        ),
    )
    assert_committed(winner, path=path)
    await touch_tmp(integrated_sandbox, proceed)
    rejected, rejected_metric = await shell_task
    assert not rejected.success
    assert rejected.changed_paths == ()

    compact = await integrated_sandbox.tool.compact(max_depth=4)
    metrics = await integrated_sandbox.tool.layer_metrics()
    await assert_read(integrated_sandbox, path, "winner\n")
    assert compact["success"] is True
    assert metrics["active_leases"] == 0
    assert metrics["staging_dirs"] == 0
    emit_metric(
        "failure_recovery.publish_conflict",
        {
            **summarize_calls([winner_metric, rejected_metric]),
            "active_leases": metrics["active_leases"],
            "staging_dirs": metrics["staging_dirs"],
            "orphan_layers_removed": compact["orphan_layers_removed"],
            "orphan_staging_removed": compact["orphan_staging_removed"],
        },
    )


async def test_kill_runtime_mid_squash_no_orphan_checkpoint(
    integrated_sandbox: SandboxHandle,
) -> None:
    for index in range(6):
        result = await integrated_sandbox.tool.write_file(
            f"recovery/squash-{index:02d}.txt",
            f"value-{index:02d}\n",
            description=f"phase3 seed squash layer {index:02d}",
        )
        assert_committed(result, path=f"recovery/squash-{index:02d}.txt")

    orphan_name = f"squash-abandoned-{token('checkpoint')}"
    await _inject_orphan_staging(integrated_sandbox, orphan_name)
    before = await integrated_sandbox.tool.layer_metrics()
    compact = await integrated_sandbox.tool.compact(max_depth=2)
    after = await integrated_sandbox.tool.layer_metrics()
    assert compact["success"] is True
    assert orphan_name in compact["orphan_staging_removed"]
    assert after["manifest_depth"] <= 2
    assert after["staging_dirs"] == 0
    for index in range(6):
        await assert_read(
            integrated_sandbox,
            f"recovery/squash-{index:02d}.txt",
            f"value-{index:02d}\n",
        )
    emit_metric(
        "failure_recovery.squash_checkpoint",
        {
            "before_depth": before["manifest_depth"],
            "after_depth": after["manifest_depth"],
            "before_staging_dirs": before["staging_dirs"],
            "after_staging_dirs": after["staging_dirs"],
            "orphan_staging_removed": compact["orphan_staging_removed"],
            "orphan_layers_removed": compact["orphan_layers_removed"],
        },
    )


async def test_lease_cleaned_when_owning_shell_killed(
    integrated_sandbox: SandboxHandle,
) -> None:
    path = "recovery/timeout.txt"
    run = token("lease-timeout")
    started = tmp_path(f"{run}-started")
    await remove_tmp(integrated_sandbox, started)
    command = (
        "set -e; "
        f"touch {q(started)}; "
        "sleep 5; "
        f"printf 'late\\n' > {q(path)}"
    )
    shell_task = asyncio.create_task(
        integrated_sandbox.tool.shell(
            command,
            timeout=1,
            description="phase3 shell timeout releases lease",
        )
    )
    await wait_for_tmp(integrated_sandbox, started)
    try:
        result = await shell_task
    except Exception as exc:  # runtime reports subprocess timeout as an error envelope
        result = exc

    read = await integrated_sandbox.tool.read_file(path)
    compact = await integrated_sandbox.tool.compact(max_depth=4)
    metrics = await integrated_sandbox.tool.layer_metrics()
    assert read.success
    assert not read.exists
    assert compact["success"] is True
    assert metrics["active_leases"] == 0
    assert metrics["staging_dirs"] == 0
    emit_metric(
        "failure_recovery.timeout_lease",
        {
            "timeout_result_type": type(result).__name__,
            "late_write_visible": read.exists,
            "active_leases": metrics["active_leases"],
            "staging_dirs": metrics["staging_dirs"],
            "orphan_staging_removed": compact["orphan_staging_removed"],
        },
    )
