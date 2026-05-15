"""Integration test for task_center_runner.stores — per-schema PostgreSQL isolation.

Skipped when ``EPHEMERALOS_DATABASE_URL`` is not configured. When configured,
verifies that:

1. A bundle creates a fresh per-test schema.
2. ORM writes via the routed session_factory land in the per-test schema.
3. The same row is invisible from the ``public`` schema.
4. ``close()`` drops the schema cascade.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text

from task_center_runner.stores import (
    TaskCenterStoreBundle,
    create_per_test_task_center_stores,
)


pytestmark = pytest.mark.skipif(
    not os.environ.get("EPHEMERALOS_DATABASE_URL"),
    reason="EPHEMERALOS_DATABASE_URL not set — task_center_runner requires PostgreSQL",
)


def _new_run(bundle: TaskCenterStoreBundle, *, run_id: str, request_id: str) -> str:
    bundle.task_store.create_request(
        request_id=request_id,
        cwd="/tmp",
        sandbox_id=None,
        request_prompt="task_center_runner stores test",
    )
    row = bundle.task_store.create_run(
        task_center_run_id=run_id, request_id=request_id
    )
    return row["id"]


def test_per_schema_isolation_round_trip() -> None:
    bundle = create_per_test_task_center_stores()
    schema = bundle.schema
    try:
        assert schema.startswith("live_e2e_")
        with bundle.engine.connect() as conn:
            existing = conn.execute(
                text(
                    "SELECT schema_name FROM information_schema.schemata "
                    "WHERE schema_name = :s"
                ),
                {"s": schema},
            ).scalar()
        assert existing == schema, f"schema {schema!r} not created"

        run_id = _new_run(bundle, run_id=f"r-{schema}", request_id="req-1")

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

        fetched = bundle.task_store.get_run(run_id)
        assert fetched is not None and fetched["id"] == run_id
    finally:
        bundle.close()

    with bundle.engine.connect() as conn:
        still = conn.execute(
            text(
                "SELECT schema_name FROM information_schema.schemata "
                "WHERE schema_name = :s"
            ),
            {"s": schema},
        ).scalar()
    assert still is None, f"schema {schema!r} not dropped"


def test_two_bundles_dont_collide() -> None:
    a = create_per_test_task_center_stores()
    b = create_per_test_task_center_stores()
    try:
        assert a.schema != b.schema
        run_a = _new_run(a, run_id=f"a-{a.schema}", request_id="req-a")
        run_b = _new_run(b, run_id=f"b-{b.schema}", request_id="req-b")

        with a.engine.connect() as conn:
            visible_in_a = conn.execute(
                text(
                    f'SELECT count(*) FROM "{a.schema}".task_center_runs '
                    "WHERE id = :rid"
                ),
                {"rid": run_a},
            ).scalar()
            cross_in_a = conn.execute(
                text(
                    f'SELECT count(*) FROM "{a.schema}".task_center_runs '
                    "WHERE id = :rid"
                ),
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
    """Both bundles wrap the same shared engine pool — no extra connections."""
    a: TaskCenterStoreBundle = create_per_test_task_center_stores()
    b: TaskCenterStoreBundle = create_per_test_task_center_stores()
    try:
        # Each bundle's engine is a per-bundle execution_options clone but the
        # underlying connection pool is the shared project pool.
        assert a.engine.pool is b.engine.pool
    finally:
        a.close()
        b.close()
