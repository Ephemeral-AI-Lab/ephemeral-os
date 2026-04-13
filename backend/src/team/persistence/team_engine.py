"""Async database bootstrap for team coordination stores."""

from __future__ import annotations

import importlib.util
import logging

from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from config.settings import Settings, load_settings
from db.base import Base
from db.engine import _add_missing_columns, _ensure_indexes, get_engine, get_session_factory, initialize_db

logger = logging.getLogger(__name__)

_async_engine: AsyncEngine | None = None
_async_session_factory: async_sessionmaker[AsyncSession] | None = None


def _ensure_team_models_registered() -> None:
    from team.persistence.exploration_memory_store import ExplorationMemoryRecord  # noqa: F401
    from team.persistence.file_change_store import FileChangeRecord  # noqa: F401
    from team.persistence.task_note_record import TaskNoteRecord  # noqa: F401
    from team.persistence.task_record import TaskRecord  # noqa: F401


def _async_database_url(url: str) -> URL:
    parsed = make_url(url)
    if parsed.drivername in {"postgresql+psycopg", "postgresql+asyncpg"}:
        return parsed
    if parsed.drivername in {"postgresql", "postgresql+psycopg2"}:
        return parsed.set(drivername="postgresql+psycopg")
    if parsed.drivername == "sqlite":
        return parsed.set(drivername="sqlite+aiosqlite")
    return parsed


def get_team_engine() -> AsyncEngine | None:
    return _async_engine


def get_team_session_factory() -> async_sessionmaker[AsyncSession] | None:
    return _async_session_factory


def create_team_engine(
    settings: Settings | None = None,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    global _async_engine, _async_session_factory
    if _async_engine is not None and _async_session_factory is not None:
        return _async_engine, _async_session_factory

    settings = settings or load_settings()
    if importlib.util.find_spec("greenlet") is None:
        raise RuntimeError(
            "Team runtime async stores require `greenlet`; sync the environment with project dependencies first."
        )

    sync_session_factory = get_session_factory() or initialize_db(settings.database)
    sync_engine = get_engine()
    if sync_session_factory is None or sync_engine is None:
        raise RuntimeError("Team runtime requires a configured database.")

    _ensure_team_models_registered()
    with sync_engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS ltree"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
    Base.metadata.create_all(sync_engine)
    _add_missing_columns(sync_engine)
    _ensure_indexes(sync_engine)

    _async_engine = create_async_engine(
        _async_database_url(settings.database.url),
        pool_pre_ping=settings.database.pool_pre_ping,
        pool_size=settings.database.pool_size,
        max_overflow=settings.database.max_overflow,
        echo=settings.database.echo,
    )
    _async_session_factory = async_sessionmaker(
        bind=_async_engine,
        autoflush=False,
        expire_on_commit=False,
    )
    logger.info("Team async engine initialised")
    return _async_engine, _async_session_factory
