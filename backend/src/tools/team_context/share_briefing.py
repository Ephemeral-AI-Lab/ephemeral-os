"""``share_briefing`` — promote a brief into the run-scoped shared context.

Per plan §13:

- Reads ``team_run_id`` from ``context.metadata`` and looks up the live
  ``TeamRun`` from the in-process registry.
- Validates the new ``Briefing`` (XOR enforced by ``__post_init__``).
- Resolves a ``canonical_scope`` key in three layers:
  1. Explicit ``canonical_scope`` field on the loaded artifact, else
  2. Derived from ``artifact["target_paths"]`` via ``canonicalize_scope``, else
  3. The briefing ``name`` (last-resort fallback for inline briefings or
     non-scout artifacts).
- Enforces ``BudgetConfig.max_shared_briefings``; rejects when full.
- Latest-wins replacement on key collision (logged via the result text).
"""

from __future__ import annotations

import logging
from typing import Any

from team.context.canonicalize import scope_of_artifact
from team.models import Briefing
from team.runtime.registry import get as _get_team_run
from tools.core.base import ToolExecutionContext, ToolResult
from tools.core.decorator import tool

logger = logging.getLogger(__name__)


@tool(
    name="share_briefing",
    description=(
        "Promote a brief into the run-scoped shared context so future "
        "WorkItems and subagents inherit it. Use after reading a brief "
        "with high coverage that you trust will be relevant to siblings."
    ),
)
async def share_briefing(
    name: str,
    source: str,
    ref: str | None = None,
    inline: str | None = None,
    description: str | None = None,
    *,
    context: ToolExecutionContext,
) -> ToolResult:
    """Promote a brief into ``project_context.shared_briefings``.

    Args:
        name: Short identifier (used as a fallback dedup key and prompt label).
        source: ``"artifact"`` or ``"inline"``.
        ref: Artifact id when ``source="artifact"``.
        inline: Literal text when ``source="inline"``.
        description: Optional one-line hint shown alongside the brief.

    Returns:
        scope_key (str): The dedup key chosen for this briefing.
        replaced (bool): True if a prior briefing under the same key was replaced.
    """
    team_run_id = context.metadata.get("team_run_id")
    if not team_run_id:
        return ToolResult(
            output="share_briefing unavailable: no team_run_id in execution context",
            is_error=True,
        )
    team_run = _get_team_run(team_run_id)
    if team_run is None:
        return ToolResult(
            output=f"share_briefing: team_run {team_run_id!r} not registered",
            is_error=True,
        )

    try:
        briefing = Briefing(
            name=name, source=source, ref=ref, inline=inline, description=description
        )
    except ValueError as exc:
        return ToolResult(output=f"invalid briefing: {exc}", is_error=True)

    project_ctx = team_run.project_context
    cap = team_run.budgets.max_shared_briefings
    scope_key = _resolve_scope_key(briefing, team_run.artifacts)
    replaced = scope_key in project_ctx.shared_briefings
    if not replaced and len(project_ctx.shared_briefings) >= cap:
        return ToolResult(
            output=(
                f"share_briefing rejected: shared_briefings cap reached "
                f"({cap}). Existing keys: {sorted(project_ctx.shared_briefings.keys())}"
            ),
            is_error=True,
        )

    if replaced:
        logger.info(
            "share_briefing: replacing shared briefing under canonical_scope=%r",
            scope_key,
        )
    project_ctx.shared_briefings[scope_key] = briefing
    return ToolResult(
        output=(
            f"shared briefing promoted under scope={scope_key!r} "
            f"(replaced={replaced}, total={len(project_ctx.shared_briefings)})"
        ),
        metadata={"scope_key": scope_key, "replaced": replaced},
    )


def _resolve_scope_key(briefing: Briefing, artifact_store: Any) -> str:
    """Three-layer resolution: explicit → derived → name fallback."""
    if briefing.source == "artifact" and briefing.ref is not None:
        scope = scope_of_artifact(artifact_store.load(briefing.ref))
        if scope:
            return scope
    return briefing.name
