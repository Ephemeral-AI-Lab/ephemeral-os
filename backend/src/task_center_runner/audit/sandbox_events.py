"""Derive sandbox subsystem audit events from tool-completion metadata."""

from __future__ import annotations

from task_center_runner.audit.events import Event, EventType
from task_center_runner.audit.node_id import NodeId
from message.stream_events import ToolExecutionCompleted


_SANDBOX_TOOLS = frozenset({"read_file", "write_file", "edit_file", "shell"})
_CONFLICT_STATUSES = frozenset(
    {
        "aborted_lock",
        "aborted_overlap",
        "aborted_version",
        "failed",
        "not_found",
        "old_text_not_found",
        "rejected",
    }
)


def sandbox_events_from_tool_completion(
    stream_event: ToolExecutionCompleted,
    *,
    task_center_run_id: str,
) -> tuple[Event, ...]:
    """Translate sandbox timing metadata into explicit subsystem events."""
    tool_name = str(stream_event.tool_name or "")
    if tool_name not in _SANDBOX_TOOLS:
        return ()

    metadata = dict(stream_event.metadata or {})
    timings = _timings(metadata.get("timings"))
    changed_paths = _string_list(metadata.get("changed_paths"))
    status = str(metadata.get("status") or "")
    conflict_reason = str(metadata.get("conflict_reason") or "")
    node = NodeId(
        task_center_run_id=task_center_run_id,
        agent_name=stream_event.agent_name or None,
        agent_run_id=stream_event.run_id or None,
        tool_name=tool_name,
    )
    base_payload = {
        "tool_name": tool_name,
        "tool_id": stream_event.tool_id,
        "status": status,
        "changed_paths": changed_paths,
        "conflict_reason": conflict_reason or None,
    }
    events: list[Event] = []

    if _conflict_detected(
        is_error=stream_event.is_error,
        status=status,
        conflict_reason=conflict_reason,
    ):
        events.append(
            Event(
                type=EventType.SANDBOX_CONFLICT_DETECTED,
                node=node,
                payload={**base_payload, "timings": _select(timings, "api.")},
            )
        )

    if _has_any(
        timings,
        "api.read.lease_acquire_s",
        "api.write.lease_acquire_s",
        "api.edit.lease_acquire_s",
        "command_exec.prepare_snapshot_s",
        "layer_stack.prepare_workspace_snapshot.total_s",
    ):
        events.append(
            Event(
                type=EventType.SANDBOX_LAYER_STACK_LEASE_ACQUIRED,
                node=node,
                payload={
                    **base_payload,
                    "timings": _select(
                        timings,
                        "api.read.lease_acquire_s",
                        "api.write.lease_acquire_s",
                        "api.edit.lease_acquire_s",
                        "command_exec.prepare_snapshot_s",
                        "command_exec.release_snapshot_s",
                        "layer_stack.prepare_workspace_snapshot.",
                    ),
                },
            )
        )

    if _has_prefix(timings, "overlay.") or "api.shell.overlay_s" in timings:
        events.append(
            Event(
                type=EventType.SANDBOX_OVERLAY_EXECUTED,
                node=node,
                payload={
                    **base_payload,
                    "timings": _select(timings, "overlay.", "api.shell.overlay_s"),
                },
            )
        )

    if _has_prefix(timings, "occ.prepare"):
        events.append(
            Event(
                type=EventType.SANDBOX_OCC_CHANGESET_RECEIVED,
                node=node,
                payload={
                    **base_payload,
                    "timings": _select(timings, "occ.prepare"),
                },
            )
        )

    if _has_any(
        timings,
        "api.write.occ_apply_s",
        "api.edit.occ_apply_s",
        "command_exec.occ_apply_s",
    ) or _has_prefix(timings, "occ.commit") or _has_prefix(timings, "occ.apply"):
        events.append(
            Event(
                type=EventType.SANDBOX_OCC_CHANGES_COMMITTED,
                node=node,
                payload={
                    **base_payload,
                    "timings": _select(
                        timings,
                        "api.write.occ_apply_s",
                        "api.edit.occ_apply_s",
                        "command_exec.occ_apply_s",
                        "occ.commit",
                        "occ.apply",
                    ),
                },
            )
        )

    if changed_paths and not stream_event.is_error and _has_any(
        timings,
        "occ.commit.publish_layer_s",
        "api.write.occ_apply_s",
        "api.edit.occ_apply_s",
        "command_exec.occ_apply_s",
    ):
        events.append(
            Event(
                type=EventType.SANDBOX_LAYER_STACK_LAYER_CREATED,
                node=node,
                payload={
                    **base_payload,
                    "timings": _select(
                        timings,
                        "occ.commit.publish_layer_s",
                        "layer_stack.transaction.",
                    ),
                },
            )
        )

    if _has_prefix(timings, "layer_stack.auto_squash."):
        events.append(
            Event(
                type=EventType.SANDBOX_LAYER_STACK_LAYERS_SQUASHED,
                node=node,
                payload={
                    **base_payload,
                    "timings": _select(timings, "layer_stack.auto_squash."),
                },
            )
        )

    if _has_prefix(timings, "resource."):
        events.append(
            Event(
                type=EventType.SANDBOX_RESOURCE_SNAPSHOT,
                node=node,
                payload={
                    **base_payload,
                    "timings": _select(timings, "resource."),
                },
            )
        )

    return tuple(events)


def _conflict_detected(
    *,
    is_error: bool,
    status: str,
    conflict_reason: str,
) -> bool:
    if conflict_reason:
        return True
    return is_error and status in _CONFLICT_STATUSES


def _timings(value: object) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    timings: dict[str, float] = {}
    for key, raw in value.items():
        if not isinstance(key, str):
            continue
        try:
            timings[key] = float(raw)
        except (TypeError, ValueError):
            continue
    return timings


def _string_list(value: object) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    return [str(item) for item in value if str(item or "").strip()]


def _has_prefix(timings: dict[str, float], prefix: str) -> bool:
    return any(key.startswith(prefix) for key in timings)


def _has_any(timings: dict[str, float], *keys_or_prefixes: str) -> bool:
    return any(
        key in timings or any(item.startswith(key) for item in timings)
        for key in keys_or_prefixes
    )


def _select(timings: dict[str, float], *keys_or_prefixes: str) -> dict[str, float]:
    selected: dict[str, float] = {}
    for key, value in timings.items():
        if key in keys_or_prefixes or any(
            key.startswith(prefix) for prefix in keys_or_prefixes
        ):
            selected[key] = value
    return selected


__all__ = ["sandbox_events_from_tool_completion"]
