# Sandbox API Surface

## 1. Foreground Tool Verbs

Foreground sandbox tools use `api.v1.<verb>` for both default ephemeral mode
and open isolated-workspace mode:

- `api.v1.read_file`
- `api.v1.write_file`
- `api.v1.edit_file`
- `api.v1.grep`
- `api.v1.glob`
- `api.v1.shell`

Daemon handlers are thin adapters. They build a `ToolCallRequest`, resolve the
active `WorkspacePipeline`, and call `run_tool_call`.

## 2. WorkspacePipeline Contract

`WorkspacePipeline` has one foreground method: `run_tool_call`.
`EphemeralPipeline` creates a per-call overlay, runs the tool in the namespace,
captures and commits write changes, and destroys the overlay in `finally`.
`IsolatedPipeline` reuses the session overlay created by `enter` and destroyed
by `exit`.

## 3. Lifecycle RPCs

`api.isolated_workspace.enter`, `api.isolated_workspace.exit`,
`api.isolated_workspace.status`, `api.isolated_workspace.list_open`, and
`api.isolated_workspace.test_reset` remain lifecycle-only daemon RPCs.
Tool-operation RPCs under `api.isolated_workspace.*` are not part of the
Phase 2 API.

## 4. Host Lifecycle API

Host-side lifecycle callers use `sandbox.lifecycle.enter_isolated_workspace`
and `sandbox.lifecycle.exit_isolated_workspace`. These functions live outside
`sandbox.api` because they are host coroutines, not client-side wire artifacts.

Agent tools expose the same lifecycle as `enter_isolated_workspace` and
`exit_isolated_workspace`.

## 5. Routing Rules

The daemon resolves workspace mode by `agent_id`. If an isolated workspace is
open for the caller, the tool call routes to `IsolatedPipeline`; otherwise it
routes to `EphemeralPipeline`.

Plugin dispatch is blocked while the caller has an open isolated workspace.
The gate rejects `api.plugin.*` and `plugin.*` operations for that agent with
`forbidden_in_isolated_workspace`.

## 6. Pass-through Paths

Read-only verbs still mount the overlay but skip capture and commit. Write
verbs mount the overlay and capture the upperdir after the namespace child
returns.

| Path class | Read-only verbs | Write verbs |
| --- | --- | --- |
| Workspace path | Read through mounted overlay | Capture and commit upperdir changes |
| Non-workspace path | Read through namespace pass-through | Allowed unless denied below |
| System path prefix | Read allowed by OS permissions | Denied by namespace child |

System-path writes are denied for `/etc`, `/var`, `/proc`, `/sys`, and `/boot`.

## 7. Namespace Child Boundary

The namespace child uses two dispatch tiers:

- `VERB_TABLE` for `read_file`, `write_file`, `edit_file`, `grep`, and `glob`.
- `tool_primitives.shell.run` for `shell`.

Both ephemeral and isolated foreground calls share `overlay.run_in_namespace`.

## 8. OCC Publish Semantics

Write-allowed ephemeral calls publish captured overlay changes through the OCC
client boundary. Single-path `write_file` and `edit_file` captures are tagged
as `api_write`; broader shell or multi-path captures are tagged as
`overlay_capture`.

Isolated workspace writes stay in the isolated upperdir until `exit`, which
destroys the session overlay.

## 9. No-follow File Access

`read_file`, `write_file`, `edit_file`, `grep`, and `glob` use the shared
`tool_primitives.file_ops.open_no_follow` chokepoint. The helper refuses
symlink components by using `openat2(RESOLVE_NO_SYMLINKS)` where available and
a per-component `open(..., O_NOFOLLOW)` walk elsewhere.

## 10. WorkspaceSession Status

`WorkspaceSession` is intentionally test-only at
`tests/mock/sandbox/_fixtures/workspace_session.py`. Production code should use
the explicit lifecycle pair.

## 11. Background Policy

Background tool lifecycle is out of scope for Phase 2 and is documented in
`docs/plans/unify_sandbox_workspace_phase2_5.md`. Phase 2 foreground code does
not add `_session_jobs`, `_drain_background_jobs`, request cancellation RPCs, or
engine-owned background task tracking.

Deployment preconditions for native overlay execution remain private mount
namespace support plus the overlay new mount API. Environments that lack those
capabilities must skip the native overlay paths rather than falling back to
isolated-workspace tool-operation RPCs.
