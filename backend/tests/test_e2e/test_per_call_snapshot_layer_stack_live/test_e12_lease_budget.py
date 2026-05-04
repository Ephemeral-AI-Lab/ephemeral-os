from __future__ import annotations

import pytest

from .conftest import pinned_layers, run_live_command

pytestmark = [pytest.mark.e2e, pytest.mark.live, pytest.mark.asyncio]


async def test_e12_api_shell_timeout_releases_snapshot_lease(live_snapshot_sandbox):
    result = await run_live_command(
        live_snapshot_sandbox,
        "sleep 5",
        timeout=1,
        label="e12.api_shell_timeout",
    )
    assert not result.success or result.error
    assert await pinned_layers(live_snapshot_sandbox) == ()
