"""Pytest fixtures for the live e2e framework — dataset-agnostic.

Dataset-specific fixtures (e.g. ``sweevo_instance``, ``sweevo_sandbox``) live
in consumer adapters such as ``task_center_runner.sweevo_adapter``. Tests that need a
real Daytona sandbox depend on the consumer fixture; tests that need only
PG-backed stores depend on :func:`stores`.

The audit env vars (``EOS_SWEEVO_AUDIT_DIR`` / ``EOS_SWEEVO_AUDIT_TMP``) are
preserved verbatim during the migration; see
``docs/wiki/live-e2e-testing-framework-design.md`` "Open questions".
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from db.engine import get_engine, initialize_db
from task_center_runner.stores import (
    TaskCenterStoreBundle,
    create_per_test_task_center_stores,
)


@pytest.fixture(scope="session")
def db_engine() -> object | None:
    """Initialize the shared project engine once per pytest worker.

    Returns ``None`` (rather than skipping) when ``EPHEMERALOS_DATABASE_URL``
    is not set so unit-test collections that happen to import this fixture
    do not fail.
    """
    if not os.environ.get("EPHEMERALOS_DATABASE_URL"):
        return None
    if get_engine() is None:
        initialize_db()
    return get_engine()


@pytest.fixture
def stores(db_engine: object | None) -> Iterator[TaskCenterStoreBundle]:
    """Per-test PG schema-isolated TaskCenter stores.

    Skipped when ``EPHEMERALOS_DATABASE_URL`` is missing so unit-test
    collections that import this fixture do not fail.
    """
    if db_engine is None:
        pytest.skip(
            "EPHEMERALOS_DATABASE_URL not set — task_center_runner requires PostgreSQL"
        )
    bundle = create_per_test_task_center_stores()
    try:
        yield bundle
    finally:
        bundle.close()


@pytest.fixture
def audit_dir(tmp_path: Path) -> Path:
    """Resolve the audit base dir.

    - ``EOS_SWEEVO_AUDIT_TMP=1`` → use the test's ``tmp_path``.
    - ``EOS_SWEEVO_AUDIT_DIR`` set → use that absolute path.
    - Otherwise → ``<repo>/.sweevo_runs/`` resolved.
    """
    if os.getenv("EOS_SWEEVO_AUDIT_TMP") == "1":
        return tmp_path / "live_e2e_run"
    override = os.getenv("EOS_SWEEVO_AUDIT_DIR")
    base = Path(override) if override else Path(".sweevo_runs")
    return base.resolve()


__all__ = [
    "audit_dir",
    "db_engine",
    "stores",
]
