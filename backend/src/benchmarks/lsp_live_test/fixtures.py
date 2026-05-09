"""Pytest fixtures for the LSP live e2e suite.

Reuses the SWE-EVO sandbox provisioning (Daytona-backed conda image with
Node/Pyright install support). The sandbox is session-scoped — all LSP
scenarios run against the same sandbox, sharing the Pyright session
across scenarios where the manifest hasn't changed.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from benchmarks.sweevo.dataset import select_sweevo_instance
from benchmarks.sweevo.models import SWEEvoInstance, _REPO_DIR

# dask__dask_2023.3.2_2023.4.0 has a stable Docker image and the existing
# sweevo live e2e suite already validates it builds reliably.
_DEFAULT_LSP_INSTANCE = "dask__dask_2023.3.2_2023.4.0"


@pytest.fixture(scope="session")
def lsp_sweevo_instance() -> SWEEvoInstance:
    instance_id = os.getenv("EOS_LSP_INSTANCE", _DEFAULT_LSP_INSTANCE)
    return select_sweevo_instance(instance_id=instance_id)


@pytest.fixture(scope="session")
async def lsp_sandbox(
    lsp_sweevo_instance: SWEEvoInstance,
) -> Iterator[dict[str, object]]:
    from benchmarks.sweevo.sandbox import create_sweevo_test_sandbox
    from sandbox.provider.daytona.bootstrap import bootstrap_daytona_provider

    bootstrap_daytona_provider()
    bundle = await create_sweevo_test_sandbox(
        lsp_sweevo_instance, register_snapshot=True
    )
    yield bundle


@pytest.fixture
def lsp_repo_root() -> str:
    return _REPO_DIR


__all__ = ["lsp_sandbox", "lsp_sweevo_instance", "lsp_repo_root"]
