"""Compatibility wrapper for the durable PostgreSQL dispatcher store.

The live PG-backed implementation moved to :mod:`team.runtime.dispatcher_store`.
Keep this module as a thin alias so legacy imports and tests resolve to the
same class the runtime actually uses.
"""

from __future__ import annotations

from team.runtime.dispatcher_store import DispatcherStore as PGDispatcher

__all__ = ["PGDispatcher"]
