"""SWE-EVO pytest fixtures — wires the SWE-EVO dataset into ``live_e2e``.

Re-exports ``audit_dir``, ``stores``, and ``db_engine`` from
:mod:`live_e2e.fixtures` so existing tests find the same fixture names. Adds
the SWE-EVO-specific fixtures (``sweevo_instance``, ``sweevo_sandbox``,
``workspace``).
"""

from __future__ import annotations

import os

import pytest

from benchmarks.sweevo.dataset import select_sweevo_instance
from benchmarks.sweevo.models import SWEEvoInstance
from live_e2e.fixtures import audit_dir, db_engine, stores  # noqa: F401 — re-exported

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
    """Per-test workspace reset on subsequent invocations.

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


__all__ = [
    "audit_dir",
    "db_engine",
    "stores",
    "sweevo_instance",
    "sweevo_sandbox",
    "workspace",
]
