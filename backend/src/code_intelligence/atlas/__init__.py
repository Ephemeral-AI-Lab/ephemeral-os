"""Project Atlas — persistent, chunked cache of scout briefs (Phase 2).

Atlas chunks use the exact same schema as Phase 1 scout briefs; consumers
see them as ordinary briefings after staging. The only added moving parts
are:

- :class:`team.atlas.store.AtlasStore` — transactional SQLAlchemy store.
- :class:`team.atlas.model.ProjectAtlasRecord` / ``ProjectAtlasChunkRecord``
  — ORM tables registered on ``db.base.Base``.
- :mod:`team.atlas.freshness` — ledger + content-hash staleness checks.
- :mod:`team.atlas.identity` — stable ``project_key`` derivation.

Everything in this package is safe to import without touching the DB.
Durable writes go through ``AtlasStore`` only after explicit
``initialize(session_factory)``. Heavier SQLAlchemy-backed symbols are
loaded lazily so callers that only need lightweight helpers like
``project_key_for`` do not pay the DB import cost.
"""

from __future__ import annotations

from importlib import import_module

__all__ = [
    "AtlasChunk",
    "AtlasStore",
    "ProjectAtlasChunkRecord",
    "ProjectAtlasRecord",
    "changes_since_chunk",
    "hash_paths_under",
    "is_chunk_fresh",
    "is_subsystem_stale",
    "project_key_for",
]


def __getattr__(name: str):
    if name == "project_key_for":
        return import_module("team.atlas.identity").project_key_for
    if name in {
        "changes_since_chunk",
        "hash_paths_under",
        "is_chunk_fresh",
        "is_subsystem_stale",
    }:
        return getattr(import_module("team.atlas.freshness"), name)
    if name in {"AtlasChunk", "AtlasStore"}:
        return getattr(import_module("team.atlas.store"), name)
    if name in {"ProjectAtlasChunkRecord", "ProjectAtlasRecord"}:
        return getattr(import_module("team.atlas.model"), name)
    raise AttributeError(name)
