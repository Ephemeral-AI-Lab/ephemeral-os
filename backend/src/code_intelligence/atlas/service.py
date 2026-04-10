"""Atlas service facade owned by the code-intelligence runtime."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from code_intelligence.atlas.freshness import (
    DEFAULT_ATLAS_MAX_AGE_SECONDS,
    canonical_subsystem_key,
    chunk_reuse_status,
)
from code_intelligence.atlas.persistence import persist_brief_to_atlas
from code_intelligence.atlas.store import AtlasChunk, AtlasStore, get_default_store


@dataclass(frozen=True)
class AtlasLookupBatch:
    """Resolved Atlas lookup decisions for one planner request."""

    entries: list[dict[str, Any]]
    atlas_disabled: bool


class AtlasService:
    """Service-layer Atlas API hanging off ``CodeIntelligenceService``."""

    def __init__(
        self,
        *,
        workspace_root: str,
        ledger: Any | None = None,
        symbol_index: Any | None = None,
        store: AtlasStore | None = None,
    ) -> None:
        self.workspace_root = workspace_root
        self.ledger = ledger
        self.symbol_index = symbol_index
        self.store = store if store is not None else get_default_store()

    def status(self) -> dict[str, Any]:
        """Return a small status summary for the parent CI service."""
        store_ready = self.store.is_initialised()
        return {
            "store_initialized": store_ready,
            "workspace_root": self.workspace_root,
            "ledger_attached": self.ledger is not None,
            "symbol_index_attached": self.symbol_index is not None,
        }

    def lookup_subsystems(
        self,
        *,
        team_run: Any,
        subsystems: list[str],
        max_age_seconds: float | None = DEFAULT_ATLAS_MAX_AGE_SECONDS,
    ) -> AtlasLookupBatch:
        """Resolve Atlas reuse decisions for *subsystems* in *team_run*."""
        if not subsystems:
            return AtlasLookupBatch(entries=[], atlas_disabled=not self.is_available())

        project_ctx = getattr(team_run, "project_context", None)
        project_key = getattr(project_ctx, "project_key", "") or ""
        if not project_key or not self.is_available():
            return AtlasLookupBatch(
                entries=[self._as_scout(subsystem) for subsystem in subsystems],
                atlas_disabled=True,
            )

        keys = [self._normalise_key(raw) for raw in subsystems]
        wanted = [key for key in keys if key]
        chunks = self.store.get_chunks(project_key, list(dict.fromkeys(wanted)))
        chunk_map = {chunk.subsystem: chunk for chunk in chunks}

        entries: list[dict[str, Any]] = []
        for key in wanted:
            chunk = chunk_map.get(key)
            if chunk is None:
                entries.append(self._as_scout(key))
                continue
            entries.append(
                self._decide(
                    chunk=chunk,
                    team_run=team_run,
                    max_age_seconds=max_age_seconds,
                )
            )
        return AtlasLookupBatch(entries=entries, atlas_disabled=False)

    def persist_scout_brief(
        self,
        *,
        team_run: Any,
        brief: dict[str, Any],
        reason: str = "direct-scout",
    ) -> bool:
        """Persist one scout-shaped brief into Atlas."""
        return persist_brief_to_atlas(
            team_run=team_run,
            brief=brief,
            ci_service=self,
            store=self.store,
            reason=reason,
        )

    def is_available(self) -> bool:
        return self.store.is_initialised()

    def _decide(
        self,
        *,
        chunk: AtlasChunk,
        team_run: Any,
        max_age_seconds: float | None,
    ) -> dict[str, Any]:
        fresh, reason = chunk_reuse_status(
            chunk,
            ledger=self.ledger,
            max_age_seconds=max_age_seconds,
        )
        if fresh:
            staged_ref = self._stage_into_run(team_run, chunk)
            return {
                "subsystem": chunk.subsystem,
                "action": "use",
                "stale": False,
                "staleness_reason": None,
                "staged_artifact_ref": staged_ref,
                "symbol_ids": list(chunk.symbol_ids),
            }
        return {
            "subsystem": chunk.subsystem,
            "action": "refresh",
            "stale": True,
            "staleness_reason": reason,
            "staged_artifact_ref": None,
            "symbol_ids": list(chunk.symbol_ids),
        }

    @staticmethod
    def _as_scout(subsystem: str) -> dict[str, Any]:
        return {
            "subsystem": subsystem,
            "action": "scout",
            "stale": False,
            "staleness_reason": None,
            "staged_artifact_ref": None,
            "symbol_ids": [],
        }

    @staticmethod
    def _normalise_key(raw: str) -> str:
        if not isinstance(raw, str):
            return ""
        return canonical_subsystem_key([raw])

    @staticmethod
    def _stage_into_run(team_run: Any, chunk: AtlasChunk) -> str:
        ref = f"atlas:{chunk.subsystem}:{uuid.uuid4().hex[:8]}"
        team_run.artifacts.save(ref, dict(chunk.brief))
        return ref
