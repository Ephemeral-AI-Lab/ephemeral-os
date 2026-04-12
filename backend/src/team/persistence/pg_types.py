"""Minimal PostgreSQL-specific SQLAlchemy types for team persistence."""

from __future__ import annotations

from sqlalchemy.types import UserDefinedType


class LTREE(UserDefinedType):
    """Minimal ``ltree`` type without adding a new dependency."""

    cache_ok = True

    def get_col_spec(self, **kw: object) -> str:
        return "LTREE"
