"""Async database bootstrap for team coordination stores.

Delegates engine creation to db.engine which manages both sync and async
engines. This module handles team-specific concerns: registering team
ORM models and creating team tables.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy.engine import Engine

from db.base import Base
from db.engine import (
    _add_missing_columns,
    _ensure_indexes,
    get_async_engine,
    get_async_session_factory,
    get_engine,
    get_session_factory,
    initialize_db,
)

if TYPE_CHECKING:
    from config.settings import Settings
    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)


def _ensure_team_models_registered() -> None:
    """Import team ORM models so Base.metadata knows about them."""
    from team.persistence.tasks_sql import TaskRecord  # noqa: F401


def _ensure_team_schema(engine: Engine) -> None:
    """Register team models and backfill any missing team columns/indexes."""
    _ensure_team_models_registered()
    Base.metadata.create_all(engine)
    _add_missing_columns(engine)
    _ensure_indexes(engine)


def create_team_engine(
    settings: "Settings | None" = None,
) -> "tuple[AsyncEngine, async_sessionmaker[AsyncSession]]":
    """Ensure the team coordination tables exist and return the async engine.

    Registers team ORM models and creates/repairs supported tables.
    Delegates engine creation to db.engine.initialize_db.
    """
    factory = get_async_session_factory()
    engine = get_async_engine()
    sync_engine = get_engine()
    if factory is not None and engine is not None and sync_engine is not None:
        _ensure_team_schema(sync_engine)
        return engine, factory

    # Ensure sync+async engines exist.
    if get_session_factory() is None:
        if settings is not None:
            initialize_db(settings.database)
        else:
            from config.settings import load_settings

            initialize_db(load_settings().database)

    sync_engine = get_engine()
    if sync_engine is None:
        raise RuntimeError("Team runtime requires a configured database.")

    _ensure_team_schema(sync_engine)

    engine = get_async_engine()
    factory = get_async_session_factory()
    if engine is None or factory is None:
        raise RuntimeError(
            "Team runtime requires an async database engine. "
            "Ensure greenlet is installed and EPHEMERALOS_DATABASE_URL is set."
        )

    logger.info("Team async engine ready")
    return engine, factory
