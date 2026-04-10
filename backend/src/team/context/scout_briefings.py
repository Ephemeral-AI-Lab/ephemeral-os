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


def store_stable_scout_artifact(
    team_run: Any,
    artifact: dict[str, Any],
    *,
    run_id: str | None = None,
) -> str | None:
    """Persist the latest scout artifact under a stable per-scope key."""
    if not isinstance(artifact, dict):
        return None
    scope = scope_of_artifact(artifact)
    if not scope:
        return None
    ref = stable_scout_artifact_ref(scope)
    existing = team_run.artifacts.load(ref)
    current_version = team_run.project_context.stable_scout_versions.get(scope)
    if current_version is None:
        current_version = _version_from_artifact(existing)
    incoming_version = _version_from_artifact(artifact, run_id=run_id)
    if isinstance(existing, dict) and not _should_replace(current_version, incoming_version):
        return ref
    team_run.artifacts.save(ref, dict(artifact))
    team_run.project_context.stable_scout_versions[scope] = incoming_version
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

    project_ctx = team_run.project_context
    briefings = project_ctx.shared_briefings
    replaceable_scopes = team_run.project_context.auto_promoted_scout_scopes
    existing_is_replaceable = scope in replaceable_scopes
    is_new_scope = scope not in briefings
    if is_new_scope and len(briefings) >= team_run.budgets.max_shared_briefings:
        victim = evict_auto_promoted_scout_briefing(team_run)
        if victim is None:
            logger.debug(
                "scout auto-promotion skipped for %s: shared briefing cap reached",
                scope,
            )
            return False

    briefings[scope] = Briefing(
        name=stable_scout_artifact_ref(scope),
        source="artifact",
        ref=artifact_ref,
    )
    if is_new_scope or existing_is_replaceable:
        replaceable_scopes.add(scope)
    else:
        replaceable_scopes.discard(scope)
    return True


def evict_auto_promoted_scout_briefing(team_run: Any) -> str | None:
    victim = _select_auto_promoted_victim(team_run)
    if victim is None:
        return None
    team_run.project_context.shared_briefings.pop(victim, None)
    team_run.project_context.auto_promoted_scout_scopes.discard(victim)
    return victim


def _select_auto_promoted_victim(team_run: Any) -> str | None:
    candidates: list[tuple[float, float, str]] = []
    for scope in team_run.project_context.auto_promoted_scout_scopes:
        briefing = team_run.project_context.shared_briefings.get(scope)
        if briefing is None:
            continue
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


def _should_replace(
    current_version: dict[str, Any] | None,
    incoming_version: dict[str, Any] | None,
) -> bool:
    current_snapshot = _version_snapshot(current_version)
    incoming_snapshot = _version_snapshot(incoming_version)
    if incoming_snapshot != current_snapshot:
        return incoming_snapshot > current_snapshot

    current_run_id = _version_run_id(current_version)
    incoming_run_id = _version_run_id(incoming_version)
    if current_run_id and incoming_run_id:
        return incoming_run_id > current_run_id
    return False


def _version_from_artifact(
    artifact: Any,
    *,
    run_id: str | None = None,
) -> dict[str, Any]:
    version: dict[str, Any] = {}
    snapshot = _snapshot_time(artifact)
    if snapshot > 0:
        version["snapshot_time"] = snapshot
    if isinstance(run_id, str) and run_id:
        version["run_id"] = run_id
    return version


def _version_snapshot(version: dict[str, Any] | None) -> float:
    if not isinstance(version, dict):
        return 0.0
    raw = version.get("snapshot_time")
    return float(raw) if isinstance(raw, (int, float)) and raw > 0 else 0.0


def _version_run_id(version: dict[str, Any] | None) -> str:
    if not isinstance(version, dict):
        return ""
    raw = version.get("run_id")
    return raw if isinstance(raw, str) else ""


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
