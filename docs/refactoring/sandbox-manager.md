# sandbox-manager Crate Spec

## Identity

```text
Path:    crates/sandbox-manager
Package: sandbox-manager
Import:  sandbox_manager
```

`sandbox-manager` is the host-side control plane. It owns sandbox lifecycle,
daemon placement, daemon endpoint tracking, and manager operation dispatch.

## Owns

- Sandbox identity and lifecycle state.
- Sandbox registry/store.
- Host runtime abstraction for creating and destroying sandboxes.
- Installing, starting, stopping, and health-checking `sandbox-daemon`.
- Mapping `SandboxId` to `SandboxDaemonEndpoint`.
- Manager operation catalog and dispatch.
- Routing sandbox-scoped daemon requests to the selected sandbox daemon.

## Must Not Own

- Command execution semantics.
- Workspace capture/remount semantics.
- Layerstack publish/compaction semantics.
- Overlayfs mount primitives.
- CLI argument parsing.
- In-sandbox daemon transport internals.

Forwarding a daemon operation is allowed. Implementing the daemon operation is
not allowed.

## Target Modules

```text
src/
  lib.rs
  model.rs
  error.rs
  store.rs
  runtime.rs
  daemon_install.rs
  daemon_client.rs

  operation/
    mod.rs
    specs.rs
    dispatch.rs
    impls/
      create_sandbox.rs
      destroy_sandbox.rs
      list_sandboxes.rs
      inspect_sandbox.rs
      start_sandbox_daemon.rs
      stop_sandbox_daemon.rs
      describe_manager_operations.rs
      describe_daemon_operations.rs

  server/
    mod.rs
    config.rs
    lifecycle.rs
    connection.rs
    dispatch.rs
    forward.rs
```

## Core Types

```rust
pub struct SandboxId(String);

pub struct SandboxRecord {
    pub id: SandboxId,
    pub state: SandboxState,
    pub daemon: Option<SandboxDaemonEndpoint>,
}

pub enum SandboxState {
    Creating,
    Ready,
    Stopping,
    Stopped,
    Failed,
}

pub struct SandboxDaemonEndpoint {
    pub socket_path: PathBuf,
    pub auth_token: Option<String>,
}
```

## Operation Catalog

Manager operations:

```text
create_sandbox
destroy_sandbox
list_sandboxes
inspect_sandbox
start_sandbox_daemon
stop_sandbox_daemon
describe_manager_operations
describe_daemon_operations
```

`OperationSpec` comes from `sandbox-protocol`. Dispatch entries are local to
`sandbox-manager`.

## Request Routing

The manager accepts the unified `sandbox_protocol::SandboxRequest` DTO.

```rust
pub struct SandboxRequest {
    pub request_id: String,
    pub scope: OperationScope,
    pub op: String,
    pub args: serde_json::Value,
}
```

Manager operations are dispatched locally. Daemon-owned operations are routed
through `SandboxDaemonEndpoint` when the request has
`OperationScope::Sandbox { sandbox_id }`.

There is no public `invoke_sandbox_daemon` operation and no separate
`RoutedRequest` wrapper. Forwarding is an implementation detail of the manager
server.

## Dependency Rules

Allowed:

- `sandbox-protocol`
- process/socket/runtime crates needed for host-side sandbox lifecycle
- `tokio` if the manager server is async

Forbidden:

- `sandbox-runtime`
- `sandbox-runtime-command`
- `sandbox-runtime-workspace`
- `sandbox-runtime-layerstack`
- `sandbox-runtime-overlay`

The manager may depend on a daemon client transport, not daemon runtime
implementation.

## Verification

```sh
cargo fmt --check -p sandbox-manager
cargo check -p sandbox-manager --tests
cargo test -p sandbox-manager
```
