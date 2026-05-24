"""Host-side exit-isolated-workspace coroutine."""

from __future__ import annotations

import os

from sandbox._shared.models import (
    ExitIsolatedWorkspaceRequest,
    ExitIsolatedWorkspaceResult,
    LifecycleError,
)
from sandbox.audit.lifecycle import lifecycle_operation
from sandbox.isolated_workspace._types import IsolatedWorkspaceError
from sandbox.isolated_workspace.pipeline import require_pipeline


async def exit_isolated_workspace(
    req: ExitIsolatedWorkspaceRequest,
    *,
    background_manager: object | None = None,
) -> ExitIsolatedWorkspaceResult:
    agent_id = req.caller.agent_id
    try:
        async with lifecycle_operation(
            kind="exit_isolated_workspace",
            agent_id=agent_id,
            audit_path=os.environ.get("EOS_WORKSPACE_LIFECYCLE_AUDIT_PATH"),
        ) as timings:
            evicted_background_tasks = await _cancel_by_agent(
                background_manager,
                agent_id,
                grace_s=req.grace_s,
            )
            result = await require_pipeline().exit(agent_id, grace_s=0.0)
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


__all__ = ["exit_isolated_workspace"]
