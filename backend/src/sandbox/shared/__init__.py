"""Shared building blocks for the sandbox package.

Contains models, tool primitives, clock, timing keys, lease guard, ordered
lock, async bridge, and the command-exec contract / policy / resource-metrics
types used across `sandbox.daemon`, `sandbox.ephemeral_workspace`,
`sandbox.isolated_workspace`, `sandbox.layer_stack`, `sandbox.occ`, and
host-side audit/api code.

Intentionally public — consumed by `sandbox.api`, `sandbox.daemon`, `tools/`,
`plugins/`, `engine/`, `task_center_runner/`, and the test trees.
"""
