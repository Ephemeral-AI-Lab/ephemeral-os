"""Host-side enter/exit coroutines for the isolated_workspace tool layer.

These wrap the daemon-side ``IsolatedPipeline.{enter,exit}`` RPCs with the
host-only concerns the tool layer needs:

* in-flight background-task gating on enter (rejects when ephemeral jobs
  are still running for this agent — local + daemon counts)
* background-task drain on exit (cancels per-agent background work before
  tearing the pipeline handle down)
* the ``lifecycle_operation`` audit wrapper

Phase 2.6 C4 collapsed ``isolated_workspace/lifecycle/`` into this single
host-side module so the only place that knows the host orchestration is
``sandbox.host``; tool definitions in ``tools/`` consume these coroutines
directly. Earlier drafts considered colocating with the tool definitions,
but the ``tools.isolated_workspace`` package init eagerly loads
``definition.py`` which pulls in ``tools.sandbox._lib.tool_context`` —
co-locating ``_lifecycle.py`` inside that package triggered a circular
import through ``sandbox.api``. Hosting here breaks the cycle.
"""

from __future__ import annotations

import os

from sandbox.shared.models import (
    EnterIsolatedWorkspaceRequest,
    EnterIsolatedWorkspaceResult,
    ExitIsolatedWorkspaceRequest,
    ExitIsolatedWorkspaceResult,
    LifecycleError,
)
from sandbox.audit.lifecycle import lifecycle_operation
from sandbox.host.daemon_client import _DaemonDispatchError, call_daemon_api
from sandbox.isolated_workspace._control_plane import (
    pipeline_registry as isolated_pipeline_registry,
)
from sandbox.isolated_workspace._control_plane.pipeline_state import IsolatedWorkspaceError


async def enter_isolated_workspace(
    request: EnterIsolatedWorkspaceRequest,
    *,
    background_manager: object | None = None,
    sandbox_id: str = "",
) -> EnterIsolatedWorkspaceResult:
    agent_id = request.caller.agent_id
    try:
        local_count = _count_by_agent(background_manager, agent_id)
        daemon_count = await _daemon_inflight_count(sandbox_id, agent_id)
        in_flight = max(local_count, daemon_count)
        if in_flight > 0:
            return EnterIsolatedWorkspaceResult(
                success=False,
                error=LifecycleError(
                    kind="ephemeral_jobs_in_flight",
                    message="sandbox-bound background tasks are still running",
                    details={"count": str(in_flight)},
                ),
            )
        async with lifecycle_operation(
            kind="enter_isolated_workspace",
            agent_id=agent_id,
            audit_path=os.environ.get("EOS_WORKSPACE_LIFECYCLE_AUDIT_PATH"),
        ) as timings:
            if sandbox_id:
                return await _daemon_enter(sandbox_id, request, timings=dict(timings))
            pipeline = await isolated_pipeline_registry.ensure_pipeline(
                {"layer_stack_root": request.layer_stack_root}
            )
            handle = await pipeline.enter(agent_id)
            return EnterIsolatedWorkspaceResult(
                success=True,
                manifest_version=str(handle.manifest_version),
                manifest_root_hash=str(handle.manifest_root_hash),
                timings=dict(timings),
            )
    except RuntimeError as exc:
        return EnterIsolatedWorkspaceResult(
            success=False,
            error=LifecycleError(
                kind="inflight_count_unavailable",
                message=str(exc),
                details={"sandbox_id": sandbox_id},
            ),
        )
    except IsolatedWorkspaceError as exc:
        return EnterIsolatedWorkspaceResult(
            success=False,
            error=LifecycleError(
                kind=exc.kind,
                message=str(exc),
                details={str(k): str(v) for k, v in exc.details.items()},
            ),
        )


async def exit_isolated_workspace(
    request: ExitIsolatedWorkspaceRequest,
    *,
    background_manager: object | None = None,
    sandbox_id: str = "",
) -> ExitIsolatedWorkspaceResult:
    agent_id = request.caller.agent_id
    try:
        async with lifecycle_operation(
            kind="exit_isolated_workspace",
            agent_id=agent_id,
            audit_path=os.environ.get("EOS_WORKSPACE_LIFECYCLE_AUDIT_PATH"),
        ) as timings:
            evicted_background_tasks = await _cancel_by_agent(
                background_manager,
                agent_id,
                grace_s=request.grace_s,
            )
            if sandbox_id:
                return await _daemon_exit(
                    sandbox_id,
                    request,
                    evicted_background_tasks=evicted_background_tasks,
                    timings=dict(timings),
                )
            result = await isolated_pipeline_registry.require_pipeline().exit(
                agent_id,
                grace_s=0.0,
            )
            phases = dict(result.get("phases_ms", {}))
            phases["evicted_background_tasks"] = float(evicted_background_tasks)
            timings.update(result.get("phases_ms", {}))
            return ExitIsolatedWorkspaceResult(
                success=bool(result.get("success", True)),
                evicted_upperdir_bytes=int(result.get("evicted_upperdir_bytes", 0)),
                lifetime_s=float(result.get("lifetime_s", 0.0)),
                phases_ms=phases,
                timings=dict(timings),
            )
    except IsolatedWorkspaceError as exc:
        return ExitIsolatedWorkspaceResult(
            success=False,
            error=LifecycleError(
                kind=exc.kind,
                message=str(exc),
                details={str(k): str(v) for k, v in exc.details.items()},
            ),
        )


async def _daemon_enter(
    sandbox_id: str,
    request: EnterIsolatedWorkspaceRequest,
    *,
    timings: dict[str, float],
) -> EnterIsolatedWorkspaceResult:
    try:
        response = await call_daemon_api(
            sandbox_id,
            "api.isolated_workspace.enter",
            {
                "agent_id": request.caller.agent_id,
                "layer_stack_root": request.layer_stack_root,
            },
            layer_stack_root=request.layer_stack_root,
            timeout=180,
        )
    except _DaemonDispatchError as exc:
        return EnterIsolatedWorkspaceResult(
            success=False,
            timings=timings,
            error=_lifecycle_error_from_dispatch(exc),
        )
    error = response.get("error")
    if error is not None:
        return EnterIsolatedWorkspaceResult(
            success=False,
            timings=timings,
            error=_lifecycle_error_from_mapping(error),
        )
    return EnterIsolatedWorkspaceResult(
        success=bool(response.get("success", True)),
        manifest_version=str(response.get("manifest_version") or ""),
        manifest_root_hash=str(response.get("manifest_root_hash") or ""),
        timings=timings,
    )


async def _daemon_exit(
    sandbox_id: str,
    request: ExitIsolatedWorkspaceRequest,
    *,
    evicted_background_tasks: int,
    timings: dict[str, float],
) -> ExitIsolatedWorkspaceResult:
    try:
        response = await call_daemon_api(
            sandbox_id,
            "api.isolated_workspace.exit",
            {"agent_id": request.caller.agent_id},
            timeout=180,
        )
    except _DaemonDispatchError as exc:
        return ExitIsolatedWorkspaceResult(
            success=False,
            timings=timings,
            error=_lifecycle_error_from_dispatch(exc),
        )
    error = response.get("error")
    if error is not None:
        return ExitIsolatedWorkspaceResult(
            success=False,
            timings=timings,
            error=_lifecycle_error_from_mapping(error),
        )
    phases = dict(response.get("phases_ms") or {})
    phases["evicted_background_tasks"] = float(evicted_background_tasks)
    timings.update({str(key): float(value) for key, value in phases.items()})
    return ExitIsolatedWorkspaceResult(
        success=bool(response.get("success", True)),
        evicted_upperdir_bytes=int(response.get("evicted_upperdir_bytes") or 0),
        lifetime_s=float(response.get("lifetime_s") or 0.0),
        phases_ms=phases,
        timings=timings,
    )


def _lifecycle_error_from_dispatch(exc: _DaemonDispatchError) -> LifecycleError:
    return LifecycleError(
        kind=str(exc.kind or "internal_error"),
        message=str(exc.message or ""),
        details={str(k): str(v) for k, v in (exc.details or {}).items()},
    )


def _lifecycle_error_from_mapping(error: object) -> LifecycleError:
    if not isinstance(error, dict):
        return LifecycleError(kind="internal_error", message=str(error))
    details = error.get("details")
    return LifecycleError(
        kind=str(error.get("kind") or "internal_error"),
        message=str(error.get("message") or ""),
        details={str(k): str(v) for k, v in (details if isinstance(details, dict) else {}).items()},
    )


def _count_by_agent(background_manager: object | None, agent_id: str) -> int:
    if background_manager is None:
        return 0
    counter = getattr(background_manager, "count_by_agent", None)
    if not callable(counter):
        return 0
    return int(counter(agent_id))


async def _cancel_by_agent(
    background_manager: object | None,
    agent_id: str,
    *,
    grace_s: float,
) -> int:
    if background_manager is None:
        return 0
    canceller = getattr(background_manager, "cancel_by_agent", None)
    if not callable(canceller):
        return 0
    return int(await canceller(agent_id, grace_s=grace_s))


async def _daemon_inflight_count(sandbox_id: str, agent_id: str) -> int:
    if not sandbox_id:
        return 0
    try:
        import sandbox.api as sandbox_api

        return await sandbox_api.inflight_count(sandbox_id, agent_id)
    except Exception as exc:
        raise RuntimeError("daemon in-flight request count check failed") from exc


__all__ = ["enter_isolated_workspace", "exit_isolated_workspace"]
