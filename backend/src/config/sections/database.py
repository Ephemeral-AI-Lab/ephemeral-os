"""Database config.

Environment bindings:
- ``EOS__DATABASE__URL``
- ``EPHEMERALOS_DATABASE_URL`` (process-env override)
"""

from __future__ import annotations

from pydantic import Field

from config.base import ModuleConfigBase

DEFAULT_SQLITE_DATABASE_URL = "sqlite:///./.ephemeralos/ephemeralos.db"


class DatabaseConfig(ModuleConfigBase):
    """Database configuration.

    SQLite is the local/default runtime database. PostgreSQL remains available
    through an explicit process environment override.
    """

    url: str = DEFAULT_SQLITE_DATABASE_URL
    pool_pre_ping: bool = True
    pool_size: int = Field(default=5, ge=1)
    max_overflow: int = Field(default=10, ge=0)
    echo: bool = False
