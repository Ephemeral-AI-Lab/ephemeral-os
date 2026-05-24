"""Host-side enter-isolated-workspace coroutine."""

from __future__ import annotations

import os

from sandbox._shared.models import (
    EnterIsolatedWorkspaceRequest,
    EnterIsolatedWorkspaceResult,
    LifecycleError,
)
from sandbox.audit.lifecycle import lifecycle_operation
from sandbox.isolated_workspace import handlers
from sandbox.isolated_workspace._types import IsolatedWorkspaceError


async def enter_isolated_workspace(
    req: EnterIsolatedWorkspaceRequest,
    *,
    background_manager: object | None = None,
    sandbox_id: str = "",
) -> EnterIsolatedWorkspaceResult:
    agent_id = req.caller.agent_id
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
            manager = await handlers._ensure_manager(  # noqa: SLF001
                {"layer_stack_root": req.layer_stack_root}
            )
            handle = await manager.enter(agent_id)
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


def _count_by_agent(background_manager: object | None, agent_id: str) -> int:
    if background_manager is None:
        return 0
    counter = getattr(background_manager, "count_by_agent", None)
    if not callable(counter):
        return 0
    return int(counter(agent_id))


async def _daemon_inflight_count(sandbox_id: str, agent_id: str) -> int:
    if not sandbox_id:
        return 0
    try:
        import sandbox.api as sandbox_api

        return await sandbox_api.inflight_count(sandbox_id, agent_id)
    except Exception as exc:
        raise RuntimeError("daemon in-flight request count check failed") from exc


__all__ = ["enter_isolated_workspace"]
