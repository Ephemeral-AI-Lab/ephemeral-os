"""Tool-side context shim around :mod:`sandbox.api.attribution`.

The actual ``AgentAttribution`` type and ``RequestActor`` adapters live
in :mod:`sandbox.api.attribution`. This shim adds the
``ToolExecutionContextService``-aware helpers tools rely on, so that the
provider-neutral attribution layer stays free of any ``tools.*`` imports.

Step 10 of the migration plan deletes this module once
``tools/sandbox_toolkit`` replaces ``tools/daytona_toolkit`` and the
``svc.cmd``-style call sites disappear.
"""

from __future__ import annotations

from typing import Any

from sandbox.api.attribution import (
    AgentAttribution,
    actor_from_attribution,
    attribution_from_actor,
    build_actor,
)
from sandbox.api.models import RequestActor
from tools.core.context import ToolExecutionContextService

__all__ = [
    "AgentAttribution",
    "RequestActor",
    "actor_from_attribution",
    "actor_from_context",
    "agent_attribution_from_context",
    "attribution_from_actor",
    "build_actor",
    "rebind_ci_service",
    "resolved_agent_id",
]


def rebind_ci_service(context: ToolExecutionContextService, svc: Any) -> None:
    """Point *svc* at the sandbox on *context* before a sync OCC call.

    Typed ``svc.*`` APIs run sync inside a worker thread; they read through
    :class:`ContentManager`, which speaks to whatever sandbox the service
    is currently bound to. When a tool holds a newer sandbox than the
    service (e.g. after ``_recover_sandbox`` reattaches), reads must not
    go through the stale handle — rebind first so the sync path sees the
    current one.

    No-op when the context has no sandbox or *svc* cannot be rebound.
    """
    sandbox = context.get("ci_sandbox") or context.daytona_sandbox
    rebind = getattr(svc, "rebind_sandbox", None)
    if sandbox is None or not callable(rebind):
        return
    rebind(sandbox)


def resolved_agent_id(
    context: ToolExecutionContextService,
    *,
    preferred: str = "",
) -> str:
    """Return a non-empty actor label for ledger attribution.

    Priority: caller-supplied *preferred* → ``agent_run_id`` →
    ``agent_name``. Matches the original helper exactly so existing
    arbiter ledger keys stay stable across this refactor.
    """
    explicit = str(preferred or "").strip()
    if explicit:
        return explicit
    agent_run_id = str(context.agent_run_id or "").strip()
    if agent_run_id:
        return agent_run_id
    return str(context.agent_name or "").strip()


def agent_attribution_from_context(
    context: ToolExecutionContextService,
    *,
    preferred_agent_id: str = "",
) -> AgentAttribution:
    """Build an :class:`AgentAttribution` from a tool execution context."""
    return AgentAttribution(
        agent_id=resolved_agent_id(context, preferred=preferred_agent_id),
        run_id=str(context.get("run_id") or ""),
        agent_run_id=str(context.agent_run_id or ""),
        task_id=str(context.get("task_id") or ""),
    )


def actor_from_context(
    context: ToolExecutionContextService,
    *,
    preferred_agent_id: str = "",
) -> RequestActor:
    """Build a :class:`RequestActor` from a tool execution context."""
    return actor_from_attribution(
        agent_attribution_from_context(
            context,
            preferred_agent_id=preferred_agent_id,
        ),
    )
