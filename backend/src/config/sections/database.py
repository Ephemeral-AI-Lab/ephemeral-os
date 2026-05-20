"""Database config.

Environment bindings:
- ``EOS__DATABASE__URL``
- ``EPHEMERALOS_DATABASE_URL`` (legacy secret binding)
"""

from __future__ import annotations

from pydantic import Field

from config.base import ModuleConfigBase


class DatabaseConfig(ModuleConfigBase):
    """PostgreSQL database configuration."""

    url: str = ""
    pool_pre_ping: bool = True
    pool_size: int = Field(default=5, ge=1)
    max_overflow: int = Field(default=10, ge=0)
    echo: bool = False
