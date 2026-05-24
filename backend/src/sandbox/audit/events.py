"""Sandbox audit event type constants."""

from __future__ import annotations

OPERATION_STARTED = "sandbox.operation.started"
OPERATION_COMPLETED = "sandbox.operation.completed"
OPERATION_FAILED = "sandbox.operation.failed"
OPERATION_CONFLICTED = "sandbox.operation.conflicted"

OCC_PREPARED = "sandbox.occ.prepared"
OCC_COMMITTED = "sandbox.occ.committed"
OCC_CONFLICTED = "sandbox.occ.conflicted"

OVERLAY_EXECUTED = "sandbox.overlay.executed"

LAYER_STACK_LEASE_ACQUIRED = "sandbox.layer_stack.lease_acquired"
LAYER_STACK_LAYER_PUBLISHED = "sandbox.layer_stack.layer_published"
LAYER_STACK_AUTO_SQUASHED = "sandbox.layer_stack.auto_squashed"

RESOURCE_SNAPSHOT = "sandbox.resource.snapshot"

WORKSPACE_LIFECYCLE_STARTED = "workspace_lifecycle_started"
WORKSPACE_LIFECYCLE_COMPLETED = "workspace_lifecycle_completed"
WORKSPACE_LIFECYCLE_FAILED = "workspace_lifecycle_failed"

# Background shell job lifecycle (plan §Audit events).
# Emitted by :class:`sandbox.ephemeral_workspace.shell_job.ShellJobRegistry` via
# the optional ``audit_callback`` constructor argument. AC-5 requires exactly
# one ``shell_reaped`` per ``shell_launched`` with matching ``job_id``.
SHELL_LAUNCHED = "sandbox.shell.launched"
SHELL_POLLED = "sandbox.shell.polled"
SHELL_CANCELLED = "sandbox.shell.cancelled"
SHELL_REAPED = "sandbox.shell.reaped"

__all__ = [
    "LAYER_STACK_AUTO_SQUASHED",
    "LAYER_STACK_LAYER_PUBLISHED",
    "LAYER_STACK_LEASE_ACQUIRED",
    "OCC_COMMITTED",
    "OCC_CONFLICTED",
    "OCC_PREPARED",
    "OPERATION_COMPLETED",
    "OPERATION_CONFLICTED",
    "OPERATION_FAILED",
    "OPERATION_STARTED",
    "OVERLAY_EXECUTED",
    "RESOURCE_SNAPSHOT",
    "WORKSPACE_LIFECYCLE_COMPLETED",
    "WORKSPACE_LIFECYCLE_FAILED",
    "WORKSPACE_LIFECYCLE_STARTED",
    "SHELL_CANCELLED",
    "SHELL_LAUNCHED",
    "SHELL_POLLED",
    "SHELL_REAPED",
]
