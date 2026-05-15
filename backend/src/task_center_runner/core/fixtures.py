"""Pytest fixtures for ``task_center_runner`` — canonical location.

Phase 5 of the restructure consolidates the former top-level
``task_center_runner/fixtures.py`` (`db_engine`, `stores`, `audit_dir`)
with the Phase-3 ``pipeline_run`` fixture into one module under
``core/``. The legacy ``task_center_runner.fixtures`` path is preserved
via the ``live_e2e/`` shim's prefix remap so any caller using the old
path continues to resolve to this module.

Fixtures:

- ``db_engine``: session-scoped; bootstraps the project PG engine if
  ``EPHEMERALOS_DATABASE_URL`` is configured, else returns ``None``.
- ``stores``: per-test; yields a PG-schema-isolated
  ``TaskCenterStoreBundle``. Skips if ``db_engine`` is ``None``.
- ``audit_dir``: per-test; resolves the audit base directory honoring
  ``EOS_SWEEVO_AUDIT_TMP`` / ``EOS_SWEEVO_AUDIT_DIR``.
- ``pipeline_run``: yields a tracker callable; on teardown awaits each
  tracked report's ``performance_report_task`` so tests do not leak
  unfinished asyncio tasks.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from db.engine import get_engine, initialize_db
from task_center_runner.core.stores import (
    TaskCenterStoreBundle,
    create_per_test_task_center_stores,
)


_log = logging.getLogger(__name__)


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


@pytest.fixture
def pipeline_run() -> Iterator[Any]:
    """Yield a tracker that auto-awaits each tracked report's perf-report task.

    Usage::

        async def test_something(pipeline_run):
            report = await run_scenario(scenario, ...)
            pipeline_run(report)  # register; teardown awaits the perf task
            ...

    On teardown the fixture awaits every tracked report's
    ``performance_report_task`` if non-None.
    """
    tracked: list[Any] = []

    def track(report: Any) -> Any:
        tracked.append(report)
        return report

    yield track

    pending = [
        getattr(report, "performance_report_task", None) for report in tracked
    ]
    pending_tasks = [task for task in pending if task is not None and not task.done()]
    if not pending_tasks:
        return

    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    async def _drain() -> None:
        results = await asyncio.gather(*pending_tasks, return_exceptions=True)
        for task, result in zip(pending_tasks, results, strict=True):
            if isinstance(result, BaseException):
                _log.warning(
                    "pipeline_run: perf-report task %s raised: %s", task.get_name(), result
                )
            else:
                _log.debug(
                    "pipeline_run: perf-report task %s wrote %s",
                    task.get_name(),
                    result,
                )

    if loop.is_running():
        _log.warning(
            "pipeline_run: event loop still running at teardown; "
            "perf-report draining may not complete before the loop closes."
        )
    else:
        loop.run_until_complete(_drain())


__all__ = ["audit_dir", "db_engine", "pipeline_run", "stores"]
