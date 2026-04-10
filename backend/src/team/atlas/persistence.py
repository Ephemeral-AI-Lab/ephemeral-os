"""Shared helpers for converting scout briefs into durable Atlas chunks."""

from __future__ import annotations

import time
from typing import Any

from team.atlas.freshness import hash_paths_under
from team.atlas.store import AtlasChunk
from team.context.canonicalize import scope_of_artifact


def build_chunk_from_brief(
    *,
    brief: dict[str, Any],
    repo_root: str,
    ci_service: Any | None = None,
    subsystem: str | None = None,
) -> AtlasChunk:
    """Build one Atlas chunk from a scout-shaped brief."""
    resolved_subsystem = (subsystem or scope_of_artifact(brief) or "").strip()
    if not resolved_subsystem:
        raise ValueError("brief has no canonical scope for Atlas persistence")
    brief_snapshot = brief.get("snapshot_time") if isinstance(brief, dict) else None
    snapshot_time = (
        float(brief_snapshot)
        if isinstance(brief_snapshot, (int, float)) and brief_snapshot > 0
        else time.time()
    )
    target_paths = _target_paths(brief)
    content_hashes = hash_paths_under(target_paths, repo_root)
    symbol_ids = _collect_symbol_ids(ci_service, content_hashes.keys())
    return AtlasChunk(
        subsystem=resolved_subsystem,
        brief=dict(brief),
        content_hashes=content_hashes,
        symbol_ids=symbol_ids,
        snapshot_time=snapshot_time,
    )

def _target_paths(brief: dict[str, Any]) -> list[str]:
    raw = brief.get("target_paths") if isinstance(brief, dict) else None
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw if isinstance(item, str) and item.strip()]


def _collect_symbol_ids(ci_service: Any, file_paths: Any) -> list[str]:
    symbol_index = getattr(ci_service, "symbol_index", None)
    if symbol_index is None:
        return []
    out: list[str] = []
    for path in file_paths:
        try:
            symbols = symbol_index.file_symbols(path)
        except Exception:
            continue
        for sym in symbols:
            name = getattr(sym, "name", None)
            if name:
                out.append(f"{path}:{name}")
    return out
