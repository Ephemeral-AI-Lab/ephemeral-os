# Sandbox API — op catalog

GENERATED from `contract/ops.json` by `cargo run -p xtask -- gen-docs`.
Do not edit by hand: `cargo run -p xtask -- check-contract` fails when
this file drifts from the committed catalog.

Protocol version: **1**

## Public ops (client socket)

The complete public vocabulary served on the `eos-api` client socket.

| Op | Aliases | Served by | Family | Mutates | Summary |
|---|---|---|---|---|---|
| `sandbox.acquire` | — | host | Sandbox | yes | Provision a sandbox container plus daemon and return its sandbox_id. |
| `sandbox.release` | — | host | Sandbox | yes | Destroy the sandbox container and drop its registry entry. |
| `sandbox.status` | — | host | Sandbox | no | Host view of one sandbox (container/endpoint/recovery state) plus embedded daemon readiness. |
| `sandbox.list` | — | host | Sandbox | no | Enumerate the sandbox registry. |
| `sandbox.call.heartbeat` | `api.v1.heartbeat` | daemon | Control | yes | Extend the lease on an in-flight invocation. |
| `sandbox.call.cancel` | `api.v1.cancel` | daemon | Control | yes | Request cooperative cancellation of an in-flight invocation. |
| `sandbox.call.count` | `api.v1.inflight_count` | daemon | Control | no | Count in-flight invocations. |
| `sandbox.file.read` | `api.v1.read_file` | daemon | Files | no | Read one file from the layer stack or isolated workspace. |
| `sandbox.file.write` | `api.v1.write_file` | daemon | Files | yes | Write one file through the OCC gate. |
| `sandbox.file.edit` | `api.v1.edit_file` | daemon | Files | yes | Edit one file through the OCC gate. |
| `sandbox.plugin.ensure` | `api.plugin.ensure` | daemon | Plugins | yes | Ensure a plugin service is installed and running. |
| `sandbox.plugin.status` | `api.plugin.status` | daemon | Plugins | no | Inspect plugin service status. |
| `sandbox.isolation.enter` | `api.isolated_workspace.enter` | daemon | IsolatedWorkspace | yes | Enter isolated workspace mode for a caller. |
| `sandbox.isolation.exit` | `api.isolated_workspace.exit` | daemon | IsolatedWorkspace | yes | Exit isolated workspace mode for a caller. |
| `sandbox.isolation.status` | `api.isolated_workspace.status` | daemon | IsolatedWorkspace | no | Inspect isolated workspace status. |
| `sandbox.command.exec` | `api.v1.exec_command` | daemon | CommandSession | yes | Run a foreground command or start a command session. |
| `sandbox.command.write_stdin` | `api.v1.write_stdin` | daemon | CommandSession | yes | Write stdin to a command session. |
| `sandbox.command.poll` | `api.v1.command.read_progress` | daemon | CommandSession | no | Poll command-session progress without writing stdin. |
| `sandbox.command.cancel` | `api.v1.command.cancel` | daemon | CommandSession | yes | Cancel a command session. |
| `sandbox.command.collect_completed` | `api.v1.command.collect_completed` | daemon | CommandSession | yes | Collect completed command-session notifications. |
| `sandbox.command.count` | `api.v1.command_session_count` | daemon | CommandSession | no | Count live command sessions. |
| `sandbox.run.end` | `api.v1.cancel_workspace_runs_by_caller_id` | daemon | WorkspaceRun | yes | End a run: cancel every workspace run owned by one caller (caller_id == agent_run_id), discarding its command sessions and exiting its isolated workspace. |

## Operator ops (`eos-api admin`)

Served only on the operator socket beside the client socket; never the client socket.

| Op | Aliases | Served by | Family | Mutates | Summary |
|---|---|---|---|---|---|
| `sandbox.checkpoint.layer_metrics` | `api.layer_metrics` | daemon | Checkpoint | no | Report LayerStack and storage metrics for the sandbox. |
| `sandbox.checkpoint.ensure_base` | `api.ensure_workspace_base` | daemon | Checkpoint | yes | Ensure a workspace base binding exists. |
| `sandbox.checkpoint.build_base` | `api.build_workspace_base` | daemon | Checkpoint | yes | Build or rebuild a workspace base binding. |
| `sandbox.checkpoint.commit_to_workspace` | `api.commit_to_workspace` | daemon | Checkpoint | yes | Materialize LayerStack state into the bound workspace. |
| `sandbox.checkpoint.commit_to_git` | `api.commit_to_git` | daemon | Checkpoint | yes | Commit a LayerStack snapshot into the bound workspace's durable Git repo. |
| `sandbox.checkpoint.binding` | `api.workspace_binding` | daemon | Checkpoint | no | Inspect the workspace binding for a layer stack root. |
| `sandbox.audit.pull` | `api.audit.pull` | daemon | Audit | no | Pull audit events after a cursor. |
| `sandbox.audit.snapshot` | `api.audit.snapshot` | daemon | Audit | no | Snapshot audit ring metadata. |
| `sandbox.audit.reset_floor` | `api.audit.reset_floor` | daemon | Audit | yes | Reset the audit floor when the daemon-side test gate allows it. |
| `sandbox.isolation.list_open` | `api.isolated_workspace.list_open` | daemon | IsolatedWorkspace | no | List open isolated workspaces. |
| `sandbox.run.cancel_all` | `api.v1.cancel_workspace_runs` | daemon | WorkspaceRun | yes | Cancel every workspace run in the sandbox: the whole-sandbox sweep backstop. |

## Internal ops

Reserved for the host recovery machine; not served from any socket.

| Op | Aliases | Served by | Family | Mutates | Summary |
|---|---|---|---|---|---|
| `sandbox.runtime.ready` | `api.runtime.ready` | daemon | Control | no | Daemon readiness probe used by the host recovery machine. |

## Test ops

Daemon-side test hooks; refused by `eos-api` and exercised only by direct-daemon test harnesses.

| Op | Aliases | Served by | Family | Mutates | Summary |
|---|---|---|---|---|---|
| `sandbox.isolation.test_reset` | `api.isolated_workspace.test_reset` | daemon | IsolatedWorkspace | yes | Test-only isolated workspace reset hook. |

## Dynamic plugin ops

`plugin.<id>.<op>` names are registered at runtime by plugin services inside a sandbox. They are daemon-served, public, and treated as mutating (fail-closed) by the recovery ladder; they never appear in the static catalog.
