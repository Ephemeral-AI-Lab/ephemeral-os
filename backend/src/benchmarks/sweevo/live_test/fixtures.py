"""Pytest fixtures for the SWE-EVO live e2e framework (plan §12).

The live-Daytona fixtures are intentionally optional — pytest collection
should not fail if Daytona credentials are absent. Tests that need a real
sandbox depend on the ``sweevo_sandbox`` fixture which fails fast with a
clear message when Daytona is unavailable.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from benchmarks.sweevo.dataset import select_sweevo_instance
from benchmarks.sweevo.models import SWEEvoInstance
from benchmarks.sweevo.live_test.stores import (
    TaskCenterStoreBundle,
    create_in_memory_task_center_stores,
)

_DEFAULT_INSTANCE_ID = "dask__dask_2023.3.2_2023.4.0"
_WORKSPACE_USED_KEY = "sweevo_sandbox_used"


@pytest.fixture(scope="session")
def sweevo_instance() -> SWEEvoInstance:
    instance_id = os.getenv("EOS_SWEEVO_INSTANCE", _DEFAULT_INSTANCE_ID)
    return select_sweevo_instance(instance_id=instance_id)


@pytest.fixture(scope="session")
async def sweevo_sandbox(sweevo_instance: SWEEvoInstance) -> dict[str, object]:
    """Provision a real Daytona sandbox for the configured SWE-EVO instance.

    Skipped when Daytona is unreachable so unit-test collections that
    happen to import this fixture (via ``pytest_plugins``) do not fail.
    """
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
    """Per-test workspace reset on subsequent invocations (plan §3 decision 2).

    On the first call after a fresh sandbox provision the reset is skipped.
    Subsequent calls re-import :func:`reset_sweevo_workspace` lazily.
    """
    cache = request.session.config.cache
    if cache is not None and cache.get(_WORKSPACE_USED_KEY, False):
        from benchmarks.sweevo.sandbox import reset_sweevo_workspace

        await reset_sweevo_workspace(str(sweevo_sandbox["sandbox_id"]))
    elif cache is not None:
        cache.set(_WORKSPACE_USED_KEY, True)
    return sweevo_sandbox


@pytest.fixture
def audit_dir(tmp_path: Path) -> Path:
    """Resolve the audit base dir per plan §12.

    - ``EOS_SWEEVO_AUDIT_TMP=1`` → use the test's ``tmp_path``.
    - ``EOS_SWEEVO_AUDIT_DIR`` set → use that absolute path.
    - Otherwise → ``<repo>/.sweevo_runs/`` resolved.
    """
    if os.getenv("EOS_SWEEVO_AUDIT_TMP") == "1":
        return tmp_path / "sweevo_run"
    override = os.getenv("EOS_SWEEVO_AUDIT_DIR")
    base = Path(override) if override else Path(".sweevo_runs")
    return base.resolve()


@pytest.fixture
def stores() -> Iterator[TaskCenterStoreBundle]:
    bundle = create_in_memory_task_center_stores()
    try:
        yield bundle
    finally:
        bundle.close()


__all__ = [
    "audit_dir",
    "stores",
    "sweevo_instance",
    "sweevo_sandbox",
    "workspace",
]
