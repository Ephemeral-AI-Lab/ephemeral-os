"""Shared helper-tool composer plumbing for ``ask_advisor`` / ``ask_resolver``.

Both helper tools need the same setup: look up the parent task's persisted
context_packet_id, derive the mission id, build a :class:`ContextScope`,
and call :meth:`ContextComposer.compose`. Centralised here so both call
sites stay tiny.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from task_center.context_engine.composer import ContextComposer, LaunchBundle
from task_center.context_engine.scope import ContextScope
from tools.core.context import ToolExecutionContextService
from tools.core.results import ToolResult


@dataclass(frozen=True, slots=True)
class HelperComposeError(Exception):
    """Raised inline so the caller can wrap as a ToolResult error."""

    message: str

    def to_tool_result(self) -> ToolResult:
        return ToolResult(output=self.message, is_error=True)


def compose_helper_bundle(
    *,
    helper_role: str,
    base_agent_name: str,
    context: ToolExecutionContextService,
) -> LaunchBundle:
    """Build the helper :class:`LaunchBundle` from the calling tool's context.

    Raises :class:`HelperComposeError` for any wiring or lookup failure.
    The caller maps it to a :class:`ToolResult` error.
    """
    composer: ContextComposer | None = context.composer
    if composer is None:
        raise HelperComposeError(
            f"ask_{helper_role}: composer is not wired into execution context."
        )

    parent_task_id = context.task_center_task_id
    if not parent_task_id:
        raise HelperComposeError(
            f"ask_{helper_role}: parent task id is missing from execution context."
        )

    deps = composer.engine.deps
    if deps.context_packet_store is None:
        raise HelperComposeError(
            f"ask_{helper_role}: composer is missing ContextPacketStore."
        )

    parent_task = deps.task_store.get_task(parent_task_id)
    if parent_task is None:
        raise HelperComposeError(
            f"ask_{helper_role}: parent task {parent_task_id!r} not found."
        )
    parent_packet_id = parent_task.get("context_packet_id")
    if not parent_packet_id:
        raise HelperComposeError(
            f"ask_{helper_role}: parent task {parent_task_id!r} has no "
            "context_packet_id; helper inheritance unavailable."
        )

    mission_id = context.task_center_request_id
    if not mission_id:
        parent_packet = deps.context_packet_store.get(parent_packet_id)
        if parent_packet is None:
            raise HelperComposeError(
                f"ask_{helper_role}: parent packet {parent_packet_id!r} not found."
            )
        mission_id = parent_packet.canonical_refs.mission_id

    helper_task_id = f"{helper_role}:{uuid.uuid4()}"
    scope = ContextScope(
        mission_id=mission_id,
        task_id=helper_task_id,
        parent_packet_id=parent_packet_id,
        parent_task_id=parent_task_id,
    )
    return composer.compose(base_agent_name=base_agent_name, scope=scope)


__all__ = ["HelperComposeError", "compose_helper_bundle"]
