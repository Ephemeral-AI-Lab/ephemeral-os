"""``atlas_lookup`` — planner-side read path for the Project Atlas."""

from __future__ import annotations

from code_intelligence.atlas.freshness import DEFAULT_ATLAS_MAX_AGE_SECONDS
from code_intelligence.atlas.service import AtlasService
from code_intelligence.atlas.store import AtlasStore
from team.runtime.registry import get as _get_team_run
from tools.core.base import ToolExecutionContext, ToolResult
from tools.core.decorator import tool


@tool(
    name="atlas_lookup",
    description=(
        "Consult the persistent Project Atlas for one or more subsystems. "
        "Returns a per-subsystem decision (use | refresh | scout). Use-marked "
        "entries include a staged artifact_ref you can attach as a briefing. "
        "Refresh/scout results mean the planner should use fresh exploration "
        "for this turn."
    ),
    read_only=True,
)
async def atlas_lookup(
    subsystems: list[str],
    *,
    context: ToolExecutionContext,
) -> ToolResult:
    """Look up atlas chunks and stage fresh ones into the current run."""
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

    atlas = _resolve_atlas_service(context, store=_store_override(context))
    batch = atlas.lookup_subsystems(
        team_run=team_run,
        subsystems=subsystems,
        max_age_seconds=_resolve_max_age_seconds(context),
    )
    return _build_result(batch.entries, atlas_disabled=batch.atlas_disabled)


def _build_result(entries: list[dict[str, object]], *, atlas_disabled: bool) -> ToolResult:
    if atlas_disabled:
        summary = (
            "atlas disabled for this run (no project_key / uninitialised store); "
            f"all {len(entries)} subsystem(s) routed to scout"
        )
    else:
        actions = {"use": 0, "refresh": 0, "scout": 0}
        for entry in entries:
            action = str(entry.get("action") or "")
            actions[action] = actions.get(action, 0) + 1
        summary = (
            f"atlas_lookup: use={actions['use']} refresh={actions['refresh']} "
            f"scout={actions['scout']}"
        )
    return ToolResult(output=summary, metadata={"lookups": entries})


def _store_override(context: ToolExecutionContext) -> AtlasStore | None:
    """Allow tests to inject an override via ``metadata.extras['atlas_store']``."""
    override = context.metadata.extras.get("atlas_store") if hasattr(
        context.metadata, "extras"
    ) else None
    if isinstance(override, AtlasStore):
        return override
    return None


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


def _resolve_atlas_service(
    context: ToolExecutionContext,
    *,
    store: AtlasStore | None,
) -> object:
    svc = getattr(context.metadata, "ci_service", None)
    atlas = getattr(svc, "atlas", None)
    if atlas is not None and store is None:
        return atlas
    workspace_root = (
        str(getattr(svc, "workspace_root", "") or "")
        or str(getattr(context, "cwd", "") or "")
    )
    return AtlasService(
        workspace_root=workspace_root,
        ledger=getattr(svc, "ledger", None),
        symbol_index=getattr(svc, "symbol_index", None),
        store=store,
    )
