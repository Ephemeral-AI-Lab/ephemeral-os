"""SWE-EVO adapter for the project-wide live e2e framework.

The framework itself is dataset-agnostic. This module keeps the SWE-EVO entry
prompt, sandbox provisioning, and pytest fixtures in one explicit adapter while
the dataset glue remains under ``benchmarks.sweevo``.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path

import pytest

from benchmarks.sweevo.dataset import select_sweevo_instance
from benchmarks.sweevo.models import SWEEvoInstance, _REPO_DIR
from benchmarks.sweevo.prompt import build_sweevo_user_prompt
from live_e2e.hooks.registry import Hook
from live_e2e.runner import RunReport
from live_e2e.runner import run_scenario as _generic_run_scenario
from live_e2e.scenarios.base import Scenario
from live_e2e.stores import TaskCenterStoreBundle

_DEFAULT_INSTANCE_ID = "dask__dask_2023.3.2_2023.4.0"
_SESSION_WORKSPACE_USED_ATTR = "_ephemeralos_sweevo_workspace_used_sandboxes"


async def run_sweevo_scenario(
    scenario: Scenario,
    *,
    instance: SWEEvoInstance,
    sandbox_id: str,
    audit_dir: Path,
    stores: TaskCenterStoreBundle | None = None,
    repo_dir: str = _REPO_DIR,
    extra_hooks: Sequence[Hook] = (),
    user_prompt: str | None = None,
) -> RunReport:
    """Run a live e2e scenario with SWE-EVO prompt semantics."""
    entry_prompt = (
        user_prompt
        if user_prompt is not None
        else build_sweevo_user_prompt(instance, repo_dir=repo_dir)
    )
    return await _generic_run_scenario(
        scenario,
        sandbox_id=sandbox_id,
        audit_dir=audit_dir,
        repo_dir=repo_dir,
        entry_prompt=entry_prompt,
        stores=stores,
        extra_hooks=extra_hooks,
        instance_id=instance.instance_id,
    )


@pytest.fixture(scope="session")
def sweevo_instance() -> SWEEvoInstance:
    instance_id = os.getenv("EOS_SWEEVO_INSTANCE", _DEFAULT_INSTANCE_ID)
    return select_sweevo_instance(instance_id=instance_id)


@pytest.fixture(scope="session")
async def sweevo_sandbox(sweevo_instance: SWEEvoInstance) -> dict[str, object]:
    """Provision a real Daytona sandbox for the configured SWE-EVO instance."""
    from sandbox.provider.daytona.bootstrap import bootstrap_daytona_provider

    from benchmarks.sweevo.sandbox import create_sweevo_test_sandbox

    bootstrap_daytona_provider()
    return await create_sweevo_test_sandbox(
        sweevo_instance,
        register_snapshot=True,
        reuse_existing_auto=_reuse_existing_auto_enabled(),
    )


@pytest.fixture
async def workspace(
    sweevo_sandbox: dict[str, object],
    request: pytest.FixtureRequest,
) -> dict[str, object]:
    """Return a SWE-EVO workspace with per-test reset isolation."""
    sandbox_id = str(sweevo_sandbox["sandbox_id"])
    used_sandboxes = _session_workspace_used_sandboxes(request.session)
    first_use = sandbox_id not in used_sandboxes
    should_reset = (not first_use) or bool(sweevo_sandbox.get("reused_existing"))
    if should_reset:
        from benchmarks.sweevo.sandbox import reset_sweevo_workspace

        await reset_sweevo_workspace(sandbox_id)
    used_sandboxes.add(sandbox_id)
    return sweevo_sandbox


def _reuse_existing_auto_enabled() -> bool:
    """Return whether SWE-EVO tests may attach to an existing auto sandbox."""
    if os.getenv("EOS_SWEEVO_FORCE_FRESH_SANDBOX") == "1":
        return False
    return os.getenv("EOS_SWEEVO_REUSE_SANDBOX") == "1"


def _session_workspace_used_sandboxes(session: object) -> set[str]:
    """Track workspace use in the current pytest process only."""
    current = getattr(session, _SESSION_WORKSPACE_USED_ATTR, None)
    if isinstance(current, set):
        return current
    used: set[str] = set()
    setattr(session, _SESSION_WORKSPACE_USED_ATTR, used)
    return used


__all__ = [
    "RunReport",
    "run_sweevo_scenario",
    "sweevo_instance",
    "sweevo_sandbox",
    "workspace",
]
