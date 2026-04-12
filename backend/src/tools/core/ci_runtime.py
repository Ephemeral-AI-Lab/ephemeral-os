"""Shared code-intelligence runtime helpers used across toolkits."""

from __future__ import annotations

import copy
import dataclasses
import logging
from typing import Any

from code_intelligence.editing.merge import detect_edit_window
from team._path_utils import normalize_scope_paths
from team._path_utils import scopes_overlap
from tools.core.base import ToolExecutionContext
from tools.daytona_toolkit.scope_builder import build_scope_packet_for_context

logger = logging.getLogger(__name__)
_DEFAULT_SCOPE_RECENT_SECONDS = 300.0


def get_ci_service(context: ToolExecutionContext) -> Any | None:
    """Get the CodeIntelligenceService from context, or None if unavailable."""
    return context.metadata.get("ci_service")


def scope_paths_for_write(
    context: ToolExecutionContext,
    *,
    fallback_paths: list[str] | None = None,
) -> list[str]:
    """Return the scope paths a write should be validated against."""
    baseline = context.metadata.get("scope_packet")
    if isinstance(baseline, dict):
        paths = baseline.get("scope_paths")
        if isinstance(paths, list) and paths:
            return normalize_scope_paths([str(item) for item in paths if isinstance(item, str)])
    return normalize_scope_paths(fallback_paths or [])


def build_live_scope_packet(
    context: ToolExecutionContext,
    *,
    scope_paths: list[str] | None = None,
    recent_seconds: float = _DEFAULT_SCOPE_RECENT_SECONDS,
) -> dict[str, Any]:
    """Build the current live scope packet for *scope_paths*."""
    baseline = context.metadata.get("scope_packet")
    return build_scope_packet_for_context(
        context,
        scope_paths=scope_paths,
        baseline_packet=baseline if isinstance(baseline, dict) else None,
        recent_seconds=recent_seconds,
    )


def refresh_scope_baseline(
    context: ToolExecutionContext,
    *,
    scope_paths: list[str] | None = None,
    packet: dict[str, Any] | None = None,
    recent_seconds: float = _DEFAULT_SCOPE_RECENT_SECONDS,
) -> dict[str, Any]:
    """Persist the latest live scope packet into the tool metadata."""
    resolved = (
        packet
        if isinstance(packet, dict)
        else build_live_scope_packet(
            context,
            scope_paths=scope_paths,
            recent_seconds=recent_seconds,
        )
    )
    if not isinstance(resolved, dict):
        return {}
    context.metadata["scope_packet"] = resolved
    context.metadata["coherence_token"] = str(resolved.get("coherence_token") or "")
    return resolved


def enforce_scope_coherence(
    context: ToolExecutionContext,
    *,
    scope_paths: list[str] | None,
) -> tuple[dict[str, Any], str | None]:
    """Return the live scope packet plus an error when the baseline token drifted."""
    packet = build_live_scope_packet(context, scope_paths=scope_paths)
    expected = str(context.metadata.get("coherence_token") or "")
    current = str(packet.get("coherence_token") or "")
    if expected and current and expected != current:
        return packet, (
            "Scope coherence changed since the work item started. Refresh CI state before writing."
        )
    return packet, None


def _update_prepared_write(prepared: Any, **fields: Any) -> Any:
    """Return a shallow copy of *prepared* with updated fields."""
    if dataclasses.is_dataclass(prepared) and not isinstance(prepared, type):
        return dataclasses.replace(prepared, **fields)
    updated = copy.copy(prepared)
    for key, value in fields.items():
        setattr(updated, key, value)
    return updated


def _enrich_prepared_write_with_line_range(prepared: Any, content: str) -> Any:
    """Attach the minimal changed line range to *prepared* when possible."""
    current_content = str(getattr(prepared, "current_content", "") or "")
    line_start, line_end, operation_type = detect_edit_window(current_content, content)
    if line_start is None:
        return prepared
    return _update_prepared_write(
        prepared,
        line_start=line_start,
        line_end=line_end,
        operation_type=operation_type,
    )


def _enrich_prepared_write_with_symbol_boundaries(
    prepared: Any, context: ToolExecutionContext
) -> Any:
    """Widen line anchors to the narrowest enclosing symbol when available."""
    line_start = getattr(prepared, "line_start", None)
    if line_start is None:
        return prepared

    svc = get_ci_service(context)
    symbol_index = getattr(svc, "symbol_index", None)
    file_path = str(getattr(prepared, "file_path", "") or "")
    if symbol_index is None or not file_path:
        return prepared

    try:
        boundaries = symbol_index.symbol_boundaries_for_file(file_path)
    except Exception:
        logger.debug("symbol_boundaries_for_file failed for %s", file_path, exc_info=True)
        return prepared

    if not isinstance(boundaries, list) or not boundaries:
        return prepared

    diff_start = int(line_start)
    diff_end = getattr(prepared, "line_end", None)
    diff_end = int(diff_end) if diff_end is not None else diff_start

    best: tuple[str, int, int] | None = None
    best_size: int | None = None
    for sym_name, sym_start, sym_end in boundaries:
        if sym_start <= diff_start and sym_end >= diff_end - 1:
            size = sym_end - sym_start
            if best is None or best_size is None or size < best_size:
                best = (sym_name, sym_start, sym_end)
                best_size = size

    if best is None:
        return prepared

    _, sym_start, sym_end = best
    return _update_prepared_write(
        prepared,
        line_start=sym_start,
        line_end=sym_end + 1,
    )


def _intent_symbols_for_prepared_write(prepared: Any, context: ToolExecutionContext) -> list[str]:
    """Return the narrowest enclosing symbol for *prepared* when possible."""
    line_start = getattr(prepared, "line_start", None)
    if line_start is None:
        return []

    svc = get_ci_service(context)
    symbol_index = getattr(svc, "symbol_index", None)
    file_path = str(getattr(prepared, "file_path", "") or "")
    if symbol_index is None or not file_path:
        return []

    try:
        boundaries = symbol_index.symbol_boundaries_for_file(file_path)
    except Exception:
        logger.debug("symbol_boundaries_for_file failed for %s", file_path, exc_info=True)
        return []

    if not isinstance(boundaries, list) or not boundaries:
        return []

    diff_start = int(line_start)
    diff_end = getattr(prepared, "line_end", None)
    diff_end = int(diff_end) if diff_end is not None else diff_start

    best: tuple[str, int, int] | None = None
    best_size: int | None = None
    for sym_name, sym_start, sym_end in boundaries:
        if sym_start <= diff_start and sym_end >= diff_end - 1:
            size = sym_end - sym_start
            if best is None or best_size is None or size < best_size:
                best = (sym_name, sym_start, sym_end)
                best_size = size
    return [best[0]] if best is not None else []


def prepare_ci_edit_intent(
    context: ToolExecutionContext,
    prepared: Any,
    *,
    content: str,
) -> tuple[Any, str | None]:
    """Enrich *prepared* and publish an edit intent when the CI service supports it."""
    prepared = _enrich_prepared_write_with_line_range(prepared, content)
    prepared = _enrich_prepared_write_with_symbol_boundaries(prepared, context)

    svc = get_ci_service(context)
    publish = getattr(svc, "publish_edit_intent", None)
    if svc is None or type(svc).__module__ == "unittest.mock" or not callable(publish):
        return prepared, None

    symbols = _intent_symbols_for_prepared_write(prepared, context)
    scope = (
        "symbol"
        if symbols
        else ("line" if getattr(prepared, "line_start", None) is not None else "file")
    )
    try:
        intent_id = publish(
            filepath=str(getattr(prepared, "file_path", "") or ""),
            agent_id=str(context.metadata.get("agent_run_id") or ""),
            symbols=symbols or None,
            scope=scope,
        )
    except Exception:
        logger.debug(
            "publish_edit_intent failed for %s", getattr(prepared, "file_path", ""), exc_info=True
        )
        return prepared, None

    heartbeat = getattr(svc, "heartbeat_edit_intent", None)
    if callable(heartbeat):
        try:
            heartbeat(intent_id)
        except Exception:
            logger.debug("heartbeat_edit_intent failed for %s", intent_id, exc_info=True)
    return prepared, intent_id


def release_ci_edit_intent(context: ToolExecutionContext, intent_id: str | None) -> None:
    """Release an edit intent when the CI service supports it."""
    if not intent_id:
        return
    svc = get_ci_service(context)
    release = getattr(svc, "release_edit_intent", None) if svc is not None else None
    if not callable(release):
        return
    try:
        release(intent_id)
    except Exception:
        logger.debug("release_edit_intent failed for %s", intent_id, exc_info=True)


def prepare_ci_write(
    context: ToolExecutionContext,
    file_path: str,
    *,
    expected_hash: str = "",
    allow_scope_drift: bool = False,
) -> tuple[Any | None, dict[str, Any], str | None]:
    """Run scope/token prechecks and reserve *file_path* for a write."""
    scope_paths = scope_paths_for_write(context, fallback_paths=[file_path])
    if scope_paths and not any(scopes_overlap(file_path, scope) for scope in scope_paths):
        # Treat the inherited lane scope as a soft starting surface. When a worker
        # discovers that the minimal coherent fix lives in an adjacent file, widen
        # the live scope packet instead of hard-failing the write precheck.
        scope_paths = normalize_scope_paths([*scope_paths, file_path])
    packet, err = enforce_scope_coherence(context, scope_paths=scope_paths)
    if err is not None and not allow_scope_drift:
        _note_team_memory_conflict(
            context,
            file_path=file_path,
            reason=err,
        )
        return None, packet, err
    svc = get_ci_service(context)
    if svc is None or not hasattr(svc, "prepare_write"):
        if err is not None:
            return None, packet, err
        refresh_scope_baseline(context, packet=packet)
        return None, packet, None
    prepared = svc.prepare_write(
        file_path,
        agent_id=str(context.metadata.get("agent_run_id") or ""),
        expected_hash=expected_hash,
        allow_missing=True,
    )
    if getattr(prepared, "success", None) is False:
        message = str(getattr(prepared, "message", "") or "write precheck failed")
        _note_team_memory_conflict(
            context,
            file_path=file_path,
            reason=message,
        )
        return None, packet, message
    refreshed = refresh_scope_baseline(context, scope_paths=scope_paths)
    return prepared, refreshed or packet, None


def finalize_ci_write(
    context: ToolExecutionContext,
    prepared: Any,
    *,
    content: str,
    edit_type: str,
    description: str,
) -> Any:
    """Commit a prepared write via the CI service."""
    svc = get_ci_service(context)
    assert svc is not None and hasattr(svc, "commit_prepared_write")
    prepared = _enrich_prepared_write_with_line_range(prepared, content)
    prepared = _enrich_prepared_write_with_symbol_boundaries(prepared, context)
    result = svc.commit_prepared_write(
        prepared,
        content,
        edit_type=edit_type,
        description=description,
    )
    if getattr(result, "success", False):
        refresh_scope_baseline(
            context,
            scope_paths=scope_paths_for_write(
                context,
                fallback_paths=[getattr(prepared, "file_path", "")],
            ),
        )
    elif bool(getattr(result, "conflict", False)):
        _note_team_memory_conflict(
            context,
            file_path=str(getattr(prepared, "file_path", "") or ""),
            reason=str(
                getattr(result, "conflict_reason", "")
                or getattr(result, "message", "")
                or "write conflict"
            ),
        )
    return result


def abort_ci_write(context: ToolExecutionContext, prepared: Any | None) -> None:
    """Release any prepared CI write reservation."""
    if prepared is None:
        return
    svc = get_ci_service(context)
    if svc is None or not hasattr(svc, "abort_prepared_write"):
        return
    try:
        svc.abort_prepared_write(prepared)
    except Exception:
        logger.debug(
            "abort_prepared_write failed for %s", getattr(prepared, "file_path", ""), exc_info=True
        )
    finally:
        refresh_scope_baseline(
            context,
            scope_paths=scope_paths_for_write(
                context,
                fallback_paths=[getattr(prepared, "file_path", "")],
            ),
        )


def prime_cache_after_write(context: ToolExecutionContext, file_path: str, content: str) -> None:
    """Prime the tree cache and refresh the symbol index after a write."""
    svc = get_ci_service(context)
    if svc is None:
        refresh_scope_baseline(
            context,
            scope_paths=scope_paths_for_write(context, fallback_paths=[file_path]),
        )
        return
    try:
        svc.symbol_index.refresh(file_path, content)
        svc.lsp_client.invalidate(file_path)
    except Exception:
        logger.debug("CI prime_cache_after_write failed for %s", file_path)
    finally:
        refresh_scope_baseline(
            context,
            scope_paths=scope_paths_for_write(context, fallback_paths=[file_path]),
        )


def sync_write_to_ci(
    context: ToolExecutionContext,
    file_path: str,
    content: str,
    *,
    agent_id: str = "",
    edit_type: str = "write",
    description: str = "",
    old_hash: str = "",
    new_hash: str = "",
) -> None:
    """Record a write in the arbiter and refresh CI caches."""
    svc = get_ci_service(context)
    if svc is not None:
        try:
            arbiter = getattr(svc, "arbiter", None)
            if arbiter is not None:
                arbiter.record_edit(
                    file_path,
                    agent_id,
                    edit_type=edit_type,
                    old_hash=old_hash,
                    new_hash=new_hash,
                    description=description,
                )
        except Exception:
            logger.debug("CI arbiter sync failed for %s", file_path, exc_info=True)
    prime_cache_after_write(context, file_path, content)


def sync_deleted_file(
    context: ToolExecutionContext,
    file_path: str,
    *,
    agent_id: str = "",
    edit_type: str = "delete",
    description: str = "",
) -> None:
    """Best-effort CI invalidation for a deleted file."""
    svc = get_ci_service(context)
    if svc is not None:
        try:
            arbiter = getattr(svc, "arbiter", None)
            if arbiter is not None:
                arbiter.record_edit(
                    file_path,
                    agent_id,
                    edit_type=edit_type,
                    description=description,
                )
        except Exception:
            logger.debug("CI arbiter delete sync failed for %s", file_path, exc_info=True)
        try:
            svc.symbol_index.refresh(file_path, "")
            svc.lsp_client.invalidate(file_path)
        except Exception:
            logger.debug("CI delete invalidation failed for %s", file_path, exc_info=True)

    refresh_scope_baseline(
        context,
        scope_paths=scope_paths_for_write(context, fallback_paths=[file_path]),
    )


def prepare_declared_shell_outputs(
    context: ToolExecutionContext,
    *,
    declared_output_paths: list[str] | None,
) -> tuple[list[Any], dict[str, Any], str | None]:
    """Reserve declared shell outputs before running a mutating command."""
    paths = normalize_scope_paths(declared_output_paths or [])
    packet, err = enforce_scope_coherence(context, scope_paths=paths)
    if err is not None and not paths:
        return [], packet, err
    if not paths:
        return [], packet, None
    prepared_items: list[Any] = []
    for path in paths:
        prepared, _, prep_err = prepare_ci_write(
            context,
            path,
            allow_scope_drift=True,
        )
        if prep_err is not None:
            for item in prepared_items:
                abort_ci_write(context, item)
            return [], packet, prep_err
        if prepared is not None:
            prepared_items.append(prepared)
    latest = context.metadata.get("scope_packet")
    return prepared_items, latest if isinstance(latest, dict) else packet, None


def release_declared_shell_outputs(
    context: ToolExecutionContext, prepared_items: list[Any]
) -> None:
    """Release any declared shell reservations."""
    for item in prepared_items:
        abort_ci_write(context, item)
    if prepared_items:
        refresh_scope_baseline(
            context,
            scope_paths=normalize_scope_paths(
                [str(getattr(item, "file_path", "") or "") for item in prepared_items]
            ),
        )


def record_edit_in_arbiter(
    context: ToolExecutionContext,
    file_path: str,
    agent_id: str = "",
    edit_type: str = "edit",
    old_hash: str = "",
    new_hash: str = "",
    description: str = "",
) -> None:
    """Record an edit in the CI arbiter if available."""
    svc = get_ci_service(context)
    if svc is None:
        return
    try:
        svc.arbiter.record_edit(
            file_path=file_path,
            agent_id=agent_id,
            edit_type=edit_type,
            old_hash=old_hash,
            new_hash=new_hash,
            description=description,
        )
    except Exception:
        logger.debug("CI record_edit_in_arbiter failed for %s", file_path)


def _note_team_memory_conflict(
    context: ToolExecutionContext,
    *,
    file_path: str,
    reason: str,
) -> None:
    """Persist a typed conflict event when a TeamRun is active."""
    team_run_id = context.metadata.get("team_run_id")
    if not team_run_id:
        return
    team_run = _get_team_run(str(team_run_id))
    if team_run is None or not hasattr(team_run, "note_conflict_event"):
        return
    try:
        team_run.note_conflict_event(
            file_path=file_path,
            reason=reason,
            work_item_id=str(context.metadata.get("work_item_id") or ""),
            agent_name=str(context.metadata.get("agent_name") or ""),
        )
    except Exception:
        logger.debug("team memory conflict persistence failed for %s", file_path, exc_info=True)


def _get_team_run(team_run_id: str) -> Any | None:
    try:
        from team.runtime.registry import get as get_team_run
    except Exception:
        return None
    try:
        return get_team_run(team_run_id)
    except Exception:
        return None


__all__ = [
    "abort_ci_write",
    "build_live_scope_packet",
    "enforce_scope_coherence",
    "finalize_ci_write",
    "get_ci_service",
    "prepare_ci_edit_intent",
    "prepare_ci_write",
    "prepare_declared_shell_outputs",
    "prime_cache_after_write",
    "record_edit_in_arbiter",
    "refresh_scope_baseline",
    "release_ci_edit_intent",
    "release_declared_shell_outputs",
    "scope_paths_for_write",
    "sync_deleted_file",
    "sync_write_to_ci",
]
