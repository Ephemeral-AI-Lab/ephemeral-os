"""Integration test for task_center_runner.core.stores isolation.

Skipped only when no database URL is configured. The repository default is
SQLite, so local runs do not require Postgres. The test verifies that:

1. A bundle creates a fresh per-test store.
2. ORM writes via the routed session_factory land in that isolated store.
3. A second bundle does not see the first bundle's rows.
4. ``close()`` releases per-test resources.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text

from task_center_runner.tests._live_config import database_configured
from task_center_runner.core.stores import (
    TaskStoreBundle,
    create_per_test_task_center_stores,
)


pytestmark = pytest.mark.skipif(
    not database_configured(),
    reason="database URL not configured",
)


def _new_run(bundle: TaskStoreBundle, *, run_id: str, request_id: str) -> str:
    del run_id
    bundle.task_store.create_request(
        request_id=request_id,
        cwd="/tmp",
        sandbox_id=None,
        request_prompt="task_center_runner stores test",
    )
    return request_id


def test_per_schema_isolation_round_trip() -> None:
    bundle = create_per_test_task_center_stores()
    schema = bundle.schema
    db_path = (
        Path(bundle.engine.url.database)
        if bundle.engine.dialect.name == "sqlite" and bundle.engine.url.database
        else None
    )
    try:
        assert schema.startswith("task_center_runner_")
        if bundle.engine.dialect.name == "postgresql":
            with bundle.engine.connect() as conn:
                existing = conn.execute(
                    text(
                        "SELECT schema_name FROM information_schema.schemata "
                        "WHERE schema_name = :s"
                    ),
                    {"s": schema},
                ).scalar()
            assert existing == schema, f"schema {schema!r} not created"
        elif db_path is not None:
            assert db_path.exists(), f"sqlite bundle db {db_path!s} not created"

        run_id = _new_run(bundle, run_id=f"r-{schema}", request_id="req-1")

        if bundle.engine.dialect.name == "postgresql":
            with bundle.engine.connect() as conn:
                rows_in_schema = conn.execute(
                    text(
                        f'SELECT count(*) FROM "{schema}".task_center_runs '
                        "WHERE id = :rid"
                    ),
                    {"rid": run_id},
                ).scalar()
            assert rows_in_schema == 1, "row not found in per-test schema"

            with bundle.engine.connect() as conn:
                rows_in_public = conn.execute(
                    text("SELECT count(*) FROM public.task_center_runs WHERE id = :rid"),
                    {"rid": run_id},
                ).scalar()
            assert rows_in_public == 0, "row leaked into public schema"
        else:
            with bundle.engine.connect() as conn:
                rows_in_bundle = conn.execute(
                    text("SELECT count(*) FROM task_center_runs WHERE id = :rid"),
                    {"rid": run_id},
                ).scalar()
            assert rows_in_bundle == 1, "row not found in sqlite bundle db"

        fetched = bundle.task_store.get_run(run_id)
        assert fetched is not None and fetched["id"] == run_id
    finally:
        bundle.close()

    if bundle.engine.dialect.name == "postgresql":
        with bundle.engine.connect() as conn:
            still = conn.execute(
                text(
                    "SELECT schema_name FROM information_schema.schemata "
                    "WHERE schema_name = :s"
                ),
                {"s": schema},
            ).scalar()
        assert still is None, f"schema {schema!r} not dropped"
    elif db_path is not None:
        assert not db_path.exists(), f"sqlite bundle db {db_path!s} not removed"


def test_two_bundles_dont_collide() -> None:
    a = create_per_test_task_center_stores()
    b = create_per_test_task_center_stores()
    try:
        assert a.schema != b.schema
        run_a = _new_run(a, run_id=f"a-{a.schema}", request_id="req-a")
        run_b = _new_run(b, run_id=f"b-{b.schema}", request_id="req-b")

        if a.engine.dialect.name == "postgresql":
            table_name = f'"{a.schema}".task_center_runs'
        else:
            table_name = "task_center_runs"
        with a.engine.connect() as conn:
            visible_in_a = conn.execute(
                text(f"SELECT count(*) FROM {table_name} WHERE id = :rid"),
                {"rid": run_a},
            ).scalar()
            cross_in_a = conn.execute(
                text(f"SELECT count(*) FROM {table_name} WHERE id = :rid"),
                {"rid": run_b},
            ).scalar()
        assert visible_in_a == 1
        assert cross_in_a == 0
        # And b's bundle does NOT see a's row.
        assert b.task_store.get_run(run_a) is None
        assert a.task_store.get_run(run_b) is None
    finally:
        a.close()
        b.close()


def test_bundle_engine_pool_is_shared() -> None:
    """Bundle pool ownership matches the configured database dialect."""
    a: TaskStoreBundle = create_per_test_task_center_stores()
    b: TaskStoreBundle = create_per_test_task_center_stores()
    try:
        if a.engine.dialect.name == "postgresql":
            # Each bundle's engine is a per-bundle execution_options clone but the
            # underlying connection pool is the shared project pool.
            assert a.engine.pool is b.engine.pool
        else:
            assert a.engine.pool is not b.engine.pool
            assert a.engine.url.database != b.engine.url.database
    finally:
        a.close()
        b.close()
