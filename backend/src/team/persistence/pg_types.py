"""Minimal PostgreSQL-specific SQLAlchemy types for team persistence.

Historically used the ``ltree`` PostgreSQL extension. Now stores
scope labels as plain TEXT to remove the extension dependency.
"""

from __future__ import annotations

from sqlalchemy.types import UserDefinedType


class LTREE(UserDefinedType):
    """Plain TEXT column — backwards-compatible alias for former ltree type."""

    cache_ok = True

    def get_col_spec(self, **kw: object) -> str:
        return "TEXT"
