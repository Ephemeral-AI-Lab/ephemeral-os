"""Stable scout artifact storage and same-run auto-promotion helpers."""

from __future__ import annotations

import logging
from typing import Any

from team.atlas.freshness import MIN_COMPLETE_SCOPE_COVERAGE, brief_reuse_status
from team.context.canonicalize import scope_of_artifact
from team.models import Briefing

logger = logging.getLogger(__name__)

_SCOUT_ARTIFACT_PREFIX = "scout:"


def stable_scout_artifact_ref(scope: str) -> str:
    """Return the canonical team artifact ref for *scope*."""
    return f"{_SCOUT_ARTIFACT_PREFIX}{scope}"


def store_stable_scout_artifact(team_run: Any, artifact: dict[str, Any]) -> str | None:
    """Persist the latest scout artifact under a stable per-scope key."""
    if not isinstance(artifact, dict):
        return None
    scope = scope_of_artifact(artifact)
    if not scope:
        return None
    ref = stable_scout_artifact_ref(scope)
    existing = team_run.artifacts.load(ref)
    if isinstance(existing, dict) and not _should_replace(existing, artifact):
        return ref
    team_run.artifacts.save(ref, dict(artifact))
    return ref


def auto_promote_scout_briefing(team_run: Any, artifact_ref: str) -> bool:
    """Promote a reusable scout artifact into run-scoped shared briefings."""
    artifact = team_run.artifacts.load(artifact_ref)
    if not isinstance(artifact, dict):
        return False
    scope = scope_of_artifact(artifact)
    if not scope:
        return False
    reusable, reason = brief_reuse_status(
        artifact,
        min_scope_coverage=MIN_COMPLETE_SCOPE_COVERAGE,
    )
    if not reusable:
        logger.debug(
            "scout auto-promotion skipped for %s: %s",
            scope,
            reason or "not reusable",
        )
        return False

    briefings = team_run.project_context.shared_briefings
    if scope not in briefings and len(briefings) >= team_run.budgets.max_shared_briefings:
        victim = _select_auto_promoted_victim(team_run)
        if victim is None:
            logger.debug(
                "scout auto-promotion skipped for %s: shared briefing cap reached",
                scope,
            )
            return False
        briefings.pop(victim, None)

    briefings[scope] = Briefing(
        name=stable_scout_artifact_ref(scope),
        source="artifact",
        ref=artifact_ref,
    )
    return True


def _select_auto_promoted_victim(team_run: Any) -> str | None:
    candidates: list[tuple[float, float, str]] = []
    for scope, briefing in team_run.project_context.shared_briefings.items():
        if briefing.source != "artifact" or not briefing.ref:
            continue
        if not briefing.ref.startswith(_SCOUT_ARTIFACT_PREFIX):
            continue
        artifact = team_run.artifacts.load(briefing.ref)
        candidates.append(
            (
                _scope_coverage(artifact),
                _snapshot_time(artifact),
                scope,
            )
        )
    if not candidates:
        return None
    _, _, victim = min(candidates)
    return victim


def _should_replace(current: dict[str, Any], incoming: dict[str, Any]) -> bool:
    current_snapshot = _snapshot_time(current)
    incoming_snapshot = _snapshot_time(incoming)
    if incoming_snapshot and current_snapshot:
        return incoming_snapshot >= current_snapshot
    if current_snapshot and not incoming_snapshot:
        return False
    return True


def _snapshot_time(artifact: Any) -> float:
    if not isinstance(artifact, dict):
        return 0.0
    raw = artifact.get("snapshot_time")
    return float(raw) if isinstance(raw, (int, float)) and raw > 0 else 0.0


def _scope_coverage(artifact: Any) -> float:
    if not isinstance(artifact, dict):
        return -1.0
    raw = artifact.get("scope_coverage")
    return float(raw) if isinstance(raw, (int, float)) else -1.0
