"""Notification trigger helpers for workflow-depth checks."""

from __future__ import annotations

from typing import Any

from workflow._core.primitives import WorkflowInvariantViolation
from workflow._core.workflow_depth import is_nested_workflow


def tool_context_is_nested_workflow(context: Any) -> bool:
    metadata = getattr(context, "tool_metadata", None)
    if metadata is None:
        return False
    runtime = getattr(metadata, "attempt_runtime", None)
    workflow_id = getattr(metadata, "workflow_id", None)
    if runtime is None or not workflow_id:
        get = getattr(metadata, "get", None)
        if callable(get):
            runtime = runtime or get("attempt_runtime")
            workflow_id = workflow_id or get("workflow_id")
    if runtime is None or not workflow_id:
        return False
    try:
        return is_nested_workflow(workflow_id=str(workflow_id), deps=runtime)
    except WorkflowInvariantViolation:
        return False


__all__ = ["tool_context_is_nested_workflow"]
