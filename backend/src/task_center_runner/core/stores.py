"""Per-test PostgreSQL schema isolation for live e2e TaskCenter stores.

Reuses the project's shared SQLAlchemy engine via ``db.engine.initialize_db()``
and carves a fresh schema per test so concurrent tests do not collide and the
production ``public`` schema is never touched.

Schema routing uses SQLAlchemy's ``schema_translate_map`` execution option on
a per-bundle engine clone — no engine-level listeners, no cross-test leakage.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy import Engine, MetaData, text
from sqlalchemy.orm import Session, sessionmaker

from db.base import Base
import db.models  # noqa: F401 — populate SQLAlchemy metadata
from db.engine import get_engine, initialize_db
from db.stores.attempt_store import AttemptStore
from db.stores.context_packet_store import ContextPacketStore
from db.stores.iteration_store import IterationStore
from db.stores.goal_store import GoalStore
from db.stores.task_center_store import TaskCenterStore


@dataclass(slots=True)
class TaskCenterStoreBundle:
    """Bundle of TaskCenter stores bound to a per-test PostgreSQL schema."""

    engine: Engine
    schema: str
    session_factory: sessionmaker[Session]
    task_store: TaskCenterStore
    goal_store: GoalStore
    iteration_store: IterationStore
    attempt_store: AttemptStore
    context_packet_store: ContextPacketStore

    def close(self) -> None:
        """Drop the per-test schema. The shared engine is never disposed."""
        with self.engine.begin() as conn:
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{self.schema}" CASCADE'))


def _ensure_initialized() -> Engine:
    """Bootstrap the shared engine when needed; reject non-postgres dialects."""
    engine = get_engine()
    if engine is None:
        initialize_db()
        engine = get_engine()
    if engine is None:
        raise RuntimeError(
            "EPHEMERALOS_DATABASE_URL not configured — set it to the project "
            "PostgreSQL DSN before running task_center_runner tests."
        )
    if engine.dialect.name != "postgresql":
        raise RuntimeError(
            f"task_center_runner requires PostgreSQL, got dialect={engine.dialect.name!r}"
        )
    return engine


def create_per_test_task_center_stores(
    *, schema_prefix: str = "task_center_runner"
) -> TaskCenterStoreBundle:
    """Carve a fresh schema, run create_all against it, return wired stores.

    DDL is emitted against a cloned metadata bound to the new schema; DML
    issued by the ORM against the original ``Base`` mappers is rewritten via
    ``schema_translate_map`` on a per-bundle engine clone so it lands in the
    per-test schema instead of ``public``.
    """
    shared_engine = _ensure_initialized()
    schema = f"{schema_prefix}_{uuid4().hex[:12]}"

    with shared_engine.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))

    test_metadata = MetaData(schema=schema)
    for table in Base.metadata.sorted_tables:
        table.to_metadata(test_metadata, schema=schema)
    test_metadata.create_all(shared_engine)

    # The bundle's `engine` is a per-bundle clone of the shared engine with a
    # schema_translate_map option, so any sessionmaker bound to ``bundle.engine``
    # sends DML to the per-test schema rather than ``public``.
    routed_engine = shared_engine.execution_options(
        schema_translate_map={None: schema}
    )
    session_factory = sessionmaker(
        bind=routed_engine, autoflush=False, expire_on_commit=False
    )

    bundle = TaskCenterStoreBundle(
        engine=routed_engine,
        schema=schema,
        session_factory=session_factory,
        task_store=TaskCenterStore(),
        goal_store=GoalStore(),
        iteration_store=IterationStore(),
        attempt_store=AttemptStore(),
        context_packet_store=ContextPacketStore(),
    )
    for store in (
        bundle.task_store,
        bundle.goal_store,
        bundle.iteration_store,
        bundle.attempt_store,
        bundle.context_packet_store,
    ):
        store.initialize(session_factory)
    return bundle


__all__ = [
    "TaskCenterStoreBundle",
    "create_per_test_task_center_stores",
]
