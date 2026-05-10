"""Compat shim — re-export TaskCenterStoreBundle from ``live_e2e.stores``.

Legacy callers import ``create_in_memory_task_center_stores`` from this module.
The generic framework's stores are PostgreSQL-backed with per-test schema
isolation; this shim forwards to :func:`live_e2e.stores.create_per_test_task_center_stores`
so existing tests keep working without changes.
"""

from __future__ import annotations

from live_e2e.stores import (
    TaskCenterStoreBundle,
    create_per_test_task_center_stores,
)


def create_in_memory_task_center_stores() -> TaskCenterStoreBundle:
    """Legacy alias for :func:`create_per_test_task_center_stores`.

    Despite the name, the bundle is now backed by a per-test PostgreSQL
    schema rather than an in-memory SQLite engine — the migration to PG is
    described in ``docs/wiki/live-e2e-testing-framework-design.md``.
    """
    return create_per_test_task_center_stores()


__all__ = [
    "TaskCenterStoreBundle",
    "create_in_memory_task_center_stores",
]
