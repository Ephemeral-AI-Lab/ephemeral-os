"""Public in-flight shell lease coverage over an imported workspace base."""

from __future__ import annotations

import asyncio

import pytest

from .._harness.integrated_cases import (
    assert_committed,
    assert_read,
    q,
    remove_tmp,
    timed_call,
    tmp_path,
    token,
    touch_tmp,
    wait_for_tmp,
)
from .._harness.sandbox_fixture import SandboxHandle
from .._harness.workspace_base_public import seed_imported_base


pytestmark = pytest.mark.asyncio


async def test_in_flight_public_shell_lease_survives_active_edit(
    workspace_base_sandbox: SandboxHandle,
) -> None:
    handle = workspace_base_sandbox
    path = "tracked/lease-squash/value.txt"
    shell_output = "dist/lease-squash/frozen-view.txt"
    await seed_imported_base(
        handle,
        {
            ".gitignore": "dist/\n",
            path: "base-view\n",
        },
    )

    for index in range(8):
        result = await handle.tool.write_file(
            f"tracked/lease-squash/depth-{index:02d}.txt",
            f"depth-{index:02d}\n",
            description=f"phase01 lease squash depth seed {index:02d}",
        )
        assert_committed(result)

    run = token("workspace-base-lease-squash")
    started = tmp_path(f"{run}-started")
    proceed = tmp_path(f"{run}-go")
    await remove_tmp(handle, started, proceed)
    command = (
        "set -e; "
        f"first=$(cat {q(path)}); "
        f"touch {q(started)}; "
        f"while [ ! -f {q(proceed)} ]; do sleep 0.01; done; "
        f"second=$(cat {q(path)}); "
        "mkdir -p dist/lease-squash; "
        f"printf '%s|%s\\n' \"$first\" \"$second\" > {q(shell_output)}"
    )

    shell_task = asyncio.create_task(
        timed_call(
            "base_shell_lease_frozen_view",
            handle.tool.shell(
                command,
                timeout=30,
                description="phase01 shell lease frozen view across squash",
            ),
        )
    )
    await wait_for_tmp(handle, started)

    mid_metrics = await handle.tool.layer_metrics()
    assert int(mid_metrics["active_leases"]) >= 1, mid_metrics

    update, _ = await timed_call(
        "base_shell_lease_active_update",
        handle.tool.write_file(
            path,
            "active-after\n",
            description="phase01 active update while shell lease is held",
        ),
    )
    assert_committed(update, path=path)

    await touch_tmp(handle, proceed)
    shell, _ = await shell_task
    assert_committed(shell, path=shell_output)
    assert shell.exit_code == 0, shell.stderr

    await assert_read(handle, path, "active-after\n")
    await assert_read(handle, shell_output, "base-view|base-view\n")

    after_metrics = await handle.tool.layer_metrics()
    assert int(after_metrics["active_leases"]) == 0, after_metrics
