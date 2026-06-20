# Phase 5 Prompt: Add `sandbox-manager` Server And Forwarding

Use this prompt after phase 4 has completed.

```text
You are working in:

/Users/yifanxu/machine_learning/LoVC/ephemeral-ai/ephemeral-os

Task:

Implement phase 5 only: add the `sandbox-manager` server endpoint and route
unified `SandboxRequest` messages. Manager-owned operations dispatch locally.
Sandbox-scoped daemon operations are forwarded through the existing
`SandboxDaemonClient` abstraction.

Before editing, read:

- docs/refactoring/sandbox-implementation-guide.md
- docs/refactoring/sandbox-manager.md
- docs/refactoring/sandbox-protocol.md
- docs/refactoring/sandbox-manager-daemon-split.md

Required starting state:

- `crates/sandbox-protocol` exists.
- `crates/sandbox-protocol/src/scope.rs` exists.
- `sandbox_protocol::SandboxRequest` exists.
- `sandbox_protocol::OperationScope` exists.
- `sandbox_protocol::SandboxResponse` exists.
- `crates/sandbox-runtime/operation` exists.
- `crates/sandbox-daemon` exists.
- `crates/sandbox-manager` exists.
- `crates/sandbox-manager/src/server` does not exist yet.
- `crates/sandbox-manager/src/operation/impls/invoke_sandbox_daemon.rs` does
  not exist.
- The manager operation catalog does not contain `invoke_sandbox_daemon`.
- Runtime support crates still exist under `crates/daemon`:
  `command`, `workspace`, `namespace-process`, `layerstack`, `overlay`, and
  `config`.

If this starting state is not true, stop and report that phase 4 is not
complete. Do not implement phase 5 against the pre-unification manager API.

Phase goal:

- Make `sandbox-manager` usable as a process endpoint.
- Add server config, listener lifecycle, connection handling, and request
  routing modules.
- Decode exactly one JSON-line `SandboxRequest` per connection.
- Dispatch manager operations locally.
- Forward daemon-owned sandbox-scoped operations through `SandboxDaemonClient`.
- Preserve separation from `sandbox-runtime`, `sandbox-daemon`, and
  `sandbox-gateway-cli`.

Package changed:

```text
Path:    crates/sandbox-manager
Package: sandbox-manager
Import:  sandbox_manager
```

Keep in `sandbox-manager`:

- Manager server config.
- Manager listener lifecycle.
- Manager connection handling.
- Request decoding at the server edge.
- Scope-based routing for `SandboxRequest`.
- Forwarding through `SandboxDaemonClient`.
- Tests using fake daemon clients and in-memory streams.

Keep out of `sandbox-manager`:

- Command execution semantics.
- Workspace capture/remount semantics.
- Layerstack publish/compaction semantics.
- Overlayfs mount primitives.
- CLI argument parsing.
- Direct dependency on `sandbox-runtime`, `sandbox-daemon`, or
  `sandbox-gateway-cli`.
- Public `invoke_sandbox_daemon` operation.
- A separate `RoutedRequest`, `ManagerRequest`, or `OperationTarget` wrapper.
- Real Docker, Firecracker, container, or VM lifecycle wiring.

Implementation steps:

1. Check current status:

   ```sh
   git status --short
   ```

2. Verify the phase 4 starting state:

   ```sh
   test -d crates/sandbox-protocol
   test -f crates/sandbox-protocol/src/scope.rs
   test -d crates/sandbox-runtime/operation
   test -d crates/sandbox-daemon
   test -d crates/sandbox-manager
   test ! -d crates/sandbox-manager/src/server
   test ! -f crates/sandbox-manager/src/operation/impls/invoke_sandbox_daemon.rs
   rg -n "SandboxRequest|OperationScope|SandboxResponse" crates/sandbox-protocol/src
   rg -n "invoke_sandbox_daemon" crates/sandbox-manager/src/operation
   ```

   The final `rg` command should return no matches.

3. Run and record baseline results:

   ```sh
   cargo fmt --check -p sandbox-protocol -p sandbox-manager
   cargo check -p sandbox-protocol -p sandbox-manager --tests
   cargo test -p sandbox-protocol -p sandbox-manager
   ```

   If any command fails, record that it was pre-existing and continue only if
   the failure is unrelated to adding the manager server.

4. Add server modules:

   ```text
   crates/sandbox-manager/src/server/
     mod.rs
     config.rs
     lifecycle.rs
     connection.rs
     dispatch.rs
     forward.rs
     error.rs
   ```

5. Export the server module from `src/lib.rs`.

   Re-export only the intended public surface, such as:

   ```rust
   pub use server::{SandboxManagerServer, ServerConfig, ServerError};
   ```

6. Add async server dependencies to `crates/sandbox-manager/Cargo.toml` only as
   needed:

   ```toml
   tokio.workspace = true
   tokio-util.workspace = true
   ```

   Do not add `sandbox-runtime`, `sandbox-daemon`, `sandbox-gateway-cli`,
   `command`, `workspace`, `layerstack`, `overlay`, or `namespace-process`.

7. Add `server/config.rs`:

   - Define `ServerConfig`.
   - Include at minimum:

     ```rust
     pub struct ServerConfig {
         pub socket_path: PathBuf,
         pub pid_path: PathBuf,
         pub max_concurrent_connections: usize,
     }
     ```

   - Prefer Unix socket transport for this phase.
   - Do not add TCP manager transport unless there is a concrete existing
     caller that requires it.

8. Add `server/error.rs`:

   - Use `thiserror`.
   - Convert I/O, JSON, bad request, invalid scope, missing daemon endpoint,
     forwarding, and task join failures into stable protocol error responses.
   - Reuse `sandbox_protocol::error_kind` values where possible.

9. Add `server/mod.rs`:

   - Define `SandboxManagerServer`.
   - Store:

     ```rust
     pub struct SandboxManagerServer {
         pub config: ServerConfig,
         pub services: Arc<ManagerServices>,
         pub shutdown: CancellationToken,
     }
     ```

   - Keep construction explicit and testable.

10. Add `server/lifecycle.rs`:

    - Bind the Unix socket.
    - Create parent directories for socket and pid paths.
    - Remove stale socket before binding.
    - Set Unix socket permissions to `0600` where supported.
    - Write the pid file.
    - Accept connections until shutdown.
    - Limit concurrent connections using a semaphore.
    - Remove pid file and socket on shutdown.

    Follow the existing `sandbox-daemon` server lifecycle style where useful,
    but keep the manager server independent.

11. Add `server/connection.rs`:

    - Handle exactly one request per connection.
    - Read one newline-delimited JSON request.
    - Enforce `sandbox_protocol::MAX_REQUEST_BYTES`.
    - Enforce `sandbox_protocol::REQUEST_READ_TIMEOUT_S`.
    - Decode the request into `sandbox_protocol::SandboxRequest`.
    - Write one newline-delimited response using
      `sandbox_protocol::response_line`.
    - Shutdown the writer after the response.

12. Add `server/dispatch.rs`:

    - Route by operation name and request scope.
    - Manager-owned operations:

      ```text
      if request.op is in sandbox_manager::operation_specs()
      and request.scope == OperationScope::System
      then dispatch locally
      ```

    - Reject manager-owned operations with `OperationScope::Sandbox`.
    - If request scope is `OperationScope::System` and the operation is not in
      the manager catalog, return unknown operation.
    - If request scope is `OperationScope::Sandbox { sandbox_id }` and the
      operation is not in the manager catalog, forward it to the daemon.

    This rule avoids a daemon-operation dependency in `sandbox-manager` while
    still keeping manager operations explicit.

13. Add `server/forward.rs`:

    - Validate the sandbox id from `OperationScope::Sandbox`.
    - Resolve `sandbox_id -> SandboxDaemonEndpoint` through `SandboxStore`.
    - Require the sandbox record to be ready and daemon endpoint to be present.
    - Forward the same `SandboxRequest` to:

      ```rust
      services.daemon_client.invoke(&endpoint, request)
      ```

    - Do not rewrite the request into a nested operation.
    - Do not create `invoke_sandbox_daemon`.

14. Update protocol serialization only if needed for forwarding tests:

    - If `SandboxRequest` needs to be sent over a socket by a real client, add
      `Serialize` / `Deserialize` derives in `sandbox-protocol`.
    - Do not reintroduce compatibility aliases such as `OwnedRequest`,
      `RpcRequest`, or `Response`.
    - Preserve existing protocol tests.

15. Add tests.

    Include focused tests such as:

    - `manager_server_dispatches_system_manager_operation_locally`.
    - `manager_server_rejects_manager_operation_with_sandbox_scope`.
    - `manager_server_unknown_system_operation_returns_unknown_op`.
    - `manager_server_forwards_sandbox_scoped_unknown_to_daemon_client`.
    - `manager_server_rejects_sandbox_scope_when_sandbox_missing`.
    - `manager_server_rejects_sandbox_scope_when_daemon_unavailable`.
    - `manager_connection_rejects_bad_json`.
    - `manager_connection_rejects_oversized_request`.

    Prefer in-memory stream tests for connection handling where possible.
    Use fake runtime, fake installer, fake store data, and fake daemon client.

16. Keep manager operation catalog unchanged:

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

    Do not add daemon operations such as `exec_command`, `poll_command`, or
    `cancel_command` to the manager catalog.

Non-goals:

- Do not create `sandbox-gateway-cli`.
- Do not add CLI parsing.
- Do not add public `invoke_sandbox_daemon`.
- Do not introduce `RoutedRequest`, `ManagerRequest`, or `OperationTarget`.
- Do not add direct dependencies on `sandbox-runtime`, `sandbox-daemon`, or
  `sandbox-gateway-cli`.
- Do not move runtime support crates.
- Do not implement command/workspace/layerstack/overlay behavior.
- Do not implement Docker, Firecracker, container, or VM lifecycle.
- Do not remove `command-request.json`.
- Do not change daemon command operation behavior.

Acceptance checks:

```sh
test -d crates/sandbox-manager/src/server
test -f crates/sandbox-manager/src/server/config.rs
test -f crates/sandbox-manager/src/server/lifecycle.rs
test -f crates/sandbox-manager/src/server/connection.rs
test -f crates/sandbox-manager/src/server/dispatch.rs
test -f crates/sandbox-manager/src/server/forward.rs
test ! -f crates/sandbox-manager/src/operation/impls/invoke_sandbox_daemon.rs
rg -n "invoke_sandbox_daemon" crates/sandbox-manager/src/operation
rg -n "RoutedRequest|ManagerRequest|OperationTarget" crates/sandbox-manager/src
rg -n "pub type (OwnedRequest|RpcRequest|Response)" crates/sandbox-protocol/src
rg -n "sandbox_runtime::|sandbox_daemon::|sandbox_gateway_cli::|command::|workspace::|layerstack::|overlay::|namespace_process::" crates/sandbox-manager/src
cargo fmt --check -p sandbox-protocol -p sandbox-manager
cargo check -p sandbox-protocol -p sandbox-manager --tests
cargo test -p sandbox-protocol -p sandbox-manager
cargo clippy -p sandbox-protocol -p sandbox-manager --all-targets --no-deps -- -D warnings
```

The first three `rg` acceptance scans should return no matches.

Final response requirements:

- Summarize the new server modules.
- State whether phase 4 starting-state checks passed.
- State whether baseline checks had pre-existing failures.
- State final verification commands and results.
- Call out that forwarding is scope-based server routing, not a public manager
  operation.
- Call out that manager still does not depend on daemon runtime crates.
- Do not claim phase 6 or gateway work was done.
```
