"""Shell-call snapshot isolation under concurrent public API edits.

Backs Phase 3 integrated P0. The workload uses public sandbox tools for
workspace reads/writes/shell calls; raw exec only coordinates /tmp
side-channel files that are outside the captured workspace.
"""

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
    wait_for_tmps,
)
from .._harness.sandbox_fixture import SandboxHandle


pytestmark = pytest.mark.asyncio


async def test_in_flight_shell_does_not_see_concurrent_api_edit(
    integrated_sandbox: SandboxHandle,
) -> None:
    path = "isolation/repeated.txt"
    seed = await integrated_sandbox.tool.write_file(path, "before\n")
    assert_committed(seed, path=path)

    run = token("isolation-repeated")
    started = tmp_path(f"{run}-started")
    proceed = tmp_path(f"{run}-go")
    await remove_tmp(integrated_sandbox, started, proceed)
    command = (
        "set -e; "
        f"touch {q(started)}; "
        f"while [ ! -f {q(proceed)} ]; do sleep 0.01; done; "
        "i=0; "
        "while [ \"$i\" -lt 100 ]; do "
        f"cat {q(path)}; "
        "i=$((i + 1)); "
        "done"
    )

    shell_task = asyncio.create_task(
        timed_call(
            "shell_isolation_100_reads",
            integrated_sandbox.tool.shell(
                command,
                timeout=30,
                description="phase3 shell snapshot isolation repeated reads",
            ),
        )
    )
    await wait_for_tmp(integrated_sandbox, started)
    edit, edit_metric = await timed_call(
        "concurrent_api_write",
        integrated_sandbox.tool.write_file(
            path,
            "after\n",
            description="phase3 concurrent write during shell snapshot",
        ),
    )
    assert_committed(edit, path=path)
    await touch_tmp(integrated_sandbox, proceed)
    shell, shell_metric = await shell_task
    assert_committed(shell)

    observed = [line for line in shell.stdout.splitlines() if line]
    drift = sum(1 for line in observed if line != "before")
    assert len(observed) == 100
    assert drift == 0
    await assert_read(integrated_sandbox, path, "after\n")
    emit_metric(
        "shell_call_isolation.repeated_reads",
        {
            **summarize_calls([shell_metric, edit_metric]),
            "paired_reads": len(observed),
            "drift": drift,
        },
    )


async def test_shell_started_before_edit_sees_pre_edit_view(
    integrated_sandbox: SandboxHandle,
) -> None:
    path = "isolation/single.txt"
    seed = await integrated_sandbox.tool.write_file(path, "token=old\n")
    assert_committed(seed, path=path)

    run = token("isolation-single")
    started = tmp_path(f"{run}-started")
    proceed = tmp_path(f"{run}-go")
    await remove_tmp(integrated_sandbox, started, proceed)
    command = (
        "set -e; "
        f"touch {q(started)}; "
        f"while [ ! -f {q(proceed)} ]; do sleep 0.01; done; "
        f"cat {q(path)}"
    )

    shell_task = asyncio.create_task(
        timed_call(
            "shell_pre_edit_view",
            integrated_sandbox.tool.shell(
                command,
                timeout=30,
                description="phase3 shell sees leased pre-edit view",
            ),
        )
    )
    await wait_for_tmp(integrated_sandbox, started)
    edit, edit_metric = await timed_call(
        "api_edit_after_shell_lease",
        integrated_sandbox.tool.edit_file(
            path,
            [("token=old", "token=new")],
            description="phase3 edit after shell snapshot lease",
        ),
    )
    assert_committed(edit, path=path)
    await touch_tmp(integrated_sandbox, proceed)
    shell, shell_metric = await shell_task
    assert_committed(shell)
    assert shell.stdout == "token=old\n"
    await assert_read(integrated_sandbox, path, "token=new\n")
    emit_metric(
        "shell_call_isolation.pre_edit_view",
        {
            **summarize_calls([shell_metric, edit_metric]),
            "drift": 0,
        },
    )


async def test_two_shells_overlapping_paths_first_commits_wins(
    integrated_sandbox: SandboxHandle,
) -> None:
    path = "isolation/conflict.txt"
    seed = await integrated_sandbox.tool.write_file(path, "base\n")
    assert_committed(seed, path=path)

    run = token("isolation-conflict")
    start_a = tmp_path(f"{run}-a-started")
    start_b = tmp_path(f"{run}-b-started")
    proceed = tmp_path(f"{run}-go")
    await remove_tmp(integrated_sandbox, start_a, start_b, proceed)

    def command(name: str, started: str) -> str:
        return (
            "set -e; "
            f"touch {q(started)}; "
            f"while [ ! -f {q(proceed)} ]; do sleep 0.01; done; "
            f"printf 'winner={name}\\n' > {q(path)}"
        )

    task_a = asyncio.create_task(
        timed_call(
            "shell_conflict_a",
            integrated_sandbox.tool.shell(
                command("A", start_a),
                timeout=30,
                description="phase3 overlapping shell writer A",
            ),
        )
    )
    task_b = asyncio.create_task(
        timed_call(
            "shell_conflict_b",
            integrated_sandbox.tool.shell(
                command("B", start_b),
                timeout=30,
                description="phase3 overlapping shell writer B",
            ),
        )
    )
    await wait_for_tmps(integrated_sandbox, [start_a, start_b])
    await touch_tmp(integrated_sandbox, proceed)
    (result_a, metric_a), (result_b, metric_b) = await asyncio.gather(task_a, task_b)

    successes = [result for result in (result_a, result_b) if result.success]
    rejects = [result for result in (result_a, result_b) if not result.success]
    assert len(successes) == 1
    assert len(rejects) == 1
    assert path in successes[0].changed_paths
    assert rejects[0].changed_paths == ()
    final = await integrated_sandbox.tool.read_file(path)
    assert final.success and final.exists
    assert final.content in {"winner=A\n", "winner=B\n"}
    assert final.content == ("winner=A\n" if successes[0] is result_a else "winner=B\n")
    emit_metric(
        "shell_call_isolation.overlapping_shells",
        {
            **summarize_calls([metric_a, metric_b]),
            "accepted": len(successes),
            "rejected": len(rejects),
            "final": final.content.strip(),
        },
    )
