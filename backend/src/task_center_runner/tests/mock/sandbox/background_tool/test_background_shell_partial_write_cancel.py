"""T6 — Partial-write cancel for ``shell(background=True)``.

Plan §Step 7. A background shell runs ``dd of=tracked.bin bs=1M count=200``
and is cancelled mid-write at 5 s. AC-6 requires the cancelled job to
contribute zero ``changed_paths``: the upperdir is discarded and the OCC
publish path is skipped at
``backend/src/sandbox/daemon/service/shell_job.py:262-276``.

Verification path: after cancel, a follow-up foreground read against
``tracked.bin`` must show ``exists=False`` (no truncated publish leaked
into the workspace OCC).
"""

from __future__ import annotations

import asyncio

import pytest

import sandbox.api as sandbox_api
from benchmarks.sweevo.models import SWEEvoInstance
from sandbox._shared.models import (
    ReadFileRequest,
    SandboxCaller,
    ShellRequest,
)
from task_center_runner.agent.mock.background_shell_probe import seed_workspace
from task_center_runner.tests._live_config import (
    database_configured,
    live_e2e_heavy_enabled,
)


pytestmark = pytest.mark.asyncio

_TARGET_PATH = "/testbed/.ephemeralos/sweevo-mock/background_shell/tracked.bin"
_DD_COMMAND = (
    f"mkdir -p $(dirname {_TARGET_PATH}) && "
    f"dd if=/dev/urandom of={_TARGET_PATH} bs=1M count=200 status=none"
)


@pytest.mark.skipif(
    not database_configured(),
    reason="database URL not configured",
)
@pytest.mark.skipif(
    not live_e2e_heavy_enabled(),
    reason="heavy live e2e disabled in runner.live_e2e.heavy_enabled",
)
@pytest.mark.timeout(300)
async def test_background_shell_partial_write_cancel(
    sweevo_image_instance: SWEEvoInstance,
    workspace: dict[str, object],
) -> None:
    sandbox_id = str(workspace["sandbox_id"])
    await seed_workspace(sandbox_id)

    request = ShellRequest(
        command=_DD_COMMAND,
        cwd=".",
        timeout=120,
        background=True,
        caller=SandboxCaller(agent_id="background-shell-partial-write.dd"),
        description="background_shell.partial_write_cancel.dd",
    )
    # Cancel after ~5 s — well before the 200 MB write finishes.
    try:
        await asyncio.wait_for(
            sandbox_api.shell(sandbox_id, request),
            timeout=5.0,
        )
        pytest.fail("dd completed before cancel — increase count or reduce cancel deadline")
    except asyncio.TimeoutError:
        pass  # expected — cancel routed through host dispatcher

    # AC-6: tracked.bin must NOT exist in the workspace OCC after cancel.
    read = await sandbox_api.read_file(
        sandbox_id,
        ReadFileRequest(
            path=_TARGET_PATH,
            caller=SandboxCaller(agent_id="background-shell-partial-write.fg-check"),
        ),
    )
    assert not read.exists, (
        f"AC-6 violation: cancelled partial write left {_TARGET_PATH} in workspace OCC "
        f"(read result: success={read.success}, content len={len(read.content or '')})"
    )
