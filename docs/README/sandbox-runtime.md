# Sandbox Runtime

Crate path: `crates/sandbox-runtime/operation`

Package: `sandbox-runtime`

`sandbox-runtime` is the protocol-free runtime application. It owns runtime
handlers, public/internal/HTTP-only registries, typed argument handling,
structured responses, and orchestration over runtime primitives. It does not
own semantic operation declarations, CLI presentation, or wire transport.

## Ownership and Boundary

Sandbox-scoped public requests reach the runtime application through the
composition and routing layers:

```text
CLI / MCP / console
  -> sandbox-operation-client
    -> sandbox-gateway
      -> sandbox-manager
        -> sandbox-daemon
          -> sandbox_runtime::dispatch_operation
            -> SandboxRuntimeOperations
```

`sandbox-operation-contract` owns `OperationRequest`, `OperationResponse`,
scope, route, and error vocabulary. The runtime domain in
`crates/sandbox-operations/catalog/src/runtime.rs` owns public semantic
declarations and routes; canonical internal identifiers live in
`crates/sandbox-operations/catalog/src/internal/runtime.rs`. The daemon applies
the protocol-owned wire codec and owns application composition. CLI paths,
flags, usage, examples, and help live in `sandbox-cli::projection`.

The runtime application's workspace dependencies are limited to the contract,
the catalog's `runtime` feature, `sandbox-observability`, and the workspace,
layerstack, namespace-execution, and namespace-process runtime primitives. It
must not depend on `sandbox-protocol`, `sandbox-operation-client`, product
adapters, composition roots, manager, or the observability application.

## Operation Registries

The public runtime catalog has two families:

- Command: `exec_command`, `write_command_stdin`, `read_command_lines`.
- File: `file_read`, `file_write`, `file_edit`, `file_blame`.

The canonical internal runtime set is
`create_workspace_session`, `destroy_workspace_session`,
`squash_layerstack`, `export_layerstack`, and `read_export_chunk`.
`file_list` shares the catalog's runtime-internal identifier module but is a
separate HTTP-only exception, served only by `POST /files/list`.

`src/operations/registry/` binds public and internal declarations to runtime
handlers. Dispatch keys are `(scope kind, operation name)`, and the public,
canonical-internal, and HTTP-only registries are disjoint.

CLI help joins the semantic runtime catalog with CLI-owned projection metadata:

```text
sandbox-runtime-cli help
sandbox-runtime-cli help exec_command
```

Help does not require `--sandbox-id`. Every non-help runtime operation requires
`--sandbox-id` before a request is sent.

## Runtime Services

- `src/command/` owns command admission, active/completed command tracking,
  transcript access, launch, input, cancellation, and finalization.
- `src/file/` owns snapshot and live-session file read/write/edit/blame plus
  the HTTP-only listing handler.
- `src/workspace_session/` owns internal session create, resolve, capture,
  remount, destroy, and finalization transitions.
- `src/layerstack/` owns application-level publish, squash, export, and read
  orchestration over the layerstack primitive.

Command execution targets a workspace session. An automatically created session
publishes and destroys according to its finalization policy; an explicitly
managed session remains until internal teardown. File operations and remounts
run through the session admission gate without independently extending the
session lifecycle.

## Runtime Primitive Packages

- `sandbox-runtime-workspace` owns workspace lifecycle, handles, capture,
  destroy, and remount primitives.
- `sandbox-runtime-layerstack` owns content hashes, manifests, layers, storage,
  leases, and CAS fixtures.
- `sandbox-runtime-namespace-execution` owns command launch, PTY I/O,
  transcripts, and execution state.
- `sandbox-runtime-namespace-process` owns namespace holder/runner bodies,
  runner transport DTOs, and `setns` execution.
- `sandbox-runtime-overlay` owns low-level overlay mount, move, and unmount
  primitives used by the other runtime primitives.
- `sandbox-observability` owns leaf tracing, event, sampling, and reading
  primitives used by runtime services.

`crates/sandbox-runtime/` is an organizational namespace only. It has no root
`Cargo.toml`, Rust facade, package identity, or re-export layer.

## Wiring

The daemon constructs the service graph and exposes one runtime aggregate:

```rust
pub struct SandboxRuntimeOperations {
    pub command: Arc<CommandOperationService>,
    pub workspace_session: Arc<WorkspaceSessionService>,
    pub layerstack: Arc<LayerStackService>,
    pub file: Arc<FileService>,
}
```

```text
FileService -> LayerStackService
WorkspaceRuntimeService + LayerStackService -> WorkspaceSessionService
WorkspaceSessionService -> CommandOperationService
CommandOperationService + WorkspaceSessionService + LayerStackService + FileService
  -> SandboxRuntimeOperations
```

The daemon's observability adapter reads neutral snapshots from this aggregate
and supplies them to `sandbox-observability-application`; the two applications
do not depend on one another.

## Verification

```sh
cargo fmt -p sandbox-runtime -- --check
cargo check -p sandbox-runtime --all-targets --all-features
cargo test -p sandbox-runtime --all-features
cargo clippy -p sandbox-runtime --all-targets --all-features -- -D warnings
```
