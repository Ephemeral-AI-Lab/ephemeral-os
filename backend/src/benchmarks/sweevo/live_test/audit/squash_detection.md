# Squash event surface — Step 0 spike (2026-05-08)

## Decision

The `squash_after_n_edits` scenario from plan §10 is **deferred to the next phase**.
This phase ships only `correctness_testing` per plan §15 step 7.

## Investigation

`backend/src/sandbox/api/tool/{edit,write,shell}.py` route through
`call_daemon_api` and return `EditFileResult` / `WriteFileResult` / `ShellResult`
(`backend/src/sandbox/models/`). Inspecting those result types:

- `EditFileResult` exposes: `success`, `changed_paths`, `applied_edits`,
  `status`, `conflict`, `conflict_reason`, `timings`.
- `WriteFileResult` exposes: `success`, `changed_paths`, `status`,
  `conflict`, `conflict_reason`, `timings`.
- `sandbox.api.status` has `get_health` / `get_sandbox` / `list_sandboxes`
  but no `get_workspace_status` returning a layer count.

There is **no `layer_count` field and no `squash_triggered` flag** on the public
result types today. The metadata-path option from plan §13.1 is therefore not
available without a daemon-side change.

## Implication for next phase

When `squash_after_n_edits` lands, the implementer chooses between:

1. **Add a `layer_count` field to the existing tool-result types** (preferred —
   one-time daemon change, then every scenario can observe layer growth without
   extra wire chatter). This requires a small surface bump in `sandbox/models`
   plus the daemon handler emitting the count.

2. **Read-back probe via a new sandbox-status verb** (e.g., a public
   `get_workspace_status(sandbox_id)` returning the active layer-stack depth).
   Heavier — adds a public API surface — but does not change the existing
   write/edit result schemas.

Both paths are documented in plan §13. The decision is intentionally deferred so
the framework infrastructure ships without coupling to a specific squash
detection mechanism.

## What this phase still does

Even without squash detection, the framework emits `SANDBOX_WRITE_COMMITTED`,
`SANDBOX_EDIT_COMMITTED`, `SANDBOX_SHELL_COMMITTED`,
`SANDBOX_BATCH_EDIT_APPLIED`, and `SANDBOX_CONFLICT_DETECTED` events from the
existing result fields. `SANDBOX_LAYER_GROWN` and `SANDBOX_SQUASH_TRIGGERED` are
declared in `audit/events.py::EventType` so the next-phase scenario can publish
them without further enum churn — but no producer wires them in this phase.
