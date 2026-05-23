"""External UDP inbound to the workspace ns_ip is rejected."""

from __future__ import annotations

import pytest

from sandbox.api import raw_exec
from benchmarks.sweevo.models import _REPO_DIR
from task_center_runner.tests._live_config import (
    database_configured,
    live_e2e_heavy_enabled,
)
from task_center_runner.tests.mock.sandbox.isolated_workspace import (
    _iws_invariants,
    _iws_rpc,
)


pytestmark = pytest.mark.asyncio


@pytest.mark.skipif(
    not database_configured(),
    reason="database URL not configured",
)
@pytest.mark.skipif(
    not live_e2e_heavy_enabled(),
    reason="heavy live e2e disabled in runner.live_e2e.heavy_enabled",
)
@pytest.mark.timeout(180)
async def test_external_inbound_udp_rejected(
    iws_clean_sandbox, iws_audit_jsonl
) -> None:
    sandbox_id = str(iws_clean_sandbox["sandbox_id"])
    enter = await _iws_rpc.enter(
        sandbox_id, "agent-A", layer_stack_root=_iws_rpc.IWS_LAYER_STACK_ROOT,
    )
    assert enter.get("success") is True, enter
    try:
        jsonl = await iws_audit_jsonl()
        ns_ip = next(
            (row.get("payload") or {}).get("ns_ip")
            for row in _iws_invariants.events_of_type(
                jsonl, "sandbox_isolated_workspace_enter"
            )
            if (row.get("payload") or {}).get("agent_id") == "agent-A"
        )
        assert ns_ip

        probe = await raw_exec(
            sandbox_id,
            "unshare -n -- python3 -c \""
            "import socket; s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); "
            "s.settimeout(2.0); "
            f"s.sendto(b'x', ('{ns_ip}', 53)); "
            "try: s.recvfrom(64)\nexcept (socket.timeout, ConnectionRefusedError): "
            "    raise SystemExit(0)\nraise SystemExit(1)"
            "\"",
            cwd="/", timeout=15,
        )
        assert probe.exit_code == 0, (
            "external netns must not receive UDP replies from ws", probe,
        )
    finally:
        await _iws_rpc.exit_(sandbox_id, "agent-A")
