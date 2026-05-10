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
_WORKSPACE_USED_KEY = "sweevo_sandbox_used"


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
    reuse_existing_auto = os.getenv("EOS_SWEEVO_FORCE_FRESH_SANDBOX") != "1"
    return await create_sweevo_test_sandbox(
        sweevo_instance,
        register_snapshot=True,
        reuse_existing_auto=reuse_existing_auto,
    )


@pytest.fixture
async def workspace(
    sweevo_sandbox: dict[str, object],
    request: pytest.FixtureRequest,
) -> dict[str, object]:
    """Per-test workspace reset on subsequent invocations."""
    cache = request.session.config.cache
    if cache is not None and cache.get(_WORKSPACE_USED_KEY, False):
        from benchmarks.sweevo.sandbox import reset_sweevo_workspace

        await reset_sweevo_workspace(str(sweevo_sandbox["sandbox_id"]))
    elif cache is not None:
        cache.set(_WORKSPACE_USED_KEY, True)
    return sweevo_sandbox


__all__ = [
    "RunReport",
    "run_sweevo_scenario",
    "sweevo_instance",
    "sweevo_sandbox",
    "workspace",
]
