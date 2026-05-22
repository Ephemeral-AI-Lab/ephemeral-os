"""pytest fixtures for the isolated_workspace mock-sandbox tier.

The Tier 0 pre-flight tests need none of these — they are pure AST walks.
The Tier 1+ tiers depend on a real ``sweevo_image_sandbox`` and a running
daemon with ``EOS_ISOLATED_WORKSPACE_ENABLED=true``.

Fixture layering:

    sweevo_image_sandbox  (existing, session-scoped)
        └── iws_sandbox           (this conftest, session-scoped)
            └── iws_clean_sandbox (this conftest, function-scoped reset)

Tests that need post-test state (daemon-restart, GC) skip
``iws_clean_sandbox`` and use ``iws_sandbox`` directly.

Capability gating:

    - Tier 0 (pre_flight/): no markers; runs everywhere.
    - Tier 1-8: gated on ``database_configured() and live_e2e_heavy_enabled()``
      by individual tests.
    - Tier 9 (performance/): additionally gated on
      ``_capability_probe`` (per PLAN §18).
"""

from __future__ import annotations

import shutil
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Capability probe (v2 §18)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def iws_capability_probe() -> dict[str, bool]:
    """Empirical detection of kernel-touching surfaces.

    Probes run once at session setup. Tier 9 tests inspect this fixture to
    decide skip-vs-fail per the reference-CI policy. The Linux-vs-other
    branch is intentionally absent: the daemon only runs inside the Linux
    sweevo container, and every probe degrades cleanly when its kernel
    surface is missing.
    """
    from . import _iws_fixtures

    return {
        "has_mount_overlay": _iws_fixtures.can_mount_overlay_natively(),
        "has_cgroup_freezer": _iws_fixtures.has_cgroup_freezer(),
        "has_unshare_netns": _iws_fixtures.has_unshare_netns(),
        "has_docker": shutil.which("docker") is not None,
    }


# ---------------------------------------------------------------------------
# Sandbox + cleanup
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def iws_sandbox(
    sweevo_image_sandbox: dict[str, Any],  # noqa: F811 (fixture from sweevo)
) -> dict[str, Any]:
    """Yield a sweevo sandbox configured for isolated workspaces.

    The daemon must boot with ``EOS_ISOLATED_WORKSPACE_ENABLED=true``. The
    bootstrap can either write that to ``/etc/environment`` and restart the
    daemon, or set it directly on the daemon process — both are
    sweevo-fixture concerns. For now we surface the underlying sandbox dict
    and let the daemon's existing env-var plumbing carry the flag.
    """
    return sweevo_image_sandbox


@pytest.fixture
async def iws_clean_sandbox(iws_sandbox: dict[str, Any]) -> dict[str, Any]:
    """Drive ``api.isolated_workspace.exit`` for known test agents, then yield.

    Idempotent: a "no workspace open" response is fine.
    """
    from . import _iws_rpc

    sandbox_id = str(iws_sandbox.get("sandbox_id") or iws_sandbox.get("id") or "")
    for agent_id in ("agent-A", "agent-B", "agent-C", "agent-D", "agent-E"):
        try:
            await _iws_rpc.exit_(sandbox_id, agent_id, timeout=10)
        except Exception:  # pragma: no cover — best-effort reset
            pass
    return iws_sandbox


# ---------------------------------------------------------------------------
# Audit-tail (PLAN §2)
# ---------------------------------------------------------------------------


@pytest.fixture
def iws_audit_tail(tmp_path):
    """Return a callable that waits for an audit event by type + predicate.

    The full implementation tails ``sandbox_events.jsonl`` written by the
    in-sandbox recorder. The Tier 0 tests don't need this fixture; live
    tiers consume it.
    """
    import asyncio
    import time
    from pathlib import Path
    from typing import Callable

    async def wait_for(
        jsonl_path: Path,
        event_type: str,
        *,
        timeout_s: float = 5.0,
        predicate: Callable[[dict], bool] | None = None,
    ) -> dict:
        from . import _iws_invariants

        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            for row in _iws_invariants.events_of_type(jsonl_path, event_type):
                if predicate is None or predicate(row):
                    return row
            await asyncio.sleep(0.05)
        raise AssertionError(
            f"timed out after {timeout_s}s waiting for {event_type} in {jsonl_path}"
        )

    return wait_for


# ---------------------------------------------------------------------------
# Latency baseline (v2 §15.1, full impl deferred to PR 6)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def iws_latency_baseline() -> dict[str, dict[str, float]]:
    """Session-collected per-phase medians.

    PR 6 wires this to a real 3-cycle warm-up against ``iws_sandbox``. Until
    then it returns an empty dict so dependent Tier 9 tests can
    ``pytest.skip`` cleanly when the baseline is missing.
    """
    return {}
