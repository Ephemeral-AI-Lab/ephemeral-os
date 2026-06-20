# Phase 4 Prompt: Add `sandbox-manager` Core

Use this prompt after phase 3 has completed.

```text
You are working in:

/Users/yifanxu/machine_learning/LoVC/ephemeral-ai/ephemeral-os

Task:

Implement phase 4 only: add the `sandbox-manager` core package with host-side
sandbox lifecycle model, in-memory registry, lifecycle traits, daemon-client
abstraction, and manager operation catalog/dispatch.

Before editing, read:

- docs/refactoring/sandbox-implementation-guide.md
- docs/refactoring/sandbox-manager.md
- docs/refactoring/sandbox-protocol.md
- docs/refactoring/sandbox-manager-daemon-split.md

Required starting state:

- `crates/sandbox-protocol` exists.
- `crates/sandbox-runtime/operation` exists.
- `crates/sandbox-daemon` exists.
- `crates/daemon/server` no longer exists.
- `crates/daemon/eosd` no longer exists.
- `crates/sandbox-manager` does not exist yet.
- Root `Cargo.toml` has workspace dependency:
  `sandbox-daemon = { path = "crates/sandbox-daemon" }`.
- Runtime support crates still exist under `crates/daemon`:
  `command`, `workspace`, `namespace-process`, `layerstack`, `overlay`, and
  `config`.

If this starting state is not true, stop and report that phase 3 is not
complete. Do not implement phase 4 against the old pre-phase-3 layout.

Phase goal:

- Create package `sandbox-manager`.
- Model sandbox identity, lifecycle state, daemon endpoint state, and the
  registry/store.
- Add traits for host sandbox runtime and daemon install/start/stop behavior.
- Add a daemon-client abstraction for later sandbox-scoped forwarding and
  daemon catalog discovery.
- Add manager operation specs and dispatch.
- Use test doubles or local stubs only.

New package:

```text
Path:    crates/sandbox-manager
Package: sandbox-manager
Import:  sandbox_manager
```

Keep in `sandbox-manager`:

- `SandboxId`.
- `SandboxRecord`.
- `SandboxState`.
- `SandboxDaemonEndpoint`.
- Sandbox registry/store.
- Host runtime abstraction.
- Daemon install/start/stop abstraction.
- Daemon client abstraction.
- Manager operation catalog and dispatch.
- Forwarding abstraction for sandbox-scoped daemon requests.

Keep out of `sandbox-manager`:

- Command execution semantics.
- Workspace capture/remount semantics.
- Layerstack publish/compaction semantics.
- Overlayfs mount primitives.
- CLI argument parsing.
- Manager server/listener lifecycle.
- In-sandbox daemon transport internals.
- Direct dependency on `sandbox-runtime`, `sandbox-daemon`, or
  `sandbox-gateway-cli`.

Implementation steps:

1. Check current status:

   ```sh
   git status --short
   ```

2. Verify the phase 3 starting state:

   ```sh
   test -d crates/sandbox-protocol
   test -d crates/sandbox-runtime/operation
   test -d crates/sandbox-daemon
   test ! -d crates/daemon/server
   test ! -d crates/daemon/eosd
   test ! -d crates/sandbox-manager
   rg -n "sandbox-daemon = \\{ path = \"crates/sandbox-daemon\" \\}" Cargo.toml
   ```

3. Run and record baseline results before adding the new package:

   ```sh
   cargo fmt --check -p sandbox-protocol -p sandbox-runtime -p sandbox-daemon
   cargo check -p sandbox-protocol -p sandbox-runtime -p sandbox-daemon
   cargo test -p sandbox-protocol -p sandbox-runtime -p sandbox-daemon
   ```

   If any command fails, record that it was pre-existing and continue only if
   the failure is unrelated to adding `sandbox-manager`.

4. Add the package:

   ```text
   crates/sandbox-manager/
     Cargo.toml
     src/lib.rs
   ```

5. Update root `Cargo.toml`:

   - Add workspace member `crates/sandbox-manager`.
   - Add workspace dependency:

     ```toml
     sandbox-manager = { path = "crates/sandbox-manager" }
     ```

6. Create `crates/sandbox-manager/Cargo.toml`:

   ```toml
   [package]
   name = "sandbox-manager"
   version.workspace = true
   edition.workspace = true
   rust-version.workspace = true
   license.workspace = true

   [dependencies]
   sandbox-protocol.workspace = true
   serde_json.workspace = true
   thiserror.workspace = true

   [lints]
   workspace = true
   ```

   Do not add `sandbox-runtime`, `sandbox-daemon`, `sandbox-gateway-cli`,
   `command`, `workspace`, `layerstack`, `overlay`, or `namespace-process` as
   dependencies.

7. Add domain model in `src/model.rs`:

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

   Prefer constructors/accessors where they protect invariants. `SandboxId`
   should not be an unvalidated public string field.

8. Add typed errors in `src/error.rs`:

   - Use `thiserror`.
   - Include stable mappings to `sandbox_protocol::error_kind` values or a
     helper that returns the protocol error kind.
   - Include domain errors such as duplicate sandbox, missing sandbox, invalid
     state transition, daemon unavailable, runtime failed, and forwarding
     failed.

9. Add `src/store.rs`:

   - Implement an in-memory sandbox registry.
   - Store records by `SandboxId`.
   - Support create, insert/update, list, inspect, remove, and endpoint update.
   - Keep state transitions explicit.
   - Tests may use the store directly.

10. Add `src/runtime.rs`:

    - Define a trait for host-side sandbox lifecycle.
    - Use test doubles for implementation.
    - Do not wire Docker, Firecracker, containers, or VM setup in this phase.

11. Add `src/daemon_install.rs`:

    - Define a trait for placing/starting/stopping/checking `sandbox-daemon`.
    - Return or update `SandboxDaemonEndpoint`.
    - Use test doubles for implementation.

12. Add `src/daemon_client.rs`:

    - Define a trait for daemon catalog discovery and later forwarding of
      unified `sandbox-protocol` requests to a daemon endpoint.
    - The trait may expose methods such as:

      ```rust
      fn describe_operations(
          &self,
          endpoint: &SandboxDaemonEndpoint,
      ) -> Result<sandbox_protocol::OperationCatalog, ManagerError>;

      fn invoke(
          &self,
          endpoint: &SandboxDaemonEndpoint,
          request: sandbox_protocol::SandboxRequest,
      ) -> Result<sandbox_protocol::OperationResponse, ManagerError>;
      ```

    - Use test doubles only. Do not implement real socket transport in this
      phase unless it is already available as a small protocol-only helper.

13. Add operation modules:

    ```text
    src/operation/
      mod.rs
      specs.rs
      dispatch.rs
      impls/
        mod.rs
        create_sandbox.rs
        destroy_sandbox.rs
        list_sandboxes.rs
        inspect_sandbox.rs
        start_sandbox_daemon.rs
        stop_sandbox_daemon.rs
        describe_manager_operations.rs
        describe_daemon_operations.rs
    ```

14. Add a local manager operation entry type.

    `sandbox-protocol` owns `OperationSpec`, but it must not own dispatch
    entries. Keep the manager equivalent local to `sandbox-manager`, for
    example:

    ```rust
    pub struct ManagerOperationEntry {
        pub spec: &'static sandbox_protocol::OperationSpec,
        pub dispatch: fn(
            &ManagerServices,
            sandbox_protocol::OperationRequest<'_>,
        ) -> sandbox_protocol::OperationResponse,
    }
    ```

15. Add a manager service/context type.

    It should hold the store and trait-object services needed by operations,
    for example:

    ```rust
    pub struct ManagerServices {
        pub store: Arc<SandboxStore>,
        pub runtime: Arc<dyn SandboxRuntime>,
        pub daemon_installer: Arc<dyn SandboxDaemonInstaller>,
        pub daemon_client: Arc<dyn SandboxDaemonClient>,
    }
    ```

    Use `Send + Sync` trait bounds if the object can later be shared by the
    manager server.

16. Implement manager operation specs:

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

    `OperationSpec` comes from `sandbox-protocol`. Do not add daemon operation
    specs such as `exec_command` to the manager catalog.

17. Implement manager operation dispatch:

    - `create_sandbox`: use the runtime trait and update the store.
    - `destroy_sandbox`: use the runtime trait and update/remove from store.
    - `list_sandboxes`: return store records.
    - `inspect_sandbox`: return one store record.
    - `start_sandbox_daemon`: use daemon installer trait and update endpoint.
    - `stop_sandbox_daemon`: use daemon installer trait and clear/update
      endpoint.
    - `describe_manager_operations`: return the manager catalog.
    - `describe_daemon_operations`: use daemon client abstraction; do not
      depend on `sandbox-runtime`.

18. Add tests with fakes:

    - `operation_catalog_contains_only_manager_operations`.
    - `create_list_inspect_destroy_sandbox_with_fake_runtime`.
    - `start_stop_daemon_updates_endpoint_with_fake_installer`.
    - `describe_daemon_operations_uses_daemon_client_trait`.
    - Store duplicate/missing sandbox error cases.

Non-goals:

- Do not add `src/server/`.
- Do not open sockets.
- Do not implement manager listener lifecycle.
- Do not create `sandbox-gateway-cli`.
- Do not implement Docker, Firecracker, container, or VM lifecycle.
- Do not depend on `sandbox-runtime`.
- Do not depend on `sandbox-daemon`.
- Do not depend on `sandbox-gateway-cli`.
- Do not move runtime support crates.
- Do not implement command/workspace/layerstack/overlay behavior.
- Do not remove `command-request.json`.

Acceptance checks:

```sh
test -d crates/sandbox-manager
test ! -d crates/sandbox-manager/src/server
rg -n "sandbox-runtime|sandbox-daemon|sandbox-gateway-cli|command\\.workspace|workspace\\.workspace|layerstack\\.workspace|overlay\\.workspace|namespace-process\\.workspace" crates/sandbox-manager/Cargo.toml
rg -n "sandbox_runtime::|sandbox_daemon::|sandbox_gateway_cli::|command::|workspace::|layerstack::|overlay::|namespace_process::" crates/sandbox-manager/src
rg -n "\\b(exec_command|poll_command|cancel_command)\\b" crates/sandbox-manager/src/operation
rg -n "Docker|docker|Firecracker|firecracker" crates/sandbox-manager
rg -n "ManagerOperationEntry|ManagerServices|SandboxRuntime|SandboxDaemonInstaller|SandboxDaemonClient" crates/sandbox-manager/src
cargo fmt --check -p sandbox-manager
cargo check -p sandbox-manager --tests
cargo test -p sandbox-manager
```

The first four `rg` scans should return no matches. The fifth `rg` scan should
show the local manager dispatch/service abstractions.

Final response requirements:

- Summarize the new package and core modules.
- State whether phase 3 starting-state checks passed.
- State whether baseline checks had pre-existing failures.
- State final verification commands and results.
- Call out that manager server/listener work is intentionally deferred to
  phase 5.
- Call out that real Docker/Firecracker/sandbox runtime wiring is intentionally
  deferred.
- Do not claim phase 5 work was done.
```
