"""Regression tests for TaskCenter agent launcher scheduling."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from task_center.agent_launch.launcher import EphemeralAttemptAgentLauncher


@pytest.mark.asyncio
async def test_wait_for_idle_prunes_done_tasks_before_next_loop() -> None:
    launcher = EphemeralAttemptAgentLauncher(
        config=SimpleNamespace(),
        runtime=lambda: None,
    )
    done_task = asyncio.create_task(asyncio.sleep(0))
    await done_task
    launcher._pending.add(done_task)  # noqa: SLF001 - regression seam

    await asyncio.wait_for(launcher.wait_for_idle(), timeout=0.2)

    assert launcher._pending == set()  # noqa: SLF001 - regression seam
