"""Plugin and LSP dispatch fails closed while an iws handle is open."""

from __future__ import annotations

import pytest

from sandbox.host.daemon_client import _DaemonDispatchError, call_daemon_api
from test_runner.tests._live_config import (
    database_configured,
    live_e2e_heavy_enabled,
)
from test_runner.tests.mock.sandbox.isolated_workspace import _iws_rpc


pytestmark = pytest.mark.asyncio


async def _daemon_error(
    sandbox_id: str,
    op: str,
    args: dict[str, object],
) -> _DaemonDispatchError:
    with pytest.raises(_DaemonDispatchError) as raised:
        await call_daemon_api(sandbox_id, op, args, timeout=15)
    return raised.value


async def _maybe_daemon_error(
    sandbox_id: str,
    op: str,
    args: dict[str, object],
) -> _DaemonDispatchError | None:
    try:
        await call_daemon_api(sandbox_id, op, args, timeout=15)
    except _DaemonDispatchError as exc:
        return exc
    return None


@pytest.mark.skipif(
    not database_configured(),
    reason="database URL not configured",
)
@pytest.mark.skipif(
    not live_e2e_heavy_enabled(),
    reason="heavy live e2e disabled in runner.live_e2e.heavy_enabled",
)
@pytest.mark.timeout(240)
async def test_plugin_and_lsp_blocked_or_routed_in_iws(iws_clean_sandbox) -> None:
    sandbox_id = str(iws_clean_sandbox["sandbox_id"])
    agent_id = "agent-policy"

    opened = await _iws_rpc.enter(
        sandbox_id,
        agent_id,
        layer_stack_root=_iws_rpc.IWS_LAYER_STACK_ROOT,
    )
    assert opened.get("success") is True, opened
    try:
        generic = await _daemon_error(
            sandbox_id,
            "api.plugin.status",
            {"agent_id": agent_id},
        )
        assert generic.kind == "forbidden_in_isolated_workspace", generic
        assert generic.details.get("agent_id") == agent_id, generic.details

        lsp = await _daemon_error(
            sandbox_id,
            "plugin.lsp.hover",
            {
                "agent_id": agent_id,
                "file_path": "/testbed/does-not-matter.py",
                "line": 0,
                "character": 0,
            },
        )
        assert lsp.kind == "forbidden_in_isolated_workspace", lsp
        assert lsp.details.get("op") == "plugin.lsp.hover", lsp.details
    finally:
        await _iws_rpc.exit_(sandbox_id, agent_id)

    status = await call_daemon_api(
        sandbox_id,
        "api.plugin.status",
        {"agent_id": agent_id},
        timeout=15,
    )
    assert status.get("success") is True, status

    default_lsp = await _maybe_daemon_error(
        sandbox_id,
        "plugin.lsp.hover",
        {
            "agent_id": agent_id,
            "file_path": "/testbed/does-not-matter.py",
            "line": 0,
            "character": 0,
        },
    )
    if default_lsp is not None:
        assert default_lsp.kind != "forbidden_in_isolated_workspace", default_lsp
