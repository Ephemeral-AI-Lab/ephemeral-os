"""``atlas_lookup`` — planner-side read path for the Project Atlas.

The planner calls ``atlas_lookup`` with a list of subsystem keys
(canonical scope strings, or raw paths that this tool normalises). Each
entry comes back as a decision object:

- ``action="use"`` — the chunk is fresh (content hashes still match on
  disk). The brief body is staged into the current ``TeamRun.artifacts``
  store and the resulting artifact ref is returned.
- ``action="refresh"`` — the chunk exists but its scope has drifted.
  The planner should treat atlas as unavailable for this planning turn
  and fall back to fresh exploration.
- ``action="scout"`` — no chunk exists at all. The planner should fall
  back to fresh exploration.

Freshness uses :func:`team.atlas.freshness.is_chunk_fresh`, which is
git-independent — it consults the in-memory edit ledger (if available)
and falls back to stored per-file content hashes on cold start.
Decision arithmetic lives here (not in a prompt) so the behaviour is
deterministic and testable in isolation. Atlas maintenance itself is
runtime/backend work that may be triggered opportunistically after this
tool returns.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from team.atlas.freshness import (
    DEFAULT_ATLAS_MAX_AGE_SECONDS,
    canonical_subsystem_key,
    chunk_reuse_status,
)
from team.atlas.store import AtlasChunk, AtlasStore, get_default_store
from team.runtime.registry import get as _get_team_run
from tools.core.base import ToolExecutionContext, ToolResult
from tools.core.decorator import tool

logger = logging.getLogger(__name__)


@tool(
    name="atlas_lookup",
    description=(
        "Consult the persistent Project Atlas for one or more subsystems. "
        "Returns a per-subsystem decision (use | refresh | scout). Use-marked "
        "entries include a staged artifact_ref you can attach as a briefing. "
        "Refresh/scout results mean the planner should use fresh exploration "
        "for this turn; atlas maintenance is handled by the runtime."
    ),
    read_only=True,
)
async def atlas_lookup(
    subsystems: list[str],
    *,
    context: ToolExecutionContext,
) -> ToolResult:
    """Look up atlas chunks and stage fresh ones into the current run.

    Args:
        subsystems: Subsystem keys to look up. Each entry is either an
            already-canonical scope string (from a prior build) or a raw
            path — both are accepted and normalised identically.

    Returns:
        lookups (list): Per-subsystem decision objects. Each has
            ``subsystem``, ``action`` ("use" | "refresh" | "scout"),
            ``stale`` (bool), ``staleness_reason`` (optional string), and
            ``staged_artifact_ref`` (artifact id when action == "use",
            else null).
    """
    if not subsystems:
        return ToolResult(output="atlas_lookup: no subsystems supplied", is_error=True)

    team_run_id = context.metadata.get("team_run_id")
    if not team_run_id:
        return ToolResult(
            output="atlas_lookup unavailable: no team_run_id in execution context",
            is_error=True,
        )
    team_run = _get_team_run(team_run_id)
    if team_run is None:
        return ToolResult(
            output=f"atlas_lookup: team_run {team_run_id!r} not registered",
            is_error=True,
        )

    project_ctx = team_run.project_context
    project_key = getattr(project_ctx, "project_key", "") or ""
    if not project_key:
        # Atlas disabled — every subsystem falls back to "scout".
        entries = [_as_scout(s) for s in subsystems]
        return _build_result(entries, atlas_disabled=True)

    store = _resolve_store(context)
    if store is None or not store.is_initialised():
        entries = [_as_scout(s) for s in subsystems]
        return _build_result(entries, atlas_disabled=True)

    ledger = _resolve_ledger(context)

    max_age_seconds = _resolve_max_age_seconds(context)
    keys = [_normalise_key(raw) for raw in subsystems]
    wanted = [key for key in keys if key]
    chunks = store.get_chunks(project_key, list(dict.fromkeys(wanted)))
    chunk_map = {chunk.subsystem: chunk for chunk in chunks}

    entries: list[dict[str, Any]] = []
    for key in wanted:
        chunk = chunk_map.get(key)
        if chunk is None:
            entries.append(_as_scout(key))
            continue
        entries.append(
            _decide(
                chunk=chunk,
                team_run=team_run,
                ledger=ledger,
                max_age_seconds=max_age_seconds,
            )
        )
    team_run.note_atlas_lookup(entries, source="atlas_lookup")
    return _build_result(entries, atlas_disabled=False)


# ---------------------------------------------------------------------------
# Decision arithmetic
# ---------------------------------------------------------------------------


def _decide(
    *,
    chunk: AtlasChunk,
    team_run: Any,
    ledger: Any = None,
    max_age_seconds: float | None = None,
) -> dict[str, Any]:
    """Resolve a single chunk into use/refresh using ledger → hash → conservative."""
    staged_ref = _stage_into_run(team_run, chunk)
    fresh, reason = _freshness(
        chunk,
        ledger,
        max_age_seconds=max_age_seconds,
    )
    if fresh:
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
        "staged_artifact_ref": staged_ref,
        "symbol_ids": list(chunk.symbol_ids),
    }


def _freshness(
    chunk: AtlasChunk,
    ledger: Any,
    *,
    max_age_seconds: float | None,
) -> tuple[bool, str | None]:
    """Return the shared atlas reuse decision for *chunk*."""
    return chunk_reuse_status(
        chunk,
        ledger=ledger,
        max_age_seconds=max_age_seconds,
    )


def _as_scout(subsystem: str) -> dict[str, Any]:
    return {
        "subsystem": subsystem,
        "action": "scout",
        "stale": False,
        "staleness_reason": None,
        "staged_artifact_ref": None,
        "symbol_ids": [],
    }


def _normalise_key(raw: str) -> str:
    """Canonicalise a single subsystem key."""
    if not isinstance(raw, str):
        return ""
    # Single-path canonicalisation is idempotent on already-canonical keys.
    return canonical_subsystem_key([raw])


def _stage_into_run(team_run: Any, chunk: AtlasChunk) -> str:
    """Persist the chunk's brief in the current run's artifact store.

    Generates a stable-within-run ref so briefings can reference the
    staged brief the same way they reference a live scout artifact.
    """
    ref = f"atlas:{chunk.subsystem}:{uuid.uuid4().hex[:8]}"
    team_run.artifacts.save(ref, dict(chunk.brief))
    return ref


def _build_result(entries: list[dict[str, Any]], *, atlas_disabled: bool) -> ToolResult:
    if atlas_disabled:
        summary = (
            "atlas disabled for this run (no project_key / uninitialised store); "
            f"all {len(entries)} subsystem(s) routed to scout"
        )
    else:
        actions = {"use": 0, "refresh": 0, "scout": 0}
        for e in entries:
            actions[e["action"]] = actions.get(e["action"], 0) + 1
        summary = (
            f"atlas_lookup: use={actions['use']} refresh={actions['refresh']} "
            f"scout={actions['scout']}"
        )
    return ToolResult(output=summary, metadata={"lookups": entries})


def _resolve_ledger(context: ToolExecutionContext) -> Any:
    """Return the code-intelligence ledger for this run, if any.

    The ledger is attached to ``ExecutionMetadata.ci_service`` by the
    runtime (see ``code_intelligence.routing.service``). Tests also set
    it directly via a stub. Missing ledger is valid — callers fall
    through to the content-hash cold path.
    """
    svc = getattr(context.metadata, "ci_service", None)
    if svc is None:
        return None
    return getattr(svc, "ledger", None)


def _resolve_store(context: ToolExecutionContext) -> AtlasStore | None:
    """Allow tests to inject an override via ``metadata.extras['atlas_store']``."""
    override = context.metadata.extras.get("atlas_store") if hasattr(
        context.metadata, "extras"
    ) else None
    if isinstance(override, AtlasStore):
        return override
    return get_default_store()


def _resolve_max_age_seconds(context: ToolExecutionContext) -> float | None:
    """Allow tests and callers to override atlas max age per invocation."""
    raw = context.metadata.extras.get("atlas_max_age_seconds") if hasattr(
        context.metadata, "extras"
    ) else None
    if raw is None:
        return DEFAULT_ATLAS_MAX_AGE_SECONDS
    if isinstance(raw, (int, float)) and raw > 0:
        return float(raw)
    return None
