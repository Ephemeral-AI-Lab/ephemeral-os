from __future__ import annotations

import pytest

from .conftest import run_live_command, xfail_production_binding_missing

pytestmark = [pytest.mark.e2e, pytest.mark.live, pytest.mark.asyncio]


async def test_e12_process_exec_timeout_is_not_layer_lease_enforcement(live_snapshot_sandbox):
    result = await run_live_command(
        live_snapshot_sandbox,
        "sleep 5",
        timeout=1,
        label="e12.process_timeout",
    )
    assert result.exit_code != 0 or result.error


async def test_e12_production_lease_budget_contract_required():
    xfail_production_binding_missing("E12 lease budget enforcement")
