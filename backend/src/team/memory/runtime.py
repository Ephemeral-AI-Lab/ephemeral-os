"""Runtime helpers for persisting typed team memory records."""

from __future__ import annotations

from typing import Any

from team.memory.store import TeamMemoryRecord, get_default_store


def persist_memory_record(
    *,
    project_key: str,
    repo_root: str,
    kind: str,
    scope: dict[str, Any],
    content: dict[str, Any],
    source: dict[str, Any],
    status: str = "active",
    stale_hint: str = "",
    superseded_by: str = "",
) -> bool:
    """Persist one typed memory record when the store is available."""
    store = get_default_store()
    if not project_key or not store.is_initialised():
        return False
    return store.append(
        TeamMemoryRecord(
            project_key=project_key,
            repo_root=repo_root,
            kind=kind,
            status=status,
            scope=dict(scope),
            content=dict(content),
            source=dict(source),
            stale_hint=stale_hint,
            superseded_by=superseded_by,
        )
    )
